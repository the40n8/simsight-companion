"""Dining/desk chair snapping through native Build/Buy attachment slots.

No guessed chair offsets or rotation, no input polling and no game actions
until the ordinary placement acknowledgement/ownership/payment transaction.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    from typing import TypedDict

    _Transform = tuple[float, float, float, float, float, float, float]
    _SlotKey = tuple[int, int | None]

    class ChairRequest(TypedDict):
        """What Enter asked for; rechecked after the tool-drop acknowledgement."""

        zone: int
        product: int
        table: int
        definition: int
        state: tuple[int, _Transform]
        slot: _SlotKey
        slot_state: _Transform
        tile: tuple[int, ...]
        level: int
        facing: int
        focused: int | None
        seat_tile: tuple[int, ...]


CHAIRS = {'buycatssdiningchair': 'buycatssdiningtable', 'buycatssdeskchair': 'buycatssdesk'}
MAX_SLOTS = 128


def _log(text: str) -> None:
    simsight_log.line('chair snap: ' + text)


def tag_names(definition: Any) -> set[str]:
    """The definition's tags as lower-case names without underscores."""
    from tag import Tag

    def name(value: Any) -> str | None:
        try:
            return str(Tag(value).name).lower().replace('_', '')
        except (ValueError, TypeError, AttributeError):  # a tag this game version does not name
            return None

    try:
        values = definition.get_tags() or ()
    except (AttributeError, TypeError):
        return set()
    return {found for found in map(name, values) if found is not None}


def side_of(di: int, dj: int) -> int:
    """The grid facing (0..3) that points along (di, dj); (di, dj) is not (0, 0)."""
    if abs(di) > abs(dj):
        return 1 if di > 0 else 3
    return 0 if dj > 0 else 2


def _kind(item: dict[str, Any]) -> tuple[Any, str] | None:
    """(chair definition, wanted table tag) when the product is a chair."""
    import services
    import simsight_settings as settings

    if not settings.get('chair_snapping', True):
        return None
    try:
        definition = services.definition_manager().get(item['id'], get_fallback_definition_id=False)
        tags = tag_names(definition)
        for chair, table in CHAIRS.items():
            if chair in tags:
                return definition, table
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        _log(f'classification unavailable: {exc!r}')
    return None


def _root(obj: Any) -> Any:
    for _ in range(8):
        if not getattr(obj, 'is_part', False):
            return obj
        obj = getattr(obj, 'part_owner', None)
        if obj is None:
            return None
    return None


def _finite(values: _Transform, what: str) -> _Transform:
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f'Non-finite {what} transform')
    return values


def _transform(p: Any, q: Any) -> _Transform:
    return (
        float(p.x),
        float(p.y),
        float(p.z),
        float(q.x),
        float(q.y),
        float(q.z),
        float(q.w),
    )


def _state(obj: Any) -> tuple[int, _Transform]:
    return int(obj.level), _finite(_transform(obj.position, obj.orientation), 'table')


def _slot_state(slot: Any) -> _Transform:
    return _finite(_transform(slot.position, slot.transform.orientation), 'seat')


def _safe_target(obj: Any, level: int) -> bool:
    import services
    import simsight_place as place

    try:
        return (
            obj is not None
            and not getattr(obj, 'is_part', False)
            and int(obj.level) == int(level)
            and not obj.is_in_inventory()
            and not obj.is_hidden()
            and obj.get_household_owner_id() in (None, 0, place.household_id())
            and services.active_lot().is_position_on_lot(obj.position)
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _nearby_tables(frame: Any, wanted: str) -> list[tuple[int, Any]]:
    """(distance squared, table) on the cursor tile and the one it faces,
    plus its diagonal neighbours on that side, nearest first."""
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    candidates: dict[Any, tuple[int, Any]] = {}
    for di in range(-1, 2):
        for dj in range(-1, 2):
            if not frame.inside(i + di, j + dj):
                continue
            if (di or dj) and int(grid.location.facing) % 4 != side_of(di, dj):
                continue
            for obj in tiles.objects_on(frame, i + di, j + dj, grid.location.level):
                if not _safe_target(obj, grid.location.level) or wanted not in tag_names(
                    obj.definition
                ):
                    continue
                row = (di * di + dj * dj, obj)
                if obj.id not in candidates or row[0] < candidates[obj.id][0]:
                    candidates[obj.id] = row
    return sorted(candidates.values(), key=lambda row: (row[0], row[1].id))


def _target(item: dict[str, Any], frame: Any) -> tuple[Any, Any, bool] | None:
    """(chair definition, table or None when two are equally close, focused)."""
    import simsight_grid as grid
    import simsight_layers as layers

    kind = _kind(item)
    if kind is None:
        return None
    definition, wanted = kind
    focused = layers.focused_target()
    if (
        focused is not None
        and wanted in tag_names(focused.definition)
        and _safe_target(focused, grid.location.level)
    ):
        return definition, focused, True
    rows = _nearby_tables(frame, wanted)
    if not rows:
        return None
    if len(rows) > 1 and rows[0][0] == rows[1][0]:
        return definition, None, False
    return definition, rows[0][1], False


def _slots(table: Any) -> list[Any]:
    # The native SlotComponent yields part-owned slots and may also yield the
    # root-owned version of the same bone. Prefer the actual table part.
    found: dict[int, Any] = {}
    for index, slot in enumerate(table.get_runtime_slots_gen()):
        if index >= MAX_SLOTS:
            raise ValueError('Too many attachment slots to check safely')
        if _root(slot.owner) is not table:
            continue
        key = int(slot.slot_name_hash)
        if key not in found or getattr(slot.owner, 'is_part', False):
            found[key] = slot
    return list(found.values())


def _key(slot: Any) -> _SlotKey:
    owner = slot.owner
    return int(slot.slot_name_hash), (
        int(owner.part_group_index) if getattr(owner, 'is_part', False) else None
    )


def _ranked_slots(table: Any, point: Any, key: Any) -> list[tuple[float, _SlotKey, Any]]:
    """Free, enabled, non-decorative seats of table (only `key` when given)."""
    ranked = []
    for slot in _slots(table):
        try:
            if key is not None and _key(slot) != tuple(key):
                continue
            if not slot.is_enabled or not slot.empty or slot.is_deco_slot():
                continue
            p = slot.position
            distance = (float(p.x) - float(point.x)) ** 2 + (float(p.z) - float(point.z)) ** 2
            if not math.isfinite(distance):
                continue
            ranked.append((distance, _key(slot), slot))
        except Exception as exc:  # EA's native slot can refuse any query: skip that seat
            _log(f'slot metadata refused: {exc!r}')
    return sorted(ranked, key=lambda row: (row[0], row[1][0]))


def _seat(table: Any, definition: Any, point: Any, obj: Any = None, key: Any = None) -> Any:
    """The nearest seat the game's own placement test accepts, or None."""
    for _distance, _ident, slot in _ranked_slots(table, point, key):
        if _accepts(table, slot, definition, obj):
            return slot
    return None


def _accepts(table: Any, slot: Any, definition: Any, obj: Any) -> bool:
    try:
        valid = (
            slot.is_valid_for_placement(definition=definition)
            if obj is None
            else slot.is_valid_for_placement(obj=obj)
        )
        _log(f'table {table.id} slot {_key(slot)} native validation={valid!r}')
        return bool(valid)
    except Exception as exc:  # EA's native check can raise anything: try the next seat
        _log(f'native slot check failed: {exc!r}')
        return False


def _request(item: dict[str, Any], frame: Any) -> tuple[ChairRequest | None, str | None]:
    """(request, None) to snap; (None, refusal) to refuse; (None, None) when not a chair."""
    import services
    import simsight_grid as grid
    import simsight_layers as layers
    import simsight_tiles as tiles

    target = _target(item, frame)
    if target is None:
        return None, None
    definition, table, _explicit = target
    if table is None:
        return None, 'More than one table is equally close. Focus the intended table with 1 first.'
    point = tiles.position(frame, *grid.location.tile, grid.location.level)
    slot = _seat(table, definition, point)
    if slot is None:
        return (
            None,
            'The table has no free compatible chair seat that the game accepts. Nothing was bought.',
        )
    focused = layers.focused_target()
    request: ChairRequest = {
        'zone': int(services.current_zone_id()),
        'product': int(item['id']),
        'table': int(table.id),
        'definition': int(table.definition.id),
        'state': _state(table),
        'slot': _key(slot),
        'slot_state': _slot_state(slot),
        'tile': tuple(grid.location.tile),
        'level': int(grid.location.level),
        'facing': int(grid.location.facing),
        'focused': getattr(focused, 'id', None),
        'seat_tile': tuple(frame.tile_of(slot.position.x, slot.position.z)),
    }
    return request, None


def try_place(item: dict[str, Any], frame: Any) -> bool | None:
    """None delegates ordinary placement; bool means the chair path handled it."""
    import simsight_grid as grid
    import simsight_place as place

    try:
        request, error = _request(item, frame)
        if request is None and error is None:
            return None
        if error:
            speech.speak(error, explicit=True)
            return False
        target = grid.cursor()
        if target is None:
            return False
        return place.place(
            target[0], grid.location.level, grid.world_surface(), chair_request=request
        )
    except Exception:
        simsight_log.error('chair snap: request refused')
        speech.speak('The chair seat could not be checked. Nothing was bought.', explicit=True)
        return False


def _same_request(request: ChairRequest, item: dict[str, Any], level: int) -> bool:
    """Nothing the player controls changed since Enter."""
    import services
    import simsight_grid as grid
    import simsight_layers as layers

    return (
        request['zone'] == int(services.current_zone_id())
        and request['product'] == int(item['id'])
        and request['tile'] == tuple(grid.location.tile)
        and request['level'] == int(grid.location.level)
        and request['facing'] == int(grid.location.facing)
        and int(level) == request['level']
        and request['focused'] == getattr(layers.focused_target(), 'id', None)
    )


def validate(
    request: ChairRequest, obj: Any, item: dict[str, Any], position: Any, level: int
) -> tuple[Any, Any] | None:
    """Recheck exact target/slot after the native tool-drop acknowledgement."""
    import services

    try:
        if not _same_request(request, item, level):
            return None
        kind = _kind(item)
        if kind is None:
            return None
        table = services.object_manager().get(request['table'])
        if (
            not _safe_target(table, level)
            or _state(table) != request['state']
            or int(table.definition.id) != request['definition']
        ):
            return None
        if kind[1] not in tag_names(table.definition):
            return None
        slot = _seat(table, kind[0], position, obj=obj, key=request['slot'])
        if slot is not None and _slot_state(slot) == request['slot_state']:
            return table, slot
    except Exception:
        simsight_log.error('chair snap: acknowledged seat refused')
    return None


def attached(table: Any, slot: Any, obj: Any) -> bool:
    """The chair is the table's child, in exactly that seat."""
    try:
        hashed = getattr(obj.location, 'slot_hash', None) or getattr(
            obj.location, 'joint_name_hash', None
        )
        return (
            _root(obj.parent) is table
            and hashed is not None
            and int(hashed) == int(slot.slot_name_hash)
            and any(child is obj for child in slot.children)
        )
    except Exception:
        simsight_log.error('chair snap: attachment readback')
        return False


def fit_parts(item: dict[str, Any], frame: Any) -> list[str] | None:
    """Describe the same native seat Enter will request, without creating it."""
    try:
        request, error = _request(item, frame)
        if error:
            return [error.replace(' Nothing was bought.', '') + ' ']
        if request is not None:
            import services
            import simsight_grid as grid
            import simsight_tiles as tiles

            table = services.object_manager().get(request['table'])
            di, dj = (
                request['seat_tile'][0] - grid.location.tile[0],
                request['seat_tile'][1] - grid.location.tile[1],
            )
            return [
                'Chair will snap at ',
                grid.object_name_part(table),
                '. Seat ',
                tiles.relative(di, dj),
                '. Enter places it using the game’s validation. ',
            ]
    except Exception:
        simsight_log.error('chair snap: preview')
    return None


def has_target(item: dict[str, Any], frame: Any) -> bool:
    try:
        return _target(item, frame) is not None
    except Exception:
        simsight_log.error('chair snap: target check')
        return False


def announce(obj: Any, item: dict[str, Any], price: int, table: Any) -> None:
    import simsight_grid as grid
    import simsight_scanner as scanner

    scanner.speak_parts(
        [
            'Placed ',
            item.get('name') or 'chair',
            ' at ',
            grid.object_name_part(table),
            f' using the game’s chair slot, for {price} simoleons.',
        ]
    )
    _log(f'attached chair {obj.id} to table {table.id} and paid {price}')

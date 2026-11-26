"""Optional lot-planning descriptions and persistent whole-building labels.

Labels are kept in SimSight_BuildingLabels.json next to the log: a JSON
object of building signature to name (at most 60 characters), where the
signature is 'slot:zone:west:north:east:south:lowest floor:highest floor'.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Iterable

    Cell = tuple[int, int]
    Box = tuple[int, int, int, int]  # west, north, east, south (lot metres)

_SNAPSHOT_SECONDS = 3.0  # a building snapshot is reused this long
_NAME_LIMIT = 60
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class _Room:
    """One room the room scan found, with its cells in lot metres."""

    def __init__(self, room: Any, cells: set[Cell]) -> None:
        self.room = room
        self.level = int(room.level)
        self.cells = cells


class _Building:
    """Rooms that touch, side by side or one above the other."""

    def __init__(self, number: int, rooms: list[_Room], signature_head: tuple[int, int]) -> None:
        self.number = number  # 1 is the largest
        self.rooms = rooms
        self.cells: set[Cell] = set().union(*(r.cells for r in rooms))
        es = [p[0] for p in self.cells]
        ns = [p[1] for p in self.cells]
        self.levels = sorted({r.level for r in rooms})
        self.box: Box = (min(es), min(ns), max(es) + 1, max(ns) + 1)
        self.signature = '{}:{}:{}:{}:{}:{}:{}:{}'.format(
            *signature_head, *self.box, min(self.levels), max(self.levels)
        )
        self.doors: set[int] = set()
        self.windows: set[int] = set()
        self.name = ''


class _Snapshot:
    def __init__(self, frame: Any, buildings: list[_Building]) -> None:
        self.frame = frame  # simsight_plus_build's lot frame: cells in lot metres
        self.buildings = buildings


class _State:
    def __init__(self) -> None:
        self.enabled = False  # planning descriptions on the Build cursor
        self.cache_at = 0.0
        self.cache: _Snapshot | None = None
        self.labels: dict[str, str] | None = None  # loaded on first use


_state = _State()


def _log(text: str) -> None:
    simsight_log.line('lot grid: ' + text)


def _label_path() -> str:
    return os.path.join(os.path.dirname(simsight_log.PATH), 'SimSight_BuildingLabels.json')


def _labels() -> dict[str, str]:
    """The saved labels, read from the file once."""
    if _state.labels is None:
        _state.labels = _read_labels(_label_path())
    return _state.labels


def _read_labels(path: str) -> dict[str, str]:
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as ex:
        _log(f'label load failed: {ex!r}')
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v)[:_NAME_LIMIT] for k, v in data.items() if str(v).strip()}


def _save_labels() -> bool:
    path = _label_path()
    temporary = path + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(_labels(), stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as ex:
        _log(f'label save failed: {ex!r}')
        return False
    return True


def _slot_zone() -> tuple[int, int]:
    try:
        import services

        slot = int(services.get_persistence_service().get_save_slot_proto_buff().slot_id)
        return slot, int(services.current_zone_id())
    except Exception:  # no save slot yet: signatures fall back to 0:0
        return 0, 0


def _cells(room: Any, frame: Any) -> set[Cell]:
    out = set()
    for x, z in room.tiles:
        e, n = frame.local(x, z)
        out.add((int(e), int(n)))
    return out


def _touches(a: _Room, b: _Room) -> bool:
    if a.level == b.level:
        if a.cells.intersection(b.cells):
            return True
        return any((e + de, n + dn) in b.cells for e, n in a.cells for de, dn in _NEIGHBOURS)
    return abs(a.level - b.level) == 1 and bool(a.cells.intersection(b.cells))


def _object_kind(obj: Any) -> str:
    try:
        import simsight_plus_rooms as rooms

        words = ' '.join(rooms.tag_names(obj)).lower() + ' ' + type(obj).__name__.lower()
    except Exception:  # an object without tags is an ordinary object
        return 'object'
    if any(w in words for w in ('plant', 'flower', 'shrub', 'tree', 'garden')):
        return 'landscaping'
    if 'window' in words:
        return 'window'
    if 'door' in words:
        return 'door'
    return 'object'


def _group(records: list[_Room]) -> list[list[_Room]]:
    """Rooms joined into buildings, largest first."""
    remaining = set(range(len(records)))
    groups = []
    while remaining:
        todo = [remaining.pop()]
        indexes = set()
        while todo:
            i = todo.pop()
            indexes.add(i)
            linked = [j for j in list(remaining) if _touches(records[i], records[j])]
            for j in linked:
                remaining.remove(j)
                todo.append(j)
        groups.append([records[i] for i in indexes])
    groups.sort(key=lambda group: sum(len(r.cells) for r in group), reverse=True)
    return groups


def _opening(obj: Any, frame: Any) -> tuple[str, float, float, int] | None:
    """(kind, east, north, floor) of a door or window; None for anything else."""
    try:
        if obj.is_sim or obj.is_in_inventory():
            return None
        e, n = frame.local(obj.position.x, obj.position.z)
        level = int(obj.level)
    except Exception:  # an object that will not say where it is
        return None
    kind = _object_kind(obj)
    return (kind, e, n, level) if kind in ('door', 'window') else None


def _count_openings(buildings: list[_Building], frame: Any) -> None:
    """Doors and windows on or just outside each building's box."""
    import services

    for obj in services.object_manager().values():
        found = _opening(obj, frame)
        if found is None:
            continue
        kind, e, n, level = found
        for b in buildings:
            x1, z1, x2, z2 = b.box
            if level in b.levels and x1 - 1 <= e <= x2 + 1 and z1 - 1 <= n <= z2 + 1:
                (b.doors if kind == 'door' else b.windows).add(int(obj.id))
                break


def _snapshot(refresh: bool = False) -> _Snapshot:
    now = time.monotonic()
    cached = _state.cache
    if cached is not None and not refresh and now - _state.cache_at < _SNAPSHOT_SECONDS:
        return cached
    import simsight_plus_build as build
    import simsight_plus_rooms as rooms

    frame = build.frame()
    records = [_Room(room, _cells(room, frame)) for room in rooms.scan_rooms()]
    buildings = [
        _Building(number, group, _slot_zone()) for number, group in enumerate(_group(records), 1)
    ]
    _count_openings(buildings, frame)
    labels = _labels()
    for b in buildings:
        b.name = labels.get(b.signature) or (
            'Main building' if b.number == 1 else f'Unlabelled building {b.number - 1}'
        )
    snap = _Snapshot(frame, buildings)
    _state.cache, _state.cache_at = snap, now
    _log(
        'snapshot buildings={}'.format(
            [
                (b.name, b.box, b.levels, len(b.rooms), len(b.doors), len(b.windows))
                for b in buildings
            ]
        )
    )
    return snap


def _cell_of_tile(frame: Any, i: int, j: int) -> Cell | None:
    """The lot-metre cell (as the room scan counts them) under the Build
    cursor's tile (i, j); None without a lot frame."""
    import simsight_tiles as tiles

    tile_frame = tiles.frame()
    if tile_frame is None:
        return None
    e, n = frame.local(*tile_frame.world(i, j))
    return int(e), int(n)


def _room_at(i: int, j: int, level: int, snap: _Snapshot) -> tuple[_Building, _Room] | None:
    """The building and room whose cells hold the cursor tile (i, j)."""
    cell = _cell_of_tile(snap.frame, i, j)
    if cell is None:
        return None
    for building in snap.buildings:
        for record in building.rooms:
            if record.level == int(level) and cell in record.cells:
                return building, record
    return None


def _building_at(i: int, j: int, level: int, snap: _Snapshot | None = None) -> _Building | None:
    found = _room_at(i, j, level, snap or _snapshot())
    return found[0] if found is not None else None


def _plural(count: int) -> str:
    return '' if count == 1 else 's'


def _describe_tile(i: int, j: int, level: int) -> str:
    import simsight_tiles as tiles

    found = _room_at(i, j, level, _snapshot())
    if found is not None:
        building, record = found
        return f'{building.name}, {record.room.name}.'
    frame = tiles.frame()
    if frame is None:
        return 'Unclassified tile.'
    facts = tiles.ground(frame, i, j, level)
    objects = tiles.objects_on(frame, i, j, level)
    if facts.get('pool'):
        return 'Pool.'
    if objects:
        landscaping = sum(1 for obj in objects if _object_kind(obj) == 'landscaping')
        if landscaping:
            return f'Landscaping, {landscaping} plant object{_plural(landscaping)}; not clear for building.'
        return f'Occupied outdoor tile, {len(objects)} object{_plural(len(objects))}; not clear for building.'
    if facts.get('floor') and facts.get('outside'):
        return 'Exterior flooring, possible patio, porch, deck or path.'
    if facts.get('outside'):
        return 'Open outdoor ground; apparently clear, but construction still requires game validation.'
    return 'Unclassified tile.'


def room_parts(frame: Any) -> tuple[list[Any], dict[str, bool]]:
    """The Build cursor's room change parts, with the planning description
    of the tile when planning descriptions are on."""
    import simsight_grid as grid

    parts, facts = grid._room_parts_base(frame)
    if _state.enabled:
        try:
            parts = list(parts) + [_describe_tile(grid._tile[0], grid._tile[1], grid._level) + ' ']
        except Exception as ex:
            _log(f'tile announcement failed: {ex!r}')
    return parts, facts


def open_lot_grid() -> None:
    import simsight_grid as grid
    import simsight_plus_build as build
    import simsight_plus_core as core

    if not build.in_build_mode():
        core.speak('Enter Build mode first.')
        return
    _state.enabled = True
    _snapshot(refresh=True)
    if not grid.build_cursor() and not grid.enter():
        return
    core.speak(
        'Lot planning grid on. Arrow through the lot. Each tile announces buildings, rooms, pools, exterior flooring, landscaping, occupied ground or apparently clear outdoor ground. Escape leaves the ordinary grid; use the Build helper to turn planning descriptions off.'
    )
    grid.describe_build()


def close_lot_grid() -> None:
    import simsight_plus_core as core

    _state.enabled = False
    core.speak('Lot planning descriptions off. The ordinary Build grid remains unchanged.')


def _clean_name(text: str) -> str:
    return ' '.join(text.strip().split())[:_NAME_LIMIT]


def label_current_building() -> None:
    import simsight_grid as grid
    import simsight_plus_core as core

    if not grid.build_cursor():
        core.speak('Turn on the Build grid and move into the building first.')
        return
    building = _building_at(grid._tile[0], grid._tile[1], grid._level, _snapshot(refresh=True))
    if not building:
        core.speak('The cursor is not inside a detected building.')
        return
    signature = building.signature

    def got(text: str | None) -> None:
        value = _clean_name(text or '')
        labels = _labels()
        if value:
            labels[signature] = value
        else:
            labels.pop(signature, None)
        if _save_labels():
            _state.cache = None
            core.speak(f'Building labelled {value}.' if value else 'Building label cleared.')
        else:
            core.speak('The building label could not be saved.')

    core.ask_text(
        'Building name',
        'Type a custom name, or leave blank to clear it.',
        _labels().get(signature, ''),
        got,
    )


def register_box_label(box: Iterable[int], levels: Iterable[Any], name: str) -> bool:
    """Name the building whose box and floors match exactly (a planned
    detached building); False when none does or it could not be saved."""
    snap = _snapshot(refresh=True)
    wanted = {int(v) for v in levels}
    match = next(
        (b for b in snap.buildings if b.box == tuple(box) and set(b.levels) == wanted),
        None,
    )
    if not match:
        _log(f'planned building label not assigned; box={box} levels={levels} name={name!r}')
        return False
    _labels()[match.signature] = _clean_name(name)
    ok = _save_labels()
    _state.cache = None
    _log(
        'planned building label {} signature={} name={!r}'.format(
            'saved' if ok else 'failed', match.signature, name
        )
    )
    return ok


def describe_buildings() -> None:
    import simsight_plus_core as core

    snap = _snapshot(refresh=True)
    rows = []
    for b in snap.buildings:
        x1, n1, x2, n2 = b.box
        rows.append(
            '{}: footprint {} by {} metres, {} room{}, level{}, {} door{}, {} window{}.'.format(
                b.name,
                x2 - x1,
                n2 - n1,
                len(b.rooms),
                _plural(len(b.rooms)),
                ','.join(str(v) for v in b.levels),
                len(b.doors),
                _plural(len(b.doors)),
                len(b.windows),
                _plural(len(b.windows)),
            )
        )
    core.speak(' '.join(rows) if rows else 'No enclosed buildings were detected.')

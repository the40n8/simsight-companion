"""Target layers on the Build cursor: ground, six wall heights and ceiling.
Contributed by DaRainbowGirlXX (2026-10-16), merged into SimSight
2026-11-12.

Ctrl+Alt+U and Ctrl+Alt+D change the layer. With layer focus on (the
default), number 1 focuses the objects on that layer one by one, number 4
names the layer of each nearby object, Delete sells a focused object, and a
wall object is only placed while a wall height is the target. With layer
focus off, the two keys only change the wall height for a wall object.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log

if TYPE_CHECKING:
    from objects.slots import RuntimeSlot

    from simsight_tiles import Frame

SETTING_KEY = 'surface_layer_focus'
CEILING = 7  # layer 0 is the ground, 1 to 6 the wall heights, 7 the ceiling


class _State:
    def __init__(self) -> None:
        self.mode = 3  # the target layer
        self.wall_index = 2  # the last wall height chosen (middle)
        # The object number 1 focused, and where: tile, floor, layer, and
        # its place among the matches.
        self.focus_id: Any = None
        self.focus_where: tuple[Any, Any, Any] = (None, None, None)
        self.focus_index = -1


_state = _State()


def enabled() -> bool:
    import simsight_settings as settings

    return bool(settings.get(SETTING_KEY, True))


def _log(message: str) -> None:
    simsight_log.line('layers: ' + message)


def wall_height() -> tuple[str, float]:
    """(label, metres) of the chosen wall height."""
    import simsight_wall as wall

    return wall.HEIGHTS[_state.wall_index]


def _mode_info() -> tuple[str, str, float | None]:
    """(kind, spoken label, wall height or None) of the target layer."""
    if _state.mode == 0:
        return 'ground', lang.text('ground', 'podłoga'), None
    if _state.mode == CEILING:
        return 'ceiling', lang.text('ceiling', 'sufit'), None
    label, height = wall_height()
    return 'wall', lang.text('wall {}', 'ściana {}').format(label), height


def wall_refusal() -> bool:
    """True (and spoken) when a wall object would be placed while the target
    layer is not a wall height."""
    if not enabled() or _mode_info()[0] == 'wall':
        return False
    import simsight_place as place

    place.say_text(
        'Select a wall-height target with Control Alt U or Control Alt D before placing this wall object.',
        'Przed umieszczeniem obiektu ściennego wybierz wysokość ściany przez Control Alt U lub Control Alt D.',
    )
    return True


def definition_id(obj: Any) -> int | None:
    with contextlib.suppress(AttributeError, TypeError, ValueError):  # no definition: the bare id
        return int(obj.definition.id)
    try:
        return int(obj.definition_id)
    except (AttributeError, TypeError, ValueError):  # an object without a definition
        return None


def _kind(obj: Any) -> str:
    try:
        import simsight_place as place

        need = place.requirement(definition_id(obj))
    except Exception:  # unknown requirement: a ground object
        simsight_log.error('layers: object requirement')
        need = None
    if need == 'ceiling':
        return 'ceiling'
    if need in ('wall', 'fence'):
        return 'wall'
    return 'ground'


def _floor_y(frame: Frame, i: Any, j: Any, level: Any) -> float | None:
    try:
        import simsight_tiles as tiles

        return float(tiles.position(frame, i, j, level).y)
    except ValueError:  # no height at that tile
        return None
    except Exception:
        simsight_log.error('layers: floor height')
        return None


def _height(obj: Any, floor_y: float | None) -> float | None:
    if floor_y is None:
        return None
    try:
        return max(0.0, float(obj.position.y) - floor_y)
    except (AttributeError, TypeError, ValueError):  # an object without a position
        return None


def _label(
    obj: Any, frame: Frame | None = None, i: Any = None, j: Any = None, level: Any = None
) -> str:
    kind = _kind(obj)
    if kind == 'ceiling':
        return lang.text('ceiling', 'sufit')
    if kind == 'wall':
        value = _height(obj, _floor_y(frame, i, j, level)) if frame is not None else None
        if value is not None:
            return lang.text('wall, {:.1f} metres', 'ściana, {:.1f} metra').format(value)
        return lang.text('wall', 'ściana')
    return lang.text('ground', 'podłoga')


def _matches(obj: Any, frame: Frame, i: int, j: int, level: int) -> bool:
    kind, _label_text, selected_height = _mode_info()
    if _kind(obj) != kind:
        return False
    if kind != 'wall' or selected_height is None:
        return True
    value = _height(obj, _floor_y(frame, i, j, level))
    # Wall heights are 0.5 m apart. A quarter-metre window avoids silently
    # selecting an object that belongs to a neighboring height.
    return value is not None and abs(value - selected_height) <= 0.26


def _objects_here(frame: Frame) -> list[Any]:
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    return [
        obj
        for obj in tiles.objects_on(frame, i, j, grid.location.level)
        if not getattr(obj, 'is_sim', False)
    ]


def _children(obj: Any) -> list[Any]:
    import simsight_tiles as tiles

    children = tiles.children_on(obj)  # leaves out children the game will not list
    children.sort(key=lambda child: int(getattr(child, 'id', 0)))
    return children


def _focus_objects(objects: list[Any]) -> list[Any]:
    """Return tile objects followed by their attached contents."""
    result: list[Any] = []
    seen: set[Any] = set()

    def add(obj: Any, depth: int) -> None:
        obj_id = getattr(obj, 'id', None)
        if obj_id is None or obj_id in seen or getattr(obj, 'is_sim', False):
            return
        seen.add(obj_id)
        result.append(obj)
        if depth >= 4:
            return
        for child in _children(obj):
            add(child, depth + 1)

    for obj in sorted(objects, key=lambda item: int(getattr(item, 'id', 0))):
        add(obj, 0)
    return result


def _name_parts(obj: Any) -> list[Any]:
    import simsight_grid as grid

    value = grid.object_name_part(obj)
    return list(value) if isinstance(value, list) else [value]


def clear_focus() -> None:
    _state.focus_id = None
    _state.focus_where = (None, None, None)
    _state.focus_index = -1


def focus_detail(frame: Frame) -> bool:
    """Number 1: focus the next object on the target layer, then list the tile."""
    import simsight_grid as grid
    import simsight_scanner as scanner

    objects = _objects_here(frame)
    i, j = grid.location.tile
    matches = [
        obj for obj in _focus_objects(objects) if _matches(obj, frame, i, j, grid.location.level)
    ]
    matches.sort(key=lambda obj: int(getattr(obj, 'id', 0)))
    if not matches:
        clear_focus()
        scanner.speak_parts(_no_match_parts(objects))
        return True
    context = (grid.location.tile, grid.location.level, _state.mode)
    same = _state.focus_where == context
    index = (_state.focus_index + 1) % len(matches) if same else 0
    obj = matches[index]
    _state.focus_id, _state.focus_where, _state.focus_index = obj.id, context, index
    parts = [lang.text('Focused: ', 'Wybrano: ')]
    parts.extend(_name_parts(obj))
    parts.extend([', ', _label(obj, frame, i, j, grid.location.level), '. '])
    if len(matches) > 1:
        parts.append(
            lang.text(
                'Object {} of {}; press 1 again for the next. ',
                'Obiekt {} z {}; naciśnij ponownie 1, aby przejść dalej. ',
            ).format(index + 1, len(matches))
        )
    # Number 1 still lists everything on the tile, with contents and free spots.
    parts.append(lang.text('Objects here: ', 'Obiekty tutaj: '))
    parts.extend(grid.object_parts(objects))
    scanner.speak_parts(parts)
    return True


def _no_match_parts(objects: list[Any]) -> list[Any]:
    import simsight_grid as grid

    parts: list[Any] = [
        lang.text(
            'Nothing at the selected {} layer. ', 'Brak obiektów na wybranej warstwie {}. '
        ).format(_mode_info()[1])
    ]
    if objects:
        parts.append(lang.text('Objects here: ', 'Obiekty tutaj: '))
        parts.extend(grid.object_parts(objects))
    else:
        parts.append(lang.text('Nothing here.', 'Nic tu nie ma.'))
    return parts


def _nearby(frame: Frame, i: int, j: int, level: int) -> list[tuple[int, int, int, Any]]:
    """(squared distance, di, dj, object) for each object within
    tiles.NEARBY_RADIUS, the cursor tile included."""
    import simsight_tiles as tiles

    nearby: list[tuple[int, int, int, Any]] = []
    radius = tiles.NEARBY_RADIUS
    for di in range(-radius, radius + 1):
        for dj in range(-radius, radius + 1):
            if (di, dj) != (0, 0) and not frame.inside(i + di, j + dj):
                continue
            nearby.extend(
                (di * di + dj * dj, di, dj, obj)
                for obj in _objects_on_or_none(frame, i + di, j + dj, level)
            )
    return nearby


def _objects_on_or_none(frame: Frame, i: int, j: int, level: int) -> list[Any]:
    import simsight_tiles as tiles

    try:
        return tiles.objects_on(frame, i, j, level)
    except ValueError:  # no height at that tile: nothing listed there
        return []
    except Exception:
        simsight_log.error('layers: objects on a tile')
        return []


def nearby_detail(frame: Frame) -> bool:
    """Number 4: nearby objects with the layer each one is on."""
    import simsight_grid as grid
    import simsight_scanner as scanner
    import simsight_tiles as tiles

    i, j = grid.location.tile
    parts: list[Any] = []
    seen: set[Any] = set()
    count = 0
    for _distance, di, dj, obj in sorted(
        _nearby(frame, i, j, grid.location.level), key=lambda item: item[0]
    ):
        if obj.id in seen:
            continue
        seen.add(obj.id)
        parts.extend(_name_parts(obj))
        parts.extend(
            [
                ', ',
                _label(obj, frame, i + di, j + dj, grid.location.level),
                ', ',
                tiles.relative(di, dj),
                '. ',
            ]
        )
        count += 1
        if count >= 8:
            break
    if not count:
        parts = [
            lang.text('Nothing within {} tiles.', 'Nic w promieniu {} pól.').format(
                tiles.NEARBY_RADIUS
            )
        ]
    scanner.speak_parts(parts)
    return True


def focused_target() -> Any:
    """The object number 1 focused, while the cursor and layer are unchanged."""
    if not enabled() or _state.focus_id is None:
        return None
    import services
    import simsight_grid as grid
    import simsight_tiles as tiles

    if _state.focus_where != (grid.location.tile, grid.location.level, _state.mode):
        return None
    obj = services.object_manager().get(_state.focus_id)
    frame = tiles.frame()
    if (
        obj is None
        or frame is None
        or all(candidate.id != obj.id for candidate in _focus_objects(_objects_here(frame)))
    ):
        clear_focus()
        return None
    return obj


def _step_wall_height(step: int) -> bool:
    """Layer focus off: the keys only set a wall object's height."""
    import simsight_grid as grid
    import simsight_place as place
    import simsight_tiles as tiles
    import simsight_wall as wall

    item = place.product()
    if item is None or place.requirement(item['id']) != 'wall':
        place.say_text(
            'Choose a wall object first; these keys set its height.',
            'Najpierw wybierz obiekt ścienny; te klawisze ustawiają jego wysokość.',
        )
        return False
    _state.wall_index = max(0, min(len(wall.HEIGHTS) - 1, _state.wall_index + step))
    _state.mode = _state.wall_index + 1
    label, height = wall_height()
    direction = tiles.direction_name(int(grid.location.facing) % 4)
    available = wall.selected_wall() is not None
    place.say_text(
        'Height {}, {:.1f} metres. {} {}.'.format(
            label, height, 'Wall' if available else 'No wall', direction
        ),
        'Wysokość {}, {:.1f} metra. {} {}.'.format(
            label, height, 'Ściana' if available else 'Brak ściany', direction
        ),
    )
    return True


def change_layer(delta: int) -> bool:
    """Ctrl+Alt+U (up) and Ctrl+Alt+D (down) with the Build cursor on."""
    import simsight_place as place

    step = 1 if delta > 0 else -1
    if not enabled():
        return _step_wall_height(step)
    _state.mode = max(0, min(CEILING, _state.mode + step))
    clear_focus()
    if 1 <= _state.mode <= 6:
        _state.wall_index = _state.mode - 1
    _kind_name, label, height = _mode_info()
    if height is not None:
        place.say_text(
            f'Target layer: {label}, {height:.1f} metres. Press 1 to focus an object here.',
            f'Warstwa docelowa: {label}, {height:.1f} metra. Naciśnij 1, aby wybrać obiekt.',
        )
    else:
        place.say_text(
            f'Target layer: {label}. Press 1 to focus an object here.',
            f'Warstwa docelowa: {label}. Naciśnij 1, aby wybrać obiekt.',
        )
    return True


def surface_slots(surface: Any) -> list[RuntimeSlot]:
    try:
        return list(surface.get_runtime_slots_gen())
    except AttributeError:  # an object without runtime slots
        return []
    except Exception:
        simsight_log.error('layers: runtime slots')
        return []


def _slot_valid(target_id: Any, index: int, slot: RuntimeSlot, candidate: Any) -> bool:
    try:
        valid = slot.empty and slot.is_valid_for_placement(obj=candidate)
    except Exception as exc:  # EA's native placement test
        simsight_log.error('layers: slot validation')
        _log(f'surface {target_id} slot {index} validation error: {exc!r}')
        return False
    _log(f'surface {target_id} slot {index} native validation={valid!r}')
    return bool(valid)


def selected_surface_slot(target_id: Any, candidate: Any, level: Any) -> tuple[Any, Any] | None:
    """Only the explicitly focused surface; native slots decide compatibility."""
    import services
    import simsight_place as place

    target = services.object_manager().get(target_id)
    if target is None or target is not focused_target():
        return None
    if int(target.level) != int(level) or target.is_in_inventory() or target.is_hidden():
        return None
    owner = target.get_household_owner_id()
    if owner and owner != place.household_id():
        return None
    if not services.active_lot().is_position_on_lot(target.position):
        return None
    for index, slot in enumerate(surface_slots(target)):
        if _slot_valid(target_id, index, slot, candidate):
            return target, slot
    return None

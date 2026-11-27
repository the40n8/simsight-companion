"""Read-only one-metre sampling cursor on the current lot.

Inspired by glaroc's a11yscan grid, with EA 1.127's actual routing signature.
Object origins are not footprints; a navmesh point is not placement approval.
This never moves the native mouse, Sim, objects, camera, or household funds.

The cursor lives in _state, except its tile, floor and facing: other
modules read those in grid.location.
"""

from __future__ import annotations

import functools
import math
from typing import TYPE_CHECKING, Any, NamedTuple

import sims4.commands
import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    from collections.abc import Callable

    from sims.sim import Sim
    from world.lot import Lot

    from simsight_tiles import Frame

    Part = Any  # text, or a ('loc', hash) part the client resolves
    Parts = list[Part]
    Tile = tuple[int, int]

OFFER_SECONDS = 20.0


class _Offer(NamedTuple):
    """The nearest tile where the chosen object fits, after Enter found it
    does not fit on the cursor tile; a second Enter places it there."""

    origin: tuple[int, int, int, int]  # (i, j, floor, facing) where Enter was pressed
    product: Any
    point: Any
    facing: int
    at: float


class _Cursor:
    def __init__(self) -> None:
        self.active = False
        self.zone_id: Any = None
        self.x = 0.0
        self.z = 0.0
        self.origin = (0.0, 0.0)  # where the plain grid started
        # The Build cursor (see the section below).
        self.build = False
        self.offer: _Offer | None = None
        self.last_room: tuple[int | None, bool | None] = (None, None)  # of the last tile read
        # Moving furniture: the object picked up with M, and its name.
        self.move_id: Any = None
        self.move_name: str | None = None


_state = _Cursor()


class CursorLocation:
    """The cursor's tile, floor and facing, which other modules read as
    grid.location.tile, .level and .facing. Only _set_cursor() changes them."""

    def __init__(self) -> None:
        self.tile: Tile = (0, 0)
        self.level = 0
        self.facing = 0


location = CursorLocation()


def _set_cursor(
    tile: Tile | None = None, level: int | None = None, facing: int | None = None
) -> None:
    if tile is not None:
        location.tile = tile
    if level is not None:
        location.level = level
    if facing is not None:
        location.facing = facing


def _say(en: str, pl: str) -> None:
    speech.speak(lang.text(en, pl), explicit=True)


def _controls_enabled() -> bool:
    import simsight_build as build

    return build.controls_enabled()


def active() -> bool:
    try:
        import services

        if (
            services.current_zone_id() != _state.zone_id
            or services.active_lot() is None
            or not _controls_enabled()
        ):
            _state.active = False
    except AttributeError:  # no zone manager (loading, leaving): the grid is off
        _state.active = False
    return _state.active


def _sample_key(x: float, z: float) -> Tile:
    return math.floor(x + 0.5), math.floor(z + 0.5)


def world_surface() -> Any:
    import routing

    return routing.SurfaceIdentifier(
        _state.zone_id, location.level, routing.SurfaceType.SURFACETYPE_WORLD
    )


def _position(x: float | None = None, z: float | None = None) -> Any:
    import terrain
    from sims4.math import Vector3

    x = _state.x if x is None else x
    z = _state.z if z is None else z
    y = terrain.get_terrain_height(x, z, routing_surface=world_surface())
    if y is None or not math.isfinite(float(y)):
        raise ValueError('Grid height unavailable')
    return Vector3(x, float(y), z)


def _start(sim: Sim, lot: Lot) -> tuple[str, str] | None:
    """Put the cursor at its start point; returns the note to say first."""
    import services

    position, level, start_note = _start_point(sim, lot)
    _set_cursor(level=level)
    _state.x, _state.z = float(position.x), float(position.z)
    if not math.isfinite(_state.x) or not math.isfinite(_state.z):
        raise ValueError('Invalid start position')
    _state.zone_id = services.current_zone_id()
    _state.origin = (_state.x, _state.z)
    return start_note


def _started() -> tuple[bool, tuple[str, str] | None]:
    """Start the cursor and switch the Build aim off: (started, the note to
    say first); not started without a household, Sim or lot point."""
    import services

    sim = services.get_active_sim()
    lot = services.active_lot()
    if sim is None or lot is None:
        return False, None
    try:
        start_note = _start(sim, lot)
        import simsight_build as build

        if build.active() or build.aiming():
            build.request('off')
        _state.active = True
    except ValueError:  # no point on the lot to start from
        return False, None
    except Exception:  # EA's Sim and lot points, then the Build aim: the grid stays off
        simsight_log.error('grid: start')
        return False, None
    return True, start_note


def enter() -> bool:
    if not _controls_enabled():
        import simsight_build as build

        return build.refuse_disabled()
    import simsight_keys as keys
    import simsight_place as place

    building = place.in_build_mode()
    if keys.scanner_effective() and not building:
        _say(
            'Turn scanner mode off to use the lot grid.',
            'Wyłącz tryb skanera, aby użyć siatki parceli.',
        )
        return False
    started, start_note = _started()
    if not started:
        _state.active = False
        _say(
            'Load a household with an active Sim first.',
            'Najpierw wczytaj rodzinę z aktywnym Simem.',
        )
        return False
    note = (lang.text(*start_note) + ' ') if start_note is not None else ''
    if building and _start_build(note):
        return describe()
    if building:
        announce_placement(note)
    else:
        _say(
            note
            + 'Read-only lot grid on. Arrows sample one metre. Objects are listed by their origin, not their full footprint.',
            note
            + 'Siatka parceli włączona, tylko do odczytu. Strzałki przesuwają o metr. Lista podaje punkty początkowe obiektów, nie ich pełne obrysy.',
        )
    return describe()


def _selected_parts() -> Parts:
    """'Selected: name.' for the chosen catalog object; empty without one."""
    import simsight_place as place

    item = place.product()
    if item is not None and item['name']:
        return [lang.text('Selected: ', 'Wybrano: '), item['name'], '. ']
    return []


def announce_placement(note: str = '') -> bool:
    import simsight_scanner as scanner

    parts: Parts = [
        note,
        lang.text(
            'Build placement on. Control Alt arrows move one metre, Page Up and Page Down change floor, '
            'Control Alt Enter places the object chosen in the Build catalog, brackets rotate it, '
            'Z undoes, X sells what is here. ',
            'Umieszczanie włączone. Control Alt i strzałki przesuwają o metr, Page Up i Page Down zmieniają piętro, '
            'Control Alt Enter umieszcza obiekt wybrany w katalogu, nawiasy go obracają, '
            'Z cofa, X sprzedaje to, co tu stoi. ',
        ),
    ]
    parts += _selected_parts() or [
        lang.text(
            'Choose an object in the Build catalog, close the catalog, then come back here. ',
            'Wybierz obiekt w katalogu, zamknij katalog i wróć tutaj. ',
        )
    ]
    scanner.speak_parts(parts)
    return True


def placement_toggle() -> bool:
    """Ctrl+Alt+B: the lot grid as the Build placement cursor."""
    import simsight_place as place

    if not place.in_build_mode():
        _say(
            'Enter Build mode first. In Live mode, Control Alt T reads the lot grid.',
            'Najpierw włącz tryb budowania. W trybie życia Control Alt T odczytuje siatkę parceli.',
        )
        return False
    return toggle()


def _cancel_placement() -> None:
    try:
        import simsight_place as place

        place.cancel_pending()
    except Exception:
        simsight_log.error('grid: cancel pending placement')


# Start points tried when the active Sim is off the lot: (lot attribute, note).
_START_POINTS = (
    (
        'center',
        (
            'Your Sim is off the lot, so the grid starts at the centre of the lot.',
            'Twój Sim jest poza parcelą, więc siatka zaczyna się na środku parceli.',
        ),
    ),
    (
        'get_default_position',
        (
            'Your Sim is off the lot, so the grid starts near the front of the lot.',
            'Twój Sim jest poza parcelą, więc siatka zaczyna się przy froncie parceli.',
        ),
    ),
)


def _lot_point(lot: Lot, name: str) -> Any:
    """lot.center, or lot.get_default_position(); None when the lot will not say."""
    try:
        value = getattr(lot, name)
        candidate = value() if name == 'get_default_position' else value
        if candidate is not None and lot.is_position_on_lot(candidate):
            return candidate
    except (AttributeError, TypeError, ValueError):  # the lot will not give this point
        return None
    return None


def _start_point(sim: Sim, lot: Lot) -> tuple[Any, int, tuple[str, str] | None]:
    """(position, level, note): the active Sim's spot when it is on this lot;
    otherwise the lot centre or the game's default lot point (by the front
    door), whichever is on the lot, so the cursor never starts on a sidewalk
    it cannot leave."""
    position = sim.position
    if lot.is_position_on_lot(position):
        return position, int(sim.level), None
    for name, note in _START_POINTS:
        candidate = _lot_point(lot, name)
        if candidate is not None:
            return candidate, 0, note
    raise ValueError('No point on the lot to start from')


def leave() -> bool:
    _state.active = False
    _stop_build()
    _cancel_placement()
    _say('Lot grid off.', 'Siatka parceli wyłączona.')
    return True


def retire() -> bool:
    """Build controls switched off: stop the grid without a word (the
    settings row already says what changed)."""
    _state.active = False
    _stop_build()
    _cancel_placement()
    return True


def toggle() -> bool:
    return leave() if active() else enter()


def focus_position(position: Any, level: Any) -> bool:
    """Move the cursor to position on level (after a placement landed
    somewhere near the aimed point). Coordinates stay relative to the start."""
    if not active():
        return False
    x, z = float(position.x), float(position.z)
    if not (math.isfinite(x) and math.isfinite(z)):
        return False
    _state.x, _state.z = x, z
    _set_cursor(level=int(level))
    if _state.build:
        import simsight_tiles as tiles

        f = tiles.frame()
        if f is not None:
            _set_cursor(tile=f.clamp(*f.tile_of(x, z)))
            _state.x, _state.z = f.world(*location.tile)
    return True


def cursor() -> tuple[Any, int, Any] | None:
    """Validated read-only snapshot for explicitly aiming the native cursor."""
    if not active():
        return None
    import build_buy
    import services

    try:
        if (
            not int(build_buy.get_lowest_level_allowed())
            <= location.level
            <= int(build_buy.get_highest_level_allowed())
        ):
            return None
        position = _position()
        if not services.active_lot().is_position_on_lot(position):
            return None
    except (AttributeError, TypeError, ValueError):  # no height or lot here: not a point to aim at
        return None
    return position, location.level, _state.zone_id


def move(dx: int, dz: int) -> bool:
    if not active():
        return False
    if (dx, dz) not in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        return False
    if _state.build:
        return _build_move(dx, dz)
    import services

    try:
        position = _position(_state.x + dx, _state.z + dz)
        if not services.active_lot().is_position_on_lot(position):
            _say('Lot boundary.', 'Granica parceli.')
            return False
    except (AttributeError, TypeError, ValueError):  # no height or lot there
        _say('Tile information unavailable.', 'Informacje o polu niedostępne.')
        return False
    _state.x, _state.z = _state.x + dx, _state.z + dz
    return describe()


def change_level(delta: int) -> bool:
    if not active() or delta not in (-1, 1):
        return False
    import build_buy

    try:
        minimum = int(build_buy.get_lowest_level_allowed())
        maximum = int(build_buy.get_highest_level_allowed())
    except (TypeError, ValueError):  # the game will not give the floor range
        _say('Floor information unavailable.', 'Informacje o piętrach niedostępne.')
        return False
    if not minimum <= location.level + delta <= maximum:
        _say('Floor limit.', 'Granica pięter.')
        return False
    _set_cursor(level=location.level + delta)
    _state.offer = None
    return describe()


def _place_here() -> bool:
    import simsight_place as place

    target = cursor()
    if target is None:
        _say(
            'This grid point cannot take an object. Move the cursor.',
            'W tym punkcie siatki nie można umieścić obiektu. Przesuń kursor.',
        )
        return False
    position, level, _ident = target
    return place.place(position, level, world_surface())


def _enter_key() -> bool:
    import simsight_place as place

    if _state.build and place.in_build_mode():
        return _build_place()
    return _place_here() if place.in_build_mode() else describe()


def _sell_target() -> Any:
    """The object Delete would sell here: topmost first, as the game's own
    Build cursor picks, a computer on a desk before the desk (live
    2026-09-26). An object focused with number 1 on a target layer comes
    first (simsight_layers). None when the tile has nothing to sell."""
    import simsight_layers as layers

    focused = layers.focused_target()
    if focused is not None:
        return focused
    try:
        objects = [obj for obj in _objects_at_origin() if not getattr(obj, 'is_sim', False)]
        objects.sort(key=_parent_depth, reverse=True)
    except AttributeError:  # no object manager: nothing to sell
        objects = []
    return objects[0] if objects else None


def _sell_here() -> bool:
    import simsight_place as place

    return place.sell(_sell_target())


def _parent_depth(obj: Any) -> int:
    depth, parent = 0, getattr(obj, 'parent', None)
    while parent is not None and depth < 8:
        depth, parent = depth + 1, getattr(parent, 'parent', None)
    return depth


def _no_redo() -> bool:
    _say('Nothing to redo.', 'Nie ma czego ponowić.')
    return False


def _at_origin(obj: Any) -> bool:
    """On the cursor's floor and one-metre sample, not in an inventory."""
    try:
        if obj.is_in_inventory() or int(obj.level) != location.level:
            return False
        pos = obj.position
        # Cells are relative to the starting Sim so fractional starts
        # don't put the cursor and object in different arbitrary grids.
        ox, oz = _state.origin
        return _sample_key(pos.x - ox, pos.z - oz) == _sample_key(_state.x - ox, _state.z - oz)
    except Exception:  # an object that will not say where it is: not here
        simsight_log.error('grid: object origin')
        return False


def _objects_at_origin() -> list[Any]:
    """Fresh lookup: no stale references after moves, sales or zone changes."""
    import services

    return [obj for obj in services.object_manager().values() if _at_origin(obj)]


# Facts the plain grid reads at its point: (fact, build_buy function).
_POINT_FACTS = (
    ('floor', 'has_floor_at_location'),
    ('pool', 'is_location_pool'),
    ('ground', 'is_location_natural_ground'),
    ('outside', 'is_location_outside'),
)


def facts() -> dict[str, bool]:
    """Independent queries preserve unknown values rather than inventing facts."""
    import simsight_tiles as tiles

    try:
        position = _position()
    except (AttributeError, TypeError, ValueError):  # no height here: nothing is known
        return {}
    result = tiles.location_facts(position, location.level, _POINT_FACTS)
    navmesh = _navmesh(position)
    if navmesh is not None:
        result['navmesh'] = navmesh
    return result


def _navmesh(position: Any) -> bool | None:
    """Whether a walking surface is at position; None when unknown."""
    import routing

    try:
        # Verified in EA objects/pools/pond.py; this takes surface, position.
        value = routing.test_point_placement_in_navmesh(world_surface(), position)
    except Exception:  # EA's native navmesh query: the game will not say
        simsight_log.error('grid: navmesh')
        return None
    return bool(value) if value is not None else None


# What the plain grid says for each fact: (when true, when false or None).
_FACT_LABELS = {
    'floor': (('floor', 'podłoga'), ('no floor', 'brak podłogi')),
    'pool': (('pool', 'basen'), None),
    'ground': (('natural ground', 'naturalny grunt'), None),
    'outside': (('outside', 'na zewnątrz'), ('inside', 'wewnątrz')),
    'navmesh': (
        ('walking surface present', 'powierzchnia do chodzenia'),
        ('no walking surface at this point', 'brak powierzchni do chodzenia w tym punkcie'),
    ),
}


def _add_origin_parts(parts: Parts) -> None:
    """The plain grid's object origins in this sample, added to parts one
    by one (a failure part way keeps the names already added)."""
    import simsight_scanner as scanner

    objects = _objects_at_origin()
    if not objects:
        parts.append(
            lang.text(
                'No object origins in this sample.',
                'Brak punktów początkowych obiektów w tym polu.',
            )
        )
        return
    parts.append(lang.text('Object origins here: ', 'Punkty początkowe obiektów tutaj: '))
    for index, obj in enumerate(objects[:5]):
        if index:
            parts.append(', ')
        entry = scanner.entry_for(
            obj, obj.position, location.level, bool(getattr(obj, 'is_sim', False)), '', ''
        )
        parts.append(scanner.name_part(entry))
    if len(objects) > 5:
        parts.append(lang.text(' and {} more', ' i {} więcej').format(len(objects) - 5))


def describe() -> bool:
    if not active():
        return False
    if _state.build:
        return describe_build()
    import simsight_scanner as scanner

    ox, oz = _state.origin
    parts: Parts = [
        lang.text('Grid x {}, z {}, floor {}. ', 'Siatka x {}, z {}, piętro {}. ').format(
            round(_state.x - ox), round(_state.z - oz), location.level
        )
    ]
    values = facts()
    for key, pair in _FACT_LABELS.items():
        if key in values:
            label = pair[0 if values[key] else 1]
            if label:
                parts.append(lang.text(*label) + '. ')
    if not values:
        parts.append(lang.text('Tile information unavailable. ', 'Informacje o polu niedostępne. '))
    try:
        _add_origin_parts(parts)
    except Exception:  # objects the game will not describe
        simsight_log.error('grid: object origins')
        parts.append(
            lang.text('Object information unavailable.', 'Informacje o obiektach niedostępne.')
        )
    scanner.speak_parts(parts)
    return True


# Keys of the plain grid and the Build cursor: intent -> (dx, dz) or floor step.
_MOVES = {
    'left': (-1, 0),
    'right': (1, 0),
    'up': (0, 1),
    'down': (0, -1),
    'prevcat': (-1, 0),
    'nextcat': (1, 0),
    'prev': (0, 1),
    'next': (0, -1),
}
_FLOORS = {'prevsub': 1, 'nextsub': -1, 'floorup': 1, 'floordown': -1}


def _key_actions() -> dict[str, Callable[[], Any]]:
    import simsight_place as place

    actions: dict[str, Callable[[], Any]] = {
        'open': _enter_key,
        'repeat': describe,
        'where': describe,
        'status': describe,
        'rescan': describe,
        'back': leave,
        'off': leave,
        'on': enter,
        'toggle': toggle,
        'rotateleft': lambda: place.rotate(-1),
        'rotateright': lambda: place.rotate(1),
        'undo': place.undo,
        'redo': _no_redo,
        'sell': _sell_here,
        'place': _place_here,
    }
    for intent, (dx, dz) in _MOVES.items():
        actions[intent] = functools.partial(move, dx, dz)
    for intent, delta in _FLOORS.items():
        actions[intent] = functools.partial(change_level, delta)
    return actions


def _build_key_actions() -> dict[str, Callable[[], Any]]:
    """The Build cursor's own keys, over the plain grid's."""
    import simsight_layers as layers
    import simsight_rooms as rooms

    actions: dict[str, Callable[[], Any]] = {
        'place': _enter_key,
        'turn': lambda: _turn(1),
        'turnback': lambda: _turn(-1),
        'last': _jump_to_last,
        'moveobject': _move_key,
        'layerup': lambda: layers.change_layer(1),
        'layerdown': lambda: layers.change_layer(-1),
        'roomlabel': rooms.show_picker,
    }
    for topic in range(1, 7):
        actions[f'info{topic}'] = functools.partial(_detail, topic)
    return actions


def on_key(intent: str) -> Any:
    actions = _key_actions()
    if _state.build:
        if intent in ('back', 'off') and _state.move_id is not None:
            _clear_move()  # Escape cancels a move before it leaves the cursor
            _say('Move cancelled.', 'Anulowano przesuwanie.')
            return True
        actions.update(_build_key_actions())
    callback = actions.get(intent)
    return callback() if callback is not None else False


# --------------------------------------------------------- the Build cursor
#
# Ctrl+Alt+B in Build mode: the grid becomes a lot-aligned tile cursor that
# owns plain keys (TooltipManager A11yBuildGridOwner reports it; keys.py
# routes A11yBuildGrid:<intent>). Arrows step one tile and read it: what
# stands there (by footprint), walls, doors and room changes, and whether
# the chosen catalog object fits there at the current facing. R turns the
# facing, Enter places exactly on the tile, or says why not and offers the
# nearest tile where it fits (a second Enter places it there). Number keys
# read one topic each. Falls back to the plain grid when the lot has no
# frame, so nothing is placed on a guessed grid.


def flash(message: str) -> None:
    try:
        import distributor.ops
        import distributor.system

        distributor.system.Distributor.instance().add_op_with_no_owner(
            distributor.ops.SendUIMessage(message)
        )
    except Exception:  # EA's distributor
        simsight_log.error(f'grid: {message} not sent')


def build_cursor() -> bool:
    return _state.build and active()


def _street_parts(f: Frame) -> Parts:
    import simsight_tiles as tiles

    street = _street_side(f)
    if street is None:
        return []
    return [
        lang.text('The street is to the {}. ', 'Ulica jest na {}. ').format(
            tiles.direction_name(street)
        )
    ]


def _start_build(note: str) -> bool:
    """Called by enter() in Build mode. False keeps the plain grid."""
    import simsight_tiles as tiles

    f = tiles.frame()
    if f is None:
        return False
    _set_cursor(tile=f.clamp(*f.tile_of(_state.x, _state.z)), facing=0)
    _state.x, _state.z = f.world(*location.tile)
    _state.offer = None
    _state.last_room = (None, None)
    _state.build = True
    flash('A11yBuildGridOn')
    import simsight_scanner as scanner

    parts: Parts = [
        note,
        lang.text(
            'Build placement on. Arrow keys move one tile. R turns the object, Enter places it, '
            'number keys 1 to 6 read details, Space repeats, Escape leaves. ',
            'Umieszczanie włączone. Strzałki przesuwają o jedno pole. R obraca obiekt, Enter go umieszcza, '
            'klawisze od 1 do 6 czytają szczegóły, spacja powtarza, Escape kończy. ',
        ),
    ]
    parts += _selected_parts() or [
        lang.text(
            'Choose an object in the Build catalog; Tab returns to it. ',
            'Wybierz obiekt w katalogu; Tab do niego wraca. ',
        )
    ]
    parts += _street_parts(f)
    scanner.speak_parts(parts)
    return True


def _stop_build() -> None:
    if _state.build:
        flash('A11yBuildGridOff')
    _state.build, _state.offer = False, None
    _clear_move()


def _street_side(f: Frame) -> int | None:
    """Direction of the lot edge nearest the game's default lot point (by the
    street), or None."""
    try:
        import services

        point = services.active_lot().get_default_position()
        i, j = f.tile_of(point.x, point.z)
    except (AttributeError, TypeError, ValueError):  # the lot will not give its default point
        return None
    gaps = ((f.depth - 1 - j, 0), (f.width - 1 - i, 1), (j, 2), (i, 3))
    return min(gaps)[1]


def _go_to_tile(f: Frame, ni: int, nj: int) -> None:
    _set_cursor(tile=(ni, nj))
    _state.offer = None
    _state.x, _state.z = f.world(ni, nj)


def _build_move(dx: int, dz: int) -> bool:
    import simsight_tiles as tiles

    f = tiles.frame()
    if f is None:
        return False
    ni, nj = location.tile[0] + dx, location.tile[1] + dz
    if not f.inside(ni, nj):
        _say('Lot edge.', 'Granica parceli.')
        return False
    _go_to_tile(f, ni, nj)
    return describe()


def object_name_part(obj: Any) -> Part:
    import simsight_scanner as scanner

    try:
        entry = scanner.entry_for(
            obj, obj.position, location.level, bool(getattr(obj, 'is_sim', False)), '', ''
        )
        return scanner.name_part(entry)
    except Exception:  # an object the scanner cannot name
        simsight_log.error('grid: object name')
        return lang.text('an object', 'obiekt')


def _children_parts(children: list[Any]) -> Parts:
    parts: Parts = [lang.text(' with ', ' z: ')]
    for number, child in enumerate(children[:3]):
        if number:
            parts.append(lang.text(' and ', ' i '))
        parts.append(object_name_part(child))
    parts.append(lang.text(' on it', ' na nim'))
    return parts


def object_parts(objects: list[Any], spots: bool = True) -> Parts:
    import simsight_tiles as tiles

    parts: Parts = []
    for index, obj in enumerate(objects[:4]):
        if index:
            parts.append(', ')
        parts.append(object_name_part(obj))
        children = tiles.children_on(obj)
        if children:
            parts += _children_parts(children)
        if spots:
            free = tiles.free_spots(obj)
            if free:
                parts.append(lang.text(', free spots: {}', ', wolne miejsca: {}').format(free))
    if len(objects) > 4:
        parts.append(lang.text(' and {} more', ' i {} więcej').format(len(objects) - 4))
    parts.append('. ')
    return parts


def _wall_parts(f: Frame, all_sides: bool = False) -> Parts:
    """Walls (a different room on each side), possible fences, doors and
    the lot edge; beside walls, the rooms on either side."""
    import simsight_rooms as rooms
    import simsight_wall as wall

    parts: Parts = list(wall.wall_parts(f, all_sides=all_sides))
    if not all_sides:  # number 3 is the complete four-side report
        parts += rooms.wall_context(f)
    return parts


def _room_change(label: str | None, room: int | None, outside: bool | None) -> Parts:
    """'Outside.', 'Inside the kitchen.' or 'Entering the kitchen.' when the
    cursor crossed into another room since the last tile read."""
    previous_room, previous_outside = _state.last_room
    if previous_outside is not None and outside is not None and outside != previous_outside:
        if outside:
            return [lang.text('Outside. ', 'Na zewnątrz. ')]
        return [
            lang.text('Inside {}. ', 'Wewnątrz: {}. ').format(label)
            if label
            else lang.text('Inside. ', 'Wewnątrz. ')
        ]
    if previous_room is not None and room is not None and room != previous_room and not outside:
        return [
            lang.text('Entering {}. ', 'Wchodzisz: {}. ').format(label)
            if label
            else lang.text('Another room. ', 'Inne pomieszczenie. ')
        ]
    return []


def room_parts_base(f: Frame) -> tuple[Parts, dict[str, bool]]:
    import simsight_rooms as rooms
    import simsight_tiles as tiles

    i, j = location.tile
    ground = tiles.ground(f, i, j, location.level)
    room = tiles.room(f, i, j, location.level)
    outside = ground.get('outside')
    label = rooms.display_name(room, location.level) if room is not None and not outside else None
    parts = _room_change(label, room, outside)
    _state.last_room = (room, outside)
    return parts, ground


def _special_fit(f: Frame, item: dict[str, Any], need: Any) -> Parts | None:
    """What a chair, wall object, experimental surface, slot object or an
    object with a known need says here; None for an ordinary object."""
    import simsight_chair_snap as chair_snap
    import simsight_place as place
    import simsight_surfaces as surfaces_mod
    import simsight_tiles as tiles

    snap_parts = chair_snap.fit_parts(item, f)
    if snap_parts is not None:
        return snap_parts
    if need == 'wall':
        import simsight_wall as wall

        return wall.fit_parts()
    if surfaces_mod.handles(need):
        return surfaces_mod.fit_parts(need)
    if need == 'slot':
        i, j = location.tile
        surfaces = [
            obj for obj in tiles.objects_on(f, i, j, location.level) if tiles.free_spots(obj)
        ]
        return [
            lang.text('It can go on a surface here. ', 'Może stanąć tu na powierzchni. ')
            if surfaces
            else lang.text(
                'It needs a surface with a free spot. ', 'Wymaga powierzchni z wolnym miejscem. '
            )
        ]
    if need in place.NEEDS:
        return [lang.text(*place.NEEDS[need]), ' ']
    return None


def _fit_verdict(point: Any, why: str | None, facing: str | None) -> Parts:
    """'Fits here', 'Fit unknown here' or 'Does not fit here: why', with the
    facing when one is given."""
    if point is not None:
        if facing is None:
            return [lang.text('Fits here. ', 'Mieści się tutaj. ')]
        return [
            lang.text('Fits here, facing {}. ', 'Mieści się tutaj, skierowany na {}. ').format(
                facing
            )
        ]
    if why is None:
        return [lang.text('Fit unknown here. ', 'Nie wiadomo, czy się tu zmieści. ')]
    if facing is None:
        return [lang.text('Does not fit here: {}. ', 'Nie mieści się tutaj: {}. ').format(why)]
    return [
        lang.text(
            'Does not fit here facing {}: {}. ', 'Nie mieści się tutaj, skierowany na {}: {}. '
        ).format(facing, why)
    ]


def fit_parts_base(f: Frame, with_facing: bool = True) -> Parts:
    """What the chosen object would do on this tile at the current facing."""
    import simsight_place as place
    import simsight_tiles as tiles

    item = place.product()
    if item is None:
        return []
    need = place.requirement(item['id'])
    special = _special_fit(f, item, need)
    if special is not None:
        return special
    i, j = location.tile
    point, why = tiles.fit_at(f, item['id'], i, j, location.level, location.facing)
    facing = tiles.direction_name(location.facing)
    return _fit_verdict(point, why, facing if with_facing else None)


def _ground_part(objects: list[Any], ground: dict[str, bool]) -> Parts:
    """What stands on the tile, or what kind of empty tile it is."""
    if objects:
        return object_parts(objects)
    if ground.get('pool'):
        return [lang.text('Pool. ', 'Basen. ')]
    if ground.get('floor') is False and ground.get('outside'):
        return [lang.text('Empty ground. ', 'Pusty teren. ')]
    return [lang.text('Empty floor. ', 'Pusta podłoga. ')]


def describe_build() -> bool:
    import simsight_scanner as scanner
    import simsight_tiles as tiles

    f = tiles.frame()
    if f is None:
        _say('Tile information unavailable.', 'Informacje o polu niedostępne.')
        return False
    i, j = location.tile
    parts, ground = _room_parts(f)
    try:
        objects = tiles.objects_on(f, i, j, location.level)
    except ValueError:  # no height at the tile: nothing listed
        objects = []
    parts += _ground_part(objects, ground)
    parts += _wall_parts(f)
    parts += _fit_parts(f)
    scanner.speak_parts(parts)
    return True


# ---------------------------------------------------- number keys 1 to 6


def _objects_detail(f: Frame) -> Parts:
    """Number 1 (without layer focus): everything on the tile."""
    import simsight_tiles as tiles

    i, j = location.tile
    objects = tiles.objects_on(f, i, j, location.level)
    return object_parts(objects) if objects else [lang.text('Nothing here.', 'Nic tu nie ma.')]


def _ground_detail(f: Frame) -> Parts:
    """Number 2: the floor, inside or outside, floor tiles, pool."""
    import simsight_tiles as tiles

    i, j = location.tile
    ground = tiles.ground(f, i, j, location.level)
    parts: Parts = [lang.text('Floor {}. ', 'Piętro {}. ').format(location.level)]
    for key, yes, no in (
        ('outside', ('Outside', 'Na zewnątrz'), ('Inside', 'Wewnątrz')),
        ('floor', ('Floor tiles', 'Podłoga'), ('No floor tiles', 'Brak podłogi')),
        ('pool', ('Pool', 'Basen'), None),
    ):
        if key in ground:
            label = yes if ground[key] else no
            if label:
                parts.append(lang.text(*label) + '. ')
    return parts


def _walls_detail(f: Frame) -> Parts:
    """Number 3: all four sides."""
    return _wall_parts(f, all_sides=True)


def _objects_on_or_none(f: Frame, i: int, j: int) -> list[Any]:
    import simsight_tiles as tiles

    try:
        return tiles.objects_on(f, i, j, location.level)
    except ValueError:  # no height at that tile: nothing listed there
        return []


def _nearby_detail(f: Frame) -> Parts:
    """Number 4 (without layer focus): the nearest objects around."""
    import simsight_tiles as tiles

    i, j = location.tile
    radius = tiles.NEARBY_RADIUS
    nearby: list[tuple[int, int, int, Any]] = []
    for di in range(-radius, radius + 1):
        for dj in range(-radius, radius + 1):
            if (di, dj) == (0, 0) or not f.inside(i + di, j + dj):
                continue
            nearby.extend(
                (di * di + dj * dj, di, dj, obj) for obj in _objects_on_or_none(f, i + di, j + dj)
            )
    parts: Parts = []
    seen: set[Any] = set()
    for _distance, di, dj, obj in sorted(nearby, key=lambda item: item[0]):
        if obj.id in seen:
            continue
        seen.add(obj.id)
        parts += [object_name_part(obj), ', ', tiles.relative(di, dj), '. ']
        if len(seen) >= 8:
            break
    if not seen:
        return [lang.text('Nothing within {} tiles.', 'Nic w promieniu {} pól.').format(radius)]
    return parts


def _product_detail(f: Frame) -> Parts:
    """Number 5: the chosen object, its facing and whether it fits."""
    import simsight_place as place
    import simsight_tiles as tiles

    item = place.product()
    if item is None:
        return [
            lang.text(
                'No object chosen. Tab returns to the Build catalog.',
                'Nie wybrano obiektu. Tab wraca do katalogu.',
            )
        ]
    parts: Parts = [
        item['name'] or lang.text('The object', 'Obiekt'),
        '. ',
        lang.text('Facing {}. ', 'Skierowany na {}. ').format(
            tiles.direction_name(location.facing)
        ),
    ]
    return parts + _fit_parts(f)


def _front_door_parts(f: Frame) -> Parts:
    import simsight_tiles as tiles

    try:
        import services

        door = services.active_lot().get_front_door()
        if door is None:
            return []
        i, j = f.tile_of(door.position.x, door.position.z)
        di, dj = i - location.tile[0], j - location.tile[1]
        return [
            lang.text('Front door: {}. ', 'Drzwi frontowe: {}. ').format(tiles.relative(di, dj))
        ]
    except (AttributeError, TypeError, ValueError):  # no front door the game will say
        return []


def _position_detail(f: Frame) -> Parts:
    """Number 6: column and row on the lot, the street and the front door."""
    i, j = location.tile
    parts: Parts = [
        lang.text(
            'Column {} of {}, row {} of {}, floor {}. ',
            'Kolumna {} z {}, rząd {} z {}, piętro {}. ',
        ).format(i + 1, f.width, j + 1, f.depth, location.level)
    ]
    return parts + _street_parts(f) + _front_door_parts(f)


_DETAILS: dict[int, Callable[[Frame], Parts]] = {
    1: _objects_detail,
    2: _ground_detail,
    3: _walls_detail,
    4: _nearby_detail,
    5: _product_detail,
    6: _position_detail,
}


def _detail(topic: int) -> bool:
    import simsight_layers as layers
    import simsight_scanner as scanner
    import simsight_tiles as tiles

    f = tiles.frame()
    if f is None:
        _say('Tile information unavailable.', 'Informacje o polu niedostępne.')
        return False
    if topic == 1 and layers.enabled():
        return layers.focus_detail(f)
    if topic == 4 and layers.enabled():
        return layers.nearby_detail(f)
    read = _DETAILS.get(topic)
    scanner.speak_parts(read(f) if read is not None else [])
    return True


# ------------------------------------------------------------- turn and place


def _turn(step: int) -> bool:
    import simsight_place as place
    import simsight_scanner as scanner
    import simsight_tiles as tiles

    f = tiles.frame()
    if f is None:
        return False
    _set_cursor(facing=(location.facing + step) % 4)
    _state.offer = None
    if place.product() is None:
        _say('Choose an object in the Build catalog first.', 'Najpierw wybierz obiekt w katalogu.')
        return False
    scanner.speak_parts(
        [
            lang.text('Facing {}. ', 'Skierowany na {}. ').format(
                tiles.direction_name(location.facing)
            ),
            *_fit_parts(f, with_facing=False),
        ]
    )
    return True


def _place_special(item: dict[str, Any], need: Any) -> Any:
    """Enter for a wall object, an experimental surface, a chair snapped to
    a table or a focused surface; None for an object placed on the tile."""
    import simsight_chair_snap as chair_snap
    import simsight_layers as layers
    import simsight_place as place
    import simsight_surfaces as surfaces_mod
    import simsight_tiles as tiles

    if need == 'wall':
        import simsight_wall as wall

        return False if layers.wall_refusal() else wall.place(item)
    if surfaces_mod.handles(need):
        return surfaces_mod.place(item, need)
    snap_frame = tiles.frame()
    if snap_frame is not None:
        snapped = chair_snap.try_place(item, snap_frame)
        if snapped is not None:
            _state.offer = None
            return snapped
    focused = layers.focused_target() if need in (None, 'slot') else None
    if focused is not None and layers.surface_slots(focused):
        target = cursor()
        if target is None:
            return False
        return place.place(target[0], location.level, world_surface(), surface_id=focused.id)
    return None


def _build_place() -> Any:
    """Enter: place exactly here, or offer the nearest tile where it fits."""
    import simsight_place as place

    item = place.product()
    if item is None:
        _say(
            'Choose an object in the Build catalog first. Tab returns to it.',
            'Najpierw wybierz obiekt w katalogu. Tab do niego wraca.',
        )
        return False
    need = place.requirement(item['id'])
    special = _place_special(item, need)
    if special is not None:
        return special
    return _place_on_tile(item, need)


def _place_on_tile(item: dict[str, Any], need: Any) -> Any:
    import time

    import simsight_place as place
    import simsight_tiles as tiles

    f = tiles.frame()
    if f is None:
        return False
    i, j = location.tile
    now = time.monotonic()
    offer = _state.offer
    _state.offer = None
    if (
        offer is not None
        and offer.origin == (i, j, location.level, location.facing)
        and offer.product == item['id']
        and now - offer.at < OFFER_SECONDS
    ):
        return place.place(
            offer.point, location.level, world_surface(), angle=f.yaw(offer.facing), exact=True
        )
    if need == 'slot':
        target = cursor()
        if target is None:
            _say('This tile cannot take an object.', 'To pole nie może przyjąć obiektu.')
            return False
        return place.place(target[0], location.level, world_surface())
    if need in place.NEEDS:
        place.say_text(*place.NEEDS[need])
        return False
    point, why = tiles.fit_at(f, item['id'], i, j, location.level, location.facing)
    if point is not None:
        return place.place(
            point, location.level, world_surface(), angle=f.yaw(location.facing), exact=True
        )
    if why is None:
        _say(
            'The game could not check this tile, so nothing was bought.',
            'Gra nie mogła sprawdzić tego pola, więc nic nie kupiono.',
        )
        return False
    _offer_nearest(f, item, why, now)
    return False


def _offer_nearest(f: Frame, item: dict[str, Any], why: str, now: float) -> None:
    """It does not fit here: offer the nearest tile where it does."""
    import simsight_scanner as scanner
    import simsight_tiles as tiles

    i, j = location.tile
    name = item['name'] or lang.text('It', 'Obiekt')
    found = tiles.nearest_fit(f, item['id'], i, j, location.level, location.facing)
    if found is None:
        scanner.speak_parts(
            [
                name,
                lang.text(
                    ' does not fit here: {}. No tile within {} fits it either. Try another spot or facing.',
                    ' nie mieści się tutaj: {}. W promieniu {} pól też nie ma miejsca. Spróbuj innego miejsca lub kierunku.',
                ).format(why, tiles.NEAREST_RADIUS),
            ]
        )
        return
    ni, nj, spot, facing = found
    _state.offer = _Offer((i, j, location.level, location.facing), item['id'], spot, facing, now)
    scanner.speak_parts(
        [
            name,
            lang.text(
                ' does not fit here: {}. It fits {}, facing {}. Press Enter again to place it there, or move to cancel.',
                ' nie mieści się tutaj: {}. Zmieści się: {}, skierowany na {}. Naciśnij Enter ponownie, aby go tam umieścić, albo przesuń kursor.',
            ).format(why, tiles.relative(ni - i, nj - j), tiles.direction_name(facing)),
        ]
    )


# Moving furniture (contributed by DaRainbowGirlXX, 2026-10-16): M on an
# object picks it up, M on another tile puts it down there, Escape cancels.


def _clear_move() -> None:
    _state.move_id = None
    _state.move_name = None


def _object_name(obj: Any) -> str:
    value = object_name_part(obj)
    return ''.join(str(part) for part in value) if isinstance(value, list) else str(value)


def _pick_up() -> bool:
    """M with nothing picked up: pick up the object here."""
    import simsight_place as place

    obj = _sell_target()
    if obj is None:
        _say('Nothing here to move.', 'Nie ma tu nic do przesunięcia.')
        return False
    refusal = place.sell_refusal(obj)
    if refusal is not None:
        place.say_text(*place.CANNOT_SELL[refusal])
        return False
    if getattr(obj, 'parent', None) is not None:
        _say(
            'Objects attached to another object cannot be moved from the grid yet.',
            'Obiektów przymocowanych do innego obiektu nie można jeszcze przesuwać z siatki.',
        )
        return False
    _state.move_id, _state.move_name = obj.id, _object_name(obj)
    _say(
        f'{_state.move_name} selected to move. Go to the new tile and press M again. Escape cancels.',
        f'Wybrano {_state.move_name} do przesunięcia. Przejdź do nowego pola i ponownie naciśnij M. Escape anuluje.',
    )
    return True


def _put_down(obj: Any, position: Any, level: int) -> bool:
    """Move obj to a valid spot by position; put it back where it was when
    that fails."""
    import simsight_place as place

    old_position, old_orientation, old_surface = obj.position, obj.orientation, obj.routing_surface
    try:
        spot = place.good_location(
            obj, position, old_orientation, world_surface(), ignore=(obj.id,)
        )
        if spot is None:
            _say('No valid position was found here.', 'Nie znaleziono tutaj prawidłowej pozycji.')
            return False
        obj.move_to(translation=spot[0], orientation=spot[1], routing_surface=world_surface())
        moved = int(getattr(obj, 'level', level)) == int(level)
        why = 'object stayed on another floor'
    except Exception:  # EA's move runs the object's location callbacks
        simsight_log.error('grid: move an object')
        moved, why = False, 'raised'
    if not moved:
        _restore(obj, old_position, old_orientation, old_surface)
        simsight_log.line(f'grid: move failed for {_state.move_id}: {why}')
        _say(
            'The object could not be moved; its original position was kept.',
            'Nie udało się przesunąć obiektu; zachowano jego pierwotne położenie.',
        )
        return False
    return True


def _restore(obj: Any, position: Any, orientation: Any, surface: Any) -> None:
    try:
        obj.move_to(translation=position, orientation=orientation, routing_surface=surface)
    except Exception:
        simsight_log.error('grid: put a moved object back')


def _move_key() -> bool:
    import services

    if _state.move_id is None:
        return _pick_up()
    obj = services.object_manager().get(_state.move_id)
    name = _state.move_name or lang.text('Object', 'Obiekt')
    if obj is None:
        _clear_move()
        _say('The selected object is no longer available.', 'Wybrany obiekt nie jest już dostępny.')
        return False
    target = cursor()
    if target is None:
        _say('This tile cannot take the object.', 'To pole nie może przyjąć obiektu.')
        return False
    position, level, _ident = target
    if not _put_down(obj, position, level):
        return False
    _clear_move()
    _say(f'{name} moved.', f'Przesunięto {name}.')
    return True


def _jump_to_last() -> bool:
    import simsight_place as place
    import simsight_tiles as tiles

    f = tiles.frame()
    position = place.last_position()
    if f is None or position is None:
        _say('Nothing placed yet.', 'Jeszcze nic nie umieszczono.')
        return False
    ni, nj = f.tile_of(position.x, position.z)
    if not f.inside(ni, nj):
        return False
    _go_to_tile(f, ni, nj)
    return describe()


@sims4.commands.Command(  # type: ignore[misc]  # EA's Command is untyped
    'simsight.grid', 'a11y.grid', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def command(action: str = 'toggle', _connection: Any = None) -> Any:
    try:
        action = action.lower()
        if action not in ('off', 'back') and not _controls_enabled():
            import simsight_build as build

            return build.refuse_disabled()
        return on_key(action)
    except Exception:
        simsight_log.error('grid: command')
        return False


# DaRainbowGirlXX (merged 2026-11-14): chair-to-table orientation hints and
# the optional lot-planning description of each tile.
def _fit_parts(f: Frame, with_facing: bool = True) -> Parts:
    import simsight_chair_alignment as chair_alignment

    return chair_alignment.fit_parts(f, with_facing=with_facing)


def _room_parts(f: Frame) -> tuple[Parts, dict[str, bool]]:
    import simsight_lot_grid as lot_grid

    return lot_grid.room_parts(f)

"""SimSight Plus rooms: Alt+R lists the rooms of the house with their contents,
Alt+W says which room the active Sim is in. Rooms are named from what is
inside them, or by the player (simsight_plus_house)."""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_plus_core as core

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class Boxed(Protocol):
        """A room as the names file knows it: its floor and tile centres."""

        level: Any
        tiles: list[tuple[float, float]]

    BoxKey = tuple[int, int, int, int, int, int]  # zone, level, x0, z0, x1, z1

COMPASS = ('north', 'north east', 'east', 'south east', 'south', 'south west', 'west', 'north west')
# (room name, tags that make a room that kind, weight)
KINDS = (
    ('Bathroom', ('BuyCatPA_Toilet', 'BuyCatPA_Shower', 'BuyCatPA_Tub', 'Func_Bathtub'), 5),
    (
        'Kitchen',
        (
            'BuyCatPA_Refrigerator',
            'BuyCatPA_Stove',
            'Func_Fridge',
            'Func_Refrigerator',
            'Func_Oven',
        ),
        5,
    ),
    ('Nursery', ('Func_Crib',), 6),
    ('Bedroom', ('BuyCatSS_Bed',), 4),
    ('Dining room', ('BuyCatSS_DiningTable',), 2),
    (
        'Living room',
        ('BuyCatSS_Sofa', 'BuyCatSS_LoveSeat', 'BuyCatEE_TV', 'Func_TV', 'Func_Television'),
        2,
    ),
    ('Study', ('BuyCatSS_Desk', 'BuyCatEE_Computer', 'BuyCatSS_Bookshelf'), 1),
)
_UPPER_FLOORS = {1: 'first floor up', 2: 'second floor up', 3: 'third floor up'}


def floor_name(level: int) -> str:
    if level == 0:
        return 'ground floor'
    if level < 0:
        return 'basement' if level == -1 else f'basement level {-level}'
    return _UPPER_FLOORS.get(level, f'floor {level}')


def bearing(dx: float, dz: float) -> str:
    """Compass word for a step of (dx, dz); north is +z."""
    angle = math.degrees(math.atan2(dx, dz)) % 360.0
    return COMPASS[int((angle + 22.5) % 360.0 // 45) % 8]


def _tag_name(tag_type: Any, value: Any) -> str | None:
    try:
        return str(tag_type(value).name)
    except (AttributeError, KeyError, TypeError, ValueError):  # a tag the game does not name
        return None


def tag_names(obj: Any) -> set[str]:
    """The names of an object's catalog tags (empty for objects without any)."""
    names: set[str] = set()
    try:
        from tag import Tag

        for t in obj.definition.get_tags() or ():
            name = _tag_name(Tag, t)
            if name is not None:
                names.add(name)
    except (AttributeError, TypeError):  # no definition or tags: not furniture
        pass
    except Exception:
        simsight_log.error('plus rooms: tags')
    return names


def room_id_at(x: float, z: float, level: Any, y: float | None = None) -> int:
    """The game's room id at a point (0 outside any room)."""
    import build_buy
    import services
    import sims4.math
    import terrain

    if y is None:
        try:
            y = terrain.get_terrain_height(x, z)
        except Exception:  # off the terrain: height 0
            y = 0.0
    pos = sims4.math.Vector3(float(x), float(y), float(z))
    try:
        rid = build_buy.get_room_id(services.current_zone_id(), pos, int(level))
    except Exception:
        simsight_log.error('plus rooms: get_room_id')
        return 0
    return int(rid or 0)


def _obj_room(obj: Any) -> int:
    try:
        return room_id_at(obj.position.x, obj.position.z, int(obj.level), obj.position.y)
    except (AttributeError, TypeError, ValueError):  # no position or level: in no room
        return 0


class Room:
    """One room on one floor: its tile centres, furniture and Sims."""

    def __init__(self, rid: int, level: int) -> None:
        self.rid = rid
        self.level = level
        self.tiles: list[tuple[float, float]] = []  # (x, z) world tile centres
        self.objects: list[Any] = []
        self.sims: list[Any] = []
        self.name = 'Room'

    @property
    def center(self) -> tuple[float, float]:
        xs = [t[0] for t in self.tiles] or [0]
        zs = [t[1] for t in self.tiles] or [0]
        return (sum(xs) / len(xs), sum(zs) / len(zs))

    def size_text(self) -> str:
        n = len(self.tiles)
        if not n:
            return ''
        xs = [t[0] for t in self.tiles]
        zs = [t[1] for t in self.tiles]
        w = int(round(max(xs) - min(xs))) + 1
        d = int(round(max(zs) - min(zs))) + 1
        if w * d == n:
            return f'{w} by {d} meters'
        return f'about {n} square meters'


class LotGrid:
    """The lot's 1 meter tiles: nu by nv along its corners 0 to 1 (u) and 0 to 3
    (v), tile (i, j) centred at world(); levels is its floor range."""

    def __init__(self, lot: Any) -> None:
        corners = list(lot.corners)
        c0, c1, c3 = corners[0], corners[1], corners[3]
        self.x0, self.z0 = c0.x, c0.z
        self.ux, self.uz = c1.x - c0.x, c1.z - c0.z
        self.vx, self.vz = c3.x - c0.x, c3.z - c0.z
        self.nu = max(1, int(round(math.hypot(self.ux, self.uz))))
        self.nv = max(1, int(round(math.hypot(self.vx, self.vz))))
        try:
            self.levels = range(int(lot.min_level), int(lot.max_level) + 1)
        except Exception:  # no floor range on this lot: the usual three floors
            self.levels = range(3)

    def world(self, i: int, j: int) -> tuple[float, float]:
        fu, fv = (i + 0.5) / self.nu, (j + 0.5) / self.nv
        return (self.x0 + self.ux * fu + self.vx * fv, self.z0 + self.uz * fu + self.vz * fv)


def _rooms_on_grid(grid: LotGrid) -> dict[tuple[int, int], Room]:
    rooms: dict[tuple[int, int], Room] = {}
    for level in grid.levels:
        for i in range(grid.nu):
            for j in range(grid.nv):
                x, z = grid.world(i, j)
                rid = room_id_at(x, z, level)
                if rid:
                    room = rooms.get((rid, level))
                    if room is None:
                        room = rooms[(rid, level)] = Room(rid, level)
                    room.tiles.append((x, z))
    return rooms


def _is_furniture(obj: Any) -> bool:
    if obj.is_sim or not getattr(obj, 'is_on_active_lot', lambda: True)():
        return False
    if getattr(obj, 'is_in_inventory', lambda: False)():
        return False
    # helpers and hidden game objects have no catalog (BuyCat) tag
    return any(n.startswith('BuyCat') for n in tag_names(obj))


def _add_object(rooms: dict[tuple[int, int], Room], obj: Any) -> None:
    try:
        if not _is_furniture(obj):
            return
        room = rooms.get((_obj_room(obj), int(obj.level)))
    except Exception:  # an object of an odd shape is left out
        simsight_log.error('plus rooms: room object')
        return
    if room is not None:
        room.objects.append(obj)


def _add_sims(rooms: dict[tuple[int, int], Room]) -> None:
    for info in core.household_sims():
        sim = info.get_sim_instance()
        room = rooms.get((_obj_room(sim), int(sim.level)))
        if room is not None:
            room.sims.append(sim)


def scan_rooms_base() -> list[Room]:
    """Return list of Room, indoor rooms only, on every floor of the active lot."""
    import services

    lot = services.active_lot()
    if lot is None:
        return []
    grid = LotGrid(lot)
    rooms = _rooms_on_grid(grid)
    # A "room" covering more than half the lot is the yard, not a room.
    for key in [k for k, room in rooms.items() if len(room.tiles) > 0.5 * grid.nu * grid.nv]:
        del rooms[key]
    for obj in services.object_manager().values():
        _add_object(rooms, obj)
    _add_sims(rooms)
    _name_rooms(list(rooms.values()))
    out = sorted(rooms.values(), key=lambda r: (r.level, r.name, -len(r.tiles)))
    core.log(
        'rooms: lot {}x{}, {} rooms: {}'.format(
            grid.nu,
            grid.nv,
            len(out),
            ', '.join(
                f'{r.name}({len(r.tiles)} tiles, {len(r.objects)} objs, level {r.level})'
                for r in out
            ),
        )
    )
    return out


# ---------------------------------------------------------------- names given by the player
# Saved per lot in Mods\SimSight_Plus_rooms.txt: zone, level, room box (world tile centers), name.

NAMES_PATH = os.path.join(core.MODS_FOLDER, 'SimSight_Plus_rooms.txt')


class _Names:
    def __init__(self) -> None:
        self.by_box: dict[BoxKey, str] = {}
        self.loaded = False


_names = _Names()


def _box_key(room: Boxed) -> BoxKey:
    import services

    xs = [t[0] for t in room.tiles] or [0]
    zs = [t[1] for t in room.tiles] or [0]
    return (
        int(services.current_zone_id()),
        int(room.level),
        int(round(min(xs))),
        int(round(min(zs))),
        int(round(max(xs))),
        int(round(max(zs))),
    )


def parse_name_line(line: str) -> tuple[BoxKey, str] | None:
    """One line of the names file: six whole numbers and the name, tab separated."""
    parts = line.rstrip('\n').split('\t')
    if len(parts) != 7:
        return None
    (zone, level, x0, z0, x1, z1) = (int(v) for v in parts[:6])
    return (zone, level, x0, z0, x1, z1), parts[6]


def name_line(key: BoxKey, name: str) -> str:
    return '\t'.join(str(v) for v in key) + '\t' + name + '\n'


def _load_names() -> None:
    if _names.loaded:
        return
    _names.loaded = True
    try:
        if os.path.isfile(NAMES_PATH):
            with open(NAMES_PATH, encoding='utf-8') as f:
                for line in f:
                    entry = parse_name_line(line)
                    if entry is not None:
                        _names.by_box[entry[0]] = entry[1]
    except (OSError, ValueError):  # names read so far are kept
        simsight_log.error('plus rooms: load room names')


def _save_names() -> None:
    if not core.IN_GAME:
        return
    try:
        with open(NAMES_PATH, 'w', encoding='utf-8') as f:
            f.writelines(name_line(key, name) for key, name in _names.by_box.items())
    except OSError:
        simsight_log.error('plus rooms: save room names')


def set_room_name(room: Room, name: str) -> None:
    _load_names()
    key = _box_key(room)
    if name:
        _names.by_box[key] = name
    else:
        _names.by_box.pop(key, None)
    _save_names()
    room.name = name or room.name


def given_name(room: Boxed) -> str | None:
    _load_names()
    try:
        return _names.by_box.get(_box_key(room))
    except Exception:  # no zone to look up: the room keeps its own name
        simsight_log.error('plus rooms: given name')
        return None


def kind_scores(objects: Iterable[Any]) -> list[tuple[str, int]]:
    """Room kinds (KINDS) by the weight of these objects' furniture, highest
    first; on a tie, the kind of the object that comes first."""
    score: dict[str, int] = {}
    for obj in objects:
        names = tag_names(obj)
        for kind, tags, weight in KINDS:
            if names.intersection(tags):
                score[kind] = score.get(kind, 0) + weight
    return sorted(score.items(), key=lambda kv: -kv[1])


def _kind_name(room: Room) -> str:
    """The room's kind from its furniture (kind_scores' first)."""
    ranked = kind_scores(room.objects)
    if ranked:
        return ranked[0][0]
    return 'Room' if room.objects else 'Empty room'


def _name_rooms(rooms: list[Room]) -> None:
    given = [(room, given_name(room)) for room in rooms]
    unnamed = [room for (room, name) in given if not name]
    for room, name in given:
        if name:
            room.name = name
    counts: dict[str, int] = {}
    for room in unnamed:
        room.name = _kind_name(room)
        counts[room.name] = counts.get(room.name, 0) + 1
    # Two bedrooms are 'Bedroom 1' and 'Bedroom 2': lowest floor, biggest first.
    numbered: dict[str, int] = {}
    for room in sorted(unnamed, key=lambda r: (r.level, -len(r.tiles))):
        if counts[room.name] > 1:
            numbered[room.name] = numbered.get(room.name, 0) + 1
            room.name = f'{room.name} {numbered[room.name]}'


def _where_text(room: Room, sim: Any) -> str:
    (cx, cz) = room.center
    dx, dz = cx - sim.position.x, cz - sim.position.z
    dist = int(round(math.hypot(dx, dz)))
    text = floor_name(room.level)
    if sim in room.sims or dist < 1:
        return 'you are here, ' + text
    return f'{dist} meters {bearing(dx, dz)}, {text}'


def _room_row(room: Room, sim: Any) -> tuple[str, str, Room]:
    who = ', '.join(s.sim_info.first_name for s in room.sims)
    desc = '{}. {}. {} objects{}.'.format(
        room.size_text(),
        _where_text(room, sim),
        len(room.objects),
        ('. ' + who + ' here') if who else '',
    )
    return (room.name, desc, room)


@core.hotkey(ord('R'), 'rooms')
def open_rooms() -> None:
    sim = core.active_sim()
    if sim is None:
        core.speak('No active Sim.')
        return
    rooms = scan_rooms()
    if not rooms:
        core.speak('No rooms found on this lot.')
        return
    core.choose(
        'Rooms',
        f'{len(rooms)} rooms in this house. Choose a room to hear what is in it.',
        [_room_row(room, sim) for room in rooms],
        _open_room,
        tag='rooms',
    )


def _object_label(obj: Any) -> Any:
    """The object's catalog name as a LocalizedString, or 'Object'."""
    try:
        label = obj.catalog_name
        from protocolbuffers.Localization_pb2 import LocalizedString

        if not isinstance(label, LocalizedString):
            from sims4.localization import _create_localized_string

            label = _create_localized_string(int(getattr(label, 'hash', label)))
    except Exception:  # no catalog name to read: a plain word
        return 'Object'
    return label


def _open_room(room: Room) -> None:
    sim = core.active_sim()
    if sim is None:
        return
    entries = [(s, s.sim_info.first_name) for s in room.sims]
    entries += [(obj, None) for obj in room.objects]
    rows = []
    for obj, name in entries:
        dx, dz = obj.position.x - sim.position.x, obj.position.z - sim.position.z
        dist = int(round(math.hypot(dx, dz)))
        desc = 'here' if dist < 1 else f'{dist} meters {bearing(dx, dz)}'
        label = _object_label(obj) if name is None else name
        rows.append((label, desc + '. Choose to see its actions.', obj, False, obj))
    if not rows:
        core.speak(f'{room.name} is empty.')
        return
    core.choose(
        room.name,
        f'{room.name}, {room.size_text()}. {len(rows)} things. Choose one to open its actions.',
        rows,
        core.open_interactions,
        tag='rooms',
    )


@core.hotkey(ord('W'), 'where am I')
def where_am_i() -> None:
    import build_buy

    sim = core.active_sim()
    if sim is None:
        core.speak('No active Sim.')
        return
    rid = _obj_room(sim)
    name = None
    if rid:
        name = next((room.name for room in scan_rooms() if room.rid == rid), None)
    try:
        outside = build_buy.is_location_outside(sim.position, int(sim.level))
    except Exception:  # not known: said as inside
        outside = None
    first, floor = sim.sim_info.first_name, floor_name(int(sim.level))
    if name:
        core.speak(f'{first} is in the {name}, {floor}.')
    else:
        core.speak(f'{first} is {"outside" if outside else "inside"}, {floor}.')


def scan_rooms() -> list[Room]:
    """Rooms of the lot, always scanned on the simulation thread and with
    fresh floor ranges (DaRainbowGirlXX, merged 2026-11-14)."""
    import simsight_house_workflow as workflow

    workflow.install()
    return workflow.scan_rooms()

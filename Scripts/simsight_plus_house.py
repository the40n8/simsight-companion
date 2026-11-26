# SimSight Plus - "Build my house" and door tools (in the build helper, Alt+B).
# Build my house: the player picks the rooms; we lay them out (front row: living room and kitchen,
# a hallway behind them, bedrooms and bathrooms at the back), build each room with the Room Tool,
# put a front door into the living room and a door from every room to the room it opens onto,
# and name the rooms. Door tools: a door between two rooms (or to the yard), and removing a door
# or window. Everything uses the build helper's pointing (simsight_plus_build).
from __future__ import annotations

import math
import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_plus_build as build
import simsight_plus_core as core

if TYPE_CHECKING:
    from collections.abc import Sequence

    # A room: name, kind, level, uv=(u0, v0, u1, v1) in house coordinates, box=(e0, n0, e1, n1)
    # on the lot. A door: what, level, front, a=(u, v) tile inside, b=(u, v) tile on the
    # other side, tile_a / tile_b on the lot. Other modules read and extend these dicts.
    Room = dict[str, Any]
    Door = dict[str, Any]
    Tile = tuple[int, int]
    TilePair = tuple[Tile, Tile]
    KindName = tuple[str, str]  # (kind, room name)

WALL_PRICE = 60  # Room Tool, per meter of wall (measured)
FRONT_DEPTH, HALL_DEPTH, BACK_DEPTH = 5, 2, 4
SETBACK = 2  # meters of front yard between the street side of the lot and the house

# kind -> (row, width, name)
KINDS = {
    'living': ('front', 6, 'Living room'),
    'kitchen': ('front', 4, 'Kitchen'),
    'dining': ('front', 4, 'Dining room'),
    'bedroom': ('back', 4, 'Bedroom'),
    'bathroom': ('back', 3, 'Bathroom'),
    'study': ('back', 3, 'Study'),
}
PRESETS = (
    ('Living room, kitchen, bathroom and 1 bedroom', ['living', 'kitchen', 'bathroom', 'bedroom']),
    (
        'Living room, kitchen, bathroom and 2 bedrooms',
        ['living', 'kitchen', 'bathroom', 'bedroom', 'bedroom'],
    ),
    (
        'Living room, kitchen, bathroom and 3 bedrooms',
        ['living', 'kitchen', 'bathroom', 'bedroom', 'bedroom', 'bedroom'],
    ),
    (
        'Living room, kitchen, 2 bathrooms and 3 bedrooms',
        ['living', 'kitchen', 'bathroom', 'bathroom', 'bedroom', 'bedroom', 'bedroom'],
    ),
)
# Two-floor houses for smaller lots: (label, variant). See make_two_floor_plan.
TWO_FLOOR_PRESETS = (
    (
        (
            'Two floors, 3 bedrooms. Downstairs: living room, kitchen, dining room and bathroom. '
            'Upstairs: 3 bedrooms and a bathroom'
        ),
        '3bed',
    ),
    (
        (
            'Two floors, 4 bedrooms. Downstairs: living room, kitchen, a bedroom and bathroom. '
            'Upstairs: 3 bedrooms and a bathroom'
        ),
        '4bed',
    ),
    (
        (
            'Two floors, 2 bedrooms. Downstairs: living room, kitchen, dining room and bathroom. '
            'Upstairs: 2 bedrooms, a study and a bathroom'
        ),
        '2bed',
    ),
)
TWO_W = 10  # a two-floor house is 10 meters along the street and 11 deep
STAIRS = ('Unremarkable Staircase', 'Stairs', 'Sturdy Stairs')
STAIR_LEN = 3  # tiles of steps, for normal wall height (measured October 2026)
STAIR_PRICE = 600
INTERIOR_DOORS = ('Simple Single-Panel Door', 'HBD Interior Door', 'Simple Two-Panel Door')
FRONT_DOORS = ('Wooden Four-Panel Door', 'HBD Front Door', 'Simple Single-Panel Door')
# Catalog doors that are not a plain door (looked for in the lower-case name).
_NOT_PLAIN_DOOR = ('arch', 'frame', 'double', 'wide', 'sliding', 'window', 'commercial')
_NOT_PLAIN_STAIRS = ('ladder', 'mega', 'floating', 'spiral')
_BUSY_TEXT = 'Still working on the last build step.'
_LOG_HELP = 'Send the SimSight_Plus.log file from the Mods folder for help.'

# ---------------------------------------------------------------- which side is the street


def street_side() -> str:
    """The lot edge nearest the place where Sims arrive: 'north', 'south', 'west' or 'east'."""
    import services

    f = build.frame()
    point = None
    try:
        sp = services.current_zone().active_lot_arrival_spawn_point
        c = sp.get_approximate_center()
        point = f.local(c.x, c.z)
    except Exception:  # no arrival point on this lot (or a new shape): logged, north it is
        core.log_exc('arrival spawn point')
    if point is None:
        return 'north'
    (e, n) = point
    dist = {'north': abs(n), 'south': abs(f.depth - n), 'west': abs(e), 'east': abs(f.width - e)}
    return min(dist, key=lambda side: dist[side])


# ---------------------------------------------------------------- layout
# Planned in house coordinates: u along the street (left to right seen from the street),
# v away from the street (0 = the front wall). Then mapped to meters from the west and north edges.


class Plan:
    def __init__(self) -> None:
        self.rooms: list[Room] = []
        self.doors: list[Door] = []
        self.width = 0
        self.depth = 0
        self.side = 'north'
        self.problem: str | None = None
        self.floors = 1
        # top=(u, v) highest step tile, climb=(du, dv); once mapped also top_lot, climb_lot
        self.stairs: dict[str, Any] | None = None


def _names_for(kinds: Sequence[str]) -> list[str]:
    counts: dict[str, int] = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    seen: dict[str, int] = {}
    out = []
    for k in kinds:
        base = KINDS[k][2]
        if counts[k] > 1:
            seen[k] = seen.get(k, 0) + 1
            out.append(f'{base} {seen[k]}')
        else:
            out.append(base)
    return out


def _rows_for(kinds: Sequence[str]) -> tuple[list[KindName], list[KindName]]:
    """(front row, back row) of (kind, name), in the order they stand, left to right."""
    names = _names_for(kinds)
    front = [(k, n) for (k, n) in zip(kinds, names) if KINDS[k][0] == 'front']
    back = [(k, n) for (k, n) in zip(kinds, names) if KINDS[k][0] == 'back']
    if not any(k == 'living' for (k, _n) in front):
        front.insert(0, ('living', 'Living room'))
    # Living room first (left), then kitchen, then dining room.
    order = {'living': 0, 'kitchen': 1, 'dining': 2}
    front.sort(key=lambda kn: order[kn[0]])
    # Back row: bathrooms between bedrooms, a study at the end.
    beds = [kn for kn in back if kn[0] == 'bedroom']
    baths = [kn for kn in back if kn[0] == 'bathroom']
    others = [kn for kn in back if kn[0] not in ('bedroom', 'bathroom')]
    row = []
    while beds or baths:
        if beds:
            row.append(beds.pop(0))
        if baths:
            row.append(baths.pop(0))
    return front, row + others


def _row_widths(front: list[KindName], back: list[KindName]) -> tuple[list[int], list[int], int]:
    """(front widths, back widths, house width): both rows as wide as the wider one."""
    fw = [KINDS[k][1] for (k, _n) in front]
    bw = [KINDS[k][1] for (k, _n) in back]
    width = max(sum(fw), sum(bw))
    fw[0] += width - sum(fw)  # the living room takes up any spare width
    if back and sum(bw) < width:
        spare = width - sum(bw)
        any_bedroom = any(k == 'bedroom' for (k, _n) in back)
        i = 0
        while spare > 0:  # bedrooms grow first
            j = i % len(back)
            if back[j][0] == 'bedroom' or not any_bedroom:
                bw[j] += 1
                spare -= 1
            i += 1
    return fw, bw, width


def _plan_rooms(
    front: list[KindName], back: list[KindName], fw: list[int], bw: list[int], width: int
) -> list[Room]:
    u = 0
    rooms: list[Room] = []
    for (k, name), w in zip(front, fw):
        rooms.append({'kind': k, 'name': name, 'uv': (u, 0, u + w, FRONT_DEPTH)})
        u += w
    if back:
        hall = {
            'kind': 'hall',
            'name': 'Hallway',
            'uv': (0, FRONT_DEPTH, width, FRONT_DEPTH + HALL_DEPTH),
        }
        rooms.append(hall)
        u = 0
        v0 = FRONT_DEPTH + HALL_DEPTH
        for (k, name), w in zip(back, bw):
            rooms.append({'kind': k, 'name': name, 'uv': (u, v0, u + w, v0 + BACK_DEPTH)})
            u += w
    for r in rooms:
        r['level'] = 0
    return rooms


def _plan_doors(rooms: list[Room], front_count: int, has_back: bool) -> list[Door]:
    """The front door, doors along the front row, and from the hallway to each back room."""
    (lu0, _, lu1, _) = rooms[0]['uv']  # the living room
    mid = (lu0 + lu1) // 2
    doors: list[Door] = [
        {'what': 'Front door into the living room', 'a': (mid, 0), 'b': (mid, -1), 'front': True}
    ]
    for i in range(1, front_count):
        left, right = rooms[i - 1], rooms[i]
        doors.append(
            {
                'what': 'Door between the {} and the {}'.format(
                    left['name'].lower(), right['name'].lower()
                ),
                'a': (left['uv'][2] - 1, FRONT_DEPTH // 2),
                'b': (left['uv'][2], FRONT_DEPTH // 2),
                'front': False,
            }
        )
    if has_back:
        doors.append(
            {
                'what': 'Door from the living room to the hallway',
                'a': (mid, FRONT_DEPTH - 1),
                'b': (mid, FRONT_DEPTH),
                'front': False,
            }
        )
        for r in rooms:
            if KINDS.get(r['kind'], ('front',))[0] != 'back':
                continue  # front rooms and the hallway
            (u0, v0, u1, _v1) = r['uv']
            m = (u0 + u1) // 2
            doors.append(
                {
                    'what': 'Door from the hallway to {}'.format(r['name'].lower()),
                    'a': (m, v0),
                    'b': (m, v0 - 1),
                    'front': False,
                }
            )
    for d in doors:
        d['level'] = 0
    return doors


def _along_deep(side: str, f: Any) -> tuple[int, int]:
    """Lot size (along the street, away from the street)."""
    return (f.width, f.depth) if side in ('north', 'south') else (f.depth, f.width)


def make_plan(kinds: Sequence[str]) -> Plan:
    f = build.frame()
    p = Plan()
    p.side = street_side()
    front, back = _rows_for(kinds)
    fw, bw, width = _row_widths(front, back)
    depth = FRONT_DEPTH + (HALL_DEPTH + BACK_DEPTH if back else 0)
    along, deep = _along_deep(p.side, f)
    if width > along - 2 or depth > deep - SETBACK - 1:
        p.problem = (
            f'A house of {width} by {depth} meters does not fit on this {f.width} by {f.depth} meter lot. Choose fewer rooms, '
            'or one of the two-floor houses, which need only 10 by 11 meters.'
        )
        return p
    p.width, p.depth = width, depth
    p.rooms = _plan_rooms(front, back, fw, bw, width)
    p.doors = _plan_doors(p.rooms, len(front), bool(back))
    _map_plan(p, f, along)
    return p


def _map_plan(p: Plan, f: Any, along: int) -> None:
    """House coordinates to lot meters, for rooms, doors and stairs."""
    u_off = (along - p.width) // 2
    for r in p.rooms:
        (u0, v0, u1, v1) = r['uv']
        (e0, n0) = _to_lot(p, f, u_off, u0, v0)
        (e1, n1) = _to_lot(p, f, u_off, u1, v1)
        r['box'] = (min(e0, e1), min(n0, n1), max(e0, e1), max(n0, n1))
    for d in p.doors:
        d['tile_a'] = _tile_to_lot(p, f, u_off, d['a'])
        d['tile_b'] = _tile_to_lot(p, f, u_off, d['b'])
    if p.stairs:
        p.stairs['top_lot'] = _tile_to_lot(p, f, u_off, p.stairs['top'])
        p.stairs['climb_lot'] = _dir_to_lot(p, p.stairs['climb'])


def _the(name: str) -> str:
    """'the dining room', but 'bedroom 4' (a numbered room needs no 'the')."""
    return name.lower() if name[-1].isdigit() else 'the ' + name.lower()


def _dir_to_lot(p: Plan, d: Sequence[int]) -> tuple[int, int]:
    """A direction (du, dv) in house coordinates to (de, dn) on the lot."""
    (du, dv) = d
    return {'north': (du, dv), 'south': (-du, -dv), 'west': (dv, -du), 'east': (-dv, du)}[p.side]


def _room(kind: str, name: str, uv: tuple[int, int, int, int], level: int) -> Room:
    return {'kind': kind, 'name': name, 'uv': uv, 'level': level}


def _door(what: str, a: Tile, b: Tile, level: int, front: bool = False) -> Door:
    return {'what': what, 'a': a, 'b': b, 'level': level, 'front': front}


def make_two_floor_plan(variant: str) -> Plan:
    """A 10 by 11 meter house with two floors. Both floors: a 5 meter deep front row, a 2 meter hallway
    across the house with the stairs in it, and a 4 meter deep back row."""
    f = build.frame()
    p = Plan()
    p.side = street_side()
    p.floors = 2
    W, F, H = TWO_W, FRONT_DEPTH, FRONT_DEPTH + HALL_DEPTH
    D = H + BACK_DEPTH
    along, deep = _along_deep(p.side, f)
    if along - 2 < W or deep - SETBACK - 1 < D:
        p.problem = (
            f'Even a two-floor house, {W} by {D} meters, does not fit on this {f.width} by {f.depth} meter lot. '
            'Choose a bigger lot.'
        )
        return p
    p.width, p.depth = W, D
    down_back = ('bedroom', 'Bedroom 4') if variant == '4bed' else ('dining', 'Dining room')
    up_back = ('study', 'Study') if variant == '2bed' else ('bedroom', 'Bedroom 3')
    p.rooms = [
        _room('living', 'Living room', (0, 0, 6, F), 0),
        _room('kitchen', 'Kitchen', (6, 0, W, F), 0),
        _room('hall', 'Hallway', (0, F, W, H), 0),
        _room(down_back[0], down_back[1], (0, H, 7, D), 0),
        _room('bathroom', 'Bathroom', (7, H, W, D), 0),
        _room('bedroom', 'Bedroom 1', (0, 0, 5, F), 1),
        _room('bedroom', 'Bedroom 2', (5, 0, W, F), 1),
        _room('hall', 'Upstairs hallway', (0, F, W, H), 1),
        _room(up_back[0], up_back[1], (0, H, 7, D), 1),
        _room('bathroom', 'Upstairs bathroom', (7, H, W, D), 1),
    ]
    # The stairs sit in the front half of the hallway and go up toward the right (seen from the street):
    # steps on the 3 tiles before the last one; you step on downstairs at the left end and step off
    # upstairs at the right end, by the door of bedroom 2. Doors stay clear of the stairs.
    p.stairs = {'top': (W - 2, F), 'climb': (1, 0)}
    p.doors = [
        _door('Front door into the living room', (3, 0), (3, -1), 0, True),
        _door('Door between the living room and the kitchen', (5, 2), (6, 2), 0),
        _door('Door from the living room to the hallway', (3, F - 1), (3, F), 0),
        _door(f'Door from the hallway to the {_the(down_back[1])}', (3, H), (3, H - 1), 0),
        _door('Door from the hallway to the bathroom', (8, H), (8, H - 1), 0),
        _door('Upstairs, door from the hallway to bedroom 1', (2, F - 1), (2, F), 1),
        _door('Upstairs, door from the hallway to bedroom 2', (W - 1, F - 1), (W - 1, F), 1),
        _door(f'Upstairs, door from the hallway to {_the(up_back[1])}', (3, H), (3, H - 1), 1),
        _door('Upstairs, door from the hallway to the upstairs bathroom', (8, H), (8, H - 1), 1),
    ]
    _map_plan(p, f, along)
    return p


def _to_lot(p: Plan, f: Any, u_off: int, u: float, v: float) -> tuple[Any, Any]:
    """House point (u, v) to (meters from west, meters from north)."""
    u += u_off
    v += SETBACK
    if p.side == 'north':
        return (u, v)
    if p.side == 'south':
        return (f.width - u, f.depth - v)
    if p.side == 'west':
        return (v, f.depth - u)
    return (f.width - v, u)


def _tile_to_lot(p: Plan, f: Any, u_off: int, tile: Sequence[int]) -> tuple[int, int]:
    (e, n) = _to_lot(p, f, u_off, tile[0] + 0.5, tile[1] + 0.5)
    return (int(math.floor(e)), int(math.floor(n)))


def wall_meters(p: Plan) -> int:
    total = 0
    for level in {r.get('level', 0) for r in p.rooms}:
        total += _wall_meters([r for r in p.rooms if r.get('level', 0) == level])
    return total


def _wall_meters(room_list: list[Room]) -> int:
    edges = set()
    for r in room_list:
        (e0, n0, e1, n1) = r['box']
        for e in range(e0, e1):
            edges.add(('h', e, n0))
            edges.add(('h', e, n1))
        for n in range(n0, n1):
            edges.add(('v', e0, n))
            edges.add(('v', e1, n))
    return len(edges)


def side_between(tile_a: Sequence[int], tile_b: Sequence[int]) -> str:
    de, dn = tile_b[0] - tile_a[0], tile_b[1] - tile_a[1]
    if dn < 0:
        return 'north'
    if dn > 0:
        return 'south'
    if de < 0:
        return 'west'
    return 'east'


# ---------------------------------------------------------------- choosing


def _custom(kinds: list[str]) -> None:
    names = ', '.join(_names_for(kinds)) if kinds else 'no rooms yet'
    rows: list[tuple[str, str | None, str]] = []
    if kinds:
        rows.append(('Done, show the plan', f'Rooms so far: {names}.', 'done'))
    for k, (row, w, name) in KINDS.items():
        if k == 'living' and 'living' in kinds:
            continue
        rows.append(
            (
                f'Add a {name.lower()}',
                '{} meters wide, at the {}.'.format(w, 'front' if row == 'front' else 'back'),
                k,
            )
        )
    if kinds:
        rows.append(('Remove the last room', None, 'undo'))

    def picked(value: str) -> None:
        if value == 'done':
            _show_plan(kinds)
        elif value == 'undo':
            _custom(kinds[:-1])
        else:
            core.speak(f'{KINDS[value][2]} added.')
            _custom([*kinds, value])

    core.choose(
        'Your rooms',
        f'Rooms so far: {names}. A living room is always included.',
        rows,
        picked,
        tag='build',
    )


def _show_plan(kinds: list[str]) -> Any:
    """Ready-made layouts go through the workflow's common review."""
    import simsight_house_workflow as workflow

    workflow.install()
    return workflow._legacy_plan(kinds)


# ---------------------------------------------------------------- building


def _select_silently(index: int, name: str) -> None:
    """Choose a catalog item without the build helper announcing it."""
    build._cat['silent_select'] = True
    try:
        build.select_item(index, name)
    finally:
        build._cat['silent_select'] = False


def _pick_door(names_wanted: Sequence[str]) -> str | None:
    """Worker thread: choose a door from the catalog. Returns its name or None."""
    items = build.fetch_catalog(2, 'BUILDTAG_DOOR', 'wall')
    if not items:
        return None
    names = [it[0] for it in items]
    index = None
    for want in names_wanted:
        if want in names:
            index = names.index(want)
            break
    if index is None:
        plain = [
            i
            for (i, it) in enumerate(items)
            if not any(w in it[0].lower() for w in _NOT_PLAIN_DOOR)
        ]
        index = plain[0] if plain else 0
    _select_silently(index, names[index])
    return names[index]


def _pick_tool(name: str) -> bool:
    items = build.fetch_catalog(16, 'BUILDTAG_BLOCK_WALLTOOL,BUILDTAG_BLOCK_WITH_WALLS', 'tool')
    names = [it[0] for it in items]
    if name not in names:
        return False
    _select_silently(names.index(name), name)
    return True


def _stair_ids() -> list[Any]:
    import services
    import simsight_plus_rooms as rooms

    return [
        obj.id
        for obj in services.object_manager().values()
        if 'Build_Stair' in rooms.tag_names(obj) and hasattr(obj, 'id')
    ]


def _stair_info(oid: Any) -> tuple[Any, Any, tuple[int, int]] | None:
    """(e, n, climb) of a placed staircase: its position is the middle of the bottom edge of the lowest
    step, and its forward direction points to the side (measured October 2026)."""
    import services

    obj = services.object_manager().get(oid)
    if obj is None:
        return None
    (e, n) = build.frame().local(obj.position.x, obj.position.z)
    fx, fz = obj.forward.x, obj.forward.z
    return (e, n, (int(round(-fz)), int(round(-fx))))


def _pick_stairs() -> tuple[int, str] | None:
    """Worker thread: the catalog index and name of plain stairs, or None."""
    items = build.fetch_catalog(14, '', 'none')
    if not items:
        return None
    names = [it[0] for it in items]
    for want in STAIRS:
        if want in names:
            return (names.index(want), want)
    plain = [i for (i, n) in enumerate(names) if not any(w in n.lower() for w in _NOT_PLAIN_STAIRS)]
    i = plain[0] if plain else 0
    return (i, names[i])


def _click_stairs(s: Sequence[Any]) -> None:
    build.move_to(*build._to_screen(*s[0]))
    time.sleep(0.8)
    build.click(s[0])


def _place_stairs_worker_base(top: Sequence[int], climb: Sequence[int]) -> bool | str:
    """Worker thread only. Straight stairs on the ground floor going up to the floor above.
    top: lot tile (e, n) of the highest step; climb: direction (de, dn) going up.
    The pointer goes on the highest step. The game keeps the last way stairs were turned, so we
    place, check which way they went, and if wrong take them back, turn them (the . key) and try again.
    Returns True or a reason."""
    pick = _pick_stairs()
    if not pick:
        return 'nocatalog'
    if not build.set_floor(0):
        return 'nofloor'
    f = build.frame()
    want = (
        top[0] + 0.5 - (STAIR_LEN - 0.5) * climb[0],
        top[1] + 0.5 - (STAIR_LEN - 0.5) * climb[1],
    )
    shift = [0.0, 0.0]
    turn = False
    for attempt in range(10):
        before = set(build.on_main(_stair_ids) or [])
        _select_silently(*pick)
        if turn:
            build.tap_scan(0x34)  # the . key turns the stairs a quarter turn
            time.sleep(0.5)
            turn = False
        pt = f.world(top[0] + 0.5 + shift[0], top[1] + 0.5 + shift[1])
        if not build.point_and_act([pt], 0, _click_stairs, f'stairs try {attempt + 1}'):
            return 'noscreen'
        time.sleep(0.5)
        new = [oid for oid in (build.on_main(_stair_ids) or []) if oid not in before]
        if not new:
            core.log('stairs: not placed: {}'.format(build._last_error['text']))
            turn = True
            continue
        info = build.on_main(_stair_info, new[0])
        core.log(f'stairs: placed {info} want pos {want} climb {climb}')
        if info is None:
            continue
        (e, n, got) = info
        if got != tuple(climb):
            build.on_main(build.undo)
            time.sleep(1.5)
            turn = True
            continue
        err = (e - want[0], n - want[1])
        if abs(err[0]) < 0.3 and abs(err[1]) < 0.3:
            return True
        build.on_main(build.undo)
        time.sleep(1.5)
        shift = [shift[0] - err[0], shift[1] - err[1]]
    return 'failed'


def door_count() -> int:
    return sum(1 for t in build._wall_things() if t[2] == 'door')


def _click_door(s: Sequence[Any]) -> None:
    build._reselect()
    build.click(s[0])


def place_door_worker(tile_a: Tile, tile_b: Tile, level: int = 0) -> bool:
    """Worker thread, door already chosen. Click the wall between two neighboring tiles.
    Returns True when a door was added."""
    side = side_between(tile_a, tile_b)
    (x, z) = build.wall_cell_point(side, tile_a)
    if not build.set_floor(level):
        return False
    before = build.on_main(door_count) or 0
    if not build.point_and_act(
        [(x, z)],
        level,
        _click_door,
        f'door {tile_a} {side}',
        height=0.15,
        view=build.VIEW_FROM[side],
    ):
        return False
    time.sleep(0.8)
    return (build.on_main(door_count) or 0) > before


def _claim_busy() -> bool:
    """Mark the build helper busy; False (and says so) when a step is still running."""
    if build._busy[0]:
        core.speak(_BUSY_TEXT)
        return False
    build._busy[0] = True
    return True


def start_build(p: Plan) -> None:
    if not _claim_busy():
        return
    core.speak(
        'Building your house. This takes a few minutes. Please do not press any keys until you hear that it is finished.'
    )
    threading.Thread(
        target=_build_worker, args=(p,), name='simsight_plus_house', daemon=True
    ).start()


def _build_worker(p: Plan) -> None:
    try:
        _build(p)
    except Exception:
        core.log_exc('build house')
        core.post(core.speak, f'Sorry, building the house failed. {_LOG_HELP}')
    finally:
        build._busy[0] = False


def _say_base(text: str) -> None:
    core.post(core.speak, text)


_STOP_REASONS = {
    'nomoney': 'there is not enough money or something is in the way',
    'open': 'the walls did not close',
    'noscreen': 'I could not get it on screen',
    'nofloor': 'I could not switch the game to the right floor',
}
_STOP_OTHER = (
    'it came out a different size. Something may already be built there, so start on an empty lot'
)


def _build_rooms(p: Plan, rooms: Any) -> bool:
    """Worker thread: each room with the Room Tool. False (after saying why) when one failed."""
    for built, r in enumerate(p.rooms):
        _say('Building the {}.'.format(r['name'].lower()))
        box = build.room_worker(r['box'], overview=True, level=r.get('level', 0))
        core.log(
            'house: {} level {} wanted {} got {}'.format(
                r['name'], r.get('level', 0), r['box'], box
            )
        )
        if box != r['box']:
            what = _STOP_REASONS.get(box, _STOP_OTHER)
            _say(
                'I stopped building at the {}, because {}. {} rooms were built. '
                'You can take back each step with Undo in the build helper.'.format(
                    r['name'].lower(), what, built
                )
            )
            _name_rooms(p, rooms)
            return False
    return True


def _build_doors(p: Plan) -> list[str] | None:
    """Worker thread: the front door first, then the inside doors. The doors that could not be
    placed, or None (after saying why) when no door could be chosen."""
    missed = []
    current = None
    for d in p.doors:
        wanted = FRONT_DOORS if d['front'] else INTERIOR_DOORS
        if current != wanted:
            name = _pick_door(wanted)
            if not name:
                _say('I could not choose a door in the catalog.')
                return None
            current = wanted
        _say(d['what'] + '.')
        level = d.get('level', 0)
        ok = place_door_worker(d['tile_a'], d['tile_b'], level)
        if not ok:  # once more, the game sometimes drops the tool
            ok = place_door_worker(d['tile_a'], d['tile_b'], level)
        if not ok:
            missed.append(d['what'].lower())
        core.log('house: {} {}'.format(d['what'], 'ok' if ok else 'MISSED'))
    return missed


def _finished_text(p: Plan, spent: int, left: int, stairs_ok: bool, missed: list[str]) -> str:
    text = f'Your house is finished. It cost {spent:,} simoleons, and you have {left:,} left. '
    if p.stairs:
        text += (
            'The stairs are in the hallway. '
            if stairs_ok
            else f'But I could not put in the stairs, so nobody can get upstairs yet. {_LOG_HELP} '
        )
        text += 'The game now shows the ground floor. Page Up and Page Down change floors. '
    if missed:
        text += 'These doors could not be placed: {}. Use Door between two rooms in the build helper to try again. '.format(
            '; '.join(missed)
        )
    text += (
        'Alt+R lists the rooms by name. Next, furnish them: Alt+B, Furnish a room with the basics.'
    )
    return text


def _build_base(p: Plan) -> None:
    import simsight_plus_rooms as rooms

    start_money = build.on_main(build.funds) or 0
    if not _pick_tool('Room Tool'):
        _say('I could not choose the Room Tool in the catalog.')
        return
    if not _build_rooms(p, rooms):
        return
    _name_rooms(p, rooms)
    stairs_ok = True
    if p.stairs:
        _say('Putting in the stairs.')
        res = place_stairs_worker(p.stairs['top_lot'], p.stairs['climb_lot'])
        core.log(f'house: stairs {res}')
        stairs_ok = res is True
    missed = _build_doors(p)
    if missed is None:
        return
    build.set_floor(0)
    spent = start_money - (build.on_main(build.funds) or 0)
    left = build.on_main(build.funds) or 0
    _say(_finished_text(p, spent, left, stairs_ok, missed))


def _name_rooms(p: Plan, rooms: Any) -> None:
    def go() -> None:
        for room in rooms.scan_rooms():
            box = build._room_box(room)
            for r in p.rooms:
                if r['box'] == box and room.level == r.get('level', 0):
                    rooms.set_room_name(room, r['name'])

    build.on_main(go)


# ---------------------------------------------------------------- door tools


def _neighbors(
    room: Any, all_rooms: list[Any]
) -> tuple[list[tuple[Any, list[TilePair]]], list[TilePair]]:
    """Rooms that share a wall with `room`, with the wall tiles: ([(other, [(tile_a, tile_b)])],
    [(tile_a, tile_b)] on the outside walls)."""
    mine = build._room_tiles(room)
    by_tile = {}
    for other in all_rooms:
        if other is room or other.level != room.level:
            continue
        for t in build._room_tiles(other):
            by_tile[t] = other
    found: dict[int, tuple[Any, list[TilePair]]] = {}
    outside: list[TilePair] = []
    for t in mine:
        for de, dn in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nb = (t[0] + de, t[1] + dn)
            if nb in mine:
                continue
            other = by_tile.get(nb)
            if other is None:
                outside.append((t, nb))
            else:
                found.setdefault(id(other), (other, []))[1].append((t, nb))
    return list(found.values()), outside


def _middle(pairs: list[TilePair]) -> TilePair:
    """The wall piece in the middle of a run of wall pieces."""
    pairs = sorted(pairs, key=lambda ab: (ab[0][0], ab[0][1]))
    return pairs[len(pairs) // 2]


def _door_target_rows(room: Any, all_rooms: list[Any]) -> list[tuple[str, str, Any]]:
    """Choices for where a door from `room` leads: the rooms next to it, then each outside wall."""
    nbs, outside = _neighbors(room, all_rooms)
    rows = []
    for other, pairs in nbs:
        if len(pairs) < 1:
            continue
        rows.append(
            (
                f'{other.name}',
                f'They share {len(pairs)} meters of wall.',
                ('room', other, pairs),
            )
        )
    by_side: dict[str, list[TilePair]] = {}
    for a, b in outside:
        by_side.setdefault(side_between(a, b), []).append((a, b))
    street = street_side()
    for side, pairs in sorted(by_side.items()):
        label = 'Outside, the {} wall{}'.format(
            side, ', facing the street' if side == street else ''
        )
        rows.append(
            (
                label,
                f'A door to the yard. {len(pairs)} meters of wall.',
                ('out', side, pairs),
            )
        )
    return rows


def door_between_rooms() -> None:
    if not build.in_build_mode():
        core.speak('Go to Build mode first. Press F2, on a laptop Fn+F2.')
        return
    import simsight_plus_rooms as rooms

    all_rooms = list(rooms.scan_rooms())
    if not all_rooms:
        core.speak('There are no rooms yet.')
        return
    rows = [(r.name, f'{r.size_text()}, {rooms.floor_name(r.level)}', r) for r in all_rooms]

    def got_room(room: Any) -> None:
        def got_other(value: tuple[str, Any, list[TilePair]]) -> None:
            (kind, other, pairs) = value
            (a, b) = _middle(pairs)
            what = 'a door between the {} and the {}'.format(
                room.name, other.name if kind == 'room' else 'yard'
            )
            _run_door(a, b, room.level, what)

        core.choose(
            f'Door from the {room.name} to',
            'Choose where the door leads. It goes in the middle of the shared wall.',
            _door_target_rows(room, all_rooms),
            got_other,
            tag='build',
        )

    core.choose('Door between two rooms', 'Choose the first room.', rows, got_room, tag='build')


def _run_door(a: Tile, b: Tile, level: int, what: str) -> None:
    if not _claim_busy():
        return
    use_chosen = (
        build._cat.get('next') == 'wall'
        and build._cat.get('chosen') is not None
        and 'door' in build.tool_name().lower()
    )
    core.speak(f'Putting {what}. Please wait.')
    threading.Thread(
        target=_door_worker, args=(a, b, level, what, use_chosen), name='a11yplus_door', daemon=True
    ).start()


def _door_worker(a: Tile, b: Tile, level: int, what: str, use_chosen: bool) -> None:
    try:
        if not use_chosen and not _pick_door(INTERIOR_DOORS):
            _say('I could not choose a door in the catalog.')
            return
        money = build.on_main(build.funds) or 0
        ok = place_door_worker(a, b, level)
        spent = money - (build.on_main(build.funds) or 0)
        if ok:
            _say(f'Done, {what} is in place. It cost {spent:,} simoleons.')
        else:
            reason = build._last_error['text']
            _say(
                'The door was not placed. {}'.format(
                    f'The game says: {reason}'
                    if reason
                    else 'Furniture or another door may be in the way.'
                )
            )
    except Exception:
        core.log_exc('door job')
        _say('Sorry, placing the door failed.')
    finally:
        build._busy[0] = False


def _wall_tiles(e: float, n: float) -> TilePair:
    """The two tiles on either side of the wall line a door or window at (e, n) sits on:
    one of e, n is a whole number."""
    if abs(e - round(e)) < abs(n - round(n)):
        return (int(round(e)) - 1, int(math.floor(n))), (int(round(e)), int(math.floor(n)))
    return (int(math.floor(e)), int(round(n)) - 1), (int(math.floor(e)), int(round(n)))


def _thing_kind(names: set[str]) -> str | None:
    if 'Build_Door' in names or 'Build_Arch' in names:
        return 'Door'
    if 'Build_Window' in names:
        return 'Window'
    return None


def _thing_row(
    obj: Any, f: Any, named: dict[tuple[int, int, int], str], rooms: Any
) -> tuple[str, tuple[Any, ...]] | None:
    """(label, (x, z, level, side, kind, id)) for a door or window, else None."""
    try:
        kind = _thing_kind(rooms.tag_names(obj))
        if not kind:
            return None
        level = int(obj.level)
        (e, n) = f.local(obj.position.x, obj.position.z)
        # A door sits on a wall line. The rooms on either side tell where it is.
        a, b = _wall_tiles(e, n)
        ra, rb = (
            named.get((a[0], a[1], level), 'outside'),
            named.get((b[0], b[1], level), 'outside'),
        )
        if ra == 'outside':
            (ra, rb, a, b) = (rb, ra, b, a)
        where = (
            f'between the {ra} and the {rb}'
            if rb != 'outside'
            else f'from the {ra} to outside, {side_between(a, b)} wall'
        )
        value = (
            obj.position.x,
            obj.position.z,
            level,
            side_between(a, b),
            kind,
            getattr(obj, 'id', 0),
        )
        return (f'{kind} {where}', value)
    except Exception:  # one odd object must not hide the other doors and windows
        core.log_exc('door row')
        return None


def _thing_rows() -> list[tuple[str, tuple[Any, ...]]]:
    """Doors and windows with where they are: list of (label, (x, z, level, side, kind, id))."""
    import services
    import simsight_plus_rooms as rooms

    f = build.frame()
    named = {}
    for r in rooms.scan_rooms():
        for t in build._room_tiles(r):
            named[(t[0], t[1], r.level)] = r.name
    out = []
    for obj in services.object_manager().values():
        row = _thing_row(obj, f, named, rooms)
        if row is not None:
            out.append(row)
    out.sort()
    return out


def _tap_key(u: Any, vk: int, scan: int) -> None:
    u.keybd_event(vk, scan, 0, 0)
    time.sleep(0.05)
    u.keybd_event(vk, scan, 2, 0)


def _remove_worker(value: tuple[Any, ...]) -> None:
    import services

    (x, z, level, side, kind, oid) = value
    try:
        money = build.on_main(build.funds) or 0
        u = build._user32()
        _tap_key(u, 0x4B, 0x25)  # the game's Sledgehammer is the K key in Build mode
        time.sleep(0.5)
        build.point_and_act(
            [(x, z)],
            level,
            lambda s: build.click(s[0]),
            'remove ' + kind,
            height=1.0,
            view=build.VIEW_FROM.get(side),
        )
        time.sleep(0.8)
        gone = build.on_main(lambda: services.object_manager().get(oid) is None)
        back = (build.on_main(build.funds) or 0) - money
        _tap_key(u, 0x1B, 0x01)  # put the Sledgehammer away again
        if gone:
            _say(f'Removed the {kind.lower()}. You got {max(0, back):,} simoleons back.')
        else:
            _say(f'The {kind.lower()} is still there. {_LOG_HELP}')
    except Exception:
        core.log_exc('remove door')
        _say('Sorry, removing failed.')
    finally:
        build._busy[0] = False


def remove_door_or_window() -> None:
    if not build.in_build_mode():
        core.speak('Go to Build mode first. Press F2, on a laptop Fn+F2.')
        return
    rows = [(label, None, value) for (label, value) in _thing_rows()]
    if not rows:
        core.speak('There are no doors or windows.')
        return

    def got(value: tuple[Any, ...]) -> None:
        kind = value[4]
        if not _claim_busy():
            return
        core.speak(f'Removing the {kind.lower()}. Please wait.')
        threading.Thread(
            target=_remove_worker, args=(value,), name='a11yplus_remove', daemon=True
        ).start()

    core.choose('Remove a door or window', 'Choose which one.', rows, got, tag='build')


# ---------------------------------------------------------------- naming a room


def name_a_room() -> None:
    import simsight_plus_rooms as rooms

    all_rooms = rooms.scan_rooms()
    if not all_rooms:
        core.speak('There are no rooms yet.')
        return
    rows = [(r.name, f'{r.size_text()}, {rooms.floor_name(r.level)}', r) for r in all_rooms]

    def got(room: Any) -> None:
        def named(text: str) -> None:
            rooms.set_room_name(room, text.strip())
            core.speak(
                f'The room is now called {text.strip()}.' if text.strip() else 'Name removed.'
            )

        core.ask_text(
            'Room name', "Type a name, for example Kitchen or Shay's bedroom.", room.name, named
        )

    core.choose('Name a room', 'Choose the room to name.', rows, got, tag='build')


# DaRainbowGirlXX's house building, merged 2026-11-14. The building step runs
# build_helpers (stairs scoped to the running plan) -> detached (detached
# structure follow-up) -> house_planner (stacked-story follow-up) -> _build_base.
def _build(p: Plan) -> Any:
    import simsight_build_helpers as helpers

    helpers.install()
    return helpers._house_build(p)


def place_stairs_worker(top: Sequence[int], climb: Sequence[int]) -> Any:
    """Stacked plans place their own stair transitions; otherwise the original."""
    import simsight_build_helpers as helpers

    helpers.install()
    return helpers._stairs_for_plan(top, climb)


def _say(text: str) -> Any:
    """A stopped basement build says why."""
    import simsight_house_workflow as workflow

    return workflow._say(text)

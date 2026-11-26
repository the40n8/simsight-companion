"""House map for the phone companion (macOS).

The house room by room: what each room is, how big, where it sits on the
floor, which rooms it opens onto, and what is in it. Built from the game's
own room ids (the same grid simsight_plus_rooms uses for Alt+R).
Runs on the game tick only (called from simsight_bridge).

The dict's keys and shapes are the pipe's HouseMap JSON, read by the Go
bridge and the phone app: keep them as they are.
"""

from __future__ import annotations

import math
import re
import sys
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_plus_rooms as rooms

if TYPE_CHECKING:
    from typing import Iterable

    Link = dict[str, Any]  # {'door': bool, 'sides': {side: n}, 'doors': {side: n}, 'stairs': str}
    Flight = tuple[Any, int, '_Room | None', '_Room | None']  # stairs, floor, bottom, top

# Same convention as simsight_plus_rooms.bearing: north is +z.
COMPASS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')

# Room kinds from simsight_plus_rooms.KINDS, as keys the phone translates.
_KIND_KEYS = {
    'Bathroom': 'BATHROOM',
    'Kitchen': 'KITCHEN',
    'Nursery': 'NURSERY',
    'Bedroom': 'BEDROOM',
    'Dining room': 'DINING',
    'Living room': 'LIVING',
    'Study': 'STUDY',
}
_DOOR = re.compile(r'(?<!Out)door', re.IGNORECASE)
_STAIR_STEPS = (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, -3.0)  # along a flight, to find its end rooms


def compass(dx: float, dz: float) -> str:
    angle = math.degrees(math.atan2(dx, dz)) % 360.0
    return COMPASS[int((angle + 22.5) % 360.0 // 45) % 8]


class _Room:
    """Cells of one room on one floor (also what plus_rooms.given_name expects)."""

    def __init__(self, rid: Any, level: int) -> None:
        self.rid = rid
        self.level = level
        self.cells: list[tuple[int, int]] = []  # (i, j) grid indices
        self.tiles: list[tuple[float, float]] = []  # (x, z) world tile centres
        self.objects: list[Any] = []
        self.sims: list[Any] = []
        self.links: dict[_Room, Link] = {}  # the room it opens onto -> how
        self.kind = 'ROOM'
        self.kind2 = ''
        self.areas: list[tuple[str, str]] = []  # (kind, side) for open-plan rooms
        self.number = 0
        self.custom = ''

    @property
    def key(self) -> str:
        return f'{self.rid}:{self.level}'

    @property
    def center(self) -> tuple[float, float]:
        n = float(len(self.tiles)) or 1.0
        return (sum(t[0] for t in self.tiles) / n, sum(t[1] for t in self.tiles) / n)

    def add_cell(self, i: int, j: int, x: float, z: float) -> None:
        self.cells.append((i, j))
        self.tiles.append((x, z))

    def link(self, other: _Room, side: str, door: bool = False, stairs: str = '') -> None:
        entry = self.links.setdefault(
            other, {'door': False, 'sides': {}, 'doors': {}, 'stairs': ''}
        )
        if stairs:
            entry['stairs'] = stairs
        if door:
            entry['door'] = True
            entry['doors'][side] = entry['doors'].get(side, 0) + 1
        else:
            entry['sides'][side] = entry['sides'].get(side, 0) + 1


def _main_side(info: Link) -> str:
    """The side a link is mostly on: by its doors when it has any."""
    sides = info['doors'] or info['sides']
    return str(max(sides.items(), key=lambda kv: kv[1])[0])


def _is_door(obj: Any) -> bool:
    names = rooms.tag_names(obj)
    if any(_DOOR.search(n) for n in names):
        return True
    return bool(_DOOR.search(str(getattr(obj.definition, 'name', '') or '')))


def _is_stairs(obj: Any) -> bool:
    if any('Stair' in n and 'Ladder' not in n for n in rooms.tag_names(obj)):
        return True
    return 'stair' in str(getattr(obj.definition, 'name', '') or '').lower()


def _is_pool(x: float, z: float, level: int) -> bool:
    import build_buy
    import sims4.math
    import terrain

    try:
        y = terrain.get_terrain_height(x, z)
        return bool(
            build_buy.is_location_pool(sims4.math.Vector3(float(x), float(y), float(z)), int(level))
        )
    except Exception:  # not known: not a pool
        return False


def _flag(obj: Any, name: str) -> bool | None:
    """obj.name() when the object has that method, else None."""
    method = getattr(obj, name, None)
    return None if method is None else bool(method())


def _is_skipped(obj: Any) -> bool:
    """Sims, terrain, hidden objects, objects off the lot or in an inventory."""
    if getattr(obj, 'is_sim', False) or getattr(obj, 'is_terrain', False) or obj.is_hidden():
        return True
    return _flag(obj, 'is_on_active_lot') is False or _flag(obj, 'is_in_inventory') is True


def _floor_part(tiles: list[tuple[float, float]], x: float, z: float) -> str:
    """Where (x, z) is on a floor made of tiles: 'C' near the middle, else a compass side."""
    mx = sum(t[0] for t in tiles) / len(tiles)
    mz = sum(t[1] for t in tiles) / len(tiles)
    spread = [abs(t[0] - mx) for t in tiles] + [abs(t[1] - mz) for t in tiles]
    span = max([*spread, 1.0])
    return 'C' if math.hypot(x - mx, z - mz) < span * 0.3 else compass(x - mx, z - mz)


def _link_sort_key(link: dict[str, Any]) -> tuple[bool, bool, bool, int]:
    # stairs first, then doors, the garden last among the rest, then by side
    return (
        not link.get('stairs'),
        not link['door'],
        link['roomId'] == 'outside:0',
        COMPASS.index(link['side']),
    )


def _links_json(room: _Room) -> list[dict[str, Any]]:
    links = []
    for other, info in room.links.items():
        side = _main_side(info)
        if other is room:
            continue
        link: dict[str, Any] = {'roomId': other.key, 'door': info['door'], 'side': side}
        if info.get('stairs'):
            link['stairs'] = info['stairs']
        links.append(link)
    links.sort(key=_link_sort_key)
    return links


def _distance(a: Any, b: Any) -> int:
    return int(round(math.hypot(a.x - b.x, a.z - b.z)))


def _item_json(bridge: Any, obj: Any, room: _Room, half: float, sim: Any) -> dict[str, Any] | None:
    try:
        group, kind, keywords = bridge._classify(obj)
        cx, cz = room.center
        dx, dz = obj.position.x - cx, obj.position.z - cz
        side = 'C' if math.hypot(dx, dz) < max(1.0, half * 0.4) else compass(dx, dz)
        target = {
            'id': str(obj.id),
            'name': bridge._object_name(obj),
            'group': group,
            'kind': kind,
            'keywords': keywords,
        }
        if sim is not None:
            target['distance'] = _distance(obj.position, sim.position)
    except Exception:  # an object the bridge cannot describe is left out
        simsight_log.error('housemap: item')
        return None
    return {'target': target, 'side': side}


def _room_json(
    room: _Room, floor_tiles: list[tuple[float, float]], bridge: Any, sim: Any
) -> dict[str, Any]:
    cx, cz = room.center
    iu = [c[0] for c in room.cells]
    jv = [c[1] for c in room.cells]
    entry: dict[str, Any] = {
        'id': room.key,
        'kind': room.kind,
        'kind2': room.kind2,
        'areas': [{'kind': k, 'side': sd} for (k, sd) in room.areas],
        'number': room.number,
        'customName': room.custom,
        'floor': room.level,
        'width': max(iu) - min(iu) + 1,
        'depth': max(jv) - min(jv) + 1,
        'area': len(room.cells),
        'part': _floor_part(floor_tiles, cx, cz),
        'sims': [s.sim_info.first_name for s in room.sims],
    }
    if sim is not None:
        entry['here'] = sim in room.sims
        if not entry['here']:
            dx, dz = cx - sim.position.x, cz - sim.position.z
            entry['distance'] = int(round(math.hypot(dx, dz)))
            entry['bearing'] = compass(dx, dz)
    entry['links'] = _links_json(room)
    half = max(entry['width'], entry['depth']) / 2.0
    items = (_item_json(bridge, obj, room, half, sim) for obj in room.objects)
    entry['items'] = [item for item in items if item is not None]
    return entry


def _areas(room: _Room, kinds: list[str]) -> list[tuple[str, str]]:
    """Where in the room each kind's furniture is: [(KIND, side)]."""
    cx, cz = room.center
    tags_of = {k: t for (k, t, _w) in rooms.KINDS}
    out = []
    for kind in kinds:
        tags = tags_of[kind]
        spots = [o.position for o in room.objects if rooms.tag_names(o).intersection(tags)]
        if not spots:
            continue
        x = sum(p.x for p in spots) / len(spots)
        z = sum(p.z for p in spots) / len(spots)
        side = 'C' if math.hypot(x - cx, z - cz) < 1.0 else compass(x - cx, z - cz)
        out.append((_KIND_KEYS.get(kind, 'ROOM'), side))
    return out


def _kind_scores(room: _Room) -> list[tuple[str, int]]:
    """Room kinds by furniture weight, highest first (KINDS order on a tie)."""
    score: dict[str, int] = {}
    for obj in room.objects:
        names = rooms.tag_names(obj)
        for kind, tags, weight in rooms.KINDS:
            if names.intersection(tags):
                score[kind] = score.get(kind, 0) + weight
    return sorted(score.items(), key=lambda kv: -kv[1])


def _name_one(room: _Room) -> None:
    try:
        room.custom = rooms.given_name(room) or ''
    except Exception:  # the room is shown without a custom name
        simsight_log.error('housemap: given name')
        room.custom = ''
    ranked = _kind_scores(room)
    if not ranked:
        room.kind = 'ROOM' if room.objects else 'EMPTY'
        return
    room.kind = _KIND_KEYS.get(ranked[0][0], 'ROOM')
    # Open plan: a second kind with real furniture of its own.
    if len(ranked) > 1 and ranked[1][1] >= 2 and len(room.cells) >= 16:
        room.kind2 = _KIND_KEYS.get(ranked[1][0], '')
        room.areas = _areas(room, [ranked[0][0], ranked[1][0]])


def _name(found: list[_Room]) -> None:
    counts: dict[str, int] = {}
    for room in found:
        _name_one(room)
        counts[room.kind] = counts.get(room.kind, 0) + 1
    numbered: dict[str, int] = {}
    for room in sorted(found, key=lambda r: (r.level, -len(r.cells))):
        if counts[room.kind] > 1:
            numbered[room.kind] = numbered.get(room.kind, 0) + 1
            room.number = numbered[room.kind]


class _HouseMap:
    """One scan of the active lot: its rooms, the garden and pool around them,
    doors, stairs, furniture and Sims."""

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
        self.grid: dict[tuple[int, int, int], _Room] = {}  # (level, i, j) -> room
        self.found: dict[tuple[Any, int], _Room] = {}  # (room id, level) -> indoor room
        # Around the rooms: the garden ("outside", also beyond walls upstairs)
        # and any pool in it.
        self.outside = _Room('outside', 0)
        self.outside.kind = 'OUTSIDE'
        self.pool = _Room('pool', 0)
        self.pool.kind = 'POOL'
        self.outdoor: dict[tuple[int, int], _Room] = {}  # ground floor (i, j) -> garden or pool

    def world(self, i: int, j: int) -> tuple[float, float]:
        fu, fv = (i + 0.5) / self.nu, (j + 0.5) / self.nv
        return (self.x0 + self.ux * fu + self.vx * fv, self.z0 + self.uz * fu + self.vz * fv)

    def _cells(self) -> Iterable[tuple[int, int]]:
        for i in range(self.nu):
            for j in range(self.nv):
                yield i, j

    def scan_rooms(self) -> None:
        for level in self.levels:
            for i, j in self._cells():
                x, z = self.world(i, j)
                rid = rooms.room_id_at(x, z, level)
                if not rid:
                    continue
                room = self.found.get((rid, level))
                if room is None:
                    room = self.found[(rid, level)] = _Room(rid, level)
                room.add_cell(i, j, x, z)
                self.grid[(level, i, j)] = room

    def drop_yard(self) -> None:
        """A "room" that reaches the edge of the lot and covers much of it is
        the yard, not a room."""
        for key, room in list(self.found.items()):
            edge = any(i in (0, self.nu - 1) or j in (0, self.nv - 1) for (i, j) in room.cells)
            if edge and len(room.cells) > 0.25 * self.nu * self.nv:
                del self.found[key]

    def _indoor_at(self, level: int, i: int, j: int) -> _Room | None:
        room = self.grid.get((level, i, j))
        return room if room is not None and (room.rid, room.level) in self.found else None

    def scan_outdoor(self) -> None:
        for i, j in self._cells():
            if self._indoor_at(0, i, j) is None:
                x, z = self.world(i, j)
                area = self.pool if _is_pool(x, z, 0) else self.outside
                area.add_cell(i, j, x, z)
                self.outdoor[(i, j)] = area

    def _room_at(self, level: int, i: int, j: int) -> _Room:
        room = self._indoor_at(level, i, j)
        if room is not None:
            return room
        return self.outdoor.get((i, j), self.outside) if level == 0 else self.outside

    def room_at_world(self, x: float, z: float, level: int) -> _Room:
        room = self.found.get((rooms.room_id_at(x, z, level), level))
        if room is not None:
            return room
        if level == 0 and self.pool.cells and _is_pool(x, z, 0):
            return self.pool
        return self.outside

    def _walled(self) -> list[_Room]:
        """The indoor rooms and the pool: the areas whose edges are scanned."""
        return [*self.found.values(), self.pool]

    def link_walls(self) -> None:
        """Walls and openings: neighbouring cells that belong to another room or outside."""
        steps = (
            (1, 0, self.ux / self.nu, self.uz / self.nu),
            (-1, 0, -self.ux / self.nu, -self.uz / self.nu),
            (0, 1, self.vx / self.nv, self.vz / self.nv),
            (0, -1, -self.vx / self.nv, -self.vz / self.nv),
        )
        for room in self._walled():
            for i, j in room.cells:
                for di, dj, dx, dz in steps:
                    other = self._room_at(room.level, i + di, j + dj)
                    if other is not room:
                        room.link(other, compass(dx, dz))

    def _add_door(self, obj: Any, level: int) -> None:
        pos, fwd = obj.position, obj.forward
        a = self.room_at_world(pos.x + fwd.x * 0.6, pos.z + fwd.z * 0.6, level)
        b = self.room_at_world(pos.x - fwd.x * 0.6, pos.z - fwd.z * 0.6, level)
        for here, there in ((a, b), (b, a)):
            if here is not None and here is not there:
                cx, cz = here.center
                here.link(there, compass(pos.x - cx, pos.z - cz), door=True)

    def _add_object(self, obj: Any, stairs: list[Any]) -> None:
        try:
            if _is_skipped(obj):
                return
            pos, level = obj.position, int(obj.level)
            if _is_stairs(obj):
                stairs.append(obj)
            elif _is_door(obj):
                self._add_door(obj, level)
            elif list(obj.super_affordances()):
                self.room_at_world(pos.x, pos.z, level).objects.append(obj)
        except Exception:  # an object of an odd shape is left out
            simsight_log.error('housemap: object')

    def add_objects(self, objects: Iterable[Any]) -> list[Any]:
        """Doors link rooms, furniture goes in its room; returns the stairs."""
        stairs: list[Any] = []
        for obj in objects:
            self._add_object(obj, stairs)
        return stairs

    def _add_sim(self, other: Any) -> None:
        try:
            room = self.room_at_world(other.position.x, other.position.z, int(other.level))
            if room is self.outside and _flag(other, 'is_on_active_lot') is False:
                return  # out on the street, not in the garden
            room.sims.append(other)
        except Exception:  # a Sim of an odd shape is left out
            simsight_log.error('housemap: sim')

    def add_sims(self, sims: Iterable[Any]) -> None:
        for other in sims:
            self._add_sim(other)

    def link_outdoor(self) -> None:
        """The garden and the pool open onto the rooms that open onto them."""
        for room in self._walled():
            for other, info in list(room.links.items()):
                if other in (self.outside, self.pool) and other is not room:
                    other.link(room, _main_side(info), door=info['door'])

    def _stair_end(self, pos: Any, fwd: Any, level: int) -> _Room | None:
        for step in _STAIR_STEPS:
            rid = rooms.room_id_at(pos.x + fwd.x * step, pos.z + fwd.z * step, level)
            room = self.found.get((rid, level))
            if room is not None:
                return room
        return None

    def _flight(self, obj: Any) -> Flight | None:
        try:
            pos, level, fwd = obj.position, int(obj.level), obj.forward
            bottom = self._stair_end(pos, fwd, level)
            top = self._stair_end(pos, fwd, level + 1)
            if bottom is not None and top is not None:
                cx, cz = bottom.center
                bottom.link(top, compass(pos.x - cx, pos.z - cz), stairs='up')
                cx, cz = top.center
                top.link(bottom, compass(pos.x - cx, pos.z - cz), stairs='down')
        except Exception:  # stairs of an odd shape are left out
            simsight_log.error('housemap: stairs')
            return None
        return (obj, level, bottom, top)

    def flights(self, stairs: list[Any]) -> list[Flight]:
        """Stairs: the room at each end, found a step or two along the flight."""
        flights = (self._flight(obj) for obj in stairs)
        return [flight for flight in flights if flight is not None]

    def to_json(self, flights: list[Flight], bridge: Any, sim: Any) -> dict[str, Any]:
        # Where each room sits on its floor, from the middle of the floor.
        middles: dict[int, list[tuple[float, float]]] = {}
        for room in self.found.values():
            middles.setdefault(room.level, []).extend(room.tiles)
        middles.setdefault(0, list(self.outside.tiles))
        indoor = sorted(
            self.found.values(), key=lambda r: (r.level, r.kind, r.number, -len(r.cells))
        )
        extra = [area for area in (self.pool, self.outside) if area.cells]
        out = [_room_json(room, middles[room.level], bridge, sim) for room in indoor + extra]
        stairs_out = []
        for obj, level, bottom, top in flights:
            tiles = middles.get(level) or self.outside.tiles
            stairs_out.append(
                {
                    'floor': level,
                    'part': _floor_part(tiles, obj.position.x, obj.position.z),
                    'bottomRoomId': bottom.key if bottom is not None else '',
                    'topRoomId': top.key if top is not None else '',
                }
            )
        return {'rooms': out, 'floors': sorted(middles), 'stairs': stairs_out}


def build() -> dict[str, Any] | None:
    """The house map as a dict in the pipe's JSON (HouseMap), or None off a lot."""
    import services

    bridge = sys.modules['simsight_bridge']
    lot = services.active_lot()
    sim = services.get_active_sim()
    if lot is None:
        return None
    house = _HouseMap(lot)
    house.scan_rooms()
    house.drop_yard()
    if not house.found:
        return {'rooms': [], 'floors': []}
    house.scan_outdoor()
    house.link_walls()
    stairs = house.add_objects(services.object_manager().get_valid_objects_gen())
    house.add_sims(services.sim_info_manager().instanced_sims_gen())
    house.link_outdoor()
    _name(list(house.found.values()))
    flights = house.flights(stairs)
    return house.to_json(flights, bridge, sim)

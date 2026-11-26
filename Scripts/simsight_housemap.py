"""House map for the phone companion (macOS).

The house room by room: what each room is, how big, where it sits on the
floor, which rooms it opens onto, and what is in it. Built from the game's
own room ids (the same grid simsight_plus_rooms uses for Alt+R).
Runs on the game tick only (called from simsight_bridge).
"""

import math
import re
import sys

import simsight_plus_rooms as rooms

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


def compass(dx, dz):
    angle = math.degrees(math.atan2(dx, dz)) % 360.0
    return COMPASS[int((angle + 22.5) % 360.0 // 45) % 8]


class _Room:
    """Cells of one room on one floor (also what plus_rooms.given_name expects)."""

    def __init__(self, rid, level):
        self.rid = rid
        self.level = level
        self.cells = []  # (i, j) grid indices
        self.tiles = []  # (x, z) world tile centres
        self.objects = []
        self.sims = []
        self.links = {}  # other room key or None (outside) -> {'door': bool, 'sides': {side: count}}
        self.kind = 'ROOM'
        self.kind2 = ''
        self.areas = []  # (kind, side) for open-plan rooms
        self.number = 0
        self.custom = ''

    @property
    def key(self):
        return f'{self.rid}:{self.level}'

    @property
    def center(self):
        n = float(len(self.tiles)) or 1.0
        return (sum(t[0] for t in self.tiles) / n, sum(t[1] for t in self.tiles) / n)

    def link(self, other, side, door=False, stairs=''):
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


def _is_door(obj):
    names = rooms.tag_names(obj)
    if any(_DOOR.search(n) for n in names):
        return True
    return bool(_DOOR.search(str(getattr(obj.definition, 'name', '') or '')))


def build():
    """The house map as a dict in the pipe's JSON (HouseMap), or None off a lot."""
    import services

    bridge = sys.modules['simsight_bridge']
    lot = services.active_lot()
    sim = services.get_active_sim()
    if lot is None:
        return None
    corners = list(lot.corners)
    c0, c1, c3 = corners[0], corners[1], corners[3]
    ux, uz = c1.x - c0.x, c1.z - c0.z
    vx, vz = c3.x - c0.x, c3.z - c0.z
    nu = max(1, int(round(math.hypot(ux, uz))))
    nv = max(1, int(round(math.hypot(vx, vz))))
    try:
        levels = range(int(lot.min_level), int(lot.max_level) + 1)
    except Exception:
        levels = range(3)

    def world(i, j):
        fu, fv = (i + 0.5) / nu, (j + 0.5) / nv
        return (c0.x + ux * fu + vx * fv, c0.z + uz * fu + vz * fv)

    grid = {}
    found = {}
    for level in levels:
        for i in range(nu):
            for j in range(nv):
                x, z = world(i, j)
                rid = rooms.room_id_at(x, z, level)
                if not rid:
                    continue
                room = found.get((rid, level))
                if room is None:
                    room = found[(rid, level)] = _Room(rid, level)
                room.cells.append((i, j))
                room.tiles.append((x, z))
                grid[(level, i, j)] = room
    # A "room" that reaches the edge of the lot and covers much of it is the
    # yard, not a room.
    for key, room in list(found.items()):
        edge = any(i in (0, nu - 1) or j in (0, nv - 1) for (i, j) in room.cells)
        if edge and len(room.cells) > 0.25 * nu * nv:
            del found[key]
    if not found:
        return {'rooms': [], 'floors': []}

    def indoor_at(level, i, j):
        room = grid.get((level, i, j))
        return room if room is not None and (room.rid, room.level) in found else None

    # Around the rooms: the garden ("outside", also beyond walls upstairs)
    # and any pool in it.
    outside = _Room('outside', 0)
    outside.kind = 'OUTSIDE'
    pool = _Room('pool', 0)
    pool.kind = 'POOL'
    outdoor = {}
    for i in range(nu):
        for j in range(nv):
            if indoor_at(0, i, j) is None:
                x, z = world(i, j)
                area = pool if _is_pool(x, z, 0) else outside
                area.cells.append((i, j))
                area.tiles.append((x, z))
                outdoor[(i, j)] = area

    def room_at(level, i, j):
        room = indoor_at(level, i, j)
        if room is not None:
            return room
        return outdoor.get((i, j), outside) if level == 0 else outside

    def room_at_world(x, z, level):
        room = found.get((rooms.room_id_at(x, z, level), level))
        if room is not None:
            return room
        return pool if level == 0 and pool.cells and _is_pool(x, z, 0) else outside

    # Walls and openings: neighbouring cells that belong to another room or outside.
    steps = (
        (1, 0, ux / nu, uz / nu),
        (-1, 0, -ux / nu, -uz / nu),
        (0, 1, vx / nv, vz / nv),
        (0, -1, -vx / nv, -vz / nv),
    )
    for room in list(found.values()) + [pool]:
        for i, j in room.cells:
            for di, dj, dx, dz in steps:
                other = room_at(room.level, i + di, j + dj)
                if other is not room:
                    room.link(other, compass(dx, dz))

    stairs = []
    for obj in services.object_manager().get_valid_objects_gen():
        try:
            if (
                getattr(obj, 'is_sim', False)
                or getattr(obj, 'is_terrain', False)
                or obj.is_hidden()
            ):
                continue
            on_lot = getattr(obj, 'is_on_active_lot', None)
            if on_lot is not None and not on_lot():
                continue
            in_inventory = getattr(obj, 'is_in_inventory', None)
            if in_inventory is not None and in_inventory():
                continue
            pos, level = obj.position, int(obj.level)
            if _is_stairs(obj):
                stairs.append(obj)
                continue
            if _is_door(obj):
                fwd = obj.forward
                a = room_at_world(pos.x + fwd.x * 0.6, pos.z + fwd.z * 0.6, level)
                b = room_at_world(pos.x - fwd.x * 0.6, pos.z - fwd.z * 0.6, level)
                for here, there in ((a, b), (b, a)):
                    if here is not None and here is not there:
                        cx, cz = here.center
                        here.link(there, compass(pos.x - cx, pos.z - cz), door=True)
                continue
            if not list(obj.super_affordances()):
                continue
            room_at_world(pos.x, pos.z, level).objects.append(obj)
        except Exception:
            continue
    for other in services.sim_info_manager().instanced_sims_gen():
        try:
            room = room_at_world(other.position.x, other.position.z, int(other.level))
            on_lot = getattr(other, 'is_on_active_lot', None)
            if room is outside and on_lot is not None and not on_lot():
                continue  # out on the street, not in the garden
            room.sims.append(other)
        except Exception:
            continue
    # The garden and the pool open onto the rooms that open onto them.
    for room in list(found.values()) + [pool]:
        for other, info in list(room.links.items()):
            if other in (outside, pool) and other is not room:
                sides = info['doors'] or info['sides']
                other.link(room, max(sides.items(), key=lambda kv: kv[1])[0], door=info['door'])

    _name(list(found.values()))

    # Stairs: the room at each end, found a step or two along the flight.
    flights = []
    for obj in stairs:
        try:
            pos, level, fwd = obj.position, int(obj.level), obj.forward
            ends = []
            for lvl in (level, level + 1):
                end = None
                for step in (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, -3.0):
                    room = found.get(
                        (rooms.room_id_at(pos.x + fwd.x * step, pos.z + fwd.z * step, lvl), lvl)
                    )
                    if room is not None:
                        end = room
                        break
                ends.append(end)
            bottom, top = ends
            if bottom is not None and top is not None:
                cx, cz = bottom.center
                bottom.link(top, compass(pos.x - cx, pos.z - cz), stairs='up')
                cx, cz = top.center
                top.link(bottom, compass(pos.x - cx, pos.z - cz), stairs='down')
            flights.append((obj, level, bottom, top))
        except Exception:
            continue

    # Where each room sits on its floor, from the middle of the floor.
    middles = {}
    for room in found.values():
        middles.setdefault(room.level, []).extend(room.tiles)
    out = []
    middles.setdefault(0, list(outside.tiles))
    extra = [area for area in (pool, outside) if area.cells]
    for room in (
        sorted(found.values(), key=lambda r: (r.level, r.kind, r.number, -len(r.cells))) + extra
    ):
        cx, cz = room.center
        tiles = middles[room.level]
        mx = sum(t[0] for t in tiles) / len(tiles)
        mz = sum(t[1] for t in tiles) / len(tiles)
        span = max(max(abs(t[0] - mx) for t in tiles), max(abs(t[1] - mz) for t in tiles), 1.0)
        part = 'C' if math.hypot(cx - mx, cz - mz) < span * 0.3 else compass(cx - mx, cz - mz)
        iu = [c[0] for c in room.cells]
        jv = [c[1] for c in room.cells]
        entry = {
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
            'part': part,
            'sims': [s.sim_info.first_name for s in room.sims],
        }
        if sim is not None:
            entry['here'] = sim in room.sims
            if not entry['here']:
                dx, dz = cx - sim.position.x, cz - sim.position.z
                entry['distance'] = int(round(math.hypot(dx, dz)))
                entry['bearing'] = compass(dx, dz)
        links = []
        for other, info in room.links.items():
            sides = info['doors'] or info['sides']
            side = max(sides.items(), key=lambda kv: kv[1])[0]
            if other is room:
                continue
            link = {'roomId': other.key, 'door': info['door'], 'side': side}
            if info.get('stairs'):
                link['stairs'] = info['stairs']
            links.append(link)
        links.sort(
            key=lambda l: (
                not l.get('stairs'),
                not l['door'],
                l['roomId'] == outside.key,
                COMPASS.index(l['side']),
            )
        )
        entry['links'] = links
        half = max(entry['width'], entry['depth']) / 2.0
        items = []
        for obj in room.objects:
            try:
                group, kind, keywords = bridge._classify(obj)
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
                    target['distance'] = int(
                        round(
                            math.hypot(
                                obj.position.x - sim.position.x, obj.position.z - sim.position.z
                            )
                        )
                    )
                items.append({'target': target, 'side': side})
            except Exception:
                continue
        entry['items'] = items
        out.append(entry)
    stairs_out = []
    for obj, level, bottom, top in flights:
        tiles = middles.get(level) or outside.tiles
        mx = sum(t[0] for t in tiles) / len(tiles)
        mz = sum(t[1] for t in tiles) / len(tiles)
        span = max(max(abs(t[0] - mx) for t in tiles), max(abs(t[1] - mz) for t in tiles), 1.0)
        dx, dz = obj.position.x - mx, obj.position.z - mz
        stairs_out.append(
            {
                'floor': level,
                'part': 'C' if math.hypot(dx, dz) < span * 0.3 else compass(dx, dz),
                'bottomRoomId': bottom.key if bottom is not None else '',
                'topRoomId': top.key if top is not None else '',
            }
        )
    return {'rooms': out, 'floors': sorted(middles), 'stairs': stairs_out}


def _is_stairs(obj):
    if any('Stair' in n and 'Ladder' not in n for n in rooms.tag_names(obj)):
        return True
    return 'stair' in str(getattr(obj.definition, 'name', '') or '').lower()


def _is_pool(x, z, level):
    import build_buy
    import sims4.math
    import terrain

    try:
        y = terrain.get_terrain_height(x, z)
        return bool(
            build_buy.is_location_pool(sims4.math.Vector3(float(x), float(y), float(z)), int(level))
        )
    except Exception:
        return False


def _areas(room, kinds):
    """Where in the room each kind's furniture is: [(KIND, side)]."""
    cx, cz = room.center
    out = []
    for kind in kinds:
        tags = dict((k, t) for (k, t, _w) in rooms.KINDS)[kind]
        spots = [o.position for o in room.objects if rooms.tag_names(o).intersection(tags)]
        if not spots:
            continue
        x = sum(p.x for p in spots) / len(spots)
        z = sum(p.z for p in spots) / len(spots)
        side = 'C' if math.hypot(x - cx, z - cz) < 1.0 else compass(x - cx, z - cz)
        out.append((_KIND_KEYS.get(kind, 'ROOM'), side))
    return out


def _name(found):
    counts = {}
    for room in found:
        try:
            room.custom = rooms.given_name(room) or ''
        except Exception:
            room.custom = ''
        score = {}
        for obj in room.objects:
            names = rooms.tag_names(obj)
            for kind, tags, weight in rooms.KINDS:
                if names.intersection(tags):
                    score[kind] = score.get(kind, 0) + weight
        if score:
            ranked = sorted(score.items(), key=lambda kv: -kv[1])
            room.kind = _KIND_KEYS.get(ranked[0][0], 'ROOM')
            # Open plan: a second kind with real furniture of its own.
            if len(ranked) > 1 and ranked[1][1] >= 2 and len(room.cells) >= 16:
                room.kind2 = _KIND_KEYS.get(ranked[1][0], '')
                room.areas = _areas(room, [ranked[0][0], ranked[1][0]])
        else:
            room.kind = 'ROOM' if room.objects else 'EMPTY'
        counts[room.kind] = counts.get(room.kind, 0) + 1
    numbered = {}
    for room in sorted(found, key=lambda r: (r.level, -len(r.cells))):
        if counts[room.kind] > 1:
            numbered[room.kind] = numbered.get(room.kind, 0) + 1
            room.number = numbered[room.kind]

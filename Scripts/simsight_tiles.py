"""Lot-aligned tiles for the Build cursor: where things are, walls and doors,
and whether the chosen catalog object fits at a tile and facing.

Every game call is checked against the 1.128.90 decompile:

  * the lot frame comes from lot.corners (lot.size_x and size_z agree);
  * an object covers a tile when obj.footprint_polygon.contains(point) (as
    HasFootprintComponent and routing.connectivity use it), or when its
    origin is on the tile (objects without a footprint);
  * walls come from routing.ray_test_verbose(start, end, context,
    return_object_id=True) between neighbouring tile centres, as
    ScriptObject.check_line_of_sight reads it: RAYCAST_HIT_TYPE_IMPASSABLE
    with no blocking object is a wall or fence; the context is the active
    Sim's raycast_context(), so walls block as they do for a Sim;
  * fit is build_buy.test_location_for_object(obj, definition id, location,
    objects to ignore), as RuntimeSlot.is_valid_for_placement and Puddle
    use it, at a sims4.math.Location on the world routing surface;
  * rooms are build_buy.get_block_id, which EA's Python never calls, so it
    is optional: anything it does not answer is simply not reported.

Simulation thread only. Unknown answers stay unknown; nothing is invented.

Game objects differ in what they answer, and EA's native calls raise
whatever they raise: a probe that fails means "unknown" and is skipped
(`except Exception` below), never a failed key press.
"""

from __future__ import annotations

import contextlib
import math
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    Point2 = tuple[float, float]
    Tile = tuple[int, int]

# Facing and direction index: north is the lot's +j axis, east its +i axis.
DIRECTIONS = (('north', 'północ'), ('east', 'wschód'), ('south', 'południe'), ('west', 'zachód'))
STEPS = ((0, 1), (1, 0), (0, -1), (-1, 0))
# Origins tried inside a tile: centre first, then edges (two-tile objects
# such as beds sit on a tile edge), then corners.
OFFSETS = (
    (0.5, 0.5),
    (0.5, 1.0),
    (1.0, 0.5),
    (0.5, 0.0),
    (0.0, 0.5),
    (1.0, 1.0),
    (1.0, 0.0),
    (0.0, 0.0),
    (0.0, 1.0),
)
NEAREST_RADIUS = 5
NEARBY_RADIUS = 3
# Floor facts the game answers for a point: (fact, build_buy function).
GROUND_FACTS = (
    ('floor', 'has_floor_at_location'),
    ('outside', 'is_location_outside'),
    ('pool', 'is_location_pool'),
)
# Words in EA's placement errors and the reason the player hears.
_REASONS = (
    (('wall',), 'a wall is in the way', 'przeszkadza ściana'),
    (
        ('intersect', 'overlap', 'object', 'footprint'),
        'something is in the way',
        'coś przeszkadza',
    ),
    (('floor',), 'it needs a floor', 'wymaga podłogi'),
    (('slope', 'terrain'), 'the ground is not flat enough', 'teren nie jest dość płaski'),
    (('pool', 'water'), 'water is in the way', 'przeszkadza woda'),
    (('lot', 'bound'), 'it would leave the lot', 'wyszedłby poza parcelę'),
)


def text(en: str, pl: str) -> str:
    return pl if lang._detect_lang() == 'pl' else en


def direction_name(index: int) -> str:
    return text(*DIRECTIONS[index % 4])


class Frame:
    """The lot's grid: origin corner, unit axes and size in whole tiles."""

    def __init__(self, corners: Iterable[Any]) -> None:
        points = [(float(c.x), float(c.z)) for c in corners]
        if len(points) < 3:
            raise ValueError('A lot needs corners')
        origin = points[0]
        others = sorted(points[1:], key=lambda p: (p[0] - origin[0]) ** 2 + (p[1] - origin[1]) ** 2)
        a, b = others[0], others[1]
        self.origin = origin
        self.width, self.east = self._axis(origin, a)
        self.depth, self.north = self._axis(origin, b)
        if self.width < 1 or self.depth < 1:
            raise ValueError('Lot too small')

    @staticmethod
    def _axis(origin: Point2, point: Point2) -> tuple[int, Point2]:
        dx, dz = point[0] - origin[0], point[1] - origin[1]
        length = math.hypot(dx, dz)
        if not math.isfinite(length) or length < 0.5:
            raise ValueError('Degenerate lot corner')
        return int(round(length)), (dx / length, dz / length)

    def world(self, i: float, j: float, du: float = 0.5, dv: float = 0.5) -> Point2:
        """(x, z) of a point in tile (i, j); du, dv run 0..1 across it."""
        u, v = i + du, j + dv
        return (
            self.origin[0] + u * self.east[0] + v * self.north[0],
            self.origin[1] + u * self.east[1] + v * self.north[1],
        )

    def tile_of(self, x: Any, z: Any) -> Tile:
        dx, dz = float(x) - self.origin[0], float(z) - self.origin[1]
        u = dx * self.east[0] + dz * self.east[1]
        v = dx * self.north[0] + dz * self.north[1]
        return int(math.floor(u)), int(math.floor(v))

    def inside(self, i: int, j: int) -> bool:
        return 0 <= i < self.width and 0 <= j < self.depth

    def clamp(self, i: int, j: int) -> Tile:
        return min(max(i, 0), self.width - 1), min(max(j, 0), self.depth - 1)

    def yaw(self, facing: int) -> float:
        """Yaw angle (sims4.math convention, atan2(x, z)) of a facing index."""
        vx, vz = (
            self.north,
            self.east,
            (-self.north[0], -self.north[1]),
            (-self.east[0], -self.east[1]),
        )[facing % 4]
        return math.atan2(vx, vz)

    def facing_of(self, angle: float) -> int:
        best, best_gap = 0, None
        for index in range(4):
            gap = abs(
                math.atan2(math.sin(angle - self.yaw(index)), math.cos(angle - self.yaw(index)))
            )
            if best_gap is None or gap < best_gap:
                best, best_gap = index, gap
        return best


class _State:
    """The cached lot frame and the placement errors already logged."""

    def __init__(self) -> None:
        self.frame: Frame | None = None
        self.frame_zone: Any = None
        self.logged_errors: set[str] = set()


_state = _State()


def frame() -> Frame | None:
    """The active lot's frame, cached per zone; None when the lot will not say."""
    try:
        import services

        zone = services.current_zone_id()
        if _state.frame is not None and zone == _state.frame_zone:
            return _state.frame
        lot = services.active_lot()
        _state.frame, _state.frame_zone = Frame(list(lot.corners)), zone
        return _state.frame
    except Exception as ex:  # no zone or lot yet (loading, CAS): no frame
        simsight_log.line(f'tiles: no lot frame ({ex!r})')
        _state.frame = None
        return None


def relative(di: int, dj: int) -> str:
    """'2 north, 1 east', or 'here'."""
    parts = []
    for amount, positive, negative in ((dj, 0, 2), (di, 1, 3)):
        if amount:
            parts.append(f'{abs(amount)} {direction_name(positive if amount > 0 else negative)}')
    return ', '.join(parts) if parts else text('here', 'tutaj')


# ------------------------------------------------------------- game queries


def surface(level: Any) -> Any:
    import routing
    import services

    return routing.SurfaceIdentifier(
        services.current_zone_id(), int(level), routing.SurfaceType.SURFACETYPE_WORLD
    )


def position(f: Frame, i: int, j: int, level: Any, du: float = 0.5, dv: float = 0.5) -> Any:
    import terrain
    from sims4.math import Vector3

    x, z = f.world(i, j, du, dv)
    y = terrain.get_terrain_height(x, z, routing_surface=surface(level))
    if y is None or not math.isfinite(float(y)):
        raise ValueError('Tile height unavailable')
    return Vector3(x, float(y), z)


def _listed(obj: Any, lot: Any, level: Any) -> bool:
    """A visible object of this floor, on the lot, not in an inventory."""
    try:
        if obj.is_in_inventory() or int(obj.level) != int(level):
            return False
        return not obj.is_hidden() and lot.is_position_on_lot(obj.position)
    except Exception:  # an object that will not say is not listed
        return False


def _lot_objects(level: Any) -> Iterator[Any]:
    import services

    lot = services.active_lot()
    for obj in list(services.object_manager().values()):
        if _listed(obj, lot, level):
            yield obj


def _footprint_covers(obj: Any, point: Any) -> bool:
    try:
        footprint = obj.footprint_polygon
        return footprint is not None and bool(footprint.contains(point))
    except Exception:  # no footprint: the origin decides
        return False


def _covers(obj: Any, f: Frame, i: int, j: int, point: Any) -> bool:
    if _footprint_covers(obj, point):
        return True
    try:
        return f.tile_of(obj.position.x, obj.position.z) == (i, j)
    except Exception:  # no position: not on any tile
        return False


def objects_on(f: Frame, i: int, j: int, level: Any) -> list[Any]:
    """Top-level objects covering the tile (Sims included), largest last so a
    rug does not hide the bed on it; slotted objects are reached through
    children_on()."""
    point = position(f, i, j, level)
    return [obj for obj in _lot_objects(level) if _top_level(obj) and _covers(obj, f, i, j, point)]


def _top_level(obj: Any) -> bool:
    try:
        return getattr(obj, 'parent', None) is None
    except Exception:  # a parent the game will not give: skip the object
        return False


def children_on(obj: Any) -> list[Any]:
    result: list[Any] = []
    # Children the game will not list are left out; extend keeps the ones
    # listed before a failure.
    with contextlib.suppress(Exception):
        result.extend(
            child
            for child in getattr(obj, 'children', ()) or ()
            if not getattr(child, 'is_sim', False)
        )
    return result


def _free_slot(slot: Any) -> bool:
    try:
        return bool(slot.empty and not slot.is_deco_slot())
    except Exception:  # a slot that will not say is not counted
        return False


def free_spots(obj: Any) -> int:
    """Empty, non-decoration slots: where a computer or plate could go."""
    try:
        return sum(1 for slot in obj.get_runtime_slots_gen() if _free_slot(slot))
    except Exception:  # no runtime slots: no free spots
        return 0


def is_door(obj: Any) -> bool:
    try:
        return any(cls.__name__ == 'Door' for cls in type(obj).__mro__)
    except (AttributeError, TypeError):
        return False


def _raycast_context() -> Any:
    try:
        import services

        sim = services.get_active_sim()
        return sim.raycast_context() if sim is not None else None
    except Exception as ex:
        simsight_log.line(f'tiles: no raycast context ({ex!r})')
        return None


def _ray_side(f: Frame, start: Any, ni: int, nj: int, level: Any, context: Any) -> str | None:
    """'wall' or 'open' between start and tile (ni, nj); None when the ray
    test fails."""
    import routing
    import sims4.math

    try:
        end = routing.Location(
            position(f, ni, nj, level), sims4.math.Quaternion.IDENTITY(), surface(level)
        )
        hit, blocker = routing.ray_test_verbose(start, end, context, return_object_id=True)
    except Exception as ex:
        simsight_log.line(f'tiles: ray test failed ({ex!r})')
        return None
    return 'wall' if hit == routing.RAYCAST_HIT_TYPE_IMPASSABLE and not blocker else 'open'


def walls(f: Frame, i: int, j: int, level: Any) -> dict[int, str]:
    """{direction: 'wall' | 'edge' | 'open'}; directions the ray test could
    not answer are left out."""
    result: dict[int, str] = {}
    context = _raycast_context()
    try:
        import routing
        import sims4.math

        start = routing.Location(
            position(f, i, j, level), sims4.math.Quaternion.IDENTITY(), surface(level)
        )
    except Exception as ex:
        simsight_log.line(f'tiles: wall probe unavailable ({ex!r})')
        return result
    for index, (di, dj) in enumerate(STEPS):
        ni, nj = i + di, j + dj
        if not f.inside(ni, nj):
            result[index] = 'edge'
            continue
        if context is None:
            continue
        side = _ray_side(f, start, ni, nj, level, context)
        if side is not None:
            result[index] = side
    return result


def snapshot(level: Any) -> list[Any]:
    """The lot's objects on one floor, listed once, for callers that check
    several tiles or edges (tyjokr's October 1 interface)."""
    return list(_lot_objects(level))


def doors(
    f: Frame, i: int, j: int, level: Any, objects: Sequence[Any] | None = None
) -> dict[int, Any]:
    """{direction: door object} for doors on this tile's edges. objects is an
    optional snapshot(level) to search instead of listing the lot again."""
    result: dict[int, Any] = {}
    for obj in objects if objects is not None else _lot_objects(level):
        if not is_door(obj):
            continue
        try:
            x, z = float(obj.position.x), float(obj.position.z)
        except Exception:  # a door without a position is on no edge
            continue
        for index, (di, dj) in enumerate(STEPS):
            ex, ez = f.world(i, j, 0.5 + di * 0.5, 0.5 + dj * 0.5)
            if (x - ex) ** 2 + (z - ez) ** 2 <= 0.36:
                result[index] = obj
    return result


def room(f: Frame, i: int, j: int, level: Any) -> int | None:
    """An opaque room id, or None when the game will not say."""
    try:
        import build_buy
        import services

        point = position(f, i, j, level)
        value = build_buy.get_block_id(services.current_zone_id(), point, int(level))
        return int(value) if value is not None else None
    except Exception:  # optional game call (see the module notes)
        return None


def location_facts(
    point: Any, level: Any, checks: Sequence[tuple[str, str]] = GROUND_FACTS
) -> dict[str, bool]:
    """{fact: answer} for each (fact, build_buy function) the game answers
    at point; facts it will not answer are left out."""
    import build_buy

    result = {}
    for label, method in checks:
        value = _ask(build_buy, method, point, level)
        if value is not None:
            result[label] = value
    return result


def _ask(module: Any, method: str, point: Any, level: Any) -> bool | None:
    try:
        value = getattr(module, method)(point, level)
    except Exception:  # the game will not say: unknown
        return None
    return bool(value) if value is not None else None


def ground(f: Frame, i: int, j: int, level: Any) -> dict[str, bool]:
    """Floor facts the game answers: floor, outside, pool."""
    try:
        point = position(f, i, j, level)
    except Exception:  # no height here: nothing is known
        return {}
    return location_facts(point, int(level))


# ------------------------------------------------------------------- fitting


def test_fit(
    obj: Any, def_id: Any, point: Any, angle: float, level: Any, ignore: Iterable[Any] = ()
) -> tuple[bool | None, str | None]:
    """(ok, reason): build_buy.test_location_for_object at an exact point."""
    try:
        import build_buy
        import sims4.math

        location = sims4.math.Location(
            sims4.math.Transform(point, sims4.math.angle_to_yaw_quaternion(angle)), surface(level)
        )
        result = build_buy.test_location_for_object(obj, def_id, location, list(ignore))
    except Exception as ex:
        simsight_log.line(f'tiles: placement test raised {ex!r}')
        return None, None
    if isinstance(result, tuple):
        ok = bool(result[0])
        errors = result[1] if len(result) > 1 else None
    else:
        ok, errors = bool(result), None
    if not ok and errors:
        key = str(errors)[:160]
        if key not in _state.logged_errors:
            _state.logged_errors.add(key)
            simsight_log.line(f'tiles: placement refused: {key}')
    return ok, (reason(errors) if not ok else None)


def reason(errors: Any) -> str:
    """A player-facing reason from EA's placement errors, when one is clear."""
    words = str(errors or '').lower()
    for needles, en, pl in _REASONS:
        if any(needle in words for needle in needles):
            return text(en, pl)
    return text('the game will not allow it here', 'gra nie pozwala na to tutaj')


def fit_at(
    f: Frame, def_id: Any, i: int, j: int, level: Any, facing: int
) -> tuple[Any, str | None]:
    """(point, None) for the first origin in the tile that fits, or
    (None, reason), or (None, None) when the game could not be asked."""
    first_reason = None
    asked = False
    angle = f.yaw(facing)
    for du, dv in OFFSETS:
        point = _point_or_none(f, i, j, level, du, dv)
        if point is None:
            continue
        ok, why = test_fit(None, def_id, point, angle, level)
        if ok is None:
            continue
        asked = True
        if ok:
            return point, None
        if first_reason is None:
            first_reason = why
    return None, (first_reason if asked else None)


def _point_or_none(f: Frame, i: int, j: int, level: Any, du: float, dv: float) -> Any:
    try:
        return position(f, i, j, level, du, dv)
    except Exception:  # no height at this origin: try the next
        return None


def _ring(f: Frame, i: int, j: int, ring: int) -> list[tuple[int, int, int]]:
    """(squared distance, ni, nj) of the lot tiles on a square ring, nearest first."""
    tiles = []
    for di in range(-ring, ring + 1):
        for dj in range(-ring, ring + 1):
            if max(abs(di), abs(dj)) != ring:
                continue
            ni, nj = i + di, j + dj
            if f.inside(ni, nj):
                tiles.append((di * di + dj * dj, ni, nj))
    tiles.sort()
    return tiles


def nearest_fit(
    f: Frame, def_id: Any, i: int, j: int, level: Any, facing: int
) -> tuple[int, int, Any, int] | None:
    """(ni, nj, point, facing) of the closest tile where it fits, trying the
    current facing before the others; None when nothing fits nearby."""
    facings = [facing] + [index for index in range(4) if index != facing]
    for ring in range(1, NEAREST_RADIUS + 1):
        tiles = _ring(f, i, j, ring)
        for option in facings:
            for _distance, ni, nj in tiles:
                point, _why = fit_at(f, def_id, ni, nj, level, option)
                if point is not None:
                    return ni, nj, point, option
    return None


def covered_tiles(f: Frame, obj: Any, level: Any) -> list[Tile]:
    """Tiles whose centre the object's footprint covers (up to a 7 by 7 area)."""
    try:
        ci, cj = f.tile_of(obj.position.x, obj.position.z)
        footprint = obj.footprint_polygon
    except Exception:  # no position or footprint: no tiles
        return []
    tiles = [
        (ci + di, cj + dj)
        for di in range(-3, 4)
        for dj in range(-3, 4)
        if footprint is not None and _centre_covered(footprint, f, ci + di, cj + dj, level)
    ]
    return tiles or [(ci, cj)]


def _centre_covered(footprint: Any, f: Frame, i: int, j: int, level: Any) -> bool:
    try:
        return bool(footprint.contains(position(f, i, j, level)))
    except Exception:  # no height there: not covered
        return False

"""The lot frame (whole meters from the west and north edges) and the tile
map of rooms, objects and free ground."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_log


if TYPE_CHECKING:
    from collections.abc import Callable

    from lots.lot import Lot

    from .state import Grid


# Positions are given in whole meters from the lot's west edge (x) and north edge (y).


class Frame:
    def __init__(self, lot: Lot) -> None:
        xs = [c.x for c in lot.corners]
        zs = [c.z for c in lot.corners]
        self.x0, self.x1 = min(xs), max(xs)
        self.z0, self.z1 = min(zs), max(zs)
        self.width = round(self.x1 - self.x0)  # west to east
        self.depth = round(self.z1 - self.z0)  # north to south

    def world(self, e: float, n: float) -> tuple[float, float]:
        """meters from west edge, meters from north edge -> world x, z"""
        return (self.x0 + e, self.z1 - n)

    def local(self, x: float, z: float) -> tuple[float, float]:
        return (x - self.x0, self.z1 - z)


def frame() -> Frame:
    import services

    return Frame(services.active_lot())


def in_build_mode() -> bool:
    try:
        import services

        return bool(services.current_zone().is_in_build_buy)
    except AttributeError:  # no zone yet (loading)
        return False


# ---------------------------------------------------------------- tile map


def tile_map(level: int = 0) -> tuple[Frame, Grid]:
    """grid[n][e] for each 1 m tile: 'room' id (int > 0), 'free', or 'object'."""
    import services
    import build_buy
    import terrain
    import sims4.math
    import simsight_plus_rooms as rooms

    f = frame()
    grid: Grid = [[None] * f.width for _ in range(f.depth)]
    for n in range(f.depth):
        for e in range(f.width):
            (x, z) = f.world(e + 0.5, n + 0.5)
            rid = rooms.room_id_at(x, z, level)
            pos = sims4.math.Vector3(x, terrain.get_terrain_height(x, z), z)
            try:
                outside = build_buy.is_location_outside(pos, level)
            except Exception:  # EA's native build data may not answer for this tile (logged)
                simsight_log.error('plus build: outside')
                outside = rid == 0
            grid[n][e] = 'free' if outside else (rid or 'inside')
    for obj in services.object_manager().values():
        tile = _object_tile(f, obj, level, rooms.tag_names)
        if tile is not None:
            (e, n) = tile
            if 0 <= n < f.depth and 0 <= e < f.width and grid[n][e] == 'free':
                grid[n][e] = 'object'
    return f, grid


def _object_tile(
    f: Frame, obj: Any, level: int, tag_names: Callable[[Any], set[str]]
) -> tuple[int, int] | None:
    """(e, n) tile of a bought object on this floor, or None."""
    try:
        if obj.is_sim or int(obj.level) != level:
            return None
        if not any(t.startswith('BuyCat') for t in tag_names(obj)):
            return None
        (e, n) = f.local(obj.position.x, obj.position.z)
        return (int(e), int(n))
    except (AttributeError, TypeError, ValueError):  # objects without a place on the lot
        return None


def free_spots(grid: Grid, w: int, d: int, limit: int = 6) -> list[tuple[int, int]]:
    """Top-left corners (e, n) where a w x d rectangle is completely free, preferring spots
    that touch the house (so the new room joins it) and are near the middle."""
    rows, cols = len(grid), len(grid[0]) if grid else 0
    spots: list[tuple[int, float, int, int]] = []
    for n in range(1, rows - d):  # rooms must stay 1 m inside the lot edge
        for e in range(1, cols - w):
            if all(grid[n + j][e + i] == 'free' for j in range(d) for i in range(w)):
                touch = 0
                for j in range(-1, d + 1):
                    for i in range(-1, w + 1):
                        if (
                            0 <= n + j < rows
                            and 0 <= e + i < cols
                            and (i in (-1, w) or j in (-1, d))
                            and grid[n + j][e + i] not in ('free', 'object')
                        ):
                            touch += 1
                spots.append((-touch, abs(e + w / 2 - cols / 2) + abs(n + d / 2 - rows / 2), e, n))
    spots.sort()
    out: list[tuple[int, int]] = []
    for _touch, _dist, e, n in spots:
        if all(abs(e - e2) >= max(2, w // 2) or abs(n - n2) >= max(2, d // 2) for (e2, n2) in out):
            out.append((e, n))
        if len(out) >= limit:
            break
    return out


def _touch_text(grid: Grid, e: int, n: int, w: int, d: int) -> str:
    sides = []
    rows, cols = len(grid), len(grid[0])

    def solid(r: int, c: int) -> bool:
        return 0 <= r < rows and 0 <= c < cols and grid[r][c] not in ('free', 'object')

    if any(solid(n - 1, e + i) for i in range(w)):
        sides.append('north')
    if any(solid(n + d, e + i) for i in range(w)):
        sides.append('south')
    if any(solid(n + j, e - 1) for j in range(d)):
        sides.append('west')
    if any(solid(n + j, e + w) for j in range(d)):
        sides.append('east')
    if not sides:
        return 'stands on its own'
    return 'joins the house on its {} side'.format(' and '.join(sides))

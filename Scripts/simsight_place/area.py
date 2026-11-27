"""Areas of a floor for guided placement: the named rooms, then the rest of
the lot in up to nine sections."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable

from .state import lot_zone_id

if TYPE_CHECKING:
    from world.lot import Lot
    from collections.abc import Iterator, Sequence

    Area = tuple[Any, 'LotArea', str | None]  # centre, area, room name


class LotArea:
    """A room or a section of the unroomed lot on a particular floor."""

    def __init__(
        self,
        index: int | None,
        centers: Sequence[Any],
        points: list[Any],
        room_at: Callable[[Any], Any],
        room_id: Any,
    ) -> None:
        self.index = index
        self.centers = centers
        self.points = points
        self.room_at = room_at
        self.room_id = room_id

    @staticmethod
    def distance(point: Any, center: Any) -> float:
        return (point.x - center.x) ** 2 + (point.z - center.z) ** 2

    def contains(self, point: Any) -> bool:
        room_id = self.room_at(point)
        if self.room_id or room_id:
            return room_id == self.room_id
        return (
            min(range(len(self.centers)), key=lambda i: self.distance(point, self.centers[i]))
            == self.index
        )


def _floor_point(x: float, z: float, routing_surface: Any) -> Any:
    """The terrain point at (x, z), or None where the height is unknown."""
    import terrain
    from sims4.math import Vector3

    height = terrain.get_terrain_height(x, z, routing_surface=routing_surface)
    if height is None or not math.isfinite(height):
        return None
    return Vector3(x, height, z)


def _sections(
    lot: Lot,
    level: int,
    routing_surface: Any,
    centers: Sequence[Any],
    room_at: Callable[[Any], Any],
) -> dict[int, list[Any]]:
    """Points outside the named rooms, grouped by the nearest of the 3x3 centres."""
    import build_buy

    sections: dict[int, list[Any]] = {}
    for sample in lot.get_uniform_sampling_of_points(
        min(33, max(9, math.ceil(lot.size_x / 2))), min(33, max(9, math.ceil(lot.size_z / 2)))
    ):
        if not lot.is_position_on_lot(sample) or build_buy.is_location_pool(sample, level):
            continue
        if level != 0 and not build_buy.has_floor_at_location(sample, level):
            continue
        point = _floor_point(sample.x, sample.z, routing_surface)
        if point is None:
            continue
        if room_at(point) is None:
            index = min(range(len(centers)), key=lambda i: LotArea.distance(point, centers[i]))
            sections.setdefault(index, []).append(point)
    return sections


def _room_points(lot: Lot, level: int, routing_surface: Any, room: Any) -> list[Any]:
    import build_buy

    points = []
    for x, z in room.tiles:
        point = _floor_point(x, z, routing_surface)
        if point is None:
            continue
        if lot.is_position_on_lot(point) and not build_buy.is_location_pool(point, level):
            points.append(point)
    return points


def guided_areas(lot: Lot, level: int, routing_surface: Any) -> Iterator[Area]:
    """The floor's named rooms, then the rest of the lot in up to nine sections."""
    import build_buy
    import simsight_plus_rooms as room_reader
    from sims4.math import Vector3

    centers = lot.get_uniform_sampling_of_points(3, 3)
    zone_id = lot_zone_id()
    named_rooms = {room.rid: room for room in room_reader.scan_rooms() if room.level == level}

    def room_at(point: Any) -> Any:
        room_id = build_buy.get_room_id(zone_id, point, level)
        return room_id if room_id in named_rooms else None

    sections = _sections(lot, level, routing_surface, centers, room_at)
    for room_id, room in named_rooms.items():
        points = _room_points(lot, level, routing_surface, room)
        if not points:
            continue
        x, z = room.center
        center = Vector3(x, points[0].y, z)
        points.sort(key=lambda point: LotArea.distance(point, center))
        yield center, LotArea(None, centers, points, room_at, room_id), room.name
    for index, points in sorted(sections.items()):
        center = centers[index]
        points.sort(key=lambda point: LotArea.distance(point, center))
        yield center, LotArea(index, centers, points, room_at, None), None

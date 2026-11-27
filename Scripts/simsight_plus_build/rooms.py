"""Rooms and walls read from the game: boxes, tiles, wall cells and what
hangs on them."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any


from .lot import Frame, frame

if TYPE_CHECKING:
    from collections.abc import Callable

    from .state import Box, Row


def _new_room_box(before: set[tuple[Box, int]]) -> Box | None:
    """Box (e0, n0, e1, n1) of rooms that did not exist before, or None. Rooms are compared by their
    box, because the game may renumber a room when a new one is built next to it."""
    import simsight_plus_rooms as rooms

    new = [room_box(r) for r in rooms.scan_rooms() if (room_box(r), r.level) not in before]
    if not new:
        return None
    return (
        min(b[0] for b in new),
        min(b[1] for b in new),
        max(b[2] for b in new),
        max(b[3] for b in new),
    )


def room_ids() -> set[tuple[Box, int]]:
    import simsight_plus_rooms as rooms

    return {(room_box(r), r.level) for r in rooms.scan_rooms()}


def room_rows() -> list[Row]:
    import simsight_plus_rooms as rooms

    return [
        (r.name, f'{r.size_text()}, {rooms.floor_name(r.level)}', r) for r in rooms.scan_rooms()
    ]


def room_box(room: Any) -> Box:
    f = frame()
    es = [f.local(t[0], t[1])[0] for t in room.tiles]
    ns = [f.local(t[0], t[1])[1] for t in room.tiles]
    return (
        math.floor(min(es)),
        math.floor(min(ns)),
        math.floor(max(es)) + 1,
        math.floor(max(ns)) + 1,
    )


def wall_point(room: Any, side: str, where: float = 0.5) -> tuple[float, float]:
    """World (x, z) on the middle of a room wall, a little inside the room so the click hits it."""
    f = frame()
    (e0, n0, e1, n1) = room_box(room)
    inset = 0.0
    if side == 'north':
        return f.world(e0 + (e1 - e0) * where, n0 + inset)
    if side == 'south':
        return f.world(e0 + (e1 - e0) * where, n1 - inset)
    if side == 'west':
        return f.world(e0 + inset, n0 + (n1 - n0) * where)
    return f.world(e1 - inset, n0 + (n1 - n0) * where)


def room_tiles(room: Any) -> set[tuple[int, int]]:
    f = frame()
    return {
        (int(math.floor(f.local(t[0], t[1])[0])), int(math.floor(f.local(t[0], t[1])[1])))
        for t in room.tiles
    }


# side -> (tile step to the outside neighbor, how to read "left to right" when facing that wall from inside)
_SIDES: dict[str, tuple[tuple[int, int], Callable[[int, int], int]]] = {
    'north': ((0, -1), lambda e, n: e),
    'south': ((0, 1), lambda e, n: -e),
    'west': ((-1, 0), lambda e, n: -n),
    'east': ((1, 0), lambda e, n: n),
}


def room_walls(room: Any) -> list[dict[str, Any]]:
    """List of walls: dict(side, cells=[(e, n), ...] left to right). Each cell is the 1 m wall piece
    on that side of the room tile (e, n)."""
    tiles = room_tiles(room)
    walls: list[dict[str, Any]] = []
    for side, ((de, dn), order) in _SIDES.items():
        cells = sorted(
            [t for t in tiles if (t[0] + de, t[1] + dn) not in tiles],
            key=lambda t: order(*t),
        )
        if cells:
            walls.append({'side': side, 'cells': cells})
    return walls


def wall_cell_point(side: str, cell: tuple[int, int]) -> tuple[float, float]:
    """World (x, z) at the middle of the wall piece on `side` of tile `cell`."""
    f = frame()
    (e, n) = cell
    if side == 'north':
        return f.world(e + 0.5, n)
    if side == 'south':
        return f.world(e + 0.5, n + 1)
    if side == 'west':
        return f.world(e, n + 0.5)
    return f.world(e + 1, n + 0.5)


def wall_things() -> list[tuple[float, float, str]]:
    """Doors and windows on the lot: list of (e, n, kind)."""
    import services
    import simsight_plus_rooms as rooms

    f = frame()
    out = []
    for obj in services.object_manager().values():
        thing = _wall_thing(f, obj, rooms.tag_names(obj))
        if thing is not None:
            out.append(thing)
    return out


def _wall_thing(f: Frame, obj: Any, names: set[str]) -> tuple[float, float, str] | None:
    kind = (
        'door'
        if ('Build_Door' in names or 'Build_Arch' in names)
        else ('window' if 'Build_Window' in names else None)
    )
    if not kind:
        return None
    try:
        (e, n) = f.local(obj.position.x, obj.position.z)
    except (AttributeError, TypeError):  # objects without a place on the lot
        return None
    return (e, n, kind)


def _cell_status(
    side: str, cell: tuple[int, int], things: list[tuple[float, float, str]]
) -> str | None:
    (x, z) = wall_cell_point(side, cell)
    (e, n) = frame().local(x, z)
    for te, tn, kind in things:
        if abs(te - e) < 0.75 and abs(tn - n) < 0.75:
            return kind
    return None

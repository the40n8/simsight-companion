"""What an object needs under it (build_buy.PlacementFlags), and the spoken
refusal for each need the grid cannot serve."""

from __future__ import annotations

from typing import Any

import simsight_log


# build_buy.PlacementFlags (build_buy.py): what an object needs under it.
FLAG_WALL = 1 | 2  # CENTER_ON_WALL | EDGE_AGAINST_WALL = REQUIRES_WALL
FLAG_CEILING = 8
FLAG_FENCE = 128
FLAG_SLOT = 1024
FLAG_TERRAIN = 65536
FLAG_WATER = 2097152
_REQUIREMENTS = (
    (FLAG_SLOT, 'slot'),
    (FLAG_WALL, 'wall'),
    (FLAG_CEILING, 'ceiling'),
    (FLAG_FENCE, 'fence'),
    (FLAG_WATER, 'water'),
    (FLAG_TERRAIN, 'terrain'),
)


def requirement(def_id: Any) -> str | None:
    """'slot', 'wall', 'ceiling', 'fence', 'terrain', 'water', None (the floor
    will do) or 'unknown' when the game would not say."""
    try:
        import build_buy

        flags = int(build_buy.get_object_placement_flags(def_id))
    except Exception:  # EA's native lookup: any failure means it would not say
        simsight_log.error('place: placement flags')
        simsight_log.line(f'place: no placement flags for {def_id}')
        return 'unknown'
    for mask, need in _REQUIREMENTS:
        if flags & mask:
            return need
    return None


NEEDS = {
    'wall': (
        'It hangs on a wall. Use the Build cursor, Control Alt B, to hang it.',
        'To wisi na ścianie. Użyj kursora budowania, Control Alt B, aby to powiesić.',
    ),
    'ceiling': (
        'It hangs from a ceiling. Ceiling placement is not available from the grid yet.',
        'To wisi pod sufitem. Umieszczanie pod sufitem nie jest jeszcze dostępne z siatki.',
    ),
    'fence': (
        'It attaches to a fence. Fence placement is not available from the grid yet.',
        'To mocuje się do płotu. Umieszczanie na płocie nie jest jeszcze dostępne z siatki.',
    ),
    'water': ('It needs a water surface.', 'To wymaga powierzchni wody.'),
    'terrain': ('It needs bare ground.', 'To wymaga gołej ziemi.'),
    'unknown': (
        'The game did not say where this object can go, so nothing was bought.',
        'Gra nie podała, gdzie można postawić ten obiekt, więc nic nie kupiono.',
    ),
}

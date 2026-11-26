"""Experimental placement on bare ground, water, ceilings and fences from the
Build cursor. Contributed by DaRainbowGirlXX (2026-10-16), merged into TS4
Access 2026-11-12. Each kind can be switched off in the settings menu.

Every placement still goes through place.place with exact=True, so the
game's own placement test must pass before anything is bought.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Callable

    from simsight_tiles import Frame

SUPPORTED = frozenset(('terrain', 'water', 'ceiling', 'fence'))
SETTING = {
    'terrain': 'experimental_bare_ground',
    'water': 'experimental_water',
    'ceiling': 'experimental_ceiling',
    'fence': 'experimental_fence',
}


def _log(message: str) -> None:
    simsight_log.line('surfaces: ' + message)


def _text(en: str, pl: str) -> str:
    import simsight_grid as grid

    return grid._text(en, pl)


def handles(need: Any) -> bool:
    """True when the Build cursor places this requirement experimentally."""
    if need not in SUPPORTED:
        return False
    import simsight_settings as settings

    return bool(settings.get(SETTING[need], True))


def _cursor_position() -> Any:
    import simsight_grid as grid

    target = grid.cursor()
    return target[0] if target is not None else None


def _natural_ground(position: Any, level: int) -> bool | None:
    try:
        import build_buy

        return bool(build_buy.is_location_natural_ground(position, level))
    except Exception as ex:
        _log(f'natural-ground check failed: {ex!r}')
        return None


def _water_point(position: Any, level: int) -> tuple[Any, float, str]:
    """Return (surface point, depth, kind), or (None, depth, kind)."""
    import sims4.math

    depth, pool, pond = 0.0, False, False
    try:
        import build_buy

        pool = bool(build_buy.is_location_pool(position, level))
        pond = bool(build_buy.get_pond_id(position))
    except Exception as ex:
        _log(f'pool/pond check failed: {ex!r}')
    try:
        import terrain

        depth = float(
            terrain.get_water_depth(float(position.x), float(position.z), int(level)) or 0.0
        )
    except Exception as ex:
        _log(f'water-depth check failed: {ex!r}')
    if not (pool or pond or depth > 0.01):
        return None, depth, 'water'
    # Grid Y is the terrain/bottom sample; water depth raises it to the surface.
    point = sims4.math.Vector3(
        float(position.x), float(position.y) + max(0.0, depth), float(position.z)
    )
    return point, depth, 'pool' if pool else ('pond' if pond else 'water')


def _ceiling_point(position: Any, level: int) -> Any:
    """The next routing level's plane is the best available ceiling anchor."""
    try:
        import routing
        import services
        import sims4.math
        import terrain

        surface = routing.SurfaceIdentifier(
            services.current_zone_id(), int(level) + 1, routing.SurfaceType.SURFACETYPE_WORLD
        )
        height = float(
            terrain.get_terrain_height(
                float(position.x), float(position.z), routing_surface=surface
            )
        )
        if not math.isfinite(height) or height <= float(position.y) + 1.0:
            return None
        return sims4.math.Vector3(float(position.x), height, float(position.z))
    except Exception as ex:
        _log(f'ceiling sample failed: {ex!r}')
        return None


def _fence_point() -> tuple[Frame, int, Any, float] | None:
    """Select an edge geometrically; exact validation proves whether it is a fence."""
    import sims4.math
    import simsight_grid as grid
    import simsight_tiles as tiles
    import simsight_wall as wall

    state = wall._wall_state()
    if state is None:
        return None
    frame, sides, doors = state
    side = int(grid._facing) % 4
    if side in doors or not wall._blocked(sides.get(side)):
        return None
    i, j = grid._tile
    di, dj = tiles.STEPS[side]
    center = tiles.position(frame, i, j, grid._level)
    neighbor = frame.world(i + di, j + dj)
    point = sims4.math.Vector3(
        (float(center.x) + float(neighbor[0])) / 2.0,
        float(center.y),
        (float(center.z) + float(neighbor[1])) / 2.0,
    )
    return frame, side, point, frame.yaw((side + 2) % 4)


def _terrain_fit(position: Any, level: int) -> str:
    if _natural_ground(position, level) is True:
        return _text(
            'Experimental bare-ground placement. Natural ground detected; Enter asks the game to validate it. ',
            'Eksperymentalne umieszczanie na ziemi. Wykryto naturalny teren; Enter prosi grę o sprawdzenie. ',
        )
    return _text(
        'Experimental bare-ground placement. This tile has flooring or is not natural ground. ',
        'Eksperymentalne umieszczanie na ziemi. To pole ma podłogę albo nie jest naturalnym terenem. ',
    )


def _water_fit(position: Any, level: int) -> str:
    point, depth, kind = _water_point(position, level)
    if point is None:
        return _text(
            'Experimental water placement. No water surface detected here. ',
            'Eksperymentalne umieszczanie w wodzie. Nie wykryto tutaj powierzchni wody. ',
        )
    return _text(
        'Experimental water placement. {} detected, depth {:.1f} metres; Enter asks the game to validate it. ',
        'Eksperymentalne umieszczanie w wodzie. Wykryto {}, głębokość {:.1f} metra; Enter prosi grę o sprawdzenie. ',
    ).format(kind, depth)


def _ceiling_fit(position: Any, level: int) -> str:
    point = _ceiling_point(position, level)
    if point is None:
        return _text(
            'Experimental ceiling placement. No ceiling anchor could be sampled here. ',
            'Eksperymentalne umieszczanie na suficie. Nie znaleziono punktu sufitu. ',
        )
    return _text(
        'Experimental ceiling placement. Sampled height {:.1f} metres; Enter asks the game to validate it. ',
        'Eksperymentalne umieszczanie na suficie. Wysokość {:.1f} metra; Enter prosi grę o sprawdzenie. ',
    ).format(float(point.y) - float(position.y))


def _fence_fit(_position: Any, _level: int) -> str:
    if _fence_point() is None:
        return _text(
            'Experimental fence placement. Selected side has no blocked edge; turn with R. ',
            'Eksperymentalne umieszczanie na płocie. Po wybranej stronie nie ma krawędzi; obróć R. ',
        )
    return _text(
        'Experimental fence placement. Enter asks the game to validate the selected edge as a fence. ',
        'Eksperymentalne umieszczanie na płocie. Enter prosi grę o sprawdzenie wybranej krawędzi jako płotu. ',
    )


# What each requirement says on the cursor tile; anything else is a fence.
_FIT: dict[str, Callable[[Any, int], str]] = {
    'terrain': _terrain_fit,
    'water': _water_fit,
    'ceiling': _ceiling_fit,
}


def fit_parts(need: str) -> list[str]:
    import simsight_grid as grid

    position = _cursor_position()
    if position is None:
        return [
            _text(
                'Experimental placement: this tile cannot be checked. ',
                'Umieszczanie eksperymentalne: nie można sprawdzić pola. ',
            )
        ]
    return [_FIT.get(need, _fence_fit)(position, grid._level)]


def _placement(
    item: dict[str, Any], need: str, frame: Frame, position: Any
) -> tuple[Any, float] | None:
    """(point, angle) to validate for this requirement, or None (said why)."""
    import simsight_grid as grid
    import simsight_place as place_mod

    angle, point = frame.yaw(int(grid._facing) % 4), position
    if need == 'terrain':
        if _natural_ground(position, grid._level) is not True:
            place_mod._say_text(
                'This object needs natural ground with no floor tiles.',
                'Ten obiekt wymaga naturalnego terenu bez podłogi.',
            )
            return None
    elif need == 'water':
        point, depth, kind = _water_point(position, grid._level)
        if point is None:
            place_mod._say_text(
                'This object needs water, and no water surface was detected here.',
                'Ten obiekt wymaga wody, a tutaj jej nie wykryto.',
            )
            return None
        _log('water product {} kind {} depth {:.2f}'.format(item['id'], kind, depth))
    elif need == 'ceiling':
        point = _ceiling_point(position, grid._level)
        if point is None:
            place_mod._say_text(
                'No ceiling anchor could be sampled here.',
                'Nie znaleziono punktu sufitu w tym miejscu.',
            )
            return None
    else:
        selected = _fence_point()
        if selected is None:
            place_mod._say_text(
                'No fence-like edge is selected. Turn with R or move beside a fence.',
                'Nie wybrano krawędzi płotu. Obróć R albo przejdź obok płotu.',
            )
            return None
        _frame, _side, point, angle = selected
    return point, angle


def place(item: dict[str, Any], need: str) -> bool:
    """Enter with an experimental-surface object chosen."""
    import simsight_grid as grid
    import simsight_place as place_mod
    import simsight_tiles as tiles

    frame, position = tiles.frame(), _cursor_position()
    if frame is None or position is None:
        place_mod._say_text(
            'Experimental placement could not check this tile. Nothing was bought.',
            'Nie można sprawdzić tego pola. Nic nie kupiono.',
        )
        return False
    chosen = _placement(item, need, frame, position)
    if chosen is None:
        return False
    point, angle = chosen
    _log('product {} requirement {} sent to exact validation'.format(item['id'], need))
    return place_mod.place(point, grid._level, grid._surface(), angle=angle, exact=True)

"""Walls on the Build cursor: telling a wall from a fence, and hanging wall
objects. Contributed by DaRainbowGirlXX (2026-10-16), merged into SimSight
2026-11-12. The Build cursor (simsight_grid) calls these directly.

The wall height comes from the target layer (simsight_layers): Ctrl+Alt+U
and Ctrl+Alt+D step through ground, six wall heights and ceiling.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log
import simsight_settings as settings

if TYPE_CHECKING:
    from simsight_tiles import Frame

    # zone, floor, tile, side, height label
    SpotKey = tuple[Any, int, tuple[int, ...], int, str]

HEIGHTS = (
    ('very low', 0.5),
    ('low', 1.0),
    ('middle', 1.5),
    ('high', 2.0),
    ('very high', 2.5),
    ('near the top', 3.0),
)


class _State:
    def __init__(self) -> None:
        # What the game said at each spot this session (Leo, 2026-11-12: say
        # whether a wall object will hang before Enter): (hung, its message).
        self.history: dict[SpotKey, tuple[bool, str]] = {}
        self.hinted = False  # the height keys were explained once
        self.attempt = 0  # diagnostics: screenshot slot 1 to 3


_state = _State()


def _log(message: str) -> None:
    simsight_log.line('wall: ' + message)


def blocked_side(state: str | None) -> bool:
    """A side the ray test found impassable with no object in the way.
    tiles.walls says 'wall'; tyjokr's October 1 build said 'blocked'."""
    return state in ('blocked', 'wall')


def selected_height() -> tuple[str, float]:
    import simsight_layers as layers

    return layers.wall_height()


def wall_state() -> tuple[Frame, dict[int, str], dict[int, Any]] | None:
    import simsight_grid as grid
    import simsight_tiles as tiles

    frame = tiles.frame()
    if frame is None:
        return None
    i, j = grid.location.tile
    objects = tiles.snapshot(grid.location.level)
    sides = tiles.walls(frame, i, j, grid.location.level)
    doors = tiles.doors(frame, i, j, grid.location.level, objects=objects)
    return frame, sides, doors


def selected_wall() -> tuple[Frame, int] | None:
    """(frame, side) when the cursor faces a real wall, else None."""
    import simsight_grid as grid

    state = wall_state()
    if state is None:
        return None
    frame, sides, doors = state
    side = int(grid.location.facing) % 4
    if side in doors or not blocked_side(sides.get(side)) or not separates_rooms(frame, side):
        return None
    return frame, side


def separates_rooms(frame: Frame, side: int) -> bool:
    """Room topology tells a wall from a fence: a wall has a different room
    on each side."""
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    di, dj = tiles.STEPS[side]
    # tiles.room answers None for anything it cannot tell.
    here = tiles.room(frame, i, j, grid.location.level)
    there = tiles.room(frame, i + di, j + dj, grid.location.level)
    return here is not None and there is not None and here != there


def _wall_anchor(frame: Frame, side: int) -> tuple[Any, float]:
    import sims4.math
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    di, dj = tiles.STEPS[side]
    center = tiles.position(frame, i, j, grid.location.level)
    neighbor = frame.world(i + di, j + dj)
    _label, height = selected_height()
    point = sims4.math.Vector3(
        (float(center.x) + float(neighbor[0])) / 2.0,
        float(center.y) + height,
        (float(center.z) + float(neighbor[1])) / 2.0,
    )
    return point, height


def _anchor_or_none(frame: Frame, side: int) -> tuple[Any, float] | None:
    """_wall_anchor, or None (logged) when the cursor tile has no height."""
    try:
        return _wall_anchor(frame, side)
    except ValueError as ex:  # no height at the cursor tile
        _log(f'could not calculate wall anchor: {ex!r}')
    except Exception as ex:
        simsight_log.error('wall: wall anchor')
        _log(f'could not calculate wall anchor: {ex!r}')
    return None


def _side_state(frame: Frame, index: int, sides: dict[int, str], doors: dict[int, Any]) -> str:
    """What number 3 says about one side of the cursor tile."""
    if index in doors:
        return lang.text('door', 'drzwi')
    if blocked_side(sides.get(index)) and separates_rooms(frame, index):
        return lang.text('wall', 'ściana')
    maybe_fence = lang.text('wall, or possibly a fence', 'ściana lub prawdopodobnie płot')
    return {
        'blocked': maybe_fence,
        'wall': maybe_fence,
        'object': lang.text('an object is in the way', 'przeszkadza obiekt'),
        'edge': lang.text('lot edge', 'granica parceli'),
        'open': lang.text('open', 'wolne'),
    }.get(sides.get(index) or '', lang.text('unknown', 'nieznane'))


def wall_parts(frame: Frame, all_sides: bool = False, objects: Any = None) -> list[str]:
    """Walls, possible fences, doors and the lot edge around the cursor."""
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    sides = tiles.walls(frame, i, j, grid.location.level)
    doors = tiles.doors(frame, i, j, grid.location.level, objects=objects)
    if all_sides:
        return [
            f'{tiles.direction_name(index).capitalize()}: {_side_state(frame, index, sides, doors)}. '
            for index in range(4)
        ]
    walls: list[str] = []
    maybe: list[str] = []
    for index in range(4):
        if blocked_side(sides.get(index)) and index not in doors:
            (walls if separates_rooms(frame, index) else maybe).append(tiles.direction_name(index))
    door_dirs = [tiles.direction_name(index) for index in sorted(doors)]
    joiner = lang.text(' and ', ' i ')
    parts = []
    if walls:
        parts.append(lang.text('Wall {}. ', 'Ściana: {}. ').format(joiner.join(walls)))
    if maybe:
        parts.append(
            lang.text('Wall or fence {}. ', 'Ściana lub płot: {}. ').format(joiner.join(maybe))
        )
    if door_dirs:
        parts.append(lang.text('Door {}. ', 'Drzwi: {}. ').format(joiner.join(door_dirs)))
    return parts


def _spot_key(side: int) -> SpotKey:
    import simsight_grid as grid
    import simsight_place as place_mod

    return (
        place_mod.lot_zone_id(),
        grid.location.level,
        tuple(grid.location.tile),
        side,
        selected_height()[0],
    )


def remember(key: SpotKey, hung: Any, message: str = '') -> None:
    _state.history[key] = (bool(hung), str(message or ''))
    _log('result at {}: {} {}'.format(key, 'hung' if hung else 'refused', message))


def _uv(frame: Frame, x: Any, z: Any) -> tuple[float, float]:
    """Continuous lot coordinates (east, north) of a world point."""
    dx, dz = float(x) - frame.origin[0], float(z) - frame.origin[1]
    return (dx * frame.east[0] + dz * frame.east[1], dx * frame.north[0] + dz * frame.north[1])


def _on_stretch(frame: Frame, obj: Any, side: int, i: int, j: int) -> bool | None:
    """Whether the object stands on the chosen side's stretch of wall of
    tile (i, j); None when it has no position."""
    try:
        u, v = _uv(frame, obj.position.x, obj.position.z)
    except (AttributeError, TypeError, ValueError):  # no position: not on the wall
        return None
    if side in (0, 2):
        across, along = v - (j + (1 if side == 0 else 0)), u - (i + 0.5)
    else:
        across, along = u - (i + (1 if side == 1 else 0)), v - (j + 0.5)
    return not (abs(across) > 0.4 or abs(along) > 0.9)


def _wall_kind(obj: Any, floor_y: float, height: float) -> str | None:
    """'window', 'door' or 'object' (a wall object at about the chosen
    height) for something on the wall; None for anything else."""
    import simsight_layers as layers
    import simsight_place as place_mod
    import simsight_scanner as scanner

    tags = ' '.join(scanner.tag_names(obj)).lower()
    if 'window' in tags:
        return 'window'
    if 'door' in tags or 'arch' in tags:
        return 'door'
    if place_mod.requirement(layers.definition_id(obj)) != 'wall' or _off_height(
        obj, floor_y, height
    ):
        return None
    return 'object'


def _off_height(obj: Any, floor_y: float, height: float) -> bool:
    """More than 0.75 m from the chosen height (unknown height: not off)."""
    try:
        return abs((float(obj.position.y) - floor_y) - height) > 0.75
    except (AttributeError, TypeError, ValueError):  # no height to compare: counted as on this wall
        return False


def _on_this_wall(frame: Frame, side: int) -> list[tuple[Any, str]]:
    """Windows, doors and wall objects already on this tile's stretch of the
    chosen wall at about the chosen height: [(name, kind)]."""
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    di, dj = tiles.STEPS[side]
    floor_y = float(tiles.position(frame, i, j, grid.location.level).y)
    _label, height = selected_height()
    found: list[tuple[Any, str]] = []
    seen: set[Any] = set()
    for ti, tj in ((i, j), (i + di, j + dj)):
        for obj in _objects_or_none(frame, ti, tj, grid.location.level):
            if getattr(obj, 'is_sim', False) or obj.id in seen:
                continue
            seen.add(obj.id)
            if not _on_stretch(frame, obj, side, i, j):
                continue
            kind = _wall_kind(obj, floor_y, height)
            if kind is not None:
                found.append((grid.object_name_part(obj), kind))
    return found


def _objects_or_none(frame: Frame, i: int, j: int, level: int) -> list[Any]:
    import simsight_tiles as tiles

    try:
        return tiles.objects_on(frame, i, j, level)
    except ValueError:  # no height at that tile: nothing listed there
        return []
    except Exception:
        simsight_log.error('wall: objects on a tile')
        return []


def _wall_continues(frame: Frame, side: int) -> tuple[list[str], list[str]]:
    """Which ends of this stretch have no wall beside them, and doors there:
    ([direction names without wall], [direction names with a door])."""
    import simsight_grid as grid
    import simsight_tiles as tiles

    i, j = grid.location.tile
    open_ends: list[str] = []
    doors_beside: list[str] = []
    for along in (1, 3) if side in (0, 2) else (0, 2):
        ai, aj = tiles.STEPS[along]
        ni, nj = i + ai, j + aj
        name = tiles.direction_name(along)
        if not frame.inside(ni, nj):
            open_ends.append(name)
            continue
        end = _stretch_end(frame, ni, nj, side)
        if end == 'door':
            doors_beside.append(name)
        elif end == 'open':
            open_ends.append(name)
    return open_ends, doors_beside


def _stretch_end(frame: Frame, i: int, j: int, side: int) -> str | None:
    """'door', 'open' or 'wall' for the same side of a neighbouring tile;
    None when it cannot be checked."""
    import simsight_grid as grid
    import simsight_tiles as tiles

    try:
        if side in tiles.doors(frame, i, j, grid.location.level):
            return 'door'
        if not blocked_side(tiles.walls(frame, i, j, grid.location.level).get(side)):
            return 'open'
    except Exception as ex:
        simsight_log.error('wall: continuation check')
        _log(f'wall continuation check failed: {ex!r}')
        return None
    return 'wall'


def _plain(name: Any) -> str:
    return ''.join(str(p) for p in name) if isinstance(name, list) else str(name)


def hang_report() -> tuple[str, list[str]]:
    """(verdict, [reasons]): 'no wall', 'no', 'maybe' or 'yes'. Built from
    what the mod can measure and what the game said here before; the game
    still has the final word when Enter clicks."""
    selected = selected_wall()
    if selected is None:
        return 'no wall', []
    frame, side = selected
    if column_here(frame):
        return 'no', [lang.text('a column is in the way', 'przeszkadza kolumna')]
    past = _state.history.get(_spot_key(side))
    reasons: list[str] = []
    verdict = 'yes'
    for name, kind in _on_this_wall(frame, side):
        if kind == 'object':
            reasons.append(
                lang.text('{} already hangs here', '{} już tu wisi').format(_plain(name))
            )
        else:
            reasons.append(
                lang.text('{} is in this stretch of wall', '{} jest na tym odcinku ściany').format(
                    _plain(name)
                )
            )
        verdict = 'no'
    open_ends, doors_beside = _wall_continues(frame, side)
    joiner = lang.text(' and ', ' i ')
    if doors_beside:
        reasons.append(
            lang.text('a door is just {}', 'tuż obok, {}, są drzwi').format(
                joiner.join(doors_beside)
            )
        )
        verdict = 'maybe' if verdict == 'yes' else verdict
    if open_ends:
        reasons.append(
            lang.text('the wall ends to the {}', 'ściana kończy się: {}').format(
                joiner.join(open_ends)
            )
        )
        verdict = 'maybe' if verdict == 'yes' else verdict
    if past is not None:
        verdict = _with_history(past, reasons, verdict)
    return verdict, reasons


def _with_history(past: tuple[bool, str], reasons: list[str], verdict: str) -> str:
    """Put what the game said here before first; return the new verdict."""
    hung, message = past
    if hung:
        reasons.insert(0, lang.text('it hung here before', 'już tu wisiało'))
        return 'yes' if verdict != 'no' else verdict
    reasons.insert(
        0,
        lang.text('the game refused this spot before', 'gra wcześniej odrzuciła to miejsce')
        + (': ' + message.rstrip('.') if message else ''),
    )
    return 'no'


def fit_parts() -> list[str]:
    """Whether the chosen wall object will hang where the cursor faces."""
    label, height = selected_height()
    verdict, reasons = hang_report()
    if verdict == 'no wall':
        return [
            lang.text(
                'Selected side has no wall. Turn with R or move to a tile beside a wall. Height {}, {:.1f} metres. ',
                'Po wybranej stronie nie ma ściany. Obracaj R albo przejdź obok ściany. Wysokość {}, {:.1f} metra. ',
            ).format(label, height)
        ]
    opening = {
        'yes': lang.text('Should hang here', 'Powinno tu zawisnąć'),
        'maybe': lang.text('Might not fit', 'Może się nie zmieścić'),
        'no': lang.text('Will not hang here', 'Tu nie zawiśnie'),
    }[verdict]
    parts = [opening + (': ' + '; '.join(reasons) if reasons else '') + '. ']
    parts.append(
        lang.text('Height {}, {:.1f} metres. ', 'Wysokość {}, {:.1f} metra. ').format(label, height)
    )
    if not _state.hinted:
        _state.hinted = True
        parts.append(
            lang.text(
                'Control Alt U and D change the height. Enter hangs it. ',
                'Control Alt U i D zmieniają wysokość. Enter wiesza. ',
            )
        )
    return parts


def place(item: dict[str, Any]) -> bool:
    """Enter with a wall object chosen. True when the game's tool was asked."""
    import simsight_grid as grid
    import simsight_place as place_mod
    import simsight_tiles as tiles

    selected = selected_wall()
    if selected is None:
        direction = tiles.direction_name(int(grid.location.facing) % 4)
        place_mod.say_text(
            f'There is no wall {direction}. Turn with R or move beside a wall.',
            f'Brak ściany w kierunku {direction}. Obracaj R albo przejdź obok ściany.',
        )
        return False
    frame, side = selected
    anchor = _anchor_or_none(frame, side)
    if anchor is None:
        place_mod.say_text(
            'The wall anchor could not be calculated, so nothing was bought.',
            'Nie udało się obliczyć punktu na ścianie, więc nic nie kupiono.',
        )
        return False
    point, height = anchor
    _log('placing product {} on side {} at height {:.1f}'.format(item['id'], side, height))
    return hang_with_game_tool(frame, side, item, point, height)


def column_here(frame: Frame) -> bool:
    """A column (the game's Build_Column tag) on the cursor tile."""
    import simsight_grid as grid
    import simsight_scanner as scanner
    import simsight_tiles as tiles

    try:
        for obj in tiles.objects_on(
            frame, grid.location.tile[0], grid.location.tile[1], grid.location.level
        ):
            if any('column' in name.lower() for name in scanner.tag_names(obj)):
                return True
    except ValueError as ex:  # no height at the cursor tile
        _log(f'column check failed: {ex!r}')
    except Exception as ex:
        simsight_log.error('wall: column check')
        _log(f'column check failed: {ex!r}')
    return False


class _Hang:
    """One wall object hung by EA's own Build tool (see hang_with_game_tool)."""

    def __init__(self, name: str, key: SpotKey, before: Any) -> None:
        import simsight_plus_build as plus

        self.plus = plus
        self.name = name
        self.key = key
        self.before = before
        self.started = time.monotonic()
        self.speak_result = plus.cost_check(
            f'Hung {name}',
            'It was not hung. Something may be in the way on that wall, or the wall is too short.',
        )

    def restore(self) -> None:
        self.plus.send_ui('A11y|plus|wallsrestore')

    def act(self, screen: list[Any]) -> None:
        """The job's click, on its worker; the player's view comes back after.
        A failure goes on to run_job, which logs it and says the step failed."""
        try:
            self._click(screen)
        finally:
            self.plus.core.post(self.restore)

    def _click(self, screen: list[Any]) -> None:
        import simsight_place as place_mod

        plus = self.plus
        plus.reselect()
        # reselect picks by list position; a list browsed since then could
        # hold another item there. Click only when the game holds this one.
        held = plus.tool_name().strip().lower()
        if held != str(self.name).strip().lower():
            _log(f'game tool: holding {held!r}, not {self.name!r}; no click')
            plus.core.post(
                place_mod.say_text,
                f'The game is holding something else. Choose {self.name} again with Control Alt O.',
                f'Gra trzyma coś innego. Wybierz {self.name} ponownie przez Control Alt O.',
            )
            return
        _state.attempt = _state.attempt % 3 + 1
        shots = bool(settings.get('logging', False))
        base = spot = None
        if shots:
            # Diagnostics (2026-11-12): the game keeps answering "Must be
            # placed on a wall". Hover first, so the shot shows the game's
            # preview where the click will land, then the result.
            base = os.path.join(
                os.path.dirname(simsight_log.PATH), f'SimSight_wallshot_{_state.attempt}'
            )
            spot = plus.to_screen(*screen[0])
            plus.move_to(*spot)
            time.sleep(0.6)
            plus.screenshot(base + '_before.bmp', mark=spot)
        plus.click(screen[0])
        time.sleep(1.0)
        if base is not None:
            plus.screenshot(base + '_after.bmp', mark=spot)

    def verify(self) -> None:
        """Remember the game's answer for this spot, so the cursor can say it
        next time ("the game refused this spot before")."""
        plus = self.plus
        after = plus.funds()
        if self.before is not None and after is not None and after != self.before:
            remember(self.key, True)
        else:
            error = plus.last_error['text'] if plus.last_error['at'] > self.started else ''
            remember(self.key, False, error)
        self.speak_result()


def hang_with_game_tool(
    frame: Frame, side: int, item: dict[str, Any], point: Any, height: float
) -> bool:
    """Let EA's own Build tool hang it (Leo's choice, 2026-10-16): exact
    placement at a computed point failed MustBeAgainstWall live for every
    distance, height and facing tried. As SimSight Plus places doors and
    windows: the catalog item goes back into the game's hand, the camera
    faces the wall from inside the cursor's room, and a real click on the
    wall at the chosen height places it; the game snaps it, checks it and
    charges for it. Success is judged by the money spent."""
    import simsight_grid as grid
    import simsight_place as place_mod
    import simsight_plus_build as plus
    import simsight_steps as steps
    import simsight_tiles as tiles

    if plus.no_mouse():  # the click below needs the real mouse
        return False
    if plus.catalog_state.get('chosen') is None:
        place_mod.say_text(
            "Choose it with Control Alt O first, so the game's tool can hold it. Nothing was bought.",
            'Najpierw wybierz to przez Control Alt O, aby narzędzie gry mogło to trzymać. Nic nie kupiono.',
        )
        return False
    # Live 2026-10-17: every refused try was on or beside a column tile; the
    # click then lands on the column or a wall end. Say so instead of trying.
    if column_here(frame):
        place_mod.say_text(
            'A column stands here, in front of the wall or at its end. Move along the wall to a plain stretch, then press Enter.',
            'Stoi tu kolumna, przed ścianą lub na jej końcu. Przejdź wzdłuż ściany na wolny odcinek i naciśnij Enter.',
        )
        return False
    center = tiles.position(
        frame, grid.location.tile[0], grid.location.tile[1], grid.location.level
    )
    dx, dz = float(center.x) - float(point.x), float(center.z) - float(point.z)
    length = (dx * dx + dz * dz) ** 0.5 or 1.0
    dx, dz = dx / length, dz / length
    # A hair inside the room, so the click lands on this side of the wall.
    target = (float(point.x) + dx * 0.05, float(point.z) + dz * 0.05)
    name = item.get('name') or 'it'
    direction = tiles.direction_name(side)
    place_mod.say_text(
        f'Hanging {name} on the {direction} wall. Please wait.',
        f'Wieszanie {name} na ścianie {direction}. Proszę czekać.',
    )
    _log(
        'game tool: product {} side {} height {:.1f} at ({:.2f}, {:.2f})'.format(
            item['id'], side, height, target[0], target[1]
        )
    )
    # Live 2026-10-16: with walls down or cut away the click passed over the
    # drawn wall ("Must be placed on a wall"). Walls up first; the player's
    # view comes back after the click, or after 15 seconds if none happens.
    plus.send_ui('A11y|plus|wallsup')
    steps.after(15.0, plus.core.post, _restore_walls)
    job = _Hang(name, _spot_key(side), plus.funds())
    # Live 2026-11-12 (screenshots): the click is the screen centre, where the
    # camera looks. Looking at the floor by the wall missed it from outside
    # (the porch hid the wall base), so look at the wall at the chosen height.
    plus.run_job(
        [target],
        grid.location.level,
        job.act,
        'wall decoration',
        height=height,
        view=(dx, dz),
        verify=job.verify,
        focus_height=height,
    )
    return True


def _restore_walls() -> None:
    import simsight_plus_build as plus

    plus.send_ui('A11y|plus|wallsrestore')

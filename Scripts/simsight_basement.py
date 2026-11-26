"""Experimental native basement drawing, isolated from ordinary room workers."""

from __future__ import annotations

import math
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Sequence

    from simsight_steps import Job

    _Box = tuple[int, int, int, int]  # west, north, east, south in lot metres
    _Rooms = set[tuple[tuple[int, ...], int]]  # (box, level) of every room
    _Result = str | _Box  # the drawn box, or why it was not drawn
    _Wanted = tuple[float, float, float, float]  # a box typed by the player


_LOG = 'basement adapter: '  # log line prefix


class _Selection(NamedTuple):
    """A native tool selection waiting for the UI's reply."""

    row: int  # catalog row of the tool
    name: str
    serial: int
    done: threading.Event


class _State:
    def __init__(self) -> None:
        # Set by install(): simsight_plus_build, simsight_plus_core and
        # simsight_build_helpers (EA-style modules, so Any).
        self.build: Any = None
        self.core: Any = None
        self.helpers: Any = None
        self.selection: _Selection | None = None


_state = _State()
_failure = threading.local()  # .reason: why this thread's last basement failed
SETTING = 'experimental_basement'
TOOL = 'Basement Tool'
_REASONS = {
    'disabled': 'experimental basement creation is turned off',
    'occupied': 'the requested footprint overlaps an existing basement room',
    'nofloor': 'the game could not reach the ground floor for the basement tool',
    'nocatalog': 'the required native tool selection was not confirmed',
    'noscreen': 'the corners could not be framed clear of the interface',
}
_UNVERIFIED_REASON = 'the requested room on floor minus one could not be verified'
# Results a drag attempt reports itself; only a finished drag is held back
# when the Room Tool cannot be restored afterwards.
_STOPPED_EARLY = ('nocatalog', 'noscreen')


def _speak_later(text: str) -> None:
    _state.core.post(_state.core.speak, text)


def _enabled() -> bool:
    import simsight_settings as settings

    return bool(settings.get(SETTING, True))


def _context() -> dict[str, Any]:
    import services

    state = _state.helpers.floor_state()
    state['household'] = int(services.active_household_id())
    return state


def _same_context(state: dict[str, Any]) -> bool:
    import services

    return (
        _enabled()
        and _state.helpers.same_world(state)
        and int(services.active_household_id()) == state['household']
    )


def _rooms() -> _Rooms:
    result = _state.build.room_ids()
    if result is None:
        raise RuntimeError('The game did not supply its room geometry')
    return {(tuple(box), int(level)) for box, level in result}


def _overlap(a: Sequence[float], b: Sequence[float]) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _bounds(want: Sequence[Any], frame: Any) -> _Box:
    """want as whole metres, at least one metre inside the lot; ValueError otherwise."""
    if len(want) != 4 or any(
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or int(v) != v
        for v in want
    ):
        raise ValueError('Basement corners must be four whole-metre coordinates')
    box = (int(want[0]), int(want[1]), int(want[2]), int(want[3]))
    if not (1 <= box[0] < box[2] <= frame.width - 1 and 1 <= box[1] < box[3] <= frame.depth - 1):
        raise ValueError('The basement must fit at least one metre inside the lot')
    return box


def _is_selection_reply(job: _Selection, encoded: str) -> bool:
    from urllib.parse import unquote

    parts = unquote(str(encoded or '')).split('|')
    return (
        len(parts) == 3
        and parts[0] == 'selected'
        and parts[1] == str(job.row)
        and unquote(parts[2]) == job.name
        and _state.build.catalog_state['serial'] == job.serial
    )


def reply(encoded: str = '') -> Any:
    """The UI's catalog reply: confirms a pending tool selection."""
    # Leave all existing catalog, placement and scanner reply behavior intact.
    result = _original_reply(encoded)
    job = _state.selection
    if job is not None:
        try:
            if _is_selection_reply(job, encoded):
                job.done.set()
        except Exception:
            simsight_log.error('basement: tool reply')
    return result


def _tool(name: str, state: dict[str, Any]) -> bool:
    """Select the native tool called name; True once the UI confirmed it."""
    build = _state.build
    if not build.on_main(_same_context, state):
        return False
    # The native bridge replaces its catalog array. An old index must never
    # reselect an unrelated tool if this query or selection is interrupted.
    build.on_main(build.catalog_state.pop, 'chosen', None)
    items = build.fetch_catalog(16, '', 'tool') or []
    names = [item[0] for item in items]
    simsight_log.json_line(_LOG, 'catalog', wanted=name, tools=names)
    if name not in names or not build.on_main(_same_context, state):
        return False
    index = names.index(name)
    job = _Selection(index, name, build.catalog_state['serial'], threading.Event())
    _state.selection = job
    silent = build.catalog_state.get('silent_select', False)
    try:
        build.catalog_state['silent_select'] = True
        build.select_item(index, name)
        acknowledged = job.done.wait(2.0)
        simsight_log.json_line(_LOG, 'tool-selection', name=name, acknowledged=acknowledged)
        return acknowledged and bool(build.on_main(_same_context, state))
    finally:
        _state.selection = None
        build.catalog_state['silent_select'] = silent


def _check_result(before: _Rooms, after: _Rooms, wanted: _Box) -> bool:
    target = (wanted, -1)
    # A bounding box alone cannot count a ground-floor room as a basement.
    return target in after - before and before.issubset(after) and after - before == {target}


def _rectangle(wanted: _Box) -> bool:
    """Exactly one room on floor -1 with that box, covering every tile of it."""
    import simsight_plus_rooms as rooms

    build = _state.build
    frame = build.frame()
    matches = [r for r in rooms.scan_rooms() if int(r.level) == -1 and build.room_box(r) == wanted]
    if len(matches) != 1:
        return False
    actual = {
        (int(math.floor(frame.local(x, z)[0])), int(math.floor(frame.local(x, z)[1])))
        for x, z in matches[0].tiles
    }
    expected = {(x, z) for x in range(wanted[0], wanted[2]) for z in range(wanted[1], wanted[3])}
    return actual == expected


def _prepare(want: Sequence[Any]) -> tuple[dict[str, Any], Any, _Box, _Rooms]:
    """(build context, lot frame, wanted box, rooms before); raises when unavailable."""
    build = _state.build
    state = build.on_main(_context)
    if not state:
        raise RuntimeError('The simulation thread did not answer')
    frame = build.on_main(build.frame)
    if frame is None:
        raise RuntimeError('The lot geometry is unavailable')
    wanted = _bounds(want, frame)
    before = build.on_main(_rooms)
    if before is None:
        raise RuntimeError('Room inspection timed out')
    return state, frame, wanted, before


def _drag(state: dict[str, Any], wanted: _Box, screen: Sequence[Any]) -> None:
    """The drag itself, once the camera shows both corners."""
    build = _state.build
    if not build.on_main(_same_context, state):
        raise RuntimeError('Build context changed before the basement drag')
    if not _tool(TOOL, state):
        raise RuntimeError('The Basement Tool could not be confirmed after camera preparation')
    info = _state.helpers.native_floor(state)
    if info['floor'] != 0:
        raise RuntimeError('The game is no longer showing the ground floor')
    simsight_log.json_line(
        _LOG, 'drag', wanted=wanted, input_floor=0, expected_floor=-1, native_floor=info
    )
    build.drag(screen[0], screen[1])


def _rooms_after(state: dict[str, Any], before: _Rooms) -> _Rooms:
    """The rooms once they changed, or as they are after five seconds."""
    build = _state.build
    after = before
    for _ in range(20):
        if not build.on_main(_same_context, state):
            raise RuntimeError('Build context changed while checking the basement')
        after = build.on_main(_rooms)
        if after is None:
            raise RuntimeError('Basement verification timed out')
        if after != before:
            break
        time.sleep(0.25)
    return after


def _drag_and_verify(
    state: dict[str, Any], frame: Any, wanted: _Box, before: _Rooms, overview: bool
) -> _Result:
    build = _state.build
    if not _tool(TOOL, state):
        _speak_later(
            'The native Basement Tool was not confirmed. Nothing was drawn. Its catalog entries were logged.'
        )
        return 'nocatalog'
    points = [frame.world(wanted[0], wanted[1]), frame.world(wanted[2], wanted[3])]
    started = time.monotonic()
    funds_before = build.on_main(build.funds)
    performed = build.point_and_act(
        points,
        0,
        lambda screen: _drag(state, wanted, screen),
        f'experimental basement {wanted}',
        overview=overview,
    )
    if not performed:
        return 'noscreen'
    simsight_log.json_line(
        _LOG,
        'after-drag',
        native_floor=_state.helpers.native_floor(state),
        funds_before=funds_before,
        funds_after=build.on_main(build.funds),
    )
    after = _rooms_after(state, before)
    okay = _check_result(before, after, wanted) and bool(build.on_main(_rectangle, wanted))
    error = build.last_error
    reason = error.get('text', '') if error.get('at', 0) >= started else ''
    simsight_log.json_line(
        _LOG,
        'verification',
        wanted=wanted,
        expected_floor=-1,
        new=sorted(after - before),
        removed=sorted(before - after),
        success=okay,
        game_reason=reason,
    )
    if okay:
        return wanted
    _speak_later(
        'Basement construction stopped: the game did not confirm the requested room on floor minus one.'
        + (' Game message: ' + reason if reason else '')
        + ' Inspect the result before using Undo or trying again.'
    )
    return 'unverified'


def _restore_room_tool(state: dict[str, Any]) -> bool:
    restored = _tool('Room Tool', state) if _state.build.on_main(_same_context, state) else False
    simsight_log.json_line(_LOG, 'restore-room-tool', success=restored)
    return restored


def _draw(want: Sequence[Any], overview: bool) -> _Result:
    state, frame, wanted, before = _prepare(want)
    if any(level == -1 and _overlap(box, wanted) for box, level in before):
        simsight_log.json_line(_LOG, 'occupied', wanted=wanted)
        return 'occupied'
    if not _state.build.set_floor(0):
        return 'nofloor'
    try:
        result = _drag_and_verify(state, frame, wanted, before, overview)
    finally:
        # Never leave Basement Tool selected for the next above-ground room.
        restored = _restore_room_tool(state)
    if result in _STOPPED_EARLY or restored:
        return result
    _speak_later('The basement step stopped because the ordinary Room Tool could not be restored.')
    return 'nocatalog'


def failure_reason() -> str:
    return getattr(_failure, 'reason', '')


def _stopped(want: Sequence[Any], exc: Exception) -> _Result:
    simsight_log.json_line(_LOG, 'stopped', wanted=want, error=str(exc))
    _speak_later('Basement construction stopped. ' + str(exc))
    return 'unverified'


def room_worker(want: Sequence[Any], overview: bool = False, tries: int = 4, level: int = 0) -> Any:
    """Worker thread: draw a room; basements (level -1) go through the Basement Tool."""
    _failure.reason = ''
    if level != -1:
        return _original_room(want, overview=overview, tries=tries, level=level)
    result: _Result
    if not _enabled():
        _speak_later('Experimental basement creation is off in accessibility settings.')
        result = 'disabled'
    else:
        try:
            result = _draw(want, overview)
        except (RuntimeError, ValueError) as exc:  # the step's own refusals: spoken as they are
            result = _stopped(want, exc)
        except Exception as exc:
            simsight_log.error('basement: draw')
            result = _stopped(want, exc)
    if result != tuple(want):
        _failure.reason = _REASONS.get(str(result), _UNVERIFIED_REASON)
    return result


def _run_busy(name: str, job: Job[None]) -> None:
    """Run job (a thread on Windows, the pump on the Mac), holding the build
    engine's busy flag."""
    import simsight_steps as steps

    busy = _state.build.busy
    busy[0] = True
    try:
        steps.start(name, _holding_busy(name, job, busy))
    except Exception:
        busy[0] = False
        raise


def _holding_busy(name: str, job: Job[None], busy: list[bool]) -> Job[None]:
    try:
        yield from job
    except Exception:
        simsight_log.error(f'basement: {name}')
        raise
    finally:
        busy[0] = False


def _call(work: Callable[[], None]) -> Job[None]:
    """A job of one blocking call: Windows worker threads only."""
    yield from ()
    work()


def _build_room(wanted: _Wanted) -> None:
    result = room_worker(wanted, overview=True, level=-1)
    if result == wanted:
        shown = _state.build.set_floor(-1)
        _speak_later(
            'The basement room was verified on floor minus one. '
            + (
                'The game now shows the basement.'
                if shown
                else 'The room exists, but the game could not switch to its floor.'
            )
        )
    else:
        _speak_later(
            f'The basement room was not completed: {result}. No further rooms were attempted.'
        )


def _start_room(wanted: _Wanted) -> None:
    core = _state.core
    if _state.build.no_mouse():
        return
    if _state.build.busy[0]:
        core.speak('Still working on the last build step.')
        return
    if not _enabled():
        core.speak('Turn on experimental basement creation in accessibility settings first.')
        return
    core.speak('Building the basement room. Please do not press keys until it finishes.')
    _run_busy('a11y_basement_room', _call(lambda: _build_room(wanted)))


def _confirm_room(width: float, depth: float, coords: Sequence[float]) -> None:
    core = _state.core
    wanted: _Wanted = (coords[0], coords[1], coords[0] + width, coords[1] + depth)
    try:
        _bounds(wanted, _state.build.frame())
    except ValueError as exc:
        core.speak(str(exc))
        return
    rows = [
        (
            'Build this basement room',
            'Uses the native tool and spends household funds only if the game accepts it.',
            True,
        ),
        ('Cancel', None, False),
    ]
    core.choose(
        'Confirm experimental basement',
        '{} by {} metres, {} from west and {} from north. Floor minus one. Existing basement rooms must not overlap this footprint.'.format(
            width, depth, *coords
        ),
        rows,
        lambda yes: _start_room(wanted) if yes else None,
        tag='build',
    )


def _ask_position(width: float, depth: float) -> None:
    def position(text: str) -> None:
        coords = _state.build.whole_numbers(text)
        if len(coords) != 2:
            _state.core.speak('Type meters from west and north, for example 2 3.')
            return
        _confirm_room(width, depth, coords)

    _state.core.ask_text(
        'Basement position',
        'Type meters from the west edge, then the north edge of the lot. For example 2 3.',
        '',
        position,
    )


def _ask_room() -> None:
    def size(text: str) -> None:
        values = _state.build.whole_numbers(text)
        if len(values) != 2 or any(v < 1 or v > 30 for v in values):
            _state.core.speak('Type a width and depth between 1 and 30 metres.')
            return
        width, depth = values
        _ask_position(width, depth)

    _state.core.ask_text(
        'Basement size',
        'Type width west to east and depth north to south in metres, for example 4 5.',
        '',
        size,
    )


def _inspect_work() -> Job[None]:
    import simsight_steps as steps

    try:
        state = yield steps.OnMain(_context)
        info = yield from _state.helpers.native_floor_steps(state)
        geometry = yield steps.OnMain(_rooms)
        if geometry is None:
            raise RuntimeError('Room inspection timed out')
        basements = sorted(box for box, level in geometry if level == -1)
        simsight_log.json_line(_LOG, 'inspect', basement_rooms=basements, native_floor=info)
        _speak_later(
            f'Logged current floor bounds and {len(basements)} basement rooms. Nothing was built or selected.'
        )
    except Exception as exc:  # a job: report every failure, never raise
        simsight_log.json_line(_LOG, 'inspect-failed', error=str(exc))
        _speak_later('Basement inspection failed. ' + str(exc))


def _inspect() -> None:
    if _state.build.busy[0]:
        _state.core.speak('Still working on the last build step.')
        return
    _run_busy('a11y_basement_inspect', _inspect_work())


def open_assistant() -> None:
    build, core = _state.build, _state.core
    if not build.in_build_mode():
        core.speak('Enter Build mode first.')
        return
    rows = [
        (
            'Build a rectangular basement room',
            'Choose size and position; verifies floor minus one before reporting success.',
            _ask_room,
        ),
        (
            'Inspect basement floors and rooms',
            'Read-only native-floor and room diagnostics; builds and selects nothing.',
            _inspect,
        ),
    ]
    core.choose(
        'Basement assistant (experimental)',
        'Stacked houses with a basement use the same adapter automatically. Creation can be turned off in accessibility settings.',
        rows,
        lambda fn: fn(),
        tag='build',
    )


def install() -> None:
    """Startup: the engine and helper modules this adapter uses. Idempotent."""
    if _state.build is not None:
        return
    import simsight_build_helpers as helpers
    import simsight_plus_build as build
    import simsight_plus_core as core

    _state.build, _state.core, _state.helpers = build, core, helpers
    simsight_log.json_line(_LOG, 'installed', experimental=True, enabled=_enabled())


def _original_room(
    want: Sequence[Any], overview: bool = False, tries: int = 4, level: int = 0
) -> Any:
    import simsight_plus_build as build

    return build.room_worker_base(want, overview=overview, tries=tries, level=level)


def _original_reply(encoded: str = '') -> Any:
    import simsight_plus_build as build

    return build.flash_reply_base(encoded)

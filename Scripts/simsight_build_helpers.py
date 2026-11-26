"""Narrow integration fixes for the existing house/structure helpers."""

from __future__ import annotations

import contextlib
import importlib
import json
import math
import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Iterator

_PAGE_UP = (33, 0x49)  # virtual key, scan code: one floor up
_PAGE_DOWN = (34, 0x51)  # one floor down
_MODIFIERS = (16, 17, 18, 91, 92)  # Shift, Ctrl, Alt, both Windows keys
_KEY_DOWN = 0x8000  # GetAsyncKeyState: the key is held


class _State:
    def __init__(self) -> None:
        self.installed = False
        # Set by install(): simsight_plus_build, simsight_build_coordinates
        # (None on the Mac) and simsight_house_planner (None when missing).
        self.build: Any = None
        self.coordinates: Any = None
        self.planner: Any = None
        # The one outstanding native floor query, the last reply sequence
        # accepted, and whether a lost reply closed the channel for good.
        self.floor_job: dict[str, Any] | None = None
        self.floor_sequence = -1
        self.floor_quarantined = False


_runtime = _State()
_floor_lock = threading.Lock()
_house_context = threading.local()  # .transitions: the running plan's stairs


def _log(kind: str, **data: Any) -> None:
    simsight_log.line(
        'build helpers: ' + json.dumps(dict(kind=kind, **data), sort_keys=True, default=str)
    )


def _state() -> dict[str, Any]:
    """Simulation thread: what a floor change must find unchanged."""
    import services
    import simsight_grid as grid
    import simsight_keys as keys

    if not _runtime.build.in_build_mode() or keys.blocking_active() or keys._editing_active:
        raise RuntimeError('Close the game dialog before changing floors')
    lot = services.active_lot()
    if lot is None:
        raise RuntimeError('No active lot')
    floor = None  # Camera position is not a native floor number.
    return {
        'zone': int(services.current_zone_id()),
        'floor': floor,
        'center': (float(lot.center.x), float(lot.center.z)),
        'grid': bool(grid.build_cursor()),
        'owner': bool(keys._build_grid_owner),
    }


def _same_world(state: dict[str, Any]) -> bool:
    import services
    import simsight_keys as keys

    return (
        _runtime.build.in_build_mode()
        and int(services.current_zone_id()) == state['zone']
        and not keys.blocking_active()
        and not keys._editing_active
    )


def _grid_message(message: str) -> None:
    import simsight_grid as grid

    grid._flash(message)


def _grid_released() -> bool:
    import simsight_keys as keys

    return not keys._build_grid_owner


def _restore_grid(state: dict[str, Any]) -> bool:
    import services
    import simsight_grid as grid

    if (
        state['grid']
        and _runtime.build.in_build_mode()
        and int(services.current_zone_id()) == state['zone']
        and grid.build_cursor()
    ):
        grid._flash('A11yBuildGridOn')
        return True
    return False


def _release_grid(state: dict[str, Any]) -> None:
    """Only release native key ownership. Preserve the Python grid's tile,
    height, room focus, orientation and enabled setting."""
    build = _runtime.build
    build.on_main(_grid_message, 'A11yBuildGridOff')
    # A fresh native request is an ordered UI round trip after Off.
    # It does not move any cursor or click. Never inject a floor key
    # before the native guard and script owner have both released.
    _runtime.coordinates._measure(state['center'][0], state['center'][1], state['floor'])
    deadline = time.monotonic() + 2.0
    while not build.on_main(_grid_released):
        if time.monotonic() >= deadline:
            raise RuntimeError('Grid ownership did not release')
        time.sleep(0.02)
    _log('floor-ownership-released', zone=state['zone'])


@contextlib.contextmanager
def _floor_input(state: dict[str, Any]) -> Iterator[tuple[Any, Any]]:
    """(user32, game window) with the grid's key ownership released meanwhile."""
    coordinates = _runtime.coordinates
    u = coordinates._api()
    sent_off = False
    try:
        with coordinates.physical_context(u):
            geometry = coordinates.window(u)
            if state['grid'] or state['owner']:
                sent_off = True
                _release_grid(state)
            yield u, geometry
    finally:
        if sent_off:
            restored = _runtime.build.on_main(_restore_grid, state)
            _log('floor-ownership-restored', zone=state['zone'], restored=bool(restored))


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _receive_floor(
    zone: str, sequence: int, status: str, current: int, minimum: int, maximum: int
) -> None:
    """Simulation-thread reply from the single native ControlPanel definer."""
    job = _runtime.floor_job
    if job is None or job['done'].is_set() or str(zone) != str(job['zone']):
        return
    if not _whole(sequence) or sequence <= _runtime.floor_sequence:
        return
    _runtime.floor_sequence = sequence
    if status != 'ok':
        job['error'] = 'Native floor query: ' + str(status)
    elif not all(_whole(v) for v in (current, minimum, maximum)) or not (
        -64 <= minimum <= current <= maximum <= 64
    ):
        job['error'] = 'Invalid native floor bounds'
    else:
        job['result'] = {'floor': current, 'minimum': minimum, 'maximum': maximum}
    job['done'].set()


def _native_floor(state: dict[str, Any], timeout: float = 3.0) -> dict[str, int]:
    """One outstanding read-only query; quarantine after timeout, never reuse late replies."""
    if _runtime.floor_quarantined:
        raise RuntimeError('Native floor channel timed out; restart the game before retrying')
    if not _floor_lock.acquire(False):
        raise RuntimeError('Another native floor query is active')
    build = _runtime.build
    try:
        job: dict[str, Any] = {'zone': state['zone'], 'done': threading.Event()}
        _runtime.floor_job = job

        def send() -> bool:
            if not _same_world(state):
                raise RuntimeError('Build context changed before the native query')
            build._send_ui('A11yNativeFloorQuery')
            return True

        if build.on_main(send) is not True:
            _runtime.floor_quarantined = True
            raise RuntimeError('Native floor query could not be dispatched')
        if not job['done'].wait(timeout):
            _runtime.floor_quarantined = True
            raise RuntimeError('Native floor UI did not answer; install the matching UI package')
        if 'error' in job:
            raise RuntimeError(job['error'])
        if not build.on_main(_same_world, state):
            raise RuntimeError('Build context changed during the native query')
        return job['result']
    finally:
        _runtime.floor_job = None
        _floor_lock.release()


def _whole_level(level: Any) -> int | None:
    """level as an int when it is a whole finite number, else None."""
    try:
        if (
            isinstance(level, bool)
            or not isinstance(level, (int, float))
            or not math.isfinite(level)
            or int(level) != level
        ):
            return None
        return int(level)
    except (ValueError, OverflowError):
        return None


def _check_input(state: dict[str, Any], u: Any, geometry: Any) -> None:
    """Raise unless the same game window, Build context and released keys."""
    build = _runtime.build
    if _runtime.coordinates.window(u) != geometry or not build.on_main(_same_world, state):
        raise RuntimeError('Game focus, window or Build context changed')
    if not build.on_main(_grid_released):
        raise RuntimeError('The grid reclaimed the floor keys')
    if any(u.GetAsyncKeyState(key) & _KEY_DOWN for key in _MODIFIERS):
        raise RuntimeError('Release modifier keys before changing floors')


def _press(u: Any, key: int, scan: int) -> None:
    pressed = False
    try:
        u.keybd_event(key, scan, 1, 0)
        pressed = True
        time.sleep(0.05)
    finally:
        if pressed:
            u.keybd_event(key, scan, 3, 0)


def _floor_after_key(state: dict[str, Any], u: Any, geometry: Any, current: int) -> int:
    """Wait for one key acknowledgement; never stack unacknowledged keys."""
    actual = current
    for _ in range(30):
        time.sleep(0.1)
        if _runtime.coordinates.window(u) != geometry or not _runtime.build.on_main(
            _same_world, state
        ):
            raise RuntimeError('Build context changed while waiting for the floor')
        actual = _native_floor(state)['floor']
        if actual != current:
            break
    return actual


def _step(state: dict[str, Any], u: Any, geometry: Any, level: int) -> bool | None:
    """One floor key toward level: True there, False when the game went
    elsewhere, None to press again."""
    _check_input(state, u, geometry)
    current = _native_floor(state)['floor']
    if current == level:
        return True
    step = 1 if current < level else -1
    key, scan = _PAGE_UP if step > 0 else _PAGE_DOWN
    if (
        not _runtime.build.on_main(_same_world, state)
        or _runtime.coordinates.window(u) != geometry
        or u.GetAsyncKeyState(key) & _KEY_DOWN
    ):
        raise RuntimeError('Input context changed before the floor key')
    _press(u, key, scan)
    actual = _floor_after_key(state, u, geometry, current)
    _log('floor-step', wanted=level, before=current, actual=actual, source='BBGetLevelInfo')
    if actual != current + step:
        return False
    if actual == level:
        return True
    return None


def _change_floor(level: int) -> bool:
    state = _runtime.build.on_main(_state)
    if state is None:
        raise RuntimeError('Simulation thread did not answer')
    info = _native_floor(state)
    state['floor'] = info['floor']
    _log('floor-query', wanted=level, **info)
    if not info['minimum'] <= level <= info['maximum']:
        _log('floor-out-of-range', wanted=level, **info)
        return False
    if state['floor'] == level:
        return True
    with _floor_input(state) as (u, geometry):
        for _ in range(abs(level - state['floor'])):
            reached = _step(state, u, geometry, level)
            if reached is not None:
                return reached
        return False


def set_floor(level: Any) -> bool:
    """Use BBGetLevelInfo, never camera height, to verify native floor keys."""
    wanted = _whole_level(level)
    if wanted is None:
        return False
    try:
        return _change_floor(wanted)
    except RuntimeError as exc:  # the step's own refusals
        _log('floor-refused', wanted=wanted, error=str(exc))
        return False
    except Exception as exc:
        simsight_log.error('build helpers: set floor')
        _log('floor-refused', wanted=wanted, error=str(exc))
        return False


def _house_build(plan: Any) -> Any:
    """Scope stacked stairs to the running plan, even after a failed room."""
    planner = _runtime.planner
    if planner is None:
        return _original_house_build(plan)
    _house_context.transitions = tuple(getattr(plan, '_a11y_transitions', ()))
    planner._pending_stairs = []
    try:
        return _original_house_build(plan)
    finally:
        planner._pending_stairs = []
        _house_context.transitions = ()


def _stairs_for_plan(top: Any, climb: Any) -> Any:
    planner = _runtime.planner
    transitions = getattr(_house_context, 'transitions', ())
    if planner is None or not transitions:
        return _original_house_stairs(top, climb)
    for stair_top, stair_climb, lower, upper in transitions:
        result = planner._place_stair_at(stair_top, stair_climb, lower)
        _log('stair-transition', lower=lower, upper=upper, result=result)
        if result is not True:
            return result
    return True


def _receive(
    zone: str = '',
    sequence: int = 0,
    status: str = '',
    current: int = 0,
    minimum: int = 0,
    maximum: int = 0,
    _connection: Any = None,
) -> None:
    try:
        _receive_floor(zone, sequence, status, current, minimum, maximum)
    except Exception:
        simsight_log.error('build helpers: floor reply')


# The game converts console arguments by these annotations; the __future__
# import keeps annotations as strings, so they are given as real types here.
_receive.__annotations__ = {
    'zone': str,
    'sequence': int,
    'status': str,
    'current': int,
    'minimum': int,
    'maximum': int,
}


def _optional(name: str) -> Any:
    """The module, or None when this build of the mod has no such module."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def install() -> None:
    """Startup: the simsight.floor_native reply command. Idempotent."""
    if _runtime.installed:
        return
    _runtime.installed = True
    import sims4.commands
    import simsight_plus_build as build

    sims4.commands.Command(
        'simsight.floor_native', 'a11y.floor_native', command_type=sims4.commands.CommandType.Live
    )(_receive)
    _runtime.planner = _optional('simsight_house_planner')
    # The Mac build leaves the coordinates out: no native floor keys there.
    _runtime.build, _runtime.coordinates = build, _optional('simsight_build_coordinates')
    _log('installed')


def _original_house_build(plan: Any) -> Any:
    import simsight_detached as detached

    return detached._build_followup(plan)


def _original_house_stairs(top: Any, climb: Any) -> Any:
    import simsight_plus_house as house

    return house._place_stairs_worker_base(top, climb)

"""Run work on the simulation thread from threads we created.

The game's Python is single-threaded by contract: Sims, objects, the
interaction system and the distributor all belong to the simulation thread.
The keyboard poller and the speech timers are threads of our own, so any
key press that wants to touch the game has to cross over first. This module
is that crossing: post() queues a callable, and a repeating wall-clock alarm
that the game itself fires on the simulation thread drains the queue.

Facts this relies on (checked against the 1.127.41 decompile):

- alarms.add_alarm_real_time schedules on time_service().wall_clock_timeline,
  which advances from the server clock, so it keeps firing while the game is
  paused.
- That timeline is torn down with the zone and takes the alarm with it, so
  the alarm is re-armed on every zone load through the same
  on_loading_screen_animation_finished hook the narrator uses. start() is
  idempotent.
- AlarmHandle keeps its owner by WEAK reference; the owner has to stay alive
  on our side or the alarm dies silently.

Nothing queued is ever run on the calling thread. When no pump is running
(no lot loaded, or the alarm went quiet), post() refuses and returns False
so the caller can tell the player, instead of touching the game from the
wrong thread. That is the deliberate difference from the mod this was
adapted from, which fell back to running game code on the key thread.
"""

from __future__ import annotations

import importlib
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import simsight_log
import simsight_patch

if TYPE_CHECKING:
    from simsight_patch import Function

_INTERVAL_SECONDS = 0.02  # the game ticks at about 60 Hz; finer buys nothing
_STALE_SECONDS = 2.0  # silence longer than this means the alarm is gone
_MAX_QUEUED = 32  # a held key must never build a backlog

# Told once the pump runs in a new zone, in this order:
# (module, its functions called in turn, what the log calls a failure).
_ZONE_NOTICES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ('simsight_travel', ('announce_setting', 'start_lease'), 'travel readiness start'),
    ('simsight_build', ('announce_controls',), 'build controls notice'),
    ('simsight_narrator', ('push_simplified_menus',), 'simplified menus notice'),
    ('simsight_keys', ('scanner_zone_started',), 'scanner mode notice'),
)


class _Owner:
    """Something for the alarm to own: it refuses None and holds it weakly."""


class _State:
    def __init__(self) -> None:
        self.handle: Any = None  # EA's AlarmHandle while the pump runs
        self.owner: _Owner | None = None
        self.last_drain = 0.0


_lock = threading.Lock()
_queue: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []
_state = _State()


def alive() -> bool:
    """True while the simulation thread has drained us recently."""
    return _state.handle is not None and (time.monotonic() - _state.last_drain) < _STALE_SECONDS


def post(fn: Callable[..., Any], *args: Any) -> bool:
    """Queue fn(*args) for the simulation thread.

    Returns False, and runs nothing, when the pump is not alive."""
    if not alive():
        return False
    with _lock:
        if len(_queue) >= _MAX_QUEUED:
            del _queue[0]
        _queue.append((fn, args))
    return True


def _drain(_alarm_handle: Any = None) -> None:
    """The alarm callback, on the simulation thread: run what was queued."""
    _state.last_drain = time.monotonic()
    with _lock:
        if not _queue:
            return
        batch = _queue[:]
        del _queue[:]
    for fn, args in batch:
        _run(fn, args)


def _run(fn: Callable[..., Any], args: tuple[Any, ...]) -> None:
    try:
        fn(*args)
    except Exception:
        simsight_log.error('pump: {}'.format(getattr(fn, '__name__', fn)))


def _tell(module_name: str, functions: tuple[str, ...], what: str) -> None:
    try:
        module = importlib.import_module(module_name)
        for function in functions:
            getattr(module, function)()
    except Exception:
        simsight_log.error(f'pump: {what} failed')


def _tell_zone_started() -> None:
    for module_name, functions, what in _ZONE_NOTICES:
        _tell(module_name, functions, what)


def start() -> bool:
    """Arm the alarm on the current zone's wall clock (replacing any old one)."""
    stop()
    try:
        import alarms
        import clock

        _state.owner = _Owner()
        _state.handle = alarms.add_alarm_real_time(
            _state.owner, clock.interval_in_real_seconds(_INTERVAL_SECONDS), _drain, repeating=True
        )
        _state.last_drain = time.monotonic()
        simsight_log.line('pump: started')
        _tell_zone_started()
    except Exception:
        _state.handle = None
        simsight_log.error('pump: start failed')
        return False
    else:
        return True


def stop() -> None:
    """Cancel the alarm and drop anything still queued."""
    if _state.handle is None:
        return
    try:
        import alarms

        alarms.cancel_alarm(_state.handle)
    except Exception:
        simsight_log.error('pump: cancel alarm')
    _state.handle = None
    with _lock:
        del _queue[:]
    try:
        import simsight_travel as travel

        travel.stop_lease()
    except Exception:
        simsight_log.error('pump: travel lease stop')


def status() -> str:
    """'not started', 'alive' or 'stale', for the diagnostics readouts."""
    if _state.handle is None:
        return 'not started'
    return 'alive' if alive() else 'stale'


def _install_zone_hook() -> None:
    import zone

    def make(original: Function) -> Function:
        def a11y_pump_on_loading_screen_animation_finished(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            try:
                start()
            except Exception:
                simsight_log.error('pump: zone start')
            return result

        return a11y_pump_on_loading_screen_animation_finished

    simsight_patch.wrap(zone.Zone, 'on_loading_screen_animation_finished', make)


try:
    _install_zone_hook()
except Exception:
    simsight_log.error('pump: zone hook install failed')

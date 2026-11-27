"""The real-time refresh alarm: rescans while the player uses the scanner,
idle after 30 seconds without input."""

from __future__ import annotations

import time
from typing import Any

import simsight_availability as availability
import simsight_log
import simsight_settings as settings

from .state import _IDLE_SECONDS, _REFRESH_SECONDS, _state, enabled
from .scan import rescan


def _ui_owns_input() -> bool:
    try:
        import simsight_keys as keys

        return bool(keys.status.modal_active or keys.status.editing_active)
    except (ImportError, AttributeError):
        return False  # the keyboard module is not loaded: nothing owns the keys


def _refresh() -> None:
    # After 30 seconds without scanner input, the alarm only checks these
    # flags. The next action refreshes before moving or opening an item.
    if not enabled() or not settings.get('keyboard', True):
        availability.reset()
        return
    if not _state.refresh_requested:
        return
    if _state.menu_open or _ui_owns_input():
        availability.pause()
        return
    now = time.monotonic()
    if now - _state.last_activity >= _IDLE_SECONDS:
        _state.refresh_requested = False
        availability.pause()
        return
    if now - _state.last_scan >= _REFRESH_SECONDS:
        rescan(preserve=True)


def _refresh_tick(_handle: Any = None) -> None:
    # A real-time game alarm: updates while paused, never on the key thread.
    try:
        _refresh()
    except Exception:
        simsight_log.error('scanner: refresh tick')


class _RefreshOwner:
    """The owner object a game alarm needs."""


def _start_refresh() -> None:
    import alarms
    import clock

    if _state.refresh_handle is not None:
        try:
            alarms.cancel_alarm(_state.refresh_handle)
        except Exception:  # the previous zone may already have destroyed its timeline
            simsight_log.error('scanner: cancel the old refresh alarm')
    _state.refresh_owner = _RefreshOwner()
    _state.refresh_handle = alarms.add_alarm_real_time(
        _state.refresh_owner,
        clock.interval_in_real_seconds(_REFRESH_SECONDS),
        _refresh_tick,
        repeating=True,
    )

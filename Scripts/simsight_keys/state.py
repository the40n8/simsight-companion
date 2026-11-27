"""State the keyboard submodules share (one object each, as before),
and the small pump and UI hand-off helpers they all use."""

from __future__ import annotations

import threading
import time
from typing import Callable

import simsight_log
import simsight_settings as settings


class Status:
    """The keyboard switch and Flash's ownership reports, which other modules
    read (and the menu and the narrator set) as keys.status.<name>."""

    def __init__(self) -> None:
        self.enabled = bool(settings.get('keyboard', True))  # keyboard navigation on
        self.modal_active = False  # Flash: a modal owner (dialog, menu, panel) has the keys
        self.editing_active = False  # Flash: a text field has focus
        self.build_grid_owner = False  # Flash: the Build cursor owns plain keys


status = Status()
_ACTIVATION_KEYS = frozenset((0x0D, 0x20))
_DROPPED_EPOCH = 'dropped: input epoch changed before the pump turn'


class _State:
    """Input routing state only this module uses."""

    def __init__(self) -> None:
        # Bumped whenever queued input must no longer act (owner changes,
        # fences): a pump job compares the epoch it was queued under.
        self.input_epoch = 0
        self.blocking_explicit: bool | None = None  # Flash's blocking bit; None = not reported
        self.scanner_owner = False  # Flash: the scanner currently owns bare keys
        self.scanner_live = False  # Flash: Live mode with the setting on (camera follow)
        self.scanner_suspended = False  # a menu, dialog or other mode took input meanwhile
        self.scanner_pending_announce = False  # speak "Scanner" on Flash's first confirmation
        self.scanner_hold_until = 0.0  # our own window replacement: resume waits until then
        self.thread: threading.Thread | None = None  # the Windows poller
        self.running = False
        self.chord_repeating = False  # the poller is sending an auto-repeat of a chord


_state = _State()
_opening_keys: set[int] = set()  # activation keys already down when a surface opened
_route_logged: dict[str, float] = {}  # reason -> when it was last logged


def _post(fn: Callable[..., object], *args: object) -> bool:
    """Queue fn(*args) on the simulation thread; False when the pump is not running."""
    import simsight_pump as pump

    return bool(pump.post(fn, *args))


def _bit(state: object) -> object:
    """A 0/1 report: a number a caller already converted becomes '0' or '1'."""
    if isinstance(state, int) and not isinstance(state, bool) and state in (0, 1):
        return str(state)
    return state


def _log_route(reason: str, intent: str) -> None:
    """One line per reason per second: why a chord went where it went."""
    now = time.monotonic()
    if now - _route_logged.get(reason, 0.0) >= 1.0:
        _route_logged[reason] = now
        simsight_log.line(f'keys: chord {intent} {reason}')


def _deliver(message_name: str) -> None:
    """Only the simulation-thread pump may call the game distributor."""
    import distributor.ops
    import distributor.system

    op = distributor.ops.SendUIMessage(message_name)
    distributor.system.Distributor.instance().add_op_with_no_owner(op)

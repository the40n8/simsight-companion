"""Contextual keyboard navigation.

Tab/Shift+Tab moves between controls; arrows move within the open surface.
Enter/Space activates once; Escape backs out. EA InputAction guards consume
these physical keys while our UI owns focus. Python polling supplies presses,
repeats and releases. The transient Windows hook remains scanner-chord-only.
Ordinary typing never waits on a permanent Python keyboard hook.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, TypeVar

import sims4.commands

import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_settings as settings
import simsight_speech as speech
import simsight_phone  # noqa: F401 - side effect: install the incoming-call observer at startup
import simsight_hub  # register the common-actions commands
import simsight_careerlog  # noqa: F401 - side effect: doctor career research log (logging only)
import simsight_modui  # noqa: F401 - side effect: mod menu census (logging only)
import simsight_rooms  # noqa: F401 - side effect: the simsight.roomlabel console command (Build cursor room labels)
import simsight_holidays  # noqa: F401 - side effect: Seasons: announce a holiday when its day starts
import simsight_plus  # noqa: F401 - side effect: SimSight Plus features (Alt keys), merged 2026-10-08

if TYPE_CHECKING:
    KeyId = tuple[str, int]  # ('chord' or 'plain', vk) in the poller's tables

ctypes: Any = None  # None on the Mac, and on Windows when the bundled ctypes fails
_ctypes_error = 'unknown'
try:
    # NOTE: the bundled ctypes package ships only the core modules, so
    # ctypes.wintypes is NOT available here; use plain c_ulong for DWORDs.
    import ctypes as _loaded_ctypes

    ctypes = _loaded_ctypes
except Exception as _import_ex:
    _ctypes_error = repr(_import_ex)

# What a ctypes call into user32/kernel32 raises when it fails.
_CTYPES_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    AttributeError,
    TypeError,
    ValueError,
) + ((ctypes.ArgumentError,) if ctypes is not None else ())

_F = TypeVar('_F', bound=Callable[..., Any])


VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_CONTROL = 0x11
VK_LMENU = 0xA4  # left Alt
VK_RMENU = 0xA5  # right Alt; AltGr on many non-US layouts

# Tab direction is resolved from Shift at press time, with one latch per key.
BINDINGS = {
    'A11yTabNext': ('next control (Shift reverses)', [(0x09, False)]),
    'A11yActivate': ('activate or confirm choice', [(0x0D, False)]),
    'A11yToggle': ('toggle or select', [(0x20, False)]),
    'A11yReturnRow': ('return to last picker row', [(0x08, False)]),
    'A11yEscape': ('back or close', [(0x1B, False)]),
    'A11yArrowUp': ('previous row', [(0x26, False)]),
    'A11yArrowDown': ('next row', [(0x28, False)]),
    'A11yArrowLeft': ('left or decrease', [(0x25, False)]),
    'A11yArrowRight': ('right or increase', [(0x27, False)]),
}
NUMPAD_BINDINGS = {
    0x64: 'A11yArrowUp',
    0x66: 'A11yArrowDown',
    0x65: 'A11yActivate',
    0x68: 'A11yRepeat',
    0x6E: 'A11yDismiss',
}

# Scanner chords (Ctrl+Alt; the layout started as glaroc's a11yscan and now
# drives the scanner tree: Up/Down siblings, Right enters, Left goes back):
# vk -> (scanner intent, repeats while held). Polled keys cannot be
# swallowed, so a chord layer keeps bare keys free for the game, and nothing
# here collides with the numpad set above. Two known collisions outside the
# game: Ctrl+Alt+arrows is Intel's screen-rotation hotkey on some laptops
# (turn it off in the Intel graphics settings), and AltGr sends Ctrl+Alt on
# many non-US layouts, so a right-Alt chord on such a layout is treated as
# typing and ignored (see _altgr_pressed).
CHORDS = {
    0x26: ('prev', True),  # Up: previous thing
    0x28: ('next', True),  # Down: next thing
    0x25: ('prevcat', True),  # Left: previous main category
    0x27: ('nextcat', True),  # Right: next main category
    0x21: ('prevsub', True),  # Page Up: previous subcategory
    0x22: ('nextsub', True),  # Page Down: next subcategory
    0x0D: ('open', False),  # Enter: open the menu / activate
    0x20: ('scannermode', False),  # Space: scanner mode on or off
    0x08: ('back', False),  # Backspace: close the menu
    0x53: ('rescan', False),  # S
    0x57: ('where', False),  # W: where am I
    0x52: ('repeat', False),  # R
    0x48: ('help', False),  # H
    0x43: ('clock', False),  # C: the day and time
    0x50: ('phone', False),  # P: waiting household call/text
    0x45: ('mood', False),  # E: emotion and moodlets
    0x47: ('wants', False),  # G: wants/fears
    0x51: ('queue', False),  # Q: the active Sim's action queue
    0x4C: ('logging', False),  # L: diagnostic logging on or off
    0x56: ('events', False),  # V: running events (end them, send guests home)
    0x4D: ('milestones', False),  # M: the active Sim's milestones (Growing Together)
    0x4B: ('values', False),  # K: the active Sim's character values (Parenthood)
    0x4F: ('catalog', False),  # O: the Buy catalog tree (Build mode)
    0x49: ('catalogfind', False),  # I: search the Buy catalog (Build mode); not N (NVDA's own key)
    # DaRainbowGirlXX's Build cursor and scanner keys (merged 2026-11-12):
    0x55: ('layerup', False),  # U: Build cursor target layer up (wall heights, ceiling)
    0x44: ('layerdown', False),  # D: Build cursor target layer down
    0x4A: ('roomlabel', False),  # J: label the room at the Build cursor (L is logging)
    0xBC: ('scopeprev', False),  # comma: smaller scanner range
    0xBE: ('scopenext', False),  # period: larger scanner range
    0x42: ('buildplace', False),  # B: Build placement on the lot grid
    0x54: ('gridtoggle', False),  # T: read-only lot grid
    0xDB: ('rotateleft', False),  # [ : native Build rotation
    0xDD: ('rotateright', False),  # ]
    0x5A: ('undo', False),  # Z: native Build undo
    0x59: ('redo', False),  # Y: native Build redo
    0x46: ('buildgrid', False),  # F: aim at sampled grid point, never place
    0x58: ('sell', False),  # X: sell the object on the grid point (twice)
    # glaroc's Ctrl+Alt+1..8: switch to household Sim N (selectable order).
    0x31: ('sim1', False),
    0x32: ('sim2', False),
    0x33: ('sim3', False),
    0x34: ('sim4', False),
    0x35: ('sim5', False),
    0x36: ('sim6', False),
    0x37: ('sim7', False),
    0x38: ('sim8', False),
}
CHORD_NAMES = {
    0x26: 'Up',
    0x28: 'Down',
    0x25: 'Left',
    0x27: 'Right',
    0x20: 'Space',
    0x21: 'PageUp',
    0x22: 'PageDown',
    0x0D: 'Enter',
    0x08: 'Backspace',
    0x53: 'S',
    0x57: 'W',
    0x52: 'R',
    0x48: 'H',
    0x43: 'C',
    0x50: 'P',
    0x45: 'E',
    0x47: 'G',
    0x51: 'Q',
    0x4C: 'L',
    0x56: 'V',
    0x4D: 'M',
    0x4B: 'K',
    0x4F: 'O',
    0x49: 'I',
    0x55: 'U',
    0x44: 'D',
    0x4A: 'J',
    0xBC: 'comma',
    0xBE: 'period',
    0x42: 'B',
    0x54: 'T',
    0xDB: '[',
    0xDD: ']',
    0x5A: 'Z',
    0x59: 'Y',
    0x46: 'F',
    0x58: 'X',
    0x31: '1',
    0x32: '2',
    0x33: '3',
    0x34: '4',
    0x35: '5',
    0x36: '6',
    0x37: '7',
    0x38: '8',
}

# Key names for the simsight.keys listing (single source in settings).
_KEY_NAMES = settings.KEY_NAMES

_POLL_SECONDS = 0.01

# Holding a navigation key auto-repeats it (OS-style: initial delay, then a
# steady rate). Only movement actions repeat; activate/clear/speech keys fire
# once per press. Flash preserves input order while a list realizes a row.
_REPEATABLE = {
    'A11yNavNext',
    'A11yNavPrev',
    'A11yArrowUp',
    'A11yArrowDown',
    'A11yArrowLeft',
    'A11yArrowRight',
    'A11yScanner:prev',
    'A11yScanner:next',
    'A11yBuildGrid:up',
    'A11yBuildGrid:down',
    'A11yBuildGrid:left',
    'A11yBuildGrid:right',
}

# Scanner mode: bare keys browse the scanner tree while Flash reports that
# the scanner owns input; its native guards consume them, so the arrows never
# pan the camera. No system hook. Escape is deliberately not a scanner key:
# it stays the game's Options menu.
SCANNER_KEYS = {
    0x26: 'A11yScanner:prev',
    0x28: 'A11yScanner:next',
    0x27: 'A11yScanner:nextcat',
    0x25: 'A11yScanner:prevcat',
    0x0D: 'A11yScanner:open',
    0x08: 'A11yScanner:back',
    0x20: 'A11yScanner:repeat',
}
# The Build cursor (grid Build placement): plain keys while Flash reports
# it owns them (TooltipManager A11yBuildGridOwner, simsight.buildgridowner).
BUILD_GRID_KEYS = {
    0x26: 'up',
    0x28: 'down',
    0x25: 'left',
    0x27: 'right',
    0x21: 'floorup',
    0x22: 'floordown',
    0x0D: 'place',
    0x20: 'repeat',
    0x08: 'back',
    0x1B: 'back',
    0x52: 'turn',
    0x24: 'last',
    0x2E: 'sell',
    0x31: 'info1',
    0x32: 'info2',
    0x33: 'info3',
    0x34: 'info4',
    0x35: 'info5',
    0x36: 'info6',
    0x4D: 'moveobject',
}  # M: pick up an object, M again puts it down here
# Flash's reports and this module's switches, read by other modules as
# module attributes (keys._modal_active, keys._enabled, ...): they stay here.
_build_grid_owner = False  # Flash: the Build cursor owns plain keys
_chord_repeating = False  # the poller is sending an auto-repeat of a chord
_REPEAT_DELAY = 0.45
_REPEAT_INTERVAL = 0.065
_enabled = bool(settings.get('keyboard', True))
_modal_active = False
_editing_active = False

_PHONE_BLOCKED = 'Close the current panel or dialog before opening a waiting call.'
_ACTIVATION_KEYS = frozenset((0x0D, 0x20))
_DROPPED_EPOCH = 'dropped: input epoch changed before the pump turn'
_PUMP_INACTIVE = 'keys: navigation unavailable (simulation pump inactive)'


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


_state = _State()
_opening_keys: set[int] = set()  # activation keys already down when a surface opened
_route_logged: dict[str, float] = {}  # reason -> when it was last logged


def _dlls() -> tuple[Any, Any]:
    return (
        ctypes.WinDLL('user32', use_last_error=True),
        ctypes.WinDLL('kernel32', use_last_error=True),
    )


def _post(fn: Callable[..., object], *args: object) -> bool:
    """Queue fn(*args) on the simulation thread; False when the pump is not running."""
    import simsight_pump as pump

    return bool(pump.post(fn, *args))


def _bit(state: object) -> object:
    """A 0/1 report: a number a caller already converted becomes '0' or '1'."""
    if isinstance(state, int) and not isinstance(state, bool) and state in (0, 1):
        return str(state)
    return state


def fence_opening_input() -> None:
    """Retire activation input already in flight when a new surface opens.

    Snapshot only keys already down/tapped; a fresh first press stays usable.
    Windows' low bit is consumed here deliberately, before polling can
    mistake the console's completed Enter tap for a new picker activation.
    """
    _state.input_epoch += 1
    if ctypes is None or not _enabled:
        return
    try:
        user32, kernel32 = _dlls()
        if not _game_is_foreground(user32, kernel32):
            return
        for vk in _ACTIVATION_KEYS:
            if user32.GetAsyncKeyState(vk) & 0x8001:
                _opening_keys.add(vk)
    except Exception:
        simsight_log.error('keys: opening input snapshot')


@sims4.commands.Command(
    'simsight.inputfence', 'a11y.inputfence', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_inputfence(payload: str = '', _connection: Any = None) -> bool:
    # Flash supplies an empty positional payload; the native command wrapper
    # also supplies _connection by keyword. Keep them separate. The startup
    # socket dispatcher calls this without either argument.
    fence_opening_input()
    return True


def _log_route(reason: str, intent: str) -> None:
    """One line per reason per second: why a chord went where it went."""
    now = time.monotonic()
    if now - _route_logged.get(reason, 0.0) >= 1.0:
        _route_logged[reason] = now
        simsight_log.line(f'keys: chord {intent} {reason}')


@sims4.commands.Command(
    'simsight.modalstate', 'a11y.modalstate', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_modalstate(state: str = '00', _connection: Any = None) -> bool:
    try:
        return apply_modal_state(state, 'modalstate')
    except Exception:
        simsight_log.error('keys: modal state')
        return False


def _modal_report(state: object, source: str) -> tuple[object, bool | None]:
    """The owner report as two digits (owner, editing), and the blocking bit
    when the report carries one."""
    if isinstance(state, int) and not isinstance(state, bool) and state in (0, 1, 10, 11):
        # sims4.commands converts only str arguments; a payload a caller
        # already turned into a number arrives as one.
        state = f'{state:02d}'
    if isinstance(state, str) and state in ('0', '1'):
        # Live evidence 2026-09-25: the native command path drops the
        # leading zero, so Flash's '00' and '01' arrive as '0' and '1'.
        state = '0' + state
    if source == 'context' and isinstance(state, str) and len(state) == 3 and state[2] in '01':
        # The context suffix carries the owner and its blocking bit as one
        # report. A three-digit direct payload is never accepted: the
        # native path drops leading zeros, so '010' would read as '10'.
        return state[:2], state[2] == '1'
    return state, None


def apply_modal_state(state: object, source: str) -> bool:
    """Flash's owner report. It arrives twice per change (simsight.modalstate and
    the simsight.context_enc suffix, from the same A11yModalFrame pass), so one
    lost post cannot leave keys routing chords to a closed owner. Applying
    the same state again changes nothing."""
    global _modal_active, _editing_active  # noqa: PLW0603 - module attributes other modules read
    report, blocking = _modal_report(state, source)
    if not isinstance(report, str) or report not in ('00', '01', '10', '11'):
        simsight_log.line(
            f'keys: modal state rejected {type(report).__name__} {repr(report)[:24]} ({source})'
        )
        return False
    before = ('1' if _modal_active else '0') + ('1' if _editing_active else '0')
    if before != report:
        simsight_log.line(f'keys: modal {before}->{report} ({source})')
    if _modal_active and report[0] == '0':
        _state.input_epoch += 1  # the owner closed: queued modal navigation is stale
    if (report[0] == '1' and not _modal_active) or (_editing_active and report[1] == '0'):
        # New modal owner, or a text field just closed (console Enter):
        # retire keys already down or tapped before this surface change.
        fence_opening_input()
    owner_changed = (report[0] == '1') != _modal_active
    _modal_active = report[0] == '1'
    _editing_active = report[1] == '1'
    if blocking is not None:
        _set_blocking(blocking, source)
    elif owner_changed:
        # An owner report without its bit: a stale permissive bit must not
        # outlive the owner it described. Unknown counts as blocking until
        # Flash reports the bit (simsight.blocking or the next context post).
        _set_blocking(None, source)
    if not _modal_active:
        # A missed pie-menu Hide message must not route future world chords
        # to the whole HUD. Flash ownership is the authoritative context.
        import simsight_scanner as scanner

        scanner.a11y_menuclosed()
    return True


def _set_blocking(value: bool | None, source: str) -> None:
    if value != _state.blocking_explicit:
        simsight_log.line(
            f'keys: blocking {_blocking_text(_state.blocking_explicit)}->{_blocking_text(value)} ({source})'
        )
    _state.blocking_explicit = value


def _blocking_text(value: bool | None) -> str:
    return '?' if value is None else ('1' if value else '0')


def blocking_active() -> bool:
    """Whether the current owner blocks a recipient switch (the phone
    shortcut). The call alert, the phone panel, SimInfo panels and HUD
    browsing own the keys without blocking; dialogs, pickers, pie menus and
    dropdowns block. Until Flash has reported the bit for the current owner
    (older UI packages never do), any owner blocks."""
    if _state.blocking_explicit is None:
        return _modal_active
    return _state.blocking_explicit


@sims4.commands.Command(
    'simsight.blocking', 'a11y.blocking', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_blocking(state: str = '1', _connection: Any = None) -> bool:
    """Flash: the blocking bit alone, posted on change after the owner
    report (context, modalstate, blocking) and again after a sender reset."""
    report = _bit(state)
    if report not in ('0', '1'):
        simsight_log.line(f'keys: blocking rejected {type(report).__name__} {repr(report)[:24]}')
        return False
    _set_blocking(report == '1', 'blocking')
    return True


def _deliver(message_name: str) -> None:
    """Only the simulation-thread pump may call the game distributor."""
    import distributor.ops
    import distributor.system

    op = distributor.ops.SendUIMessage(message_name)
    distributor.system.Distributor.instance().add_op_with_no_owner(op)


# Settings every UI copy must hear again: (module, function, what for the log).
_UI_NOTICES = (
    ('simsight_build', 'announce_controls', 'build controls notice'),
    ('simsight_travel', 'announce_setting', 'travel list notice'),
    ('simsight_narrator', 'push_simplified_menus', 'simplified menus notice'),
)


def _ui_notice(module_name: str, function: str, what: str) -> None:
    try:
        getattr(importlib.import_module(module_name), function)()
    except Exception:
        simsight_log.error(f'keys: {what}')


def _announce_ui_settings() -> None:
    """Simulation thread, with every keyboard lease: a UI copy created since
    the last change (Build and Live swap TooltipManager copies) starts with
    each switch off until it hears it."""
    for module_name, function, what in _UI_NOTICES:
        _ui_notice(module_name, function, what)
    _announce_scanner_mode()
    if _state.scanner_live:
        _scanner_call('update_camera_follow')


def _announce_scanner_mode() -> None:
    """Simulation thread: A11y|scannermode|on or off to every UI copy."""
    try:
        _deliver('A11y|scannermode|' + ('on' if _scanner_effective() else 'off'))
    except Exception:
        simsight_log.error('keys: scanner mode notice')


def _scanner_effective() -> bool:
    """The scanner mode setting counts only while keyboard mode and the
    scanner are on."""
    return (
        bool(settings.get('scanner_mode', False))
        and _enabled
        and bool(settings.get('keyboard', True))
        and bool(settings.get('scanner', True))
    )


def _build_grid_routing() -> bool:
    return _build_grid_owner and not _modal_active and not _editing_active


def _scanner_routing() -> bool:
    return (
        _scanner_effective() and _state.scanner_owner and not _modal_active and not _editing_active
    )


def _in_live() -> bool:
    try:
        import services

        zone = services.current_zone()
        return zone is not None and not zone.is_in_build_buy
    except Exception:
        simsight_log.error('keys: Live check')
        return False


def set_scanner_mode(on: object) -> bool:
    """Simulation thread: persist the scanner mode setting and apply it
    (Ctrl+Alt+Space, the settings menu and simsight.scannermode all come here)."""
    if on and (not _enabled or not settings.get('scanner', True)):
        speech.speak(lang.t('scanner mode needs scanner'), explicit=True)
        return False
    was = bool(settings.get('scanner_mode', False))
    settings.set('scanner_mode', bool(on))
    apply_scanner_setting(announce_change=(was != bool(on)))
    return True


def _scanner_mode_off(announce_change: bool) -> None:
    _state.scanner_suspended = False
    _state.scanner_pending_announce = False
    if _state.scanner_live:
        _state.scanner_live = False
        _scanner_call('set_camera_live', False)  # release at once, before Flash reports
    if announce_change:
        simsight_log.line('keys: scanner mode off')
        speech.speak(lang.t('scanner mode off'), explicit=True)


def apply_scanner_setting(announce_change: bool = False) -> None:
    """The setting (or keyboard/scanner) changed: tell every UI copy and set
    up the announcement. On: "Scanner. <location>" when Flash first confirms
    it owns the keys (Live), or a spoken note outside Live. Off: release."""
    _announce_scanner_mode()
    _state.input_epoch += 1  # nothing queued under the old setting may act
    if not _scanner_effective():
        _scanner_mode_off(announce_change)
        return
    try:
        import simsight_grid as grid

        if grid.active():
            grid.leave()  # one cursor at a time: the grid also uses arrows
    except Exception:
        simsight_log.error('keys: grid leave')
    fence_opening_input()  # the toggle's Space must not also act
    _state.scanner_suspended = False
    # "Scanner" is spoken when Flash confirms it owns the keys, never before.
    _state.scanner_pending_announce = True
    simsight_log.line('keys: scanner mode on (waiting for the client)')
    if announce_change and not _in_live():
        speech.speak(lang.t('scanner mode starts in live'), explicit=True)


def scanner_zone_started() -> None:
    """Zone load (pump start): say where the scanner is once Live input is
    confirmed again."""
    if _scanner_effective():
        _state.scanner_pending_announce = True
    _announce_scanner_mode()


def _toggle_scanner_mode(epoch: int) -> None:
    if epoch != _state.input_epoch:
        _log_route(_DROPPED_EPOCH, 'scannermode')
        return
    set_scanner_mode(not settings.get('scanner_mode', False))


def _build_grid_key(intent: str, epoch: int) -> None:
    """Simulation thread: one plain key for the Build cursor, re-checked."""
    if epoch != _state.input_epoch:
        _log_route(_DROPPED_EPOCH, 'build ' + intent)
        return
    if not _build_grid_routing():
        _log_route('dropped: the Build cursor no longer owns input', 'build ' + intent)
        return
    import simsight_grid as grid

    if not grid.build_cursor():
        _log_route('dropped: the Build cursor is off', 'build ' + intent)
        return
    _log_route('handled by the Build cursor', 'build ' + intent)
    grid.on_key(intent)


@sims4.commands.Command(
    'simsight.buildgridowner', 'a11y.buildgridowner', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_buildgridowner(state: str = '0', _connection: Any = None) -> bool:
    """Flash: whether the Build cursor owns plain keys now (on change)."""
    global _build_grid_owner  # noqa: PLW0603 - a module attribute other modules read
    report = _bit(state)
    if report not in ('0', '1'):
        return False
    owner = report == '1'
    if owner != _build_grid_owner:
        simsight_log.line(f'keys: Build cursor owner {int(_build_grid_owner)}->{int(owner)}')
        _build_grid_owner = owner
        _state.input_epoch += 1  # a key queued under the old owner must not act
    return True


def _scanner_key(intent: str, epoch: int) -> None:
    """Simulation thread: one bare key in scanner mode, re-checked here."""
    if epoch != _state.input_epoch:
        _log_route(_DROPPED_EPOCH, 'scanner ' + intent)
        return
    if not _scanner_routing():
        _log_route('dropped: the scanner no longer owns input', 'scanner ' + intent)
        return
    import simsight_scanner as scanner

    _log_route('handled by scanner mode', 'scanner ' + intent)
    scanner.on_key(intent, on_simulation_thread=True)


def hold_scanner_resume(seconds: float = 1.0) -> None:
    """Simulation thread: one of our windows (travel, phone) is replacing
    another. The scanner must not announce itself in the moment between
    them; if no window follows, it announces when the hold ends."""
    _state.scanner_hold_until = time.monotonic() + seconds


def _resume_after_hold(epoch: int) -> None:
    """Pump: the delayed resume. Any owner change since (the next window
    opened) retires it; that window's close announces as usual."""
    if epoch != _state.input_epoch:
        return
    if time.monotonic() < _state.scanner_hold_until:
        _post(_resume_after_hold, epoch)
        return
    if (
        _state.scanner_owner
        and _state.scanner_suspended
        and not _modal_active
        and not _editing_active
        and _scanner_effective()
    ):
        import simsight_scanner as scanner

        _state.scanner_suspended = False
        fence_opening_input()
        scanner.resume_mode()


def _scanner_owner_now(changed: bool) -> None:
    """The scanner owns the keys (again): announce it, or resume after a
    menu, dialog, panel or Build."""
    if not changed and not _state.scanner_pending_announce and not _state.scanner_suspended:
        return  # a heartbeat re-post: nothing to do
    if _modal_active or _editing_active:
        return  # announced when the scanner really has the keys
    import simsight_scanner as scanner

    if _state.scanner_pending_announce:
        _state.scanner_pending_announce = False
        _state.scanner_suspended = False
        scanner.enter_mode()
        return
    if _state.scanner_suspended:
        if time.monotonic() < _state.scanner_hold_until:
            _post(_resume_after_hold, _state.input_epoch)
            return
        # Back from a menu, dialog, panel or Build: a still-held Enter must
        # not act again, then say where the scanner is.
        _state.scanner_suspended = False
        fence_opening_input()
        scanner.resume_mode()


def _scanner_owner_report(state: object) -> bool:
    report = _bit(state)
    if report not in ('0', '1'):
        simsight_log.line(
            f'keys: scanner owner rejected {type(report).__name__} {repr(report)[:24]}'
        )
        return False
    owner = report == '1'
    changed = owner != _state.scanner_owner
    if changed:
        simsight_log.line(f'keys: scanner owner {int(_state.scanner_owner)}->{int(owner)}')
        _state.scanner_owner = owner
        # A scanner key queued under the old owner must not act under the
        # next one (a menu can open and close before the pump turn).
        _state.input_epoch += 1
    if not _scanner_effective():
        return True
    if owner:
        _scanner_owner_now(changed)
    elif changed:
        _state.scanner_suspended = not _state.scanner_pending_announce
    return True


@sims4.commands.Command(
    'simsight.scannerowner', 'a11y.scannerowner', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_scannerowner(state: str = '0', _connection: Any = None) -> bool:
    """Flash: whether the scanner owns bare keys now. Posted on change and
    again with every mode heartbeat; only a change does anything here."""
    try:
        return _scanner_owner_report(state)
    except Exception:
        simsight_log.error('keys: scanner owner')
        return False


def _scanner_call(name: str, *args: object) -> Any:
    """Call a scanner hook if this build of the scanner has it."""
    try:
        import simsight_scanner as scanner

        fn = getattr(scanner, name, None)
        if fn is not None:
            return fn(*args)
    except Exception:
        simsight_log.error(f'keys: scanner.{name}')
    return None


@sims4.commands.Command(
    'simsight.scannerlive', 'a11y.scannerlive', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_scannerlive(state: str = '0', _connection: Any = None) -> bool:
    """Flash: Live mode with scanner mode on, whatever owns the keys (menus
    and text fields keep it on; Build, CAS and the main menu turn it off).
    Drives the camera follow; only a change does anything."""
    report = _bit(state)
    if report not in ('0', '1'):
        simsight_log.line(
            f'keys: scanner live rejected {type(report).__name__} {repr(report)[:24]}'
        )
        return False
    live = report == '1' and _scanner_effective()
    if live != _state.scanner_live:
        simsight_log.line(f'keys: scanner live {int(_state.scanner_live)}->{int(live)}')
    _state.scanner_live = live
    # Every report goes through: a zone reset on the camera side must be
    # able to resume from the next report even when this flag did not move.
    _scanner_call('set_camera_live', live)
    return True


@sims4.commands.Command(
    'simsight.scannermode', 'a11y.scannermode', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_scannermode(action: str = 'toggle', _connection: Any = None) -> bool:
    """simsight.scannermode [on|off|toggle]"""
    action = str(action).lower()
    if action not in ('on', 'off', 'toggle'):
        return False
    try:
        current = bool(settings.get('scanner_mode', False))
        return set_scanner_mode(not current if action == 'toggle' else action == 'on')
    except Exception:
        simsight_log.error('keys: scanner mode command')
        return False


def _startup_sent(message_name: str) -> bool:
    """The startup bridge delivers navigation itself when it can."""
    try:
        import simsight_startup as startup

        return bool(startup.send_navigation(message_name))
    except ImportError:
        # Older/unavailable startup bridges retain the loaded-zone path.
        return False


def _send_owned(message_name: str) -> None:
    """Modal chord navigation: delivered only while the owner it was sampled
    under is still current. A closed dialog or pie (or a new one) advances
    the epoch, so a queued Next/Previous can no longer walk the HUD behind it.
    Scope: owner changes Flash reports (close, or a new owner through its
    opening fence). A swap between two owners with no fence in between is
    left to the client's own no-owner guard."""
    if message_name == 'A11yRepeat':
        _send(message_name)
        return
    epoch = _state.input_epoch
    try:
        if _startup_sent(message_name):
            return
        if not _post(_deliver_activation, message_name, epoch):
            simsight_log.line(_PUMP_INACTIVE)
    except Exception:
        simsight_log.error(f'keys: send {message_name}')


def _deliver_activation(message_name: str, epoch: int) -> None:
    # A queued press belongs to the surface that owned it when sampled.
    if epoch == _state.input_epoch:
        _deliver(message_name)


def _post_routed_key(
    handler: Callable[[str, int], None], message_name: str, prefix: str, unavailable: str
) -> None:
    """A bare key for the Build cursor or scanner mode, checked again on the pump."""
    try:
        if not _post(handler, message_name[len(prefix) :], _state.input_epoch):
            simsight_log.line(unavailable)
    except Exception:
        simsight_log.error(f'keys: send {message_name}')


def _send_to_client(message_name: str) -> None:
    """Focus navigation for the Flash layer, through the startup bridge or the pump."""
    try:
        if _startup_sent(message_name):
            return
        if message_name in ('A11yActivate', 'A11yToggle'):
            posted = _post(_deliver_activation, message_name, _state.input_epoch)
        else:
            posted = _post(_deliver, message_name)
        if not posted and message_name != 'A11y|lease':
            simsight_log.line(_PUMP_INACTIVE)
    except Exception:
        simsight_log.error(f'keys: send {message_name}')


def _send(message_name: str) -> None:
    # Speech-control actions are handled in Python; only focus navigation
    # goes to the Flash layer.
    if message_name == 'A11yRepeat':
        try:
            speech.repeat_last()
        except Exception:
            simsight_log.error('keys: repeat')
        return
    if message_name.startswith('A11yBuildGrid:'):
        _post_routed_key(
            _build_grid_key,
            message_name,
            'A11yBuildGrid:',
            'keys: Build cursor unavailable (simulation pump inactive)',
        )
        return
    if message_name.startswith('A11yScanner:'):
        _post_routed_key(_scanner_key, message_name, 'A11yScanner:', _PUMP_INACTIVE)
        return
    if message_name == 'A11ySpeechMode':
        try:
            speech.cycle_mode()
        except Exception:
            simsight_log.error('keys: speech mode cycle')
        return
    _send_to_client(message_name)


_altgr_layouts: dict[int, bool] = {}  # keyboard layout handle -> types with Ctrl+Alt


def _layout_has_altgr(user32: Any, hkl: int) -> bool:
    """True when this keyboard layout types characters with Ctrl+Alt (AltGr)."""
    if hkl in _altgr_layouts:
        return _altgr_layouts[hkl]
    found = False
    try:
        user32.VkKeyScanExW.restype = ctypes.c_short
        user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
        for code in range(0x20, 0x100):
            result = user32.VkKeyScanExW(chr(code), hkl)
            if result != -1 and ((result >> 8) & 0x06) == 0x06:
                found = True
                break
    except _CTYPES_ERRORS:
        found = False  # unknown layout: treat Ctrl+Alt as a chord
    _altgr_layouts[hkl] = found
    return found


def _altgr_pressed(user32: Any) -> bool:
    """Right Alt alone on an AltGr layout: the player is typing, not chording."""
    try:
        if not (user32.GetAsyncKeyState(VK_RMENU) & 0x8000):
            return False
        if user32.GetAsyncKeyState(VK_LMENU) & 0x8000:
            return False
        hwnd = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        user32.GetKeyboardLayout.restype = ctypes.c_void_p
        hkl = user32.GetKeyboardLayout(thread_id)
        return _layout_has_altgr(user32, hkl)
    except _CTYPES_ERRORS:
        return False  # no layout to check: not AltGr


# --------------------------------------------------- chord key swallowing
# Polling cannot stop the game from seeing a key, so Ctrl+Alt+Down still
# panned the camera on every scanner step (first in-game test, 2026-09-06).
# A WH_KEYBOARD_LL hook can refuse a key before it reaches the message
# queue. It lives on its own thread with its own message loop (a low-level
# hook is only called while its owning thread pumps messages), blocks ONLY
# chord keys while Ctrl+Alt is held and the game is in front, and is
# removed when polling stops or the feature is off. A blocked key never
# reaches the system key state, so the hook reports chord keys to the
# poller itself (_hook_state). Windows silently drops a hook whose callback
# is too slow (the callback needs the GIL, which a busy simulation thread
# can hold); the poller notices a chord key the hook never reported and the
# hook thread reinstalls it.
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
_KEY_MESSAGES = (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
_SWALLOW_VKS = frozenset(CHORDS)


class _Hook:
    """The swallow hook's state, shared by the hook thread and the poller."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.running = False
        self.handle: int | None = None
        self.proc: Any = None  # keep the ctypes callback alive or the hook dies
        self.last_event = 0.0
        self.missed = 0.0  # poller saw a chord key the hook did not report
        self.installs = 0
        self.swallow_generation = 0


_hook = _Hook()
_hook_state: dict[int, bool] = {}  # vk -> down, as the hook last saw it
# Key-ups the hook consumed never reach Windows, so the key's async high bit
# stays set after the physical release. Kept across hook removal until
# Windows shows a real release or a new press (see _mask_swallowed_releases).
# vk -> generation of its latest consumed key-up; the hook thread records,
# the poller retires, both under _swallow_lock.
_swallowed_up: dict[int, int] = {}
_swallow_lock = threading.Lock()
# (vk, down, when): preserve complete chord taps between poll samples.
_hook_edges: deque[tuple[int, bool, float]] = deque()
# Diagnostic: (vk, down, seen down before, gate, ms late) for each chord-key
# event the hook handled; the poller writes them to the log (fast-chord
# losses, 2026-09-26). Never read by the input logic.
_hook_trace: deque[tuple[int, bool, bool, bool, int]] = deque(maxlen=64)


def _record_swallowed_up(vk: int) -> None:
    with _swallow_lock:
        _hook.swallow_generation += 1
        _swallowed_up[vk] = _hook.swallow_generation


def _clear_swallowed_up(vk: int) -> None:
    with _swallow_lock:
        _swallowed_up.pop(vk, None)


def _swallowed_snapshot() -> dict[int, int]:
    with _swallow_lock:
        return dict(_swallowed_up)


def _swallow_enabled() -> bool:
    # Always on with keyboard mode (no longer a player option); the hook
    # still needs Ctrl+Alt held, the game in front and no AltGr typing.
    return _enabled


def _trace_hook_event(
    kernel32: Any, info: Any, vk: int, is_down: bool, previous: bool, gate: bool
) -> None:
    try:
        late = (kernel32.GetTickCount() - info[3]) & 0xFFFFFFFF
    except _CTYPES_ERRORS:
        late = -1  # diagnostic only
    _hook_trace.append((vk, is_down, previous, gate, late))


def _hook_key_event(user32: Any, kernel32: Any, info: Any, is_down: bool) -> bool:
    """One chord-key event in the hook: record it; True swallows it."""
    vk = info[0]
    previous = _hook_state.get(vk, False)
    _hook_state[vk] = is_down
    if is_down:
        _clear_swallowed_up(vk)
    if not is_down and previous:
        _hook_edges.append((vk, False, _hook.last_event))
    ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000 != 0
    alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000 != 0
    shift = user32.GetAsyncKeyState(VK_SHIFT) & 0x8000 != 0
    gate = (
        ctrl
        and alt
        and not shift
        and _swallow_enabled()
        and _game_is_foreground(user32, kernel32)
        and not _altgr_pressed(user32)
    )
    if simsight_log.enabled and not (is_down and previous):
        _trace_hook_event(kernel32, info, vk, is_down, previous, gate)
    if not gate:
        return False
    if is_down and not previous:
        _hook_edges.append((vk, is_down, _hook.last_event))
    elif not is_down:
        # Drain the old press bit so the stale high bit is never read as a
        # new press. A press this hook never saw (the key went down just
        # before the hook was installed) that the poller has not read either
        # is a whole chord tap: report it, or the drain loses a fast chord
        # (2026-09-26).
        unread = user32.GetAsyncKeyState(vk) & 1
        _record_swallowed_up(vk)
        if unread and not previous:
            _hook_edges.append((vk, True, _hook.last_event))
            _hook_edges.append((vk, False, _hook.last_event))
    return True


def _hook_callback_factory(user32: Any, kernel32: Any) -> Any:
    lresult = ctypes.c_ssize_t
    proto = ctypes.CFUNCTYPE(lresult, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
    user32.CallNextHookEx.restype = lresult
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]

    def callback(code: int, wparam: int, lparam: int) -> int:
        try:
            _hook.last_event = time.monotonic()
            if code == 0 and wparam in _KEY_MESSAGES:
                # KBDLLHOOKSTRUCT: vkCode, scanCode, flags, time (DWORDs)
                info = ctypes.cast(lparam, ctypes.POINTER(ctypes.c_ulong))
                is_down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if info[0] in _SWALLOW_VKS and _hook_key_event(user32, kernel32, info, is_down):
                    return 1
        except Exception:
            simsight_log.error('keys: swallow hook callback')
        return int(user32.CallNextHookEx(None, code, wparam, lparam))

    return proto(callback)


class _HookOwner:
    """Owns the hook and pumps messages for it, on the hook thread."""

    def __init__(self) -> None:
        user32, kernel32 = _dlls()
        self.user32 = user32
        self.kernel32 = kernel32
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_void_p]
        _hook.proc = _hook_callback_factory(user32, kernel32)
        self.module = kernel32.GetModuleHandleW(None)

        class MSG(ctypes.Structure):
            _fields_ = [
                ('hwnd', ctypes.c_void_p),
                ('message', ctypes.c_uint),
                ('wParam', ctypes.c_size_t),
                ('lParam', ctypes.c_ssize_t),
                ('time', ctypes.c_ulong),
                ('pt_x', ctypes.c_long),
                ('pt_y', ctypes.c_long),
            ]

        self.msg = MSG()

    def install(self) -> bool:
        _hook_state.clear()
        _hook.last_event = time.monotonic()
        _hook.missed = 0.0
        handle = self.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, ctypes.cast(_hook.proc, ctypes.c_void_p), self.module, 0
        )
        _hook.handle = handle
        _hook.installs += 1
        simsight_log.line(
            'keys: swallow hook {} (install #{})'.format(
                'installed' if handle else f'FAILED err={ctypes.get_last_error()}',
                _hook.installs,
            )
        )
        return bool(handle)

    def remove(self, reason: str) -> None:
        if _hook.handle:
            try:
                self.user32.UnhookWindowsHookEx(_hook.handle)
            except Exception:
                simsight_log.error('keys: swallow hook removal')
            _hook.handle = None
            _hook_state.clear()
            simsight_log.line(f'keys: swallow hook removed ({reason})')

    def _wanted(self) -> bool:
        user32 = self.user32
        return (
            _swallow_enabled()
            and bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            and bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            and not (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            and _game_is_foreground(user32, self.kernel32)
            and not _altgr_pressed(user32)
        )

    def run(self) -> None:
        # The hook is system-wide and its callback needs the GIL, so while it
        # is installed every keystroke in every program waits on the game.
        # Even ordinary typing INSIDE the game must bypass this Python hook.
        # Check modifiers on every pass; keep the hook only for scanner chords.
        user32 = self.user32
        msg = ctypes.byref(self.msg)
        last_check = time.monotonic()
        while _hook.running:
            # Pump whatever is queued (the hook callback is delivered here), then rest.
            while user32.PeekMessageW(msg, None, 0, 0, 1):
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.005)
            now = time.monotonic()
            wanted = self._wanted()
            if wanted and not _hook.handle:
                self.install()
            elif _hook.handle and not wanted:
                self.remove('scanner chord released or inactive')
            if now - last_check > 1.0:
                last_check = now
                if _hook.handle and _hook.missed > _hook.last_event + 0.5:
                    self.remove('dropped by Windows, reinstalling')
                    self.install()
        self.remove('polling stopped')


def _hook_loop() -> None:
    """The hook thread."""
    owner: _HookOwner | None = None
    try:
        owner = _HookOwner()
        owner.run()
    except Exception:
        simsight_log.error('keys: swallow hook thread')
    finally:
        # A failed message loop must not leave a hook blocking all input.
        if _hook.handle and owner is not None:
            try:
                owner.user32.UnhookWindowsHookEx(_hook.handle)
            except Exception:
                simsight_log.error('keys: swallow hook removal')
        _hook.handle = None
        _hook_state.clear()


def _mask_swallowed_releases(
    sampled: dict[int, int], before: dict[int, int] | None = None
) -> dict[int, int]:
    """Report a key as up while Windows still lacks a key-up the hook
    consumed and no new press has arrived.

    Without this, the stale high bit reads as a held key once Ctrl+Alt are
    released or the hook goes quiet, and navigation repeats until that key
    is pressed again (scanner incident 2026-09-25). Only consumed key-ups
    count: a hook Windows dropped leaves _hook_state stale, and then the
    polled state is the one to trust.

    before is the record snapshot taken just ahead of sampling. A record
    made after it belongs to a key-up that happened after (or during) the
    read, so the sample cannot retire it: a press it read still counts, and
    otherwise the consumed key-up already proves the key is up."""
    for vk, generation in _swallowed_snapshot().items():
        state = sampled.get(vk)
        if state is None:
            continue
        pressed = bool(state & 1)
        if before is not None and before.get(vk) != generation:
            if not pressed:
                sampled[vk] = 0
            continue
        if not state & 0x8000 or pressed:
            with _swallow_lock:  # retire only the record this sample saw
                if _swallowed_up.get(vk) == generation:
                    del _swallowed_up[vk]
        else:
            sampled[vk] = 0
    return sampled


def start_swallow() -> bool:
    if ctypes is None or not _swallow_enabled():
        return False
    if _hook.thread is not None and _hook.thread.is_alive():
        return True
    _hook.running = True
    _hook.thread = threading.Thread(target=_hook_loop, name='a11y_keys_hook', daemon=True)
    _hook.thread.start()
    return True


def stop_swallow() -> None:
    _hook.running = False


def swallow_status() -> str:
    if not _swallow_enabled():
        return 'off'
    if _hook.thread is None or not _hook.thread.is_alive():
        return 'not running'
    return 'installed' if _hook.handle else 'armed (hold Ctrl+Alt in the game)'


# Modal chord navigation: intent -> the owner's own navigation message.
_MODAL_CHORD_MESSAGES = {
    'prev': 'A11yNavPrev',
    'next': 'A11yNavNext',
    'prevcat': 'A11yArrowLeft',
    'nextcat': 'A11yArrowRight',
    'open': 'A11yActivate',
    'back': 'A11yDismiss',
    'repeat': 'A11yRepeat',
}
_INFO_CHORD_MESSAGES = {'mood': 'A11yMood', 'wants': 'A11yWants', 'needs': 'A11yNeeds'}
_BUILD_CHORDS = ('buildtoggle', 'buildgrid', 'buildplace')
_MODAL_HELP = (
    'Dialog. Up and down move between controls. '
    'Enter activates. Backspace goes back when allowed. '
    'R reads the current control. Tab moves between controls. Escape closes. '
    'Hold Control and Alt for scanner navigation keys.'
)


def _chord_before_owner(intent: str) -> bool:
    """Chords that only a text field owns (or, for repeat, any owner);
    True when the intent was one of them."""
    if intent == 'scannermode':
        if not _editing_active:
            _post(_toggle_scanner_mode, _state.input_epoch)
        return True
    if intent == 'repeat' and (_modal_active or _editing_active):
        _send('A11yRepeat')
        return True
    if intent == 'logging':
        # Like the settings row; only a text field owns the key.
        if not _editing_active and not _post(_toggle_logging):
            _toggle_logging()
        return True
    if intent == 'clock':
        # The clock text is the client's own; dialogs and panels do not
        # change it. Only a text field owns the key.
        if not _editing_active:
            request_clock()
        return True
    return False


def _switch_sim_chord(index: int) -> None:
    # Direct household switch (glaroc's Ctrl+Alt+digit). Never while
    # typing, and never behind a dialog, picker or focused panel: a
    # switch would pull the context out from under it (Codex review).
    if _editing_active:
        return
    if _modal_active:
        speech.speak('Close the current panel or dialog before switching Sims.', explicit=True)
        return
    if not _post(_switch_household_sim, index, _state.input_epoch):
        speech.speak(lang.t('no lot loaded'), explicit=True)


def _window_chord(intent: str) -> bool:
    """Chords that open a window or read something out; True when handled."""
    if intent == 'phone':
        if _editing_active or blocking_active():
            speech.speak(_PHONE_BLOCKED, explicit=True)
        else:
            _post(_open_waiting_checked)
        return True
    if intent.startswith('sim') and intent[3:].isdigit():
        _switch_sim_chord(int(intent[3:]))
        return True
    opener = _PICKER_CHORDS.get(intent)
    if opener is not None:
        # Native pickers, like the accessibility menu: never over a
        # dialog, picker or focused panel, and never while typing.
        if _modal_active:
            speech.speak(lang.t('close the dialog first'), explicit=True)
        elif not _post(opener, intent, _state.input_epoch):
            speech.speak(lang.t('no lot loaded'), explicit=True)
        return True
    message = _INFO_CHORD_MESSAGES.get(intent)
    if message is not None:
        if not _modal_active:
            _send(message)
        return True
    return False


def _modal_chord(intent: str) -> None:
    message = _MODAL_CHORD_MESSAGES.get(intent)
    if message is not None:
        _log_route('routed to the modal owner', intent)
        _send_owned(message)
    # Scanner rescan/category actions must not reach the world
    # behind a dialog. Help remains available without changing it.
    elif intent == 'help':
        speech.speak(_MODAL_HELP, explicit=True)


def _route_chord(intent: str) -> None:
    if _chord_before_owner(intent):
        return
    if _editing_active and not _modal_active:
        return
    if _window_chord(intent):
        return
    if intent in _BUILD_CHORDS and not _editing_active:
        # Catalog keyboard browsing also owns a scope. Let the native
        # Build handler distinguish it from an actual modal dialog.
        _post(_world_chord, intent, _state.input_epoch)
        return
    if _modal_active:
        _modal_chord(intent)
        return
    args = (intent, _state.input_epoch, True) if _chord_repeating else (intent, _state.input_epoch)
    if _post(_world_chord, *args):
        _log_route('queued for world handling', intent)
    else:
        _log_route('lost: simulation pump inactive', intent)


def _chord(intent: str) -> None:
    """An active modal owns navigation before the world scanner."""
    try:
        _route_chord(intent)
    except Exception:
        simsight_log.error(f'keys: chord {intent}')


def _repeat_chord(intent: str) -> None:
    """A chord's auto-repeat: _chord sees _chord_repeating while it runs."""
    global _chord_repeating  # noqa: PLW0603 - a module attribute simsight_mackeys sets too
    _chord_repeating = True
    try:
        _chord(intent)
    finally:
        _chord_repeating = False


def _toggle_logging() -> bool:
    """Ctrl+Alt+L: the settings window's Logging row, toggled and saved
    the same way, then its new state spoken."""
    import simsight_menu as menu

    try:
        row = next(item for item in menu.ROWS if item.key == 'logging')
        menu._change(row, 1)
    except Exception:
        simsight_log.error('keys: logging toggle')
        return False
    speech.speak(
        '{}: {}'.format(lang.t('logging'), lang.t('on') if simsight_log.enabled else lang.t('off')),
        explicit=True,
    )
    return True


def _owner_changed(intent: str, epoch: int) -> bool:
    """Simulation thread: a dialog, picker or text field may have opened
    between the key press and this pump slot (ownership re-checked)."""
    if epoch != _state.input_epoch or _editing_active or _modal_active:
        _log_route('dropped: owner changed before the pump turn', intent)
        return True
    return False


def _open_simology(intent: str, epoch: int) -> bool:
    if _owner_changed(intent, epoch):
        return False
    import simsight_simology as simology

    if intent == 'milestones':
        if not simology.milestones_available():
            speech.speak(lang.t('needs growing together'), explicit=True)
            return False
        return bool(simology.milestones_window())
    if not simology.values_available():
        speech.speak(lang.t('needs parenthood'), explicit=True)
        return False
    return bool(simology.values_window())


def _open_catalog(intent: str, epoch: int) -> bool:
    if _owner_changed(intent, epoch):
        return False
    import simsight_catalog as catalog

    return bool(catalog.open_tree() if intent == 'catalog' else catalog.find())


def _open_events(intent: str, epoch: int) -> bool:
    if _owner_changed(intent, epoch):
        return False
    return bool(simsight_hub.events_window())


def _open_queue(intent: str, epoch: int) -> bool:
    if _owner_changed(intent, epoch):
        return False
    return bool(simsight_hub.action_queue())


# Chords that open a native picker: intent -> opener(intent, epoch) on the pump.
_PICKER_CHORDS: dict[str, Callable[[str, int], bool]] = {
    'milestones': _open_simology,  # Pack Simology readers
    'values': _open_simology,
    'catalog': _open_catalog,
    'catalogfind': _open_catalog,
    'events': _open_events,
    'queue': _open_queue,
}


def _switch_household_sim(index: int, epoch: int) -> bool:
    """Simulation thread. index is 1-based in the client's selectable order.
    Ownership is re-checked here: a dialog or panel may have taken focus
    between the key press and this pump slot (Codex review)."""
    if epoch != _state.input_epoch or _editing_active or _modal_active:
        return False
    try:
        import services

        manager = services.client_manager()
        client = manager.get_first_client() if manager is not None else None
        sims = list(client.selectable_sims) if client is not None else []
    except Exception:
        simsight_log.error('keys: household lookup')
        sims = []
    if not 1 <= index <= len(sims):
        speech.speak(lang.t('sim unavailable'), explicit=True)
        return False
    return bool(simsight_hub.select_sim(sims[index - 1].sim_id))


def _open_waiting_checked() -> bool:
    """Simulation thread. The owner can change between the key and this
    pump turn; a dialog, picker or text field that opened meanwhile must
    not see the recipient switch inside open_waiting (Codex review)."""
    if _editing_active or blocking_active():
        _log_route('dropped: a blocking owner opened before the pump turn', 'phone')
        speech.speak(_PHONE_BLOCKED, explicit=True)
        return False
    import simsight_phone as phone

    return bool(phone.open_waiting(True))


def request_clock() -> bool:
    """Ask the client for its clock widget's text. The answer arrives on
    simsight.clock_enc: the localized day and time (with Paused), or empty when
    the widget is not live, which is spoken as unavailable. A dedicated
    reply, so a pointer hover over the clock is never taken for it."""
    _send('A11yClock')
    return True


def _world_chord_dropped(intent: str, epoch: int) -> bool:
    if epoch != _state.input_epoch:
        _log_route(_DROPPED_EPOCH, intent)
        return True
    if _editing_active:
        _log_route('dropped: a text field has focus', intent)
        return True
    if _modal_active and intent not in _BUILD_CHORDS:
        _log_route('dropped: a modal owner opened before the pump turn', intent)
        return True
    return False


def _mode_chord(intent: str, build: Any, grid: Any) -> bool:
    """Chords that switch a mode or act whatever mode is on; True when handled."""
    if intent == 'buildgrid':
        build.aim_grid()
    elif intent == 'buildtoggle':
        # Module entry points validate their destination before retiring the
        # other mode, so a failed request cannot disable a working cursor.
        build.toggle()
    elif intent == 'gridtoggle':
        grid.toggle()
    elif intent == 'buildplace':
        grid.placement_toggle()
    elif intent == 'help':
        simsight_hub.open_hub()
    elif intent in ('layerup', 'layerdown', 'roomlabel'):
        # Build cursor only; elsewhere these keys must not reach the scanner.
        if grid.build_cursor():
            grid.on_key(intent)
        else:
            speech.speak('Turn on the Build cursor with Control Alt B first.', explicit=True)
    elif intent in ('scopeprev', 'scopenext'):
        import simsight_scanner as scanner

        if not settings.get('scanner', True):
            speech.speak('Scanner is off.', explicit=True)
        else:
            scanner.cycle_range(1 if intent == 'scopenext' else -1)
    else:
        return False
    return True


def _live_chord(intent: str, build: Any, repeat: bool) -> None:
    """No Build or grid mode is on: repeat, or the scanner."""
    if intent == 'repeat':
        _send('A11yRepeat')
        return
    # Native transaction commands have no scanner meaning when the cursor
    # is off. They must never become unrelated actions in Live mode.
    if intent in ('rotateleft', 'rotateright', 'undo', 'redo', 'sell'):
        if not build.controls_enabled() and build._zone() is not None:
            build.refuse_disabled()  # in Build mode, say why nothing happened
        return
    if repeat and intent in ('prevcat', 'nextcat'):
        return  # holding Left/Right must not walk several tree levels
    if settings.get('scanner', True):
        import simsight_scanner as scanner

        scanner.on_key(intent, on_simulation_thread=True)


def _world_chord(intent: str, epoch: int, repeat: bool = False) -> None:
    """Resolve world modes on the simulation thread, after ownership checks."""
    if _world_chord_dropped(intent, epoch):
        return
    import simsight_build as build
    import simsight_grid as grid

    if _mode_chord(intent, build, grid):
        return
    if build.active() or build.aiming():
        build.on_key(intent)
        return
    if grid.active():
        grid.on_key(intent)
        return
    _live_chord(intent, build, repeat)


def _game_is_foreground(user32: Any, kernel32: Any) -> bool:
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return bool(pid.value == kernel32.GetCurrentProcessId())
    except _CTYPES_ERRORS:
        return False  # no foreground window to check: treat the game as behind


def _tab_message(shift: bool) -> str | None:
    if shift and not _modal_active:
        # With no menu open Shift+Tab is the game's own first-person
        # camera switch; Flash lets it through too. Menus keep it.
        return None
    return 'A11yTabPrev' if shift else 'A11yTabNext'


def _owned_key_message(vk: int, shift: bool) -> str | None:
    """A bare key the Build cursor or scanner mode owns right now."""
    if vk in BUILD_GRID_KEYS and _build_grid_routing() and (not shift or vk == 0x52):
        return 'A11yBuildGrid:' + ('turnback' if shift else BUILD_GRID_KEYS[vk])
    if vk in SCANNER_KEYS and not shift and _scanner_routing():
        return SCANNER_KEYS[vk]
    return None


def _modal_key_message(vk: int, shift: bool) -> str | None:
    """A bare key while one of our UI owners has focus."""
    if vk == 0x1B:
        # Flash checks whether its native guard actually owned this press.
        # The world Escape may already have opened Options by this poll.
        return 'A11yEscape'
    if settings.get('numpad_navigation', False) and vk in NUMPAD_BINDINGS:
        return NUMPAD_BINDINGS[vk]
    if _editing_active or shift:
        return None
    return next(
        (message for message, (_label, combos) in BINDINGS.items() if (vk, False) in combos),
        None,
    )


def _plain_message(
    vk: int, ctrl: bool = False, alt: bool = False, shift: bool = False
) -> str | None:
    """One policy for native context, editing and optional numpad navigation."""
    if not _enabled:
        return None
    if vk == 0x4E and ctrl and shift and not alt and not _modal_active and not _editing_active:
        return 'A11yNeeds'
    if ctrl or alt:
        return None
    if vk == 0x09 and (not _editing_active or _modal_active):
        return _tab_message(shift)
    owned = _owned_key_message(vk, shift)
    if owned is not None:
        return owned
    if not _modal_active:
        return None
    return _modal_key_message(vk, shift)


def _log_fire(intent: str, source: str) -> None:
    """One line per chord press (repeats excluded). The route line is
    throttled to one per second, which hides a double or a lost press;
    this one is not. source: edge (the hook saw the press), hook (the
    hook's key state), held (Windows' key state) or tap (a press bit)."""
    simsight_log.line(f'keys: chord {intent} fired ({source})')


def _key_reader(user32: Any) -> Callable[[int], int]:
    """GetAsyncKeyState for the poller's sample through a PyDLL handle,
    which keeps the GIL during each call, so one pass reads the modifiers and
    every key in a burst of well under a millisecond instead of waiting for
    the GIL once per call (up to ~50 waits a pass under load). Only for a
    real user32 handle; the hook keeps its WinDLL calls. Falls back to the
    ordinary call."""
    try:
        if isinstance(user32, ctypes.CDLL):
            reader = ctypes.PyDLL('user32').GetAsyncKeyState
            reader.argtypes = [ctypes.c_int]
            reader.restype = ctypes.c_short
            return reader
    except Exception:
        simsight_log.error('keys: GIL-holding key reader')
    return user32.GetAsyncKeyState


def _write_hook_trace() -> None:
    """Diagnostic: the hook's chord-key events since the last poll."""
    while _hook_trace:
        vk, down, seen, gate, late = _hook_trace.popleft()
        simsight_log.line(
            'keys: trace hook {} {} (down seen {}) gate {} late {} ms'.format(
                CHORD_NAMES.get(vk, hex(vk)),
                'down' if down else 'up',
                int(bool(seen)),
                int(bool(gate)),
                late,
            )
        )


@dataclass
class _Sample:
    """One poll pass: the modifiers and every key, read back to back."""

    now: float
    epoch: int  # the input epoch when the pass began
    hook_known_before: frozenset[int]  # keys the hook had reported before the read
    ctrl: bool
    alt: bool
    shift: bool
    shift_tapped: bool
    sampled: dict[int, int]  # vk -> GetAsyncKeyState, swallowed releases masked
    chord: bool  # Ctrl+Alt held, no Shift, not AltGr typing
    chord_end: bool  # diagnostic: Ctrl+Alt still held after the read
    tracing: bool
    hook_alive: bool


class _Poller:
    """The Windows key poller (its own thread): presses, repeats, releases."""

    def __init__(self, user32: Any, kernel32: Any) -> None:
        self.user32 = user32
        self.kernel32 = kernel32
        self.read_key = _key_reader(user32)
        self.was_down: dict[KeyId, bool] = {}
        self.pressed_at: dict[KeyId, float] = {}
        self.last_fire: dict[KeyId, float] = {}
        self.recovering_focus = False
        self.focus_blocked: set[int] = set()
        self.editing_prev = False
        self.last_lease = 0.0
        plain = {vk for _label, combos in BINDINGS.values() for vk, _alt in combos}
        plain.update(NUMPAD_BINDINGS)
        plain.update(BUILD_GRID_KEYS)
        plain.add(0x4E)
        self.plain = plain
        self.all_keys = plain | set(CHORDS)
        self.chord_seen_at = -10.0
        self.shift_before = False

    def run(self) -> None:
        simsight_log.line('keys: contextual polling started')
        while _state.running:
            if self._safe_poll():
                time.sleep(_POLL_SECONDS)
        simsight_log.line('keys: polling stopped')

    def _safe_poll(self) -> bool:
        """One pass; False when it already waited (the game is not in front)."""
        try:
            return self.poll()
        except Exception:
            simsight_log.error('keys: poll error')
            time.sleep(1.0)
            return True

    def poll(self) -> bool:
        if not _enabled or not _game_is_foreground(self.user32, self.kernel32):
            self._focus_left()
            time.sleep(0.05)
            return False
        now = time.monotonic()
        # A text field that just closed (cheat console on Enter) leaves the
        # submitting key's tap latch behind; if this poll lands after Flash
        # clears the editing flag, that tap would activate whatever we focus
        # next (settings row, HUD button). Retire it once, here.
        editing_ended = self.editing_prev and not _editing_active
        self.editing_prev = _editing_active
        if editing_ended:
            fence_opening_input()
        self._lease(now)
        sample = self._sample(now)
        if self.recovering_focus:
            self._recover_focus(sample)
        self._hook_edges(now)
        for vk, (intent, repeatable) in CHORDS.items():
            self._chord_key(sample, vk, intent, repeatable)
        for vk in self.plain:
            self._plain_key(sample, vk, editing_ended)
        return True

    def _focus_left(self) -> None:
        """Release native guards when focus leaves the game. Never replay
        background keystrokes on return."""
        held = [
            CHORD_NAMES.get(vk, hex(vk))
            for (kind, vk), down in self.was_down.items()
            if kind == 'chord' and down
        ]
        if held and _enabled:
            # Diagnostic: a key still held when focus returns counts
            # as a new press (an unexplained double toggle, 2026-09-26).
            simsight_log.line(
                'keys: focus left the game with chord key {} held'.format('+'.join(sorted(held)))
            )
        for vk in self.plain:
            if self.was_down.get(('plain', vk), False):
                _send(f'A11y|keyup|{vk}')
        self.was_down.clear()
        self.pressed_at.clear()
        self.last_fire.clear()
        _hook_edges.clear()
        _hook_trace.clear()
        _opening_keys.clear()
        self.recovering_focus = True

    def _lease(self, now: float) -> None:
        if now - self.last_lease < 0.5:
            return
        _send('A11y|lease')
        try:
            _post(_announce_ui_settings)
        except Exception:
            simsight_log.error('keys: settings heartbeat')
        self.last_lease = now

    def _sample(self, now: float) -> _Sample:
        # Snapshots first (the swallow snapshot takes a lock and may
        # wait), then the modifiers and every key back to back through
        # read_key. With ~50 GIL waits a pass, a chord key's press bit
        # could be read with Ctrl+Alt already down but seen up (live
        # 2026-09-26: 2 of 10 fast Ctrl+Alt+B lost). The burst is much
        # shorter, not atomic: Windows input can still change between
        # two calls, only the window is now microseconds.
        read_key = self.read_key
        epoch = _state.input_epoch
        swallowed_before = _swallowed_snapshot()
        hook_known_before = frozenset(_hook_state)
        alt = bool(read_key(VK_MENU) & 0x8000)
        ctrl = bool(read_key(VK_CONTROL) & 0x8000)
        shift_state = read_key(VK_SHIFT)
        shift = bool(shift_state & 0x8000)
        # A fast Shift+Tab can release Shift before this pass reads Tab's
        # press bit (live 2026-09-26: the game's first-person switch also
        # opened the HUD). A key seen only as a tap counts as shifted when
        # Shift was down at the last pass or was pressed since.
        shift_tapped = shift or self.shift_before or bool(shift_state & 1)
        self.shift_before = shift
        # Read each key once: GetAsyncKeyState's low bit records a short
        # tap since the last query and must not be drained by two loops.
        raw = {vk: read_key(vk) for vk in self.all_keys}
        chord = ctrl and alt and not shift and not _altgr_pressed(self.user32)
        sampled = _mask_swallowed_releases(raw, swallowed_before)
        # Diagnostic only, and only while the log is on: one pass makes
        # about 50 ctypes calls, each releasing the GIL, so under load it
        # may span a Ctrl or Alt change (live 2026-09-26, 2 of 5 fast
        # chords lost). A second modifier read after the sample shows
        # whether a press bit read with Ctrl+Alt up had them held a
        # moment later. It decides nothing: chords use the first read.
        tracing = simsight_log.enabled
        chord_end = tracing and (
            bool(read_key(VK_CONTROL) & 0x8000)
            and bool(read_key(VK_MENU) & 0x8000)
            and not (read_key(VK_SHIFT) & 0x8000)
            and not _altgr_pressed(self.user32)
        )
        if chord or chord_end:
            self.chord_seen_at = now
        if tracing:
            _write_hook_trace()
        else:
            _hook_trace.clear()
        hook_alive = _hook.handle is not None and (now - _hook.last_event) < 1.0
        return _Sample(
            now,
            epoch,
            hook_known_before,
            ctrl,
            alt,
            shift,
            shift_tapped,
            sampled,
            chord,
            chord_end,
            tracing,
            hook_alive,
        )

    def _recover_focus(self, sample: _Sample) -> None:
        """The first sample belongs to the focus boundary: neither its tap
        bits nor queued hook events prove an in-game press. Require held
        keys to be released before accepting them again."""
        _hook_edges.clear()
        self.focus_blocked.clear()
        for vk in self.all_keys:
            sample.sampled[vk] &= 0x8000
            held = (
                bool(_hook_state[vk])
                if sample.hook_alive and vk in _hook_state
                else bool(sample.sampled[vk] & 0x8000)
            )
            if held:
                self.focus_blocked.add(vk)
        self.recovering_focus = False

    def _hook_edges(self, now: float) -> None:
        """Chord presses and releases the hook saw between two samples."""
        while _hook_edges:
            vk, down, edge_time = _hook_edges.popleft()
            key = ('chord', vk)
            if vk in self.focus_blocked:
                if not down:
                    self.focus_blocked.discard(vk)
                self.was_down[key] = down
                continue
            if (
                down
                and vk not in _opening_keys
                and (not self.was_down.get(key, False) or key not in self.pressed_at)
            ):
                _log_fire(CHORDS[vk][0], 'edge')
                _chord(CHORDS[vk][0])
                self.pressed_at[key] = edge_time
                self.last_fire[key] = now
            self.was_down[key] = down

    def _still_blocked(self, vk: int, key: KeyId, held: bool) -> bool:
        """A key held across the return of focus stays ignored until released."""
        if vk not in self.focus_blocked:
            return False
        if not held:
            self.focus_blocked.discard(vk)
            return False
        self.was_down[key] = True
        return True

    def _repeat_due(self, key: KeyId, now: float) -> bool:
        return (
            now - self.pressed_at.get(key, now) >= _REPEAT_DELAY
            and now - self.last_fire.get(key, 0) >= _REPEAT_INTERVAL
        )

    def _trace_late_tap(self, sample: _Sample, vk: int, raw_tap: bool) -> None:
        if (
            sample.tracing
            and raw_tap
            and not sample.chord
            and not _editing_active
            and sample.now - self.chord_seen_at < 0.5
        ):
            simsight_log.line(
                f'keys: trace {CHORD_NAMES.get(vk, hex(vk))} press read with Ctrl+Alt up (held after the read: {int(sample.chord_end)})'
            )

    def _chord_key(self, sample: _Sample, vk: int, intent: str, repeatable: bool) -> None:
        key = ('chord', vk)
        now = sample.now
        raw_down = bool(sample.sampled[vk] & 0x8000)
        raw_tap = bool(sample.sampled[vk] & 1)
        hook_knows_key = sample.hook_alive and vk in _hook_state
        if sample.chord and raw_down and _hook.handle is not None and not hook_knows_key:
            _hook.missed = now
        # A consumed key-up may not reach GetAsyncKeyState. Its
        # stale high bit must not restart a released scanner chord.
        held = bool(_hook_state[vk]) if hook_knows_key else raw_down
        if self._still_blocked(vk, key, held):
            return
        down = sample.chord and held
        # The transient hook may not be installed yet. Preserve a
        # complete letter/arrow tap while Ctrl+Alt are still held,
        # just as plain navigation preserves taps between polls.
        # A live hook's release wins over stale Windows state, but
        # only for a key the hook knew before this sample: a press
        # read before the hook consumed its release is still a press.
        # A fresh tap/observed hook edge can recover a stale held bit,
        # but the held bit alone cannot start a second shortcut.
        fresh_tap = raw_tap and not (hook_knows_key and vk in sample.hook_known_before)
        pressed = down or (sample.chord and fresh_tap)
        self._trace_late_tap(sample, vk, raw_tap)
        if (
            pressed
            and vk not in _opening_keys
            and (not self.was_down.get(key, False) or (fresh_tap and key not in self.pressed_at))
        ):
            _log_fire(intent, ('hook' if hook_knows_key else 'held') if down else 'tap')
            _chord(intent)
            self.pressed_at[key] = now
            self.last_fire[key] = now
        elif down and repeatable and self._repeat_due(key, now):
            _repeat_chord(intent)
            self.last_fire[key] = now
        # Modifier changes are not new letter/arrow presses. Keep
        # tracking the key while Ctrl+Alt are up; otherwise an old H
        # high bit becomes another Help shortcut on every chord.
        self.was_down[key] = held
        if not sample.chord:
            self.pressed_at.pop(key, None)
            self.last_fire.pop(key, None)

    def _plain_key_in_chord(self, sample: _Sample, vk: int, key: KeyId, held: bool) -> None:
        # Holding Enter after releasing Ctrl/Alt cannot activate twice.
        # A key released while modifiers remain held must also
        # release its native guard. A swallowed hook key-up can
        # leave GetAsyncKeyState stale, so a live hook wins.
        tapped = bool(sample.sampled[vk] & 1)
        chord_held = bool(_hook_state[vk]) if sample.hook_alive and vk in _hook_state else held
        if not chord_held and (self.was_down.get(key, False) or tapped):
            _send(f'A11y|keyup|{vk}')
        self.was_down[key] = chord_held

    def _plain_key(self, sample: _Sample, vk: int, editing_ended: bool) -> None:
        key = ('plain', vk)
        now = sample.now
        held = bool(sample.sampled[vk] & 0x8000)
        tapped = bool(sample.sampled[vk] & 1)
        if self._still_blocked(vk, key, held):
            return
        if (
            vk in _ACTIVATION_KEYS
            and not held
            and (editing_ended or sample.epoch != _state.input_epoch)
        ):
            # Tap sampled across a surface change (editing ended, or a
            # fence bumped the epoch since sampling): retire it.
            self.was_down[key] = False
            return
        down = held or tapped
        message = _plain_message(
            vk, sample.ctrl, sample.alt, sample.shift if held else sample.shift_tapped
        )
        if vk in _opening_keys:
            # Consume the opener's key and its release, then allow
            # a new physical press. Never synthesize another click.
            if not held:
                _opening_keys.discard(vk)
                _send(f'A11y|keyup|{vk}')
            self.was_down[key] = held
            return
        if sample.chord:
            self._plain_key_in_chord(sample, vk, key, held)
            return
        if down and not self.was_down.get(key, False):
            if message:
                _send(message)
            self.pressed_at[key] = now
            self.last_fire[key] = now
        elif held and message is not None and message in _REPEATABLE and self._repeat_due(key, now):
            _send(message)
            self.last_fire[key] = now
        if not held and (self.was_down.get(key, False) or tapped):
            _send(f'A11y|keyup|{vk}')
        self.was_down[key] = held


def _loop() -> None:
    """The poller thread."""
    try:
        user32, kernel32 = _dlls()
        _Poller(user32, kernel32).run()
    except Exception:
        simsight_log.error('keys: poller thread')


def start() -> bool:
    if sys.platform == 'darwin':
        # macOS port: the key helper reads the keyboard (simsight_mackeys).
        return True
    if ctypes is None:
        simsight_log.line(f'keys: ctypes unavailable ({_ctypes_error}), keyboard navigation off')
        return False
    if _state.thread is not None and _state.thread.is_alive():
        return True
    _state.running = True
    _state.thread = threading.Thread(target=_loop, name='a11y_keys', daemon=True)
    _state.thread.start()
    start_swallow()
    return True


def stop() -> None:
    _state.running = False
    stop_swallow()


def _set_enabled(on: bool) -> None:
    global _enabled  # noqa: PLW0603 - a module attribute other modules read and set
    _enabled = on


def _keys_listing(output: Callable[[str], object]) -> None:
    for label, combos in BINDINGS.values():
        output('   {}: {}'.format(label, ' or '.join(_describe(vk, alt) for (vk, alt) in combos)))
    for vk, (intent, _rep) in CHORDS.items():
        output(f'   {intent}: Ctrl+Alt+{CHORD_NAMES.get(vk, hex(vk))}')
    output('   needs: Ctrl+Shift+N; accessibility menu: Ctrl+Alt+H')


@sims4.commands.Command('simsight.keys', 'a11y.keys', command_type=sims4.commands.CommandType.Live)
@real_annotations
def a11y_keys(mode: str = 'on', _connection: Any = None) -> bool:
    try:
        output = sims4.commands.CheatOutput(_connection)
        if mode.lower() in ('off', 'false', '0'):
            _set_enabled(False)
            settings.set('keyboard', False)
            _send('A11yKeyboardOff')
            output('simsight: keyboard navigation OFF')
            speech.speak('Keyboard navigation off.', explicit=True)
            return True
        _set_enabled(True)
        settings.set('keyboard', True)
        _send('A11yKeyboardOn')
        started = start()
        output('simsight: keyboard navigation {}'.format('ON' if started else 'UNAVAILABLE'))
        _keys_listing(output)
        speech.speak('Keyboard navigation on.', explicit=True)
        return True
    except Exception:
        simsight_log.error('keys: keys command')
        return False


def _describe(vk: int, needs_alt: bool) -> str:
    for name, code in _KEY_NAMES.items():
        if code == vk:
            return ('Alt+' if needs_alt else '') + name
    return ('Alt+' if needs_alt else '') + hex(vk)


@sims4.commands.Command(
    'simsight.keyprobe', 'a11y.keyprobe', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_keyprobe(mode: str = 'off', _connection: Any = None) -> bool:
    """Optional, non-consuming native/stage input diagnostics for one minute."""
    value = mode.lower()
    if value not in ('on', 'off'):
        return False
    try:
        _send('A11yKeyProbeOn' if value == 'on' else 'A11yKeyProbeOff')
        sims4.commands.CheatOutput(_connection)(
            'simsight: key probe {} requested{}'.format(
                value, ' for 60 seconds (key/modifier metadata only)' if value == 'on' else ''
            )
        )
    except Exception:
        simsight_log.error('keys: keyprobe command')
        return False
    return True


try:
    if _enabled:
        start()
    else:
        simsight_log.line('keys: keyboard navigation disabled by settings')
except Exception:
    simsight_log.error('keys: autostart')

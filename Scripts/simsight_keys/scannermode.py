"""Scanner mode and the Build cursor, the two owners of bare keys outside
dialogs, and the settings notices every UI copy must hear again."""

from __future__ import annotations

import importlib
import time
from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_settings as settings
import simsight_speech as speech

from .state import _DROPPED_EPOCH, _bit, _deliver, _log_route, _post, _state, status
from .winapi import fence_opening_input

if TYPE_CHECKING:
    from zone import Zone

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


def announce_ui_settings() -> None:
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
        _deliver('A11y|scannermode|' + ('on' if scanner_effective() else 'off'))
    except Exception:
        simsight_log.error('keys: scanner mode notice')


def scanner_effective() -> bool:
    """The scanner mode setting counts only while keyboard mode and the
    scanner are on."""
    return (
        bool(settings.get('scanner_mode', False))
        and status.enabled
        and bool(settings.get('keyboard', True))
        and bool(settings.get('scanner', True))
    )


def _build_grid_routing() -> bool:
    return status.build_grid_owner and not status.modal_active and not status.editing_active


def _scanner_routing() -> bool:
    return (
        scanner_effective()
        and _state.scanner_owner
        and not status.modal_active
        and not status.editing_active
    )


def _in_live() -> bool:
    try:
        import services

        zone: Zone | None = services.current_zone()
        live = zone is not None and not zone.is_in_build_buy
    except Exception:
        simsight_log.error('keys: Live check')
        return False
    return bool(live)


def set_scanner_mode(on: object) -> bool:
    """Simulation thread: persist the scanner mode setting and apply it
    (Ctrl+Alt+Space, the settings menu and simsight.scannermode all come here)."""
    if on and (not status.enabled or not settings.get('scanner', True)):
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
    if not scanner_effective():
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
    if scanner_effective():
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
    report = _bit(state)
    if report not in ('0', '1'):
        return False
    owner = report == '1'
    if owner != status.build_grid_owner:
        simsight_log.line(f'keys: Build cursor owner {int(status.build_grid_owner)}->{int(owner)}')
        status.build_grid_owner = owner
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
        and not status.modal_active
        and not status.editing_active
        and scanner_effective()
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
    if status.modal_active or status.editing_active:
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
    if not scanner_effective():
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
    live = report == '1' and scanner_effective()
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

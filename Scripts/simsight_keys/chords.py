"""Where a Ctrl+Alt chord goes: a text field, a modal owner, a picker or
window, or the world (Build, the grid, the scanner) on the simulation thread."""

from __future__ import annotations

from typing import Any, Callable

import simsight_lang as lang
import simsight_log
import simsight_settings as settings
import simsight_speech as speech
import simsight_hub

from .modal import blocking_active
from .scannermode import _toggle_scanner_mode
from .send import _send_owned, send_action
from .state import _DROPPED_EPOCH, _log_route, _post, _state, status

_PHONE_BLOCKED = 'Close the current panel or dialog before opening a waiting call.'

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
        if not status.editing_active:
            _post(_toggle_scanner_mode, _state.input_epoch)
        return True
    if intent == 'repeat' and (status.modal_active or status.editing_active):
        send_action('A11yRepeat')
        return True
    if intent == 'logging':
        # Like the settings row; only a text field owns the key.
        if not status.editing_active and not _post(_toggle_logging):
            _toggle_logging()
        return True
    if intent == 'clock':
        # The clock text is the client's own; dialogs and panels do not
        # change it. Only a text field owns the key.
        if not status.editing_active:
            request_clock()
        return True
    return False


def _switch_sim_chord(index: int) -> None:
    # Direct household switch (glaroc's Ctrl+Alt+digit). Never while
    # typing, and never behind a dialog, picker or focused panel: a
    # switch would pull the context out from under it (Codex review).
    if status.editing_active:
        return
    if status.modal_active:
        speech.speak('Close the current panel or dialog before switching Sims.', explicit=True)
        return
    if not _post(_switch_household_sim, index, _state.input_epoch):
        speech.speak(lang.t('no lot loaded'), explicit=True)


def _window_chord(intent: str) -> bool:
    """Chords that open a window or read something out; True when handled."""
    if intent == 'phone':
        if status.editing_active or blocking_active():
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
        if status.modal_active:
            speech.speak(lang.t('close the dialog first'), explicit=True)
        elif not _post(opener, intent, _state.input_epoch):
            speech.speak(lang.t('no lot loaded'), explicit=True)
        return True
    message = _INFO_CHORD_MESSAGES.get(intent)
    if message is not None:
        if not status.modal_active:
            send_action(message)
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
    if status.editing_active and not status.modal_active:
        return
    if _window_chord(intent):
        return
    if intent in _BUILD_CHORDS and not status.editing_active:
        # Catalog keyboard browsing also owns a scope. Let the native
        # Build handler distinguish it from an actual modal dialog.
        _post(_world_chord, intent, _state.input_epoch)
        return
    if status.modal_active:
        _modal_chord(intent)
        return
    args = (
        (intent, _state.input_epoch, True)
        if _state.chord_repeating
        else (intent, _state.input_epoch)
    )
    if _post(_world_chord, *args):
        _log_route('queued for world handling', intent)
    else:
        _log_route('lost: simulation pump inactive', intent)


def run_chord(intent: str) -> None:
    """An active modal owns navigation before the world scanner."""
    try:
        _route_chord(intent)
    except Exception:
        simsight_log.error(f'keys: chord {intent}')


def repeat_chord(intent: str) -> None:
    """A chord's auto-repeat: run_chord sees _state.chord_repeating while it runs."""
    _state.chord_repeating = True
    try:
        run_chord(intent)
    finally:
        _state.chord_repeating = False


def _toggle_logging() -> bool:
    """Ctrl+Alt+L: the settings window's Logging row, toggled and saved
    the same way, then its new state spoken."""
    import simsight_menu as menu

    try:
        row = next(item for item in menu.ROWS if item.key == 'logging')
        menu.step_value(row, 1)
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
    if epoch != _state.input_epoch or status.editing_active or status.modal_active:
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
    if epoch != _state.input_epoch or status.editing_active or status.modal_active:
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
    if status.editing_active or blocking_active():
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
    send_action('A11yClock')
    return True


def _world_chord_dropped(intent: str, epoch: int) -> bool:
    if epoch != _state.input_epoch:
        _log_route(_DROPPED_EPOCH, intent)
        return True
    if status.editing_active:
        _log_route('dropped: a text field has focus', intent)
        return True
    if status.modal_active and intent not in _BUILD_CHORDS:
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
        send_action('A11yRepeat')
        return
    # Native transaction commands have no scanner meaning when the cursor
    # is off. They must never become unrelated actions in Live mode.
    if intent in ('rotateleft', 'rotateright', 'undo', 'redo', 'sell'):
        if not build.controls_enabled() and build.build_zone() is not None:
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

"""Flash switches (keyboard, simplified menus), speech mode, tooltip
delay, the UI mark and the push at every zone load."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_log
import simsight_patch
import simsight_settings as settings
import simsight_speech as speech

from .base import _boundary, live_command
from .state import _tooltip_delay

if TYPE_CHECKING:
    from .base import Output
    from zone import Zone


def _send_ui_message(message: str) -> None:
    import distributor.ops
    import distributor.system

    distributor.system.Distributor.instance().add_op_with_no_owner(
        distributor.ops.SendUIMessage(message)
    )


def _push_autofocus() -> None:
    """Synchronize the keyboard master switch with Flash on load and mark.
    Flash starts with capture off until Python supplies the user's setting.
    The old auto_focus cfg value is retained only for file compatibility."""
    try:
        state = bool(settings.get('keyboard', True))
        _send_ui_message('A11yKeyboardOn' if state else 'A11yKeyboardOff')
    except Exception:
        simsight_log.error('autofocus push')
    push_simplified_menus()


def push_simplified_menus() -> bool:
    """Tell every live UI copy the Simplified menus switch. Flash starts with
    it on (the default) until Python says otherwise, so it goes out with the
    keyboard switch at zone load, mark and reload, on a settings change and
    with every keyboard lease. Flash acts only when the value changes."""
    try:
        state = bool(settings.get('simplified_menus', True))
        _send_ui_message('A11y|simplifiedmenus|' + ('on' if state else 'off'))
    except Exception:
        simsight_log.error('simplified menus notice')
        return False
    return True


@live_command('simsight.autofocus', 'a11y.autofocus')
def a11y_autofocus(mode: str = '', _connection: Any = None) -> bool:
    """Legacy alias for simsight.keys; focus now follows the keyboard setting."""
    with _boundary('narrator: autofocus'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower()
        if m in ('on', 'true', '1', 'off', 'false', '0'):
            import simsight_keys as keys

            output('simsight: automatic focus is part of keyboard navigation; use simsight.keys.')
            return bool(keys.a11y_keys(m, _connection=_connection))
        output(
            'simsight: keyboard navigation and automatic focus are {}'.format(
                'ON' if settings.get('keyboard', True) else 'OFF'
            )
        )
        output('simsight: use simsight.keys on|off')
        return True
    return False


# Speech mode names from earlier builds.
_LEGACY_SPEECH_MODES = {'talk': 'hover', 'ondemand': 'manual', 'on_demand': 'manual'}


@live_command('simsight.speechmode', 'a11y.speechmode')
def a11y_speechmode(mode: str = '', _connection: Any = None) -> bool:
    """simsight.speechmode hover|manual|beeps|off (also cycled by the speech
    mode key, default Numpad 9 or Insert)."""
    with _boundary('narrator: speechmode'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower().replace('-', '_').replace(' ', '_')
        m = _LEGACY_SPEECH_MODES.get(m, m)
        if m in speech.VALID_MODES:
            speech.set_mode(m)
            output(f'simsight: speech mode {m}')
        else:
            output(f'simsight: speech mode is {speech.modes.speech}')
            output('simsight: usage simsight.speechmode hover|manual|beeps|off')
        return True
    return False


def _set_tooltip_delay(raw: str, output: Output) -> bool:
    if raw in ('off', 'none'):
        value = 0.0
    else:
        try:
            value = float(raw)
        except ValueError:
            output('simsight: usage simsight.tooltipdelay <seconds|off>')
            return False
    value = max(0.0, min(10.0, value))
    settings.set('tooltip_delay', value)
    if value <= 0:
        output('simsight: tooltip delay OFF (descriptions read immediately)')
        speech.speak('Tooltip delay off.', explicit=True)
    else:
        output(f'simsight: tooltip delay {value:g} seconds')
        speech.speak(f'Tooltip delay {value:g} seconds.', explicit=True)
    return True


def _delay_text(delay: float) -> str:
    return 'off' if delay <= 0 else f'{delay:g} seconds'


@live_command('simsight.tooltipdelay', 'a11y.tooltipdelay')
def a11y_tooltipdelay(seconds: str = '', _connection: Any = None) -> bool:
    """simsight.tooltipdelay <seconds|off>: how long a panel row (needs, skills,
    mood, the time panel) holds its description back after reading its name
    and value. Moving the pointer on cancels it; the read key still reads
    everything. off (or 0) reads descriptions immediately."""
    with _boundary('narrator: tooltipdelay'):
        output = sims4.commands.CheatOutput(_connection)
        raw = seconds.strip().lower()
        if raw:
            return _set_tooltip_delay(raw, output)
        output(f'simsight: tooltip delay is {_delay_text(_tooltip_delay())}')
        output('simsight: usage simsight.tooltipdelay <seconds|off>')
        return True
    return False


# ------------------------------------------------------------------ mark and zone


@live_command('simsight.mark', 'a11y.mark')
def a11y_mark(name: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: mark'):
        simsight_log.line(f'MARK: live TooltipManager copy is from "{name}"')
        try:
            import simsight_startup as startup

            startup.note_mark(name)
        except Exception:
            simsight_log.error('narrator: startup mark note')
        _push_autofocus()
        return True
    return False


def _install_zone_hook() -> None:
    """Push Flash-side config at every zone load: the only reliably safe
    moment for SendUIMessage, and it re-applies after zone switches."""
    import zone

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def a11y_narrator_on_loading_screen_animation_finished(
            self: Zone, *args: Any, **kwargs: Any
        ) -> Any:
            result = original(self, *args, **kwargs)
            try:
                _push_autofocus()
            except Exception:
                simsight_log.error('autofocus zone push')
            return result

        return a11y_narrator_on_loading_screen_animation_finished

    simsight_patch.wrap(zone.Zone, 'on_loading_screen_animation_finished', make)

"""Console commands: speak, test, braille, log, reload, captions,
settings and status."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_log
import simsight_settings as settings
import simsight_speech as speech

from .base import _boundary, live_command
from .state import _on_off, _tooltip_delay
from .dialogs import _sim_actions_on
from .switches import _delay_text, _push_autofocus
from .voice import _sapi_summary

if TYPE_CHECKING:
    from .base import Output


@live_command('simsight.speak', 'a11y.speak')
def a11y_speak(*args: Any, _connection: Any = None) -> bool:
    with _boundary('narrator: speak'):
        text = ' '.join(str(part) for part in args).strip()
        if text:
            speech.speak(text, explicit=True)
        return True
    return False


@live_command('simsight.test', 'a11y.test')
def a11y_test(_connection: Any = None) -> bool:
    with _boundary('narrator: test'):
        output = sims4.commands.CheatOutput(_connection)
        backend = speech.init()
        speech.speak(f'Narration test successful. The active backend is {backend}.', explicit=True)
        output(f'simsight: spoke test sentence via backend "{backend}"')
        output(f'simsight: log file at {simsight_log.PATH}')
        return True
    return False


_BRAILLE_MODES = ('concise', 'full', 'only', 'off')


@live_command('simsight.braille', 'a11y.braille')
def a11y_braille(mode: str = '', _connection: Any = None) -> bool:
    """simsight.braille <concise|full|only|off>"""
    with _boundary('narrator: braille'):
        output = sims4.commands.CheatOutput(_connection)
        if mode.lower() not in _BRAILLE_MODES:
            output(f'a11y braille mode: {speech.modes.braille}')
            output(
                'a11y braille display detected: {}'.format('yes' if speech.has_braille() else 'no')
            )
            output('simsight: usage simsight.braille <{}>'.format('|'.join(_BRAILLE_MODES)))
            output('   concise = speak everything, braille the label')
            output('   full    = speak and braille everything')
            output('   only    = braille only, no speech')
            output('   off     = speech only')
            return True
        speech.modes.braille = mode.lower()
        settings.set('braille_mode', speech.modes.braille)
        output(f'simsight: braille mode set to {speech.modes.braille}')
        speech.speak(f'Braille mode {speech.modes.braille}.', explicit=True)
        return True
    return False


@live_command('simsight.log', 'a11y.log')
def a11y_log(mode: str = '', _connection: Any = None) -> bool:
    """simsight.log on|off: utterance/diagnostic log next to the ts4script."""
    with _boundary('narrator: log'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower()
        if m in ('on', 'true', '1'):
            simsight_log.enabled = True
            settings.set('logging', True)
            simsight_log.line('logging enabled via simsight.log')
            output(f'simsight: logging ON -> {simsight_log.PATH}')
            speech.speak('Logging on.', explicit=True)
        elif m in ('off', 'false', '0'):
            simsight_log.line('logging disabled via simsight.log')
            simsight_log.enabled = False
            settings.set('logging', False)
            output('simsight: logging OFF')
            speech.speak('Logging off.', explicit=True)
        else:
            output(
                'simsight: logging is {} (log file: {})'.format(
                    'ON' if simsight_log.enabled else 'OFF', simsight_log.PATH
                )
            )
            output('simsight: usage simsight.log on|off')
        return True
    return False


def _reload_speech(output: Output) -> None:
    """Apply the re-read speech, braille, caption and logging options."""
    if not settings.get('captions', False):
        speech.clear_caption()
    simsight_log.enabled = bool(settings.get('logging', simsight_log.enabled))
    braille = str(settings.get('braille_mode', speech.modes.braille))
    if braille in _BRAILLE_MODES:
        speech.modes.braille = braille
    mode = str(settings.get('speech_mode', 'hover'))
    if mode in speech.VALID_MODES:
        speech.modes.speech = mode
    speech.restart_sapi()
    if speech.retry_screen_reader():
        output('simsight: screen reader detected; speech now routes through it')


def _reload_modules() -> None:
    """Apply the re-read keyboard, object name, travel, Build and scanner options."""
    try:
        import simsight_keys as keys

        keys.status.enabled = bool(settings.get('keyboard', True))
        if keys.status.enabled:
            keys.start()
    except ImportError:
        pass  # module not in this build
    try:
        import simsight_objectnames as objectnames

        objectnames.enable(bool(settings.get('object_names', True)))
    except ImportError:
        pass  # module not in this build
    _push_autofocus()
    try:
        import simsight_travel as travel

        travel.apply_setting()
    except Exception:
        simsight_log.error('reload: travel list notice')
    try:
        import simsight_build as build

        build.apply_setting()
    except Exception:
        simsight_log.error('reload: build controls notice')
    try:
        import simsight_keys as keys

        keys.apply_scanner_setting()
    except Exception:
        simsight_log.error('reload: scanner mode notice')


@live_command('simsight.reload', 'a11y.reload')
def a11y_reload(_connection: Any = None) -> bool:
    """Re-read SimSight.cfg after a hand-edit and apply it live."""
    with _boundary('narrator: reload'):
        output = sims4.commands.CheatOutput(_connection)
        settings.reload()
        _reload_speech(output)
        _reload_modules()
        for err in settings.load_errors:
            output('simsight: warning: ' + err)
        output(f'simsight: settings reloaded from {settings.PATH}')
        if settings.load_errors:
            speech.speak(
                'Settings reloaded with {} warnings. {}'.format(
                    len(settings.load_errors), ' '.join(settings.load_errors)
                ),
                explicit=True,
            )
        else:
            speech.speak('Settings reloaded.', explicit=True)
        return True
    return False


@live_command('simsight.captions', 'a11y.captions')
def a11y_captions(mode: str = '', _connection: Any = None) -> bool:
    """simsight.captions [on|off] - display accepted narration on screen."""
    with _boundary('narrator: captions'):
        output = sims4.commands.CheatOutput(_connection)
        mode = mode.lower()
        if mode not in ('', 'on', 'off'):
            output('Usage: simsight.captions [on|off]')
            return False
        if mode:
            settings.set('captions', mode == 'on')
            if mode == 'off':
                speech.clear_caption()
        output('simsight: captions {}'.format('on' if settings.get('captions') else 'off'))
        return True
    return False


# simsight.settings: (spoken label, setting key, default) of the plain switches.
_SETTING_SWITCHES = (
    ('scanner', 'scanner', True),
    ('captions', 'captions', False),
    ('startup speech', 'startup', True),
    ('simplified menus', 'simplified_menus', True),
    ('travel list', 'travel_list', True),
    ('Build controls', 'build_controls', None),
    ('scanner mode', 'scanner_mode', False),
)


def _settings_lines() -> tuple[list[str], list[str]]:
    """(spoken option lines, console key binding lines) for simsight.settings."""
    lines = [f'Sim actions {_on_off(_sim_actions_on())}']
    try:
        import simsight_objectnames as objectnames

        lines.append(f'object names {_on_off(objectnames.enabled())}')
    except ImportError:
        lines.append('object names unavailable')
    lines.extend(
        f'{label} {_on_off(settings.get(key, default))}'
        for label, key, default in _SETTING_SWITCHES
    )
    keybind_lines = []
    try:
        import simsight_keys as keys

        lines.append(f'keyboard navigation {_on_off(keys.status.enabled)}')
        for label, combos in keys.BINDINGS.values():
            keybind_lines.append(
                '   {}: {}'.format(
                    label, ' or '.join(keys.describe_key(vk, alt) for (vk, alt) in combos)
                )
            )
    except ImportError:
        lines.append('keyboard navigation unavailable')
    lines.append('speech mode {}'.format(speech.modes.speech.replace('_', ' ')))
    lines.append(f'tooltip delay {_delay_text(_tooltip_delay())}')
    lines.append('auto focus {}'.format(_on_off(settings.get('auto_focus'))))
    lines.append(f'braille mode {speech.modes.braille}')
    lines.append('verbosity {}'.format(settings.get('verbosity')))
    lines.append(f'logging {_on_off(simsight_log.enabled)}')
    return lines, keybind_lines


@live_command('simsight.settings', 'a11y.settings')
def a11y_settings(_connection: Any = None) -> bool:
    """Read out every option and its current live value."""
    with _boundary('narrator: settings'):
        output = sims4.commands.CheatOutput(_connection)
        lines, keybind_lines = _settings_lines()
        for line in lines:
            output('simsight: ' + line)
        for line in keybind_lines:
            output(line)
        output(_sapi_summary())
        output(f'simsight: settings file: {settings.PATH}')
        for err in settings.load_errors:
            output('simsight: warning: ' + err)
        speech.speak('. '.join(lines) + '.', explicit=True)
        return True
    return False


@live_command('simsight.status', 'a11y.status')
def a11y_status(_connection: Any = None) -> bool:
    with _boundary('narrator: status'):
        output = sims4.commands.CheatOutput(_connection)
        output(f'a11y backend: {speech.backend_name()}')
        output(
            'a11y screen reader: {}'.format(speech.screen_reader_name() or 'none; Windows fallback')
        )
        output(
            'a11y speech pack: {}'.format(
                'installed'
                if speech.speech_pack_installed()
                else 'NOT installed (separate download; screen readers and braille '
                'need it, the Windows voice does not)'
            )
        )
        output(
            'a11y Prism DLL expected at: {} (present: {})'.format(
                speech.PRISM_DLL_PATH,
                'yes' if os.path.isfile(speech.PRISM_DLL_PATH) else 'no',
            )
        )
        output(
            'a11y braille: display {} / mode {}'.format(
                'supported by reader' if speech.has_braille() else 'not available',
                speech.modes.braille,
            )
        )
        output(
            'a11y log: {} ({})'.format('ON' if simsight_log.enabled else 'OFF', simsight_log.PATH)
        )
        return True
    return False

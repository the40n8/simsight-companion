"""simsight.voice: the fallback voice's rate, volume and name."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_settings as settings
import simsight_speech as speech

from .base import _boundary, live_command

if TYPE_CHECKING:
    from .base import Output


def _voice_number(sub: str, text: str, output: Output) -> bool:
    try:
        value = int(text)
    except ValueError:
        output(f'simsight: {sub} must be a number')
        return False
    if sub == 'rate':
        value = max(-10, min(10, value))
        settings.set('sapi_rate', value)
    else:
        value = max(0, min(100, value))
        settings.set('sapi_volume', value)
    speech.restart_sapi()
    output(f'simsight: SAPI {sub} set to {value}')
    speech.speak(f'Voice {sub} {value}.', explicit=True)
    return True


def _voice_name(name: str, output: Output) -> bool:
    settings.set('sapi_voice', name)
    speech.restart_sapi()
    output(
        f'simsight: SAPI voice set to "{name}" (partial name, not case-'
        'sensitive: david, zira or hazel; default voice if nothing '
        'matches)'
    )
    speech.speak('Voice changed.', explicit=True)
    return True


def _voice_list(output: Output) -> bool:
    names = speech.list_voices()
    if not names:
        output('simsight: could not list voices')
        return True
    for name in names:
        output(f'simsight: voice: {name}')
    speech.speak('{} voices installed. {}'.format(len(names), '. '.join(names)), explicit=True)
    return True


def _voice_reset(output: Output) -> bool:
    for key in ('sapi_rate', 'sapi_volume', 'sapi_voice'):
        settings.set(key, settings.DEFAULTS[key])
    speech.restart_sapi()
    output('simsight: SAPI voice options reset')
    speech.speak('Voice reset.', explicit=True)
    return True


def _sapi_summary() -> str:
    return 'simsight: SAPI fallback voice: rate {}, volume {}, name "{}"'.format(
        settings.get('sapi_rate'),
        settings.get('sapi_volume'),
        settings.get('sapi_voice') or 'default',
    )


def _voice_usage(output: Output) -> bool:
    output(_sapi_summary())
    output(
        'simsight: these only apply without a screen reader; screen reader '
        'users set voice/rate/volume in their screen reader'
    )
    output(
        'simsight: usage simsight.voice rate <-10..10> | volume <0..100> | '
        'name <voice> | list | reset'
    )
    return True


def _voice(parts: list[str], output: Output) -> bool:
    sub = parts[0].lower() if parts else ''
    if sub in ('rate', 'volume') and len(parts) >= 2:
        return _voice_number(sub, parts[1], output)
    if sub == 'name' and len(parts) >= 2:
        return _voice_name(' '.join(parts[1:]), output)
    if sub == 'list':
        return _voice_list(output)
    if sub == 'reset':
        return _voice_reset(output)
    return _voice_usage(output)


@live_command('simsight.voice', 'a11y.voice')
def a11y_voice(*args: Any, _connection: Any = None) -> bool:
    """simsight.voice rate <-10..10> | volume <0..100> | name <voice name> |
    list | reset: SAPI fallback voice only. Screen reader users control
    voice, rate and volume in their own screen reader settings."""
    with _boundary('narrator: voice'):
        output = sims4.commands.CheatOutput(_connection)
        return _voice([str(part) for part in args], output)
    return False

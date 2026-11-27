"""Starting and stopping the poller, and the simsight.keys and
simsight.keyprobe commands."""

from __future__ import annotations

import sys
import threading
from typing import Any, Callable

import sims4.commands

from simsight_commands import real_annotations
import simsight_log
import simsight_settings as settings
import simsight_speech as speech

from .bindings import BINDINGS, CHORD_NAMES, CHORDS
from .hook import start_swallow, stop_swallow
from .poller import _loop
from .send import send_action
from .state import _state, status
from .winapi import _ctypes_error, ctypes

# Key names for the simsight.keys listing (single source in settings).
_KEY_NAMES = settings.KEY_NAMES


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


def _keys_listing(output: Callable[[str], object]) -> None:
    for label, combos in BINDINGS.values():
        output(
            '   {}: {}'.format(label, ' or '.join(describe_key(vk, alt) for (vk, alt) in combos))
        )
    for vk, (intent, _rep) in CHORDS.items():
        output(f'   {intent}: Ctrl+Alt+{CHORD_NAMES.get(vk, hex(vk))}')
    output('   needs: Ctrl+Shift+N; accessibility menu: Ctrl+Alt+H')


@sims4.commands.Command('simsight.keys', 'a11y.keys', command_type=sims4.commands.CommandType.Live)
@real_annotations
def a11y_keys(mode: str = 'on', _connection: Any = None) -> bool:
    try:
        output = sims4.commands.CheatOutput(_connection)
        if mode.lower() in ('off', 'false', '0'):
            status.enabled = False
            settings.set('keyboard', False)
            send_action('A11yKeyboardOff')
            output('simsight: keyboard navigation OFF')
            speech.speak('Keyboard navigation off.', explicit=True)
            return True
        status.enabled = True
        settings.set('keyboard', True)
        send_action('A11yKeyboardOn')
        started = start()
        output('simsight: keyboard navigation {}'.format('ON' if started else 'UNAVAILABLE'))
        _keys_listing(output)
        speech.speak('Keyboard navigation on.', explicit=True)
        return True
    except Exception:
        simsight_log.error('keys: keys command')
        return False


def describe_key(vk: int, needs_alt: bool) -> str:
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
        send_action('A11yKeyProbeOn' if value == 'on' else 'A11yKeyProbeOff')
        sims4.commands.CheatOutput(_connection)(
            'simsight: key probe {} requested{}'.format(
                value, ' for 60 seconds (key/modifier metadata only)' if value == 'on' else ''
            )
        )
    except Exception:
        simsight_log.error('keys: keyprobe command')
        return False
    return True

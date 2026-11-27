"""Delivering a key's message: speech actions in Python, the Build cursor
and scanner keys through the pump, focus navigation to the Flash layer."""

from __future__ import annotations

from typing import Callable

import simsight_log
import simsight_speech as speech

from .scannermode import _build_grid_key, _scanner_key
from .state import _deliver, _post, _state

_PUMP_INACTIVE = 'keys: navigation unavailable (simulation pump inactive)'


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
        send_action(message_name)
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


def send_action(message_name: str) -> None:
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

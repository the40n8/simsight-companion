"""Dialogs, scopes, menus, control names, events and Sim actions."""

from __future__ import annotations

import re
import threading
from typing import Any

import sims4.commands

import simsight_lang as lang
import simsight_log
import simsight_settings as settings
import simsight_speech as speech

from .base import _boundary, live_command
from .decode import decode_percent
from .state import (
    _NAME_GRACE_SECONDS,
    _arm_row_head,
    _cancel_pending_name,
    _flush_pending_name,
    _name_lock,
    _state,
)
from .fields import _process, _process_or_raw


@live_command('simsight.scope_enc', 'a11y.scope_enc')
def a11y_scope_enc(text: str = '', _connection: Any = None) -> bool:
    """Opening description and first control form one uninterrupted utterance."""
    with _boundary('narrator: scope_enc'):
        _cancel_pending_name()
        parts = decode_percent(text).split('\x02', 2)
        if len(parts) != 3:
            return False
        kind, intro, focus = parts
        intro = _process(intro, 'dialog') if intro else ''
        focus = _process(focus, 'widget') if focus else ''
        if kind == 'dialog' and not settings.brief():
            intro = '{}. {}'.format(lang.t('dialog'), intro)
        line = '. '.join(part for part in (intro, focus) if part)
        _arm_row_head(line)
        speech.speak(line, source='widget')
        return True
    return False


@live_command('simsight.dialog_enc', 'a11y.dialog_enc')
def a11y_dialog_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: dialog_enc'):
        body = _process_or_raw(decode_percent(text), 'dialog')
        # Dialogs demand an answer, so they interrupt and are labelled as such
        # (unless verbosity is brief).
        speech.speak(
            body if settings.brief() else '{}. {}'.format(lang.t('dialog'), body),
            source='widget',
        )
        return True
    return False


@live_command('simsight.menuopen', 'a11y.menuopen')
def a11y_menuopen(count: str = '0', _connection: Any = None) -> bool:
    with _boundary('narrator: menuopen'):
        try:
            n = int(count)
        except ValueError:
            n = 0
        count_text = '{} {}.'.format(n, lang.t('options'))
        speech.speak(
            count_text if settings.brief() else '{}. {}'.format(lang.t('menu open'), count_text),
            source='widget',
        )
        try:
            import simsight_scanner as scanner
        except ImportError:
            return True  # no scanner in this build: nothing to tell it
        scanner.note_menu_open()
        return True
    return False


@live_command('simsight.stop', 'a11y.stop')
def a11y_stop(
    arg: str = '',  # noqa: ARG001 - Flash may post one; the stop needs none
    _connection: Any = None,
) -> bool:
    with _boundary('narrator: stop'):
        # Posted by Flash when a touched control has no text at all. Soft stop:
        # it must not cut down a row label that just started (see request_hush).
        if _state.rawdiag:
            simsight_log.line('RAWDIAG[stop]')
        speech.request_hush()
        return True
    return False


def _control_name(decoded: str) -> str:
    """Words from a control's instance identifier ("ddProfitMargin" -> "Profit Margin")."""
    # Instance identifiers arrive camel-cased ("BackgroundColorSelector");
    # split into words. English dev names, fallback for unlabeled controls.
    decoded = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', decoded)
    # Drop the control-type prefix EA's instance names carry ("dd Profit
    # Margin" was heard for the retail markup dropdown, 2026-10-04).
    decoded = re.sub(r'^(?:txt|mc|btn|dd|cb|sl|lb)\s+(?=[A-Z])', '', decoded)
    return re.sub(r'[_\d]+', ' ', decoded).strip()


@live_command('simsight.widgetname_enc', 'a11y.widgetname_enc')
def a11y_widgetname_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: widgetname_enc'):
        # An icon-only control: its tooltip is the only text it has, so it must
        # never be deferred as if it were a data row's description.
        _state.row_head = None
        decoded = decode_percent(text).strip()
        if _state.rawdiag:
            simsight_log.line('RAWDIAG[widgetname]: ' + ascii(decoded))
        name = _control_name(decoded)
        # Touching a textless control silences STALE speech, but must not cut a
        # row label that just started (see speech.request_hush).
        speech.request_hush()
        if not name:
            return True
        with _name_lock:
            _state.pending_name = name
        if _state.pending_name_timer is not None:
            _state.pending_name_timer.cancel()
        timer = threading.Timer(_NAME_GRACE_SECONDS, _flush_pending_name)
        timer.daemon = True
        timer.start()
        _state.pending_name_timer = timer
        return True
    return False


@live_command('simsight.event_enc', 'a11y.event_enc')
def a11y_event_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: event_enc'):
        speech.speak(_process_or_raw(decode_percent(text), 'event'), source='event')
        return True
    return False


# Sim-action announcements are an optional feature: the handler always exists
# (so the Flash side can never hit an unknown command), but it only speaks
# when the option is on. Toggled with simsight.simactions, persisted in settings.
def _sim_actions_on() -> bool:
    return bool(settings.get('sim_actions'))


@live_command('simsight.simactions', 'a11y.simactions')
def a11y_simactions(mode: str = '', _connection: Any = None) -> bool:
    """simsight.simactions on|off: announce what the active Sim starts doing."""
    with _boundary('narrator: simactions'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower()
        if m in ('on', 'true', '1'):
            settings.set('sim_actions', True)
            output('simsight: Sim action announcements ON')
            speech.speak('Sim action announcements on.', explicit=True)
        elif m in ('off', 'false', '0'):
            settings.set('sim_actions', False)
            output('simsight: Sim action announcements OFF')
            speech.speak('Sim action announcements off.', explicit=True)
        else:
            output(
                'simsight: Sim action announcements are {}'.format(
                    'ON' if _sim_actions_on() else 'OFF'
                )
            )
            output('simsight: usage simsight.simactions on|off')
        return True
    return False


@live_command('simsight.simaction_enc', 'a11y.simaction_enc')
def a11y_simaction_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: simaction_enc'):
        if not _sim_actions_on():
            return True
        speech.speak(_process_or_raw(decode_percent(text), 'simaction'), source='event')
        return True
    return False

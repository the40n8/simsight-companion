"""Flash channels: tooltips, widgets, focus, context, the time widget and
the clock."""

from __future__ import annotations

from typing import Any

import sims4.commands

import simsight_lang as lang
import simsight_log
import simsight_speech as speech

from .base import _boundary, live_command
from .decode import _clean_glyphs, decode_percent
from .state import _arm_row_head, _cancel_pending_name, _state, _take_row_head, _tooltip_delay
from .fields import _process_or_raw


@live_command('simsight.rawdiag', 'a11y.rawdiag')
def a11y_rawdiag(mode: str = 'on', _connection: Any = None) -> bool:
    """Hidden support diagnostic: log pre-clean payloads with unicode
    escapes (for identifying unknown glyph characters). Session-only;
    needs simsight.log on to actually write anywhere."""
    with _boundary('narrator: rawdiag'):
        _state.rawdiag = mode.lower() not in ('off', 'false', '0')
        sims4.commands.CheatOutput(_connection)(
            'simsight: raw payload logging {}'.format('ON' if _state.rawdiag else 'OFF')
        )
        return True
    return False


@live_command('simsight.timewidget_enc', 'a11y.timewidget_enc')
def a11y_timewidget_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: timewidget_enc'):
        decoded = decode_percent(text)
        if _state.rawdiag:
            simsight_log.line('RAWDIAG[timewidget]: ' + ascii(decoded))
        final = _clean_glyphs(decoded)
        if not final:
            return True
        _arm_row_head(final)
        speech.speak(final, source='widget')
        return True
    return False


@live_command('simsight.clock_enc', 'a11y.clock_enc')
def a11y_clock_enc(text: str = '', _connection: Any = None) -> bool:
    """The client's answer to A11yClock (Control Alt C, the menu row): its
    clock widget's own localized day and time, with Paused when paused,
    as 'clock:<text>'. Empty text means the widget is not live; say so in
    words rather than invent a day number. Separate from the pointer path
    (simsight.timewidget_enc) so a hover can never pass for the reply."""
    with _boundary('narrator: clock_enc'):
        decoded = decode_percent(text)
        if decoded.startswith('clock:'):
            decoded = decoded[len('clock:') :]
        final = _clean_glyphs(decoded).strip() if decoded.strip() else ''
        money = _clock_funds()
        if not final:
            speech.speak(
                lang.t('clock unavailable') + (' ' + money if money else ''), explicit=True
            )
            return True
        speech.speak(final + ('. ' + money if money else ''), source='widget', explicit=True)
        return True
    return False


def _clock_funds() -> str:
    """The clock reply also reads household funds (requested 2026-09-28). Empty
    before a household loads, e.g. at the main menu, so the clock still
    answers there on its own."""
    try:
        import simsight_hub as hub

        return hub.funds_text()
    except Exception as ex:  # the clock must still answer
        simsight_log.line(f'clock: funds unavailable ({ex!r})')
        simsight_log.error('narrator: clock funds')
        return ''


@live_command('simsight.speak_enc', 'a11y.speak_enc')
def a11y_speak_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: speak_enc'):
        # A real tooltip beats any placeholder name we derived from the control.
        _cancel_pending_name()
        final = _process_or_raw(decode_percent(text), 'tooltip')
        head = _take_row_head()
        delay = _tooltip_delay()
        if head is not None and delay > 0:
            # The row already said name and value; hold its description back.
            speech.speak_progressive(head, final, source='tooltip', delay=delay, head_spoken=True)
            return True
        speech.speak(final, source='tooltip')
        return True
    return False


@live_command('simsight.widget_enc', 'a11y.widget_enc')
def a11y_widget_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: widget_enc'):
        # A real widget line beats any derived-name placeholder still waiting
        # (e.g. the purchase picker's +/- buttons: the generic path posts their
        # bare instance name, then our cell patch posts the full
        # "Increase. <item>. <count>" line; without this, the parked name
        # flushed 0.4 s later and talked over the real announcement).
        _cancel_pending_name()
        final = _process_or_raw(decode_percent(text), 'widget')
        # This row spoke for itself, so a description arriving behind it can be
        # deferred; it also supersedes any older row still waiting.
        _arm_row_head(final)
        speech.speak(final, source='widget')
        return True
    return False


@live_command('simsight.focus_enc', 'a11y.focus_enc')
def a11y_focus_enc(text: str = '', _connection: Any = None) -> bool:
    """The settled keyboard target supersedes an older pending row."""
    with _boundary('narrator: focus_enc'):
        _cancel_pending_name()
        final = _process_or_raw(decode_percent(text), 'widget')
        _arm_row_head(final)
        speech.speak(final, source='widget', explicit=speech.modes.speech == 'hover')
        return True
    return False


@live_command('simsight.context_enc', 'a11y.context_enc')
def a11y_context_enc(text: str = '', _connection: Any = None) -> bool:
    """Log owner transitions without speaking; distinguish loaded from open.
    The ':DD' suffix is the same owner state simsight.modalstate carries; it is
    applied too, so one lost post cannot strand keyboard routing. A third
    digit is the owner's blocking bit (phone guard), applied with the
    owner as one report; the direct simsight.modalstate stays two digits."""
    with _boundary('narrator: context_enc'):
        decoded = decode_percent(text)
        simsight_log.line('UI owner: ' + decoded[:300])
        suffix = decoded.rsplit(':', 1)[-1] if ':' in decoded else ''
        if suffix[:2] in ('00', '01', '10', '11') and suffix[2:] in ('', '0', '1'):
            try:
                import simsight_keys as keys

                keys.apply_modal_state(suffix, 'context')
            except Exception:
                simsight_log.error('context: modal state not applied')
        return True
    return False

"""The loc channel: speak text that holds string-table keys once the
client has resolved them in the player's language (simsight.loc_enc)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Sequence

import sims4.commands

from simsight_commands import console_command
import simsight_log
import simsight_speech as speech

from .state import _LOC_TIMEOUT, _Pending, _loc_lock, _state, loc_cache

if TYPE_CHECKING:
    from .state import Part


def send_ui(name: str) -> bool:
    """Python -> Flash message. Must be called on the simulation thread."""
    try:
        import distributor.ops
        import distributor.system

        op = distributor.ops.SendUIMessage(name)
        distributor.system.Distributor.instance().add_op_with_no_owner(op)
    except Exception:
        simsight_log.error('scanner: send to the UI')
        return False
    return True


def _clean_text(pieces: list[str]) -> str:
    """Join spoken pieces; drop the gaps and leading punctuation that empty
    pieces (a name the client never resolved) leave behind."""
    text = ''.join(pieces).replace(' ,', ',').replace('  ', ' ').strip()
    while text.startswith((',', '.')):
        text = text[1:].strip()
    return text


def _finish(pending: _Pending) -> None:
    with _loc_lock:
        if pending.done:
            return
        pending.done = True
        if pending.timer is not None:
            pending.timer.cancel()
        if pending.seq != _state.announce_seq:
            return  # superseded by a newer announcement
        pieces = [
            loc_cache.get(part[1], '') if isinstance(part, tuple) else part
            for part in pending.parts
        ]
    text = _clean_text(pieces)
    if text:
        speech.speak(text, source='widget')


def _finish_late(pending: _Pending) -> None:
    # Timer thread: the client did not answer in time; speak what we have.
    try:
        _finish(pending)
    except Exception:
        simsight_log.error('scanner: speak after loc timeout')


def speak_parts(parts: Sequence[Part]) -> None:
    """Speak a list of strings and ('loc', hash) parts once the client has
    resolved every key (or the timeout passes). Simulation thread only."""
    need_send = []
    with _loc_lock:
        _state.announce_seq += 1
        pending = _Pending(list(parts), _state.announce_seq)
        for part in pending.parts:
            if isinstance(part, tuple) and part[1] and part[1] not in loc_cache:
                pending.missing.add(part[1])
        if pending.missing:
            for key in pending.missing:
                waiting = _state.loc_waiting.setdefault(key, [])
                if not waiting:
                    need_send.append(key)
                waiting.append(pending)
            pending.timer = threading.Timer(_LOC_TIMEOUT, _finish_late, (pending,))
            pending.timer.daemon = True
            pending.timer.start()
    for key in need_send:
        send_ui(f'A11y|loc|{key}|{key}')
    if not pending.missing:
        _finish(pending)


def _loc_reply(text: str) -> None:
    try:
        from simsight_narrator import decode_percent

        head, _sep, body = text.partition('|')
        key = int(head)
        resolved = decode_percent(body).strip()
    except (ImportError, TypeError, ValueError) as ex:
        simsight_log.line(f'scanner: bad loc reply {text[:60]!r}: {ex!r}')
        return
    ready = []
    with _loc_lock:
        loc_cache[key] = resolved
        for pending in _state.loc_waiting.pop(key, ()):
            pending.missing.discard(key)
            if not pending.missing:
                ready.append(pending)
    for pending in ready:
        _finish(pending)


@console_command('simsight.loc_enc', 'a11y.loc_enc', command_type=sims4.commands.CommandType.Live)
def a11y_loc_enc(text: str = '', _connection: Any = None) -> bool:
    """Flash reply: "<hash>|<percent-encoded localized text>"."""
    try:
        _loc_reply(text)
    except Exception:
        simsight_log.error('scanner: loc reply')
    return True

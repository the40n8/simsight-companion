"""Open the selected running event's native goal HUD, without acting on goals."""

from __future__ import annotations

import time
from typing import Any, NamedTuple

from simsight_commands import real_annotations
import simsight_log

_REPLY_SECONDS = 4.0  # a reply later than this is ignored
_TIMEOUT_SECONDS = 4.1  # then the player hears the HUD is unavailable


class _Job(NamedTuple):
    serial: int
    zone: int
    event: int
    until: float


class _State:
    serial = 0
    pending: _Job | None = None
    registered = False


_state = _State()


def _log(message: str) -> None:
    simsight_log.line('event goals: ' + message)


def _unavailable() -> None:
    import simsight_lang as lang
    import simsight_speech as speech

    speech.speak(lang.t('event goals unavailable'), explicit=True)


def _current_zone() -> int:
    import services

    return int(services.current_zone_id())


def _expire(serial: int) -> None:
    job = _state.pending
    if job is None or job.serial != serial:
        return
    _state.pending = None
    if _current_zone() != job.zone:
        return
    _log('native UI query timed out')
    _unavailable()


def parse_reply(text: str, job: _Job | None) -> str | None:
    """The HUD status in a decoded 'serial|zone|status' reply for this job."""
    parts = text.split('|')
    if len(parts) != 3 or job is None:
        return None
    if parts[0] != str(job.serial) or parts[1] != str(job.zone):
        return None
    return parts[2]


def receive(text: str = '') -> bool:
    import simsight_narrator as narrator

    try:
        job = _state.pending
        status = parse_reply(narrator.decode_percent(text), job)
        if status is None or job is None or _current_zone() != job.zone:
            return False
        _state.pending = None
        if time.monotonic() > job.until:
            return False
        _log(f'native HUD status={status} event={job.event}')
        if status != 'opened':
            _unavailable()
    except Exception:
        simsight_log.error('event goals: invalid native reply')
        return False
    else:
        return status == 'opened'


def _register() -> None:
    if _state.registered:
        return
    import sims4.commands

    @sims4.commands.Command(
        'simsight.eventgoals_enc',
        'a11y.eventgoals_enc',
        command_type=sims4.commands.CommandType.Live,
    )
    @real_annotations
    def command(text: str = '', _connection: Any = None) -> bool:
        return receive(text)

    _state.registered = True


def open_goals(situation_id: Any) -> bool:
    import simsight_hub as hub
    import simsight_pump as pump
    import simsight_steps as steps

    _manager, situation = hub.event_situation(situation_id)
    if situation is None:
        return False
    _register()
    _state.serial += 1
    job = _Job(
        serial=_state.serial,
        zone=_current_zone(),
        event=int(situation_id),
        until=time.monotonic() + _REPLY_SECONDS,
    )
    _state.pending = job
    hub.send_ui(f'A11y|eventgoals|{job.event}|{job.zone}|{job.serial}')
    # The timer never touches game data; only the simulation pump may expire it.
    steps.after(_TIMEOUT_SECONDS, pump.post, _expire, job.serial)
    return True

"""The state snapshot sent to the phone: the household and its Sims,
clock, dialogs, problems, visitors; sent only when it changed."""

from __future__ import annotations


import json
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_speech as speech

from .house import _bills_owed, _camera_follows, _room_ref, _visitors
from .pipe import send
from .problems import _problems
from .sim_life import _career, _goals, _relationships
from .sim_status import _actions, _mood, _moodlets, _needs, _skills
from .state import _in_game, _logged, _state


if TYPE_CHECKING:
    from collections.abc import Callable

    from .state import Json


def _speed() -> str:
    import services
    from clock import ClockSpeedMode

    speed = services.game_clock_service().clock_speed
    return {
        ClockSpeedMode.PAUSED: 'SPEED_PAUSED',
        ClockSpeedMode.SPEED2: 'SPEED_FAST',
        ClockSpeedMode.SPEED3: 'SPEED_ULTRA',
        ClockSpeedMode.SUPER_SPEED3: 'SPEED_SUPER',
    }.get(speed, 'SPEED_NORMAL')


def _clock() -> Json:
    import services

    clock = {'speed': _speed()}
    with _logged('clock'):
        import simsight_plus_time as t

        now = services.time_service().sim_now
        clock['day'] = t.day_text(now)
        clock['time'] = t.clock_text(now)
    return clock


def _add_part(entry: Json, key: str, part: Callable[[], Any]) -> None:
    """entry[key] = part() when it has something; a failure only leaves it out."""
    with _logged(key):
        value = part()
        if value:
            entry[key] = value


def _sim(info: Any, client: Any) -> Json:
    sim = info.get_sim_instance()
    room = _room_ref(sim) if sim is not None else None
    entry: Json = {
        'id': str(info.sim_id),
        'firstName': info.first_name or '',
        'lastName': info.last_name or '',
        'active': client is not None and client.active_sim_info is info,
        'here': sim is not None,
    }
    if room is not None:
        entry['room'] = room
    with _logged('career'):
        _career(info, entry)
    _add_part(entry, 'relationships', lambda: _relationships(info))
    _add_part(entry, 'skills', lambda: _skills(info))
    _add_part(entry, 'goals', lambda: _goals(info))
    _add_part(entry, 'mood', lambda: _mood(info))
    if sim is not None:  # only a Sim on the lot has needs, moodlets and actions now
        _add_part(entry, 'needs', lambda: _needs(info))
        _add_part(entry, 'moodlets', lambda: _moodlets(info))
        _add_part(entry, 'actions', lambda: _actions(sim))
    return entry


def _narration() -> Json:
    try:
        import simsight_settings as settings

        return {
            'macVoice': speech.modes.speech == 'hover',
            'simActions': bool(settings.get('sim_actions')),
        }
    except Exception:
        simsight_log.error('bridge: narration')
        return {}


def build_state() -> Json:
    import services

    if not _in_game():
        return {'inGame': False, 'narration': _narration()}
    household = services.active_household()
    client = services.client_manager().get_first_client()
    return {
        'inGame': True,
        'clock': _clock(),
        'funds': str(int(household.funds.money)),
        'householdName': household.name or '',
        'sims': [_sim(info, client) for info in household.sim_info_gen()],
        'narration': _narration(),
        'dialogs': _open_dialogs(),
        'problems': _problems(),
        'visitors': _visitors(household),
        'billsOwed': str(_bills_owed(household)),
        'cameraFollows': _camera_follows(),
    }


def _open_dialogs() -> list[Json]:
    if _state.dialogs is None:
        return []
    try:
        return _state.dialogs.active()
    except Exception:
        simsight_log.error('bridge: dialogs')
        return []


def _publish_state() -> None:
    try:
        state = build_state()
    except Exception:
        simsight_log.error('bridge: state')
        return
    body = json.dumps(state, sort_keys=True, ensure_ascii=False)
    if body == _state.last_state_json:
        return
    _state.last_state_json = body
    _state.seq += 1
    state['seq'] = str(_state.seq)
    send({'state': state})

"""A Sim's needs, mood, moodlets, queued actions and skills, for the state."""

from __future__ import annotations


import re
from typing import TYPE_CHECKING, Any

from .state import _logged
from .text import _pretty, text_of


if TYPE_CHECKING:
    from .state import Json


_NEEDS = (
    ('hunger', 'motive_hunger'),
    ('bladder', 'motive_bladder'),
    ('energy', 'motive_energy'),
    ('hygiene', 'motive_hygiene'),
    ('fun', 'motive_fun'),
    ('social', 'motive_social'),
)


def _need_level(p: float) -> str:
    if p < 15:
        return 'NEED_LEVEL_CRITICAL'
    if p < 30:
        return 'NEED_LEVEL_LOW'
    if p < 60:
        return 'NEED_LEVEL_OKAY'
    return 'NEED_LEVEL_GOOD'


def _needs(info: Any) -> list[Json]:
    import simsight_plus_needs as needs

    out = []
    for key, stat in _NEEDS:
        p = needs.percent(info, stat)
        if p is not None:
            out.append({'key': key, 'percent': p, 'level': _need_level(p)})
    return out


def _mood_key(mood: Any) -> str:
    name = getattr(mood, '__name__', '') or ''
    return re.sub(r'^mood_', '', name, flags=re.IGNORECASE).lower()


def _mood(info: Any) -> Json | None:
    buffs = getattr(info, 'Buffs', None)
    if buffs is None:
        return None
    mood = buffs.get_mood()
    if mood is None:
        return None
    intensity = 0
    with _logged('mood intensity'):
        intensity = int(buffs.get_mood_intensity())
    name = ''
    names = getattr(mood, 'mood_names', None)
    if names:
        with _logged('mood name'):
            name = text_of(names[min(intensity, len(names) - 1)])
    return {'key': _mood_key(mood), 'name': name, 'intensity': intensity}


def _moodlet(buff: Any, now: Any, info: Any) -> Json:
    # Buff instances share the Buff class; the tuning (name, mood, text)
    # is on buff_type.
    btype = getattr(buff, 'buff_type', None) or type(buff)
    tuning = getattr(btype, '__name__', '') or 'Buff'

    def tuned(attr: str) -> Any:
        value = getattr(btype, attr, None)
        return value if value is not None else getattr(buff, attr, None)

    mood_type = tuned('mood_type')
    moodlet = {
        'key': tuning,
        'name': text_of(tuned('buff_name'), _pretty(tuning, ('buff_',)), (info,)),
        'description': text_of(tuned('buff_description'), '', (info,)),
        'moodKey': _mood_key(mood_type) if mood_type else '',
        'weight': int(tuned('mood_weight') or 0),
        'minutesLeft': -1,
    }
    with _logged('moodlet time'):
        # (absolute ticks when it ends, rate), or (0, 0) without a timeout.
        ticks, _rate = buff.get_timeout_time()
        if ticks:
            from date_and_time import DateAndTime

            moodlet['minutesLeft'] = max(0, int((DateAndTime(ticks) - now).in_minutes()))
    return moodlet


def _moodlets(info: Any) -> list[Json]:
    import services

    buffs = getattr(info, 'Buffs', None)
    if buffs is None:
        return []
    now = services.time_service().sim_now
    return [_moodlet(buff, now, info) for buff in buffs if getattr(buff, 'visible', False)]


def _actions(sim: Any) -> list[Json]:
    import simsight_hub as hub

    out = []
    for info, interaction, state in hub.queue_entries(sim):
        fallback = ''
        if interaction is not None and hasattr(interaction, 'affordance'):
            fallback = _pretty(
                getattr(interaction.affordance, '__name__', ''), ('sim-', 'si_', 'mixer_')
            )
        name = text_of(getattr(info, 'display_name', None), fallback)
        if not name:
            continue
        out.append(
            {
                'id': str(info.interaction_id),
                'name': name,
                'state': 'ACTION_STATE_RUNNING' if state == 'running' else 'ACTION_STATE_QUEUED',
                'cancelable': bool(getattr(info, 'user_cancelable', False)),
            }
        )
    return out


_SKILL_PREFIXES = (
    'statistic_skill_adultmajor_',
    'statistic_skill_',
    'skill_',
    'adultmajor_',
)


def _skill(skill: Any) -> Json | None:
    """A skill row, or None for hidden skills and skills not started."""
    row = None
    with _logged('skill'):
        if not getattr(skill, 'hidden', False):
            level = int(skill.get_user_value())
            if level >= 1:
                name = type(skill).__name__
                row = {
                    'key': name,
                    'name': text_of(
                        getattr(skill, 'stat_name', None), _pretty(name, _SKILL_PREFIXES)
                    ),
                    'level': level,
                    'maxLevel': int(getattr(skill, 'max_level', 10) or 10),
                }
    return row


def _skills(info: Any) -> list[Json]:
    out = [row for row in (_skill(skill) for skill in list(info.all_skills())) if row]
    out.sort(key=lambda s: (-s['level'], s['name']))
    return out

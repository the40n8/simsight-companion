"""Pack parts of Simology as native pickers for the active Sim.

Milestones (Growing Together, EP13) come from the Sim's
DevelopmentalMilestoneTracker. Only milestones EA's panel would show are
listed: the tracker's own is_milestone_visible test, as in
send_all_milestones_update_to_client. Rows are grouped by EA's milestone
categories (DevelopmentalMilestone.DEVELOPMENTAL_MILESTONE_CATEGORIES) and
ordered by ui_sort_order.

Character values (Parenthood, GP05) are the LifeSkillStatistic entries in the
Sim's commodity tracker: name, current value on the tuned scale, the trait the
value's range gives at age up, and EA's description.

Everything is read again when a row is chosen; nothing here changes the game.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_menu as menu
import simsight_log
import simsight_names as names
import simsight_speech as speech

if TYPE_CHECKING:
    from simsight_hub import Row

    Entry = tuple[Any, Any]  # (milestone tuning, its tracker data)

# sims.sim_info_types.Age member names.
AGES = ('BABY', 'INFANT', 'TODDLER', 'CHILD', 'TEEN', 'YOUNGADULT', 'ADULT', 'ELDER')


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


def _no_rows(term: str, info: Any) -> bool:
    speech.speak(lang.t(term).format(names.full_name(info)), explicit=True)
    return False


def _pack(name: str) -> bool:
    try:
        from sims4.common import Pack, is_available_pack

        return bool(is_available_pack(getattr(Pack, name)))
    except (ImportError, AttributeError):
        return False  # a game version that does not know the pack


def milestones_available() -> bool:
    return _pack('EP13')


def values_available() -> bool:
    return _pack('GP05')


def _active_info() -> Any:
    import services

    client = services.client_manager().get_first_client()
    return getattr(client, 'active_sim_info', None) if client is not None else None


def _lines(*parts: Any) -> Any:
    """One localized description from EA strings and narration text."""
    from sims4.localization import LocalizationHelperTuning

    strings = [menu._raw(p) if isinstance(p, str) else p for p in parts if p]
    if not strings:
        return None
    if len(strings) == 1:
        return strings[0]
    return LocalizationHelperTuning.get_new_line_separated_strings(*strings)


def age_name(age: Any) -> str:
    try:
        from sims.sim_info_types import Age

        key = Age(age).name
    except (ImportError, ValueError, TypeError):
        key = str(getattr(age, 'name', age))
    if key not in AGES:
        return ''
    return lang.t('age ' + key.lower())


# ------------------------------------------------------------------ milestones


def _tracker(info: Any) -> Any:
    return getattr(info, 'developmental_milestone_tracker', None) if info is not None else None


def _milestone_entries(tracker: Any, info: Any) -> list[Entry]:
    """(milestone, data) pairs EA's panel shows, active and archived."""
    from event_testing.resolver import SingleSimResolver

    resolver = SingleSimResolver(info)
    entries = [
        (milestone, data)
        for milestone, data in list(tracker._active_milestones_data.items())
        if _visible(tracker, milestone, resolver)
    ]
    entries.extend(list(tracker._archived_milestones_data.items()))
    return entries


def _visible(tracker: Any, milestone: Any, resolver: Any) -> bool:
    try:
        return bool(tracker.is_milestone_visible(milestone, resolver))
    except Exception:
        return False  # its tuned tests fail to run: EA's panel would not show it


def _state(data: Any) -> str:
    from developmental_milestones.developmental_milestone_enums import DevelopmentalMilestoneStates

    state = getattr(data, 'state', None)
    if state == DevelopmentalMilestoneStates.UNLOCKED:
        return 'reached'
    if state == DevelopmentalMilestoneStates.ACTIVE:
        return 'in progress'
    return 'not reached'


def _reached_count(milestone: Any, data: Any) -> int:
    """Times reached, counting earlier goals of a repeatable milestone."""
    count = 1 if _state(data) == 'reached' else 0
    if getattr(milestone, 'repeatable', False):
        count += len(getattr(data, 'previous_goals', None) or ())
    return count


def _category_name(category: Any) -> Any:
    from developmental_milestones.developmental_milestone import DevelopmentalMilestone

    entry = DevelopmentalMilestone.DEVELOPMENTAL_MILESTONE_CATEGORIES.get(category)
    return getattr(entry, 'name', None) if entry is not None else None


def milestone_detail(milestone: Any, data: Any) -> Any:
    state = _state(data)
    count = _reached_count(milestone, data)
    if count > 1:
        status = lang.t('milestone reached times').format(count)
    elif count == 1:
        age = age_name(getattr(data, 'age_completed', None))
        status = lang.t('milestone reached at').format(age) if age else lang.t('milestone reached')
    elif state == 'in progress':
        status = lang.t('milestone in progress')
    else:
        status = lang.t('milestone not reached')
    if count:
        text = getattr(milestone, 'unlocked_text_no_context', None)
    else:
        primary = getattr(milestone, 'is_primary_milestone', None)
        text = getattr(primary, 'hint_text', None) if primary is not None else None
    return _lines(status, text)


def _groups(entries: list[Entry]) -> dict[Any, list[Entry]]:
    groups: dict[Any, list[Entry]] = {}
    for milestone, data in entries:
        groups.setdefault(getattr(milestone, 'category', None), []).append((milestone, data))
    for items in groups.values():
        items.sort(key=lambda pair: getattr(pair[0], 'ui_sort_order', 0))
    return groups


def _read(info: Any) -> dict[Any, list[Entry]] | None:
    tracker = _tracker(info)
    if tracker is None:
        return None
    return _groups(_milestone_entries(tracker, info))


def milestones_window() -> Any:
    """Categories for the active Sim; Enter lists that category's milestones."""
    import simsight_hub as hub

    info = _active_info()
    if info is None:
        _say('load a household first')
        return False
    try:
        groups = _read(info)
    except Exception as ex:
        simsight_log.line(f'simology: milestones unavailable ({ex!r})')
        _say('action unavailable')
        return False
    if not groups:
        return _no_rows('no milestones', info)
    sim_id = info.sim_id
    rows: list[Row] = []
    for category in sorted(groups, key=lambda c: int(c) if c is not None else 0):
        items = groups[category]
        name = _category_name(category)
        if name is None:
            continue
        reached = sum(1 for m, d in items if _reached_count(m, d))
        detail = lang.t('milestone category detail').format(reached, len(items))
        rows.append(
            (name, menu._raw(detail), functools.partial(milestone_category, sim_id, category))
        )
    if not rows:
        return _no_rows('no milestones', info)
    return hub._window(
        'milestones', lang.t('milestones instructions').format(names.full_name(info)), rows
    )


def milestone_category(sim_id: Any, category: Any) -> Any:
    import services
    import simsight_hub as hub

    info = services.sim_info_manager().get(sim_id)
    if info is None:
        _say('sim unavailable')
        return False
    try:
        items = (_read(info) or {}).get(category, [])
    except Exception as ex:
        simsight_log.line(f'simology: milestone category unavailable ({ex!r})')
        _say('action unavailable')
        return False
    rows: list[Row] = []
    for milestone, data in items:
        name = getattr(milestone, 'name', None)
        if name is None:
            continue
        # Reading only: Enter repeats the row instead of acting.
        detail = milestone_detail(milestone, data)
        rows.append((name, detail, lambda: milestone_category(sim_id, category)))
    if not rows:
        return _no_rows('no milestones', info)
    return hub._window(
        'milestones',
        lang.t('milestone list instructions'),
        rows,
        localized_title=_category_name(category),
    )


# ------------------------------------------------------------ character values


def _display(stat_type: Any, attr: str) -> Any:
    try:
        return getattr(stat_type, attr)
    except Exception:
        return None  # tuning that fails to load reads as missing, row skipped


def _life_skills(info: Any) -> list[Any]:
    from statistics.life_skill_statistic import LifeSkillStatistic

    tracker = getattr(info, 'commodity_tracker', None)
    if tracker is None:
        return []
    return [stat for stat in list(tracker) if isinstance(stat, LifeSkillStatistic)]


def _range_trait(stat_type: Any, value: float, info: Any) -> tuple[Any, str]:
    """The trait this value's tuned range gives at age up, if any."""
    for entry in getattr(stat_type, 'trait_on_age_up_list', None) or ():
        bounds = getattr(entry, 'life_skill_range', None)
        upgrade = getattr(entry, 'age_up_info', None)
        if bounds is None or upgrade is None:
            continue
        if not bounds.lower_bound <= value <= bounds.upper_bound:
            continue
        trait = getattr(upgrade, 'life_skill_trait', None)
        if trait is None:
            continue
        try:
            return trait.display_name(info), age_name(getattr(upgrade, 'age_to_apply_trait', None))
        except (AttributeError, TypeError):
            continue  # a trait without a usable name: try the next range
    return None, ''


def value_text(value: float, low: float, high: float) -> str:
    """'35 on a scale from -100 to 100'."""
    return lang.t('value on scale').format(int(round(value)), int(round(low)), int(round(high)))


def value_detail(stat: Any, info: Any) -> Any:
    stat_type = type(stat)
    value = stat.get_value()
    low = getattr(stat_type, 'min_value_tuning', getattr(stat, 'min_value', 0))
    high = getattr(stat_type, 'max_value_tuning', getattr(stat, 'max_value', 100))
    parts = [value_text(value, low, high)]
    trait, age = _range_trait(stat_type, value, info)
    if trait is not None:
        parts.append(
            lang.t('value heading to at').format(age) if age else lang.t('value heading to')
        )
        parts.append(trait)
    parts.append(
        _display(stat_type, 'display_description') or _display(stat_type, 'display_tooltip')
    )
    return _lines(*parts)


def values_window() -> Any:
    import simsight_hub as hub

    info = _active_info()
    if info is None:
        _say('load a household first')
        return False
    try:
        stats = _life_skills(info)
    except Exception as ex:
        simsight_log.line(f'simology: character values unavailable ({ex!r})')
        _say('action unavailable')
        return False
    rows: list[Row] = []
    for stat in stats:
        name = _display(type(stat), 'display_name')
        if name is None:
            continue
        try:
            detail = value_detail(stat, info)
        except Exception as ex:
            simsight_log.line(f'simology: value {type(stat).__name__} unreadable ({ex!r})')
            continue
        # Reading only: Enter reopens the list.
        rows.append((name, detail, values_window))
    if not rows:
        return _no_rows('no character values', info)
    return hub._window(
        'character values',
        lang.t('character values instructions').format(names.full_name(info)),
        rows,
    )

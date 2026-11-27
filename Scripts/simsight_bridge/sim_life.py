"""A Sim's job or school, goals (aspiration, wants, work tasks) and
relationships, for the state."""

from __future__ import annotations


from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_names as names

from .state import _logged
from .text import _pretty, text_of


if TYPE_CHECKING:
    from careers.career_base import CareerBase
    from relationships.relationship_tracker import RelationshipTracker
    from sims.sim_info import SimInfo

    from .state import Json


def _career_title(career: CareerBase, info: SimInfo) -> str:
    title = ''
    with _logged('career title'):
        title = text_of(career.current_level_tuning.get_title(info))
    if not title:
        with _logged('career name'):
            # As the game's own career panel: the track's name for this Sim.
            title = text_of(career.current_track_tuning.get_career_name(info))
    return title or _pretty(type(career).__name__, ('career_',))


def _career(info: SimInfo, entry: Json) -> None:
    """Job or school (the first one), whether the Sim is there now, until when, next shift."""
    import services
    import simsight_plus_time as t

    tracker = getattr(info, 'career_tracker', None)
    if tracker is None:
        return
    careers = list(tracker.careers.values())
    if not careers:
        return
    career = careers[0]
    word = t.career_word(career)
    entry['career'] = _career_title(career, info)
    performance = _performance(career)
    if performance is not None:
        entry['performance'] = performance
    now = services.time_service().sim_now
    if career.currently_at_work:
        entry['awayAt'] = word
        with _logged('career end'):
            entry['until'] = t.clock_text(now + career.time_until_end_of_work())
        return
    with _logged('next shift'):
        _span, start, _end = career.get_next_work_time()
        if start is not None:
            entry['nextShift'] = f'{t.day_text(start)} {t.clock_text(start)}'


# ---------------------------------------------------------------- goals
# Aspiration milestone, wants and fears, work tasks and promotion steps.
# Read only; every part is optional (packs and ages differ).


def _done(tracker: Any, key: Any, kind: str) -> bool:
    try:
        check = getattr(
            tracker, 'objective_completed' if kind == 'objective' else 'milestone_completed'
        )
        return bool(check(key))
    except Exception:
        simsight_log.error('bridge: goal done')
        return False


def _objectives(aspiration: Any, tracker: Any) -> list[Json]:
    out = []
    for objective in list(getattr(aspiration, 'objectives', None) or ()):
        name = text_of(getattr(objective, 'display_text', None), '')
        if not name:
            continue
        out.append({'name': name, 'done': _done(tracker, objective, 'objective')})
    return out


def _aspiration_goals(info: SimInfo, tracker: Any, goals: Json) -> None:
    """The aspiration and its first milestone not done yet, with its steps."""
    track = getattr(info, 'primary_aspiration', None)
    if track is None or tracker is None:
        return
    with _logged('aspiration'):
        goals['aspiration'] = text_of(getattr(track, 'display_text', None), '')
        aspirations = track.get_aspirations()
        items = aspirations.items() if hasattr(aspirations, 'items') else enumerate(aspirations)
        for _level, milestone in sorted(items, key=lambda kv: kv[0]):
            if not _done(tracker, milestone, 'milestone'):
                goals['milestone'] = text_of(getattr(milestone, 'display_name', None), '')
                goals['milestoneSteps'] = _objectives(milestone, tracker)
                break


def _want(slot: Any) -> Json | None:
    want = None
    with _logged('want'):
        goal = getattr(slot, 'goal_instance', None)
        if goal is not None:
            name = text_of(goal.get_display_name(), '')
            if name:
                kind = getattr(getattr(slot, 'whim_type', None), 'name', '')
                want = {'name': name, 'fear': kind == 'CONFRONTATION'}
    return want


def _wants(info: SimInfo) -> list[Json]:
    whims = getattr(info, 'whim_tracker', None)
    if whims is None:
        return []
    slots = list(getattr(whims, '_whim_slots', None) or ())
    return [want for want in (_want(slot) for slot in slots) if want is not None]


def _work_tasks(level: Any) -> list[Json]:
    tasks = []
    for metric in list(getattr(level.performance_metrics, 'statistic_metrics', None) or ()):
        tip = getattr(metric, 'tooltip_text', None)
        name = text_of(getattr(tip, 'general_description', None), '') if tip is not None else ''
        if name:
            tasks.append({'name': name, 'done': False})
    return tasks


def _work_goals(info: SimInfo, tracker: Any, goals: Json) -> None:
    """Work tasks and promotion steps of the first job."""
    careers = getattr(info, 'career_tracker', None)
    if careers is None:
        return
    for career in list(careers.careers.values())[:1]:
        with _logged('work goals'):
            level = career.current_level_tuning
            tasks = _work_tasks(level)
            if tasks:
                goals['workTasks'] = tasks
            promotion = getattr(level, 'aspiration', None)
            if promotion is not None and tracker is not None:
                steps = _objectives(promotion, tracker)
                if steps:
                    goals['promotionSteps'] = steps


def _goals(info: SimInfo) -> Json:
    goals: Json = {}
    tracker = getattr(info, 'aspiration_tracker', None)
    _aspiration_goals(info, tracker, goals)
    wants = _wants(info)
    if wants:
        goals['wants'] = wants
    _work_goals(info, tracker, goals)
    return goals


MAX_RELATIONSHIPS = 12


def _bit_label(bit: Any, other: SimInfo, info: SimInfo) -> str:
    """A relationship label (Father, Sister...) in the gender of the Sim it
    describes: the game's text takes that Sim as token 0, {M0.Father}{F0.Mother}."""
    name = getattr(bit, 'display_name', None)
    if callable(name) and not hasattr(name, 'hash'):
        with _logged('relationship label'):
            return text_of(name(other, info))
    return text_of(name)


def _relationship_labels(tracker: RelationshipTracker, other: SimInfo, info: SimInfo) -> list[str]:
    labels: list[str] = []
    with _logged('relationship labels'):
        bits = [b for b in tracker.get_all_bits(other.sim_id) if getattr(b, 'visible', False)]
        bits.sort(key=lambda b: -(getattr(b, 'priority', 0) or 0))
        for bit in bits[:3]:
            label = _bit_label(bit, other, info)
            if label and label not in labels:
                labels.append(label)
    return labels


def _relationship(
    tracker: RelationshipTracker, other: SimInfo, info: SimInfo, tracks: tuple[Any, Any]
) -> Json | None:
    """One relationship row, or None when the game hides it."""
    hidden = False
    with _logged('relationship hidden'):
        hidden = bool(tracker.is_hidden(other.sim_id))
    if hidden:
        return None
    friendship, romance = tracks
    f = r = 0
    with _logged('relationship score'):
        f = (
            round(tracker.get_relationship_score(other.sim_id, friendship) or 0)
            if friendship
            else 0
        )
        r = round(tracker.get_relationship_score(other.sim_id, romance) or 0) if romance else 0
    return {
        'simId': str(other.sim_id),
        'name': names.full_name(other).strip(),
        'friendship': f,
        'romance': r,
        'labels': _relationship_labels(tracker, other, info),
    }


def _relationships(info: SimInfo) -> list[Json]:
    """Strongest relationships first: friendship, romance and the game's labels."""
    from relationships.relationship_track import RelationshipTrack

    tracker = info.relationship_tracker
    tracks = (
        getattr(RelationshipTrack, 'FRIENDSHIP_TRACK', None),
        getattr(RelationshipTrack, 'ROMANCE_TRACK', None),
    )
    rows = []
    for other in list(tracker.get_target_sim_infos() or ()):
        if other is None:
            continue
        row = _relationship(tracker, other, info, tracks)
        if row is None:
            continue
        strength = max(abs(row['friendship']), abs(row['romance']))
        rows.append((strength + (100 if row['labels'] else 0), row))
    rows.sort(key=lambda pair: -pair[0])
    return [row for _score, row in rows[:MAX_RELATIONSHIPS]]


def _performance(career: CareerBase) -> int | None:
    """Work performance towards the next promotion, 0-100, or None."""
    try:
        value = float(career.work_performance)
        # No public accessor in EA's CareerBase for the promotion threshold.
        goal = float(career._get_promote_performance_level())  # noqa: SLF001
    except (AttributeError, TypeError, ValueError):
        return None  # careers without performance (school, odd jobs)
    if goal <= 0:
        return None
    return max(0, min(100, round(100.0 * value / goal)))

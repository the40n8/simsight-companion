"""SimSight Plus needs: Alt+N readout, and automatic low-need warnings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_plus_core as core

if TYPE_CHECKING:
    from sims.sim_info import SimInfo

MOTIVES = (
    ('motive_hunger', 'Hunger'),
    ('motive_bladder', 'Bladder'),
    ('motive_energy', 'Energy'),
    ('motive_hygiene', 'Hygiene'),
    ('motive_fun', 'Fun'),
    ('motive_social', 'Social'),
)
LOW, CRITICAL, RECOVER = 30, 15, 45  # percent
_stat_types: dict[str, Any] = {}  # motive name -> statistic tuning, or None when missing
_levels: dict[tuple[int, str], str] = {}  # (sim_id, motive) -> 'ok' | 'low' | 'critical'


def _stat(name: str) -> Any:
    if name not in _stat_types:
        try:
            import sims4.resources
            from server_commands.argument_helpers import get_tunable_instance

            _stat_types[name] = get_tunable_instance(
                sims4.resources.Types.STATISTIC, name, exact_match=True
            )
        except Exception:  # that need is left out
            simsight_log.error('plus needs: need stat ' + name)
            _stat_types[name] = None
    return _stat_types[name]


def percent(sim_info: SimInfo, name: str) -> int | None:
    """The need as 0 to 100, or None when the Sim does not have it."""
    stat = _stat(name)
    if stat is None:
        return None
    tracker = sim_info.get_tracker(stat)
    if tracker is None:
        return None
    value = tracker.get_value(stat)
    if value is None:
        return None
    try:
        lo, hi = float(stat.min_value), float(stat.max_value)
    except (AttributeError, TypeError, ValueError):  # the usual range of a need
        lo, hi = -100.0, 100.0
    return scaled(value, lo, hi)


def scaled(value: Any, lo: float, hi: float) -> int | None:
    """value between lo and hi as a whole percent, clamped to 0..100."""
    if hi <= lo:
        return None
    return max(0, min(100, round((float(value) - lo) * 100.0 / (hi - lo))))


def word(p: int) -> str:
    if p < CRITICAL:
        return 'critical'
    if p < LOW:
        return 'low'
    if p < 60:
        return 'okay'
    return 'good'


def needs_of(sim_info: SimInfo) -> list[tuple[int, str]]:
    out = []
    for name, label in MOTIVES:
        p = percent(sim_info, name)
        if p is not None:
            out.append((p, label))
    return out


@core.hotkey(ord('N'), 'read needs')
def read_needs() -> None:
    sim = core.active_sim()
    if sim is None:
        core.speak('No active Sim.')
        return
    needs = needs_of(sim.sim_info)
    if not needs:
        core.speak('Needs are not available for this Sim.')
        return
    needs.sort()
    text = ', '.join(f'{label} {p} percent {word(p)}' for (p, label) in needs)
    core.speak(f'{sim.sim_info.first_name} needs, lowest first: {text}.')


def next_level(p: int, old: str | None) -> str:
    """The warning level of a need at p percent, after `old`.

    A need leaves 'critical' only 5 points above it, and stops being low
    only at RECOVER, so a need around a threshold is not announced again
    and again.
    """
    if p < CRITICAL:
        return 'critical'
    if p < LOW:
        return 'low' if old != 'critical' or p >= CRITICAL + 5 else 'critical'
    if p >= RECOVER:
        return 'ok'
    return old or 'ok'


def _alerts_for(info: SimInfo, active_info: SimInfo) -> list[str]:
    alerts = []
    for name, label in MOTIVES:
        p = percent(info, name)
        if p is None:
            continue
        key = (info.sim_id, name)
        old = _levels.get(key)
        new = next_level(p, old)
        _levels[key] = new
        if new != old and new in ('low', 'critical'):
            who = '' if info is active_info else info.first_name + ': '
            alerts.append('{}{} {}'.format(who, label, 'very low' if new == 'critical' else 'low'))
    return alerts


def _check() -> None:
    if not core.setting_on('need alerts'):
        return
    sim = core.active_sim()
    if sim is None:
        return
    infos = (
        core.household_sims()
        if core.setting_on('need alerts for whole household')
        else [sim.sim_info]
    )
    alerts = []
    for info in infos:
        alerts.extend(_alerts_for(info, sim.sim_info))
    if alerts:
        core.speak('. '.join(alerts) + '.', interrupt=False)


core.add_ticker(2.0, _check)


@core.on_zone_started
def _start() -> None:
    _levels.clear()

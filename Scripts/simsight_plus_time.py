"""SimSight Plus time: day, game speed, work/school times, money and bills.

Also says every game speed change (P, 1, 2, 3), Build and Live mode, and an
action queued while the game is paused.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import simsight_log
import simsight_plus_core as core

DAYS = ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')


class _State:
    def __init__(self) -> None:
        # (sim_id, career uid) -> start ticks already announced
        self.reminded: dict[tuple[Any, Any], int] = {}
        self.bills_announced = False
        self.last_spoken: tuple[str, int] | None = None  # (speed text, 2-second slot)
        self.last_mode: bool | None = None  # True in Build mode
        self.queued_seen: set[Any] | None = None  # ids in the active Sim's queue


_state = _State()


def clock_text(dt: Any) -> str:
    h, m = dt.hour(), dt.minute()
    suffix = 'AM' if h < 12 else 'PM'
    h12 = h % 12 or 12
    if m == 0:
        return f'{h12} {suffix}'
    return f'{h12}:{m:02d} {suffix}'


def day_text(dt: Any) -> str:
    return DAYS[dt.day() % 7]


def _speed_names() -> dict[Any, str]:
    from clock import ClockSpeedMode

    return {
        ClockSpeedMode.PAUSED: 'Paused',
        ClockSpeedMode.NORMAL: 'Normal speed',
        ClockSpeedMode.SPEED2: 'Fast speed',
        ClockSpeedMode.SPEED3: 'Ultra fast speed',
        ClockSpeedMode.SUPER_SPEED3: 'Super speed, everyone is asleep or away',
        ClockSpeedMode.INTERACTION_STARTUP_SPEED: 'Normal speed',
    }


def speed_text() -> str:
    try:
        import services

        speed = services.game_clock_service().clock_speed
        return _speed_names().get(speed, f'Speed {int(speed)}')
    except Exception:  # said without the speed
        simsight_log.error('plus time: speed')
        return ''


def _count(n: int, unit: str) -> str:
    return '{} {}{}'.format(n, unit, '' if n == 1 else 's')


def span_words(minutes: int) -> str:
    """'45 minutes', '2 hours 5 minutes', '1 day 3 hours'."""
    if minutes < 60:
        return f'{minutes} minutes'
    hours, mins = divmod(minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        text = _count(days, 'day')
        if hours:
            text += ' ' + _count(hours, 'hour')
        return text
    text = _count(hours, 'hour')
    if mins:
        text += f' {mins} minutes'
    return text


def _span_text(span: Any) -> str:
    return span_words(int(round(span.in_minutes())))


def career_word(career: Any) -> str:
    """'school' or 'work' (read by simsight_bridge too)."""
    try:
        from careers.career_enums import CareerCategory
    except ImportError:  # no career categories: everything is work
        return 'work'
    if getattr(career, 'career_category', None) == CareerCategory.School:
        return 'school'
    return 'work'


def _work_line(sim_info: Any, career: Any) -> str | None:
    word = career_word(career)
    if career.currently_at_work:
        return f'{sim_info.first_name} is at {word} now'
    if career.is_work_time:
        return f'{sim_info.first_name} should be at {word} now'
    (span, start, _end) = career.get_next_work_time()
    if span is None or start is None:
        return None
    return f'{sim_info.first_name} {word} in {_span_text(span)}, {day_text(start)} at {clock_text(start)}'


def work_lines(sim_info: Any) -> list[str]:
    lines: list[str] = []
    tracker = getattr(sim_info, 'career_tracker', None)
    if tracker is None:
        return lines
    for career in list(tracker.careers.values()):
        try:
            line = _work_line(sim_info, career)
        except Exception:  # that career is left out
            simsight_log.error('plus time: work lines')
            continue
        if line is not None:
            lines.append(line)
    return lines


def _owed(household: Any) -> int:
    return int(household.bills_manager.current_payment_owed or 0)


def _money_parts(household: Any) -> list[str]:
    parts = []
    with contextlib.suppress(AttributeError, TypeError, ValueError):  # no funds: left out
        parts.append(f'Household money {int(household.funds.money):,} simoleons')
    try:
        owed = _owed(household)
    except (AttributeError, TypeError, ValueError):  # no bills to read: left out
        owed = 0
    if owed > 0:
        parts.append(f'Unpaid bills {owed:,} simoleons. Pay them at the mailbox')
    return parts


@core.hotkey(ord('T'), 'time and day')
def read_time() -> None:
    import services

    now = services.time_service().sim_now
    parts = [f'{day_text(now)} {clock_text(now)}', speed_text()]
    for info in core.household_sims(instanced_only=False):
        parts.extend(work_lines(info))
    hh = services.active_household()
    if hh is not None:
        parts.extend(_money_parts(hh))
    core.speak('. '.join(p for p in parts if p) + '.')


def _work_reminder(info: Any, uid: Any, career: Any) -> str | None:
    if career.currently_at_work or career.is_work_time:
        return None
    (span, start, _end) = career.get_next_work_time()
    if span is None or start is None:
        return None
    mins = span.in_minutes()
    key = (info.sim_id, uid)
    stamp = int(start.absolute_ticks())
    if not (0 < mins <= 60) or _state.reminded.get(key) == stamp:
        return None
    _state.reminded[key] = stamp
    return (
        f'{info.first_name} has {career_word(career)} in {_span_text(span)}, at {clock_text(start)}'
    )


def _work_reminders() -> list[str]:
    msgs = []
    for info in core.household_sims(instanced_only=False):
        tracker = getattr(info, 'career_tracker', None)
        if tracker is None:
            continue
        for uid, career in list(tracker.careers.items()):
            try:
                msg = _work_reminder(info, uid, career)
            except Exception:  # that career is left out
                simsight_log.error('plus time: work reminder')
                continue
            if msg is not None:
                msgs.append(msg)
    return msgs


def _bill_reminder() -> str | None:
    import services

    try:
        hh = services.active_household()
        owed = _owed(hh) if hh is not None else 0
    except Exception:
        simsight_log.error('plus time: bill reminder')
        return None
    if owed <= 0:
        _state.bills_announced = False
        return None
    if _state.bills_announced:
        return None
    _state.bills_announced = True
    return f'Bills have arrived: {owed:,} simoleons. Pay them at the mailbox'


def _check() -> None:
    if core.active_sim() is None:
        return
    msgs = []
    if core.setting_on('work reminders'):
        msgs.extend(_work_reminders())
    if core.setting_on('bill reminders'):
        bill = _bill_reminder()
        if bill is not None:
            msgs.append(bill)
    if msgs:
        core.speak('. '.join(msgs) + '.', interrupt=False)


core.add_ticker(3.0, _check)


@core.on_zone_started
def _start() -> None:
    _state.bills_announced = False  # unpaid bills are mentioned once after loading


# ---------------------------------------------------------------- say every speed change (P, 1, 2, 3)
# Only changes the player or the game makes count. Pauses while a game window or list is
# open (UI_MODAL) are left out, so opening a menu does not say "Paused".


def _effective(speed: Any) -> Any:
    from clock import ClockSpeedMode

    if speed == ClockSpeedMode.INTERACTION_STARTUP_SPEED:
        return ClockSpeedMode.NORMAL  # a short game-made pause when an action starts
    return speed


def _zone_running(default: bool) -> Any:
    """The current zone when it is running, else None."""
    import services

    zone = services.current_zone()
    if zone is None or not getattr(zone, 'is_zone_running', default):
        return None
    return zone


def _announce_speed(before: Any) -> None:
    try:
        import services

        if not core.setting_on('speak game speed'):
            return
        if _zone_running(False) is None:
            return
        now = _effective(services.game_clock_service().clock_speed)
        if now == before:
            return
        text = speed_text()
        spoken = (text, int(time.monotonic() // 2))
        if _state.last_spoken == spoken:
            return  # the same change reported twice in a row
        _state.last_spoken = spoken
        core.speak(text)
    except Exception:
        simsight_log.error('plus time: announce speed')


_HOOK_MARK = '_a11yplus'


def _wrap_clock(name: str) -> None:
    from clock import GameClock, GameSpeedChangeSource

    original = getattr(GameClock, name)
    if getattr(original, _HOOK_MARK, False):
        return

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        source = kwargs.get('source', args[1] if len(args) > 1 else GameSpeedChangeSource.GAMEPLAY)
        try:
            before = _effective(self.clock_speed)
        except Exception:  # no announcement for this change
            simsight_log.error('plus time: speed before change')
            before = None
        result = original(self, *args, **kwargs)
        if source != GameSpeedChangeSource.UI_MODAL and before is not None:
            core.post(_announce_speed, before)
        return result

    setattr(wrapper, _HOOK_MARK, True)
    setattr(GameClock, name, wrapper)


try:
    for _name in ('push_speed', 'pop_speed', 'set_clock_speed'):
        _wrap_clock(_name)
    core.log('speed announcements hooked')
except Exception:
    simsight_log.error('plus time: hook clock')


# ---------------------------------------------------------------- spoken Build / Live mode


def _in_build_mode() -> bool | None:
    """True in Build mode, False in Live mode, None while no zone is running."""
    zone = _zone_running(True)
    if zone is None:
        return None
    return bool(zone.is_in_build_buy)


def _mode_check() -> None:
    try:
        build = _in_build_mode()
    except Exception:
        simsight_log.error('plus time: mode check')
        return
    if build is None:
        _state.last_mode = None
        return
    if _state.last_mode is None:
        _state.last_mode = build
        return
    if build != _state.last_mode:
        _state.last_mode = build
        core.log('mode: ' + ('build' if build else 'live'))
        if build:
            core.speak(
                'Build mode. Press Alt B for the build helper. Fn F1 goes back to Live mode.'
            )
        else:
            core.speak('Live mode.' + _paused_hint())


def _is_paused() -> bool:
    try:
        import services
        from clock import ClockSpeedMode

        return bool(services.game_clock_service().clock_speed == ClockSpeedMode.PAUSED)
    except Exception:  # said as not paused
        simsight_log.error('plus time: paused')
        return False


def _paused_hint() -> str:
    return ' The game is paused. Press 1 to play.' if _is_paused() else ''


def _paused_queue_check() -> None:
    """Say so when the player gives the Sim an action while the game is paused (it waits in the queue)."""
    try:
        zone = _zone_running(True)
        if zone is None or zone.is_in_build_buy:
            _state.queued_seen = None
            return
        sim = core.active_sim()
        if sim is None:
            return
        ids = {si.id for si in sim.queue}
    except Exception:
        simsight_log.error('plus time: queue check')
        return
    old = _state.queued_seen
    _state.queued_seen = ids
    if old is None or not _is_paused():
        return
    if ids - old:
        core.log('queued while paused')
        core.speak(
            'Added to the queue. The game is paused, so it waits. Press 1 to play.', interrupt=False
        )


core.add_ticker(0.3, _mode_check)
core.add_ticker(0.5, _paused_queue_check)

"""Holiday announcements (Seasons). Leo, 2026-11-12: when a day is a
holiday of any sort, say so, with its traditions, so players know what to do.

Every holiday, built in or custom, starts for a household through EA's
HolidayTracker.activate_holiday(self, holiday_id, from_load) (holidays/
holiday_tracker.py, 1.129.39 bytecode); self._owner is the household. The
name, traditions and days off come from services.holiday_service():
get_holiday_display_name, get_holiday_traditions (tradition classes, each
with _display_data.instance_display_name), get_holiday_time_off_work and
get_holiday_time_off_school.

The announcement is a game notification, which the notification reader
speaks (and the game localizes custom holiday names itself). A holiday that
is already running when a save loads is announced once the loading screen
is gone. Without Seasons there is no holiday service and nothing happens.
"""

from __future__ import annotations

from typing import Any

import simsight_log

_WRAPPED = '_a11y_wrapped'  # marks our activate_holiday wrapper
_pending_load: list[Any] = []  # holiday ids active at load, announced after it


def _log(message: str) -> None:
    simsight_log.line('holidays: ' + message)


def _text(value: Any) -> Any:
    """A tuned display string: some are factories that must be called."""
    if value is None:
        return None
    try:
        from sims4.localization import LocalizedString

        if isinstance(value, LocalizedString):
            return value
    except ImportError:
        pass  # outside the game: treat it as a factory or a plain value
    try:
        return value() if callable(value) else value
    except Exception:
        return None  # a factory that needs arguments: no text for it


def _tradition_line(tradition: Any, loc: Any) -> Any:
    """A tradition's name, with its description when it has one, or None."""
    try:
        data = tradition._display_data
        name = _text(data.instance_display_name)
        description = _text(getattr(data, 'instance_display_description', None))
    except AttributeError:
        return None
    if name is None:
        return None
    return loc.get_name_value_pair(name, description) if description is not None else name


def _time_off(service: Any, holiday_id: Any) -> list[str]:
    off = []
    try:
        if service.get_holiday_time_off_work(holiday_id):
            off.append('work')
        if service.get_holiday_time_off_school(holiday_id):
            off.append('school')
    except Exception as ex:
        _log(f'time off lookup failed: {ex!r}')
    return off


def _message(holiday_id: Any) -> tuple[Any, Any] | None:
    """(title, text) LocalizedStrings for a holiday, or None."""
    import services
    from sims4.localization import LocalizationHelperTuning as Loc

    service = services.holiday_service()
    if service is None:
        return None
    title = _text(service.get_holiday_display_name(holiday_id))
    if title is None:
        return None
    lines = [Loc.get_raw_text('Today is a holiday.')]
    # Each tradition on its own line, with its description: that is what
    # tells the player what to have their Sims do (custom traditions too).
    traditions = [
        line
        for line in (
            _tradition_line(tradition, Loc)
            for tradition in service.get_holiday_traditions(holiday_id) or ()
        )
        if line is not None
    ]
    if traditions:
        lines.append(Loc.get_raw_text('Traditions:'))
        lines.extend(traditions)
    off = _time_off(service, holiday_id)
    if off:
        lines.append(Loc.get_raw_text('No {} today.'.format(' or '.join(off))))
    return title, Loc.get_new_line_separated_strings(*lines)


def announce(holiday_id: Any) -> bool:
    """Show the holiday notification. Simulation thread."""
    try:
        import services
        from ui.ui_dialog_notification import UiDialogNotification

        message = _message(holiday_id)
        if message is None:
            return False
        title, text = message
        dialog = UiDialogNotification.TunableFactory().default(
            services.active_sim_info(), text=lambda *a, **k: text, title=lambda *a, **k: title
        )
        dialog.show_dialog()
        _log(f'announced holiday {holiday_id}')
        return True
    except Exception:
        simsight_log.error(f'holidays: announcement for {holiday_id}')
        return False


def _is_active_household(tracker: Any) -> bool:
    try:
        import services

        household = services.active_household()
    except Exception:
        return False  # no household service yet (loading)
    return household is not None and getattr(tracker, '_owner', None) is household


def _on_activate(tracker: Any, holiday_id: Any, args: tuple[Any, ...], kwargs: Any) -> None:
    if not _is_active_household(tracker):
        return
    from_load = kwargs.get('from_load', args[0] if args else False)
    if from_load:
        _pending_load.append(holiday_id)
    else:
        announce(holiday_id)


def _announce_after_load() -> None:
    # Also catches a holiday already running in a save: the tracker
    # may have been loaded before the household became active.
    import services

    household = services.active_household()
    tracker = household.holiday_tracker if household is not None else None
    active = tracker.active_holiday_id if tracker is not None else None
    if active is not None and active not in _pending_load:
        _pending_load.append(active)
    for holiday_id in list(dict.fromkeys(_pending_load)):
        if active is None or holiday_id == active:
            announce(holiday_id)


def _install() -> None:
    from holidays.holiday_tracker import HolidayTracker

    original = HolidayTracker.activate_holiday
    if getattr(original, _WRAPPED, False):
        return

    def a11y_activate_holiday(self: Any, holiday_id: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, holiday_id, *args, **kwargs)
        try:
            _on_activate(self, holiday_id, args, kwargs)
        except Exception:
            simsight_log.error('holidays: activate hook')
        return result

    setattr(a11y_activate_holiday, _WRAPPED, True)
    HolidayTracker.activate_holiday = a11y_activate_holiday

    import zone

    loaded = zone.Zone.on_loading_screen_animation_finished

    def a11y_holidays_on_loading_screen_animation_finished(
        self: Any, *args: Any, **kwargs: Any
    ) -> Any:
        result = loaded(self, *args, **kwargs)
        try:
            _announce_after_load()
        except Exception:
            simsight_log.error('holidays: load announcement')
        finally:
            del _pending_load[:]
        return result

    zone.Zone.on_loading_screen_animation_finished = (
        a11y_holidays_on_loading_screen_animation_finished
    )
    _log('holiday announcements installed')


try:
    _install()
except Exception as _ex:
    _log(f'not installed: {_ex!r}')

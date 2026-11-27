"""The household's businesses read from EA's business system.

The owned-business panel (HouseholdInfoVenuePanel) shows its figures as
icons, colours and artwork labels. Every business type keeps them in one
place: business.business_manager.BusinessManager. That covers Get to Work
retail stores and businesses mods register through EA's system (WickedWhims'
strip club registers a business type with EA's BusinessTuning and runs a
BusinessManager subclass). The figures are the ones EA sends to the panel
in BusinessManager.construct_business_message: name, open, items sold,
outgoing costs and net profit without wages, business funds, customers
served, markup and revenue, plus the star rating, employees and hourly wages.

Rows only read. Enter opens EA's owned-business panel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import simsight_lang as lang
import simsight_log
import simsight_menu as menu
import simsight_speech as speech

if TYPE_CHECKING:
    from simsight_hub import Row


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


def _money(amount: float) -> str:
    return '{:,} {}'.format(int(amount), lang.t('simoleons'))


def _signed(amount: float) -> str:
    amount = int(amount)
    if amount < 0:
        return '{} {}'.format(lang.t('minus'), _money(-amount))
    return _money(amount)


def _managers() -> list[Any] | None:
    import services

    household = services.active_household()
    if household is None:
        return None
    found = services.business_service().get_business_managers_for_household(household.id) or {}
    return list(found.values())


def _value(read: Callable[[], Any], default: Any = None) -> Any:
    """read(), or default when it fails. Business managers can be a mod's
    subclass (WickedWhims): any of their properties may raise anything."""
    try:
        return read()
    except Exception:
        simsight_log.error('business: value unreadable')
        return default


def business_name(manager: Any) -> str:
    import services

    def zone_name() -> str:
        zone = services.get_persistence_service().get_zone_proto_buff(manager.business_zone_id)
        return str(getattr(zone, 'name', '') or '')

    return _value(zone_name, '') or lang.t('business')


def business_lines(manager: Any) -> list[str]:
    lines = [
        lang.t('business open') if _value(lambda: manager.is_open) else lang.t('business closed')
    ]
    stars = _value(manager.get_star_rating)
    if stars is not None:
        lines.append(lang.t('business stars').format(f'{round(float(stars), 1):g}'))
    funds = _value(lambda: manager.funds.money)
    if funds is not None:
        lines.append(lang.t('business funds').format(_money(funds)))
    lines.append(
        lang.t('business today').format(
            _value(lambda: manager.daily_items_sold, 0),
            # BusinessManager has no public accessor for today's customers.
            _value(lambda: manager._customer_manager.session_customers_served, 0),  # noqa: SLF001
            _money(_value(lambda: manager.daily_revenue, 0)),
            _money(
                _value(lambda: manager.get_daily_outgoing_costs(include_employee_wages=False), 0)
            ),
            _signed(_value(lambda: manager.get_daily_net_profit(include_employee_wages=False), 0)),
        )
    )
    markup = _value(lambda: manager.markup_multiplier)
    if markup is not None:
        lines.append(lang.t('business markup').format(round((float(markup) - 1.0) * 100)))
    employees = _value(lambda: manager.employee_count)
    if employees is not None:
        wages = _value(manager.get_total_employee_wages_per_hour, 0)
        lines.append(lang.t('business employees').format(employees, _money(wages)))
    if _value(manager.meets_minimum_employee_requirment, True) is False:
        lines.append(lang.t('business needs employees'))
    return lines


def _open_panel() -> bool:
    import simsight_hub as hub

    hub.send_ui('OpenHouseholdInfoVenuePanel')
    return True


def businesses_window() -> bool:
    import simsight_hub as hub

    try:
        managers = _managers()
    except Exception:
        simsight_log.error('business: unavailable')
        _say('action unavailable')
        return False
    if managers is None:
        _say('load a household first')
        return False
    rows: list[Row] = []
    for manager in managers:
        try:
            text = ' '.join(business_lines(manager))
        except Exception:
            simsight_log.error(f'business: {type(manager).__name__} unreadable')
            continue
        rows.append((menu.raw_text(business_name(manager)), menu.raw_text(text), _open_panel))
    if not rows:
        _say('no businesses')
        return False
    return hub.show_window('businesses', 'businesses instructions', rows)

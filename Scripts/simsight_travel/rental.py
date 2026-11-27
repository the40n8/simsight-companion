"""Vacation rentals through EA's travel group manager, and their rollback."""

from __future__ import annotations

from typing import Any


import simsight_log

from .state import _Pending, _dialog_active, _funds, _say, _service


def _party(dialog: Any) -> list[Any]:
    """Sim infos the request travels with: the actor plus companions."""
    sims = [getattr(dialog, 'owner', None), *list(getattr(dialog, 'traveling_sims', None) or ())]
    infos: list[Any] = []
    for sim in sims:
        info = getattr(sim, 'sim_info', None) if sim is not None else None
        if info is None or info in infos:
            continue
        infos.append(info)
    return infos


def _grouped(info: Any) -> bool:
    """EA's manager drops every Sim whose household already has a member in
    a travel group, so the whole party is checked at household level."""
    household = getattr(info, 'household', None)
    check = getattr(household, 'any_member_in_travel_group', None)
    if check is not None and check():
        return True
    return bool(info.is_in_travel_group())


def _created_group(manager: Any, party: list[Any], zone_id: int) -> Any:
    """The travel group the rental just created for this party, verified by
    membership and zone; None when it cannot be identified."""
    try:
        group = manager.get_travel_group_by_sim_info(party[0])
        if group is None:
            return None
        from world.street import get_vacation_zone_id

        expected = (zone_id, get_vacation_zone_id(zone_id))
        if getattr(group, 'zone_id', None) not in expected:
            return None
        if any(info not in group for info in party):
            return None
        return group
    except Exception:
        simsight_log.error('travel: created travel group')
        return None


def _rollback_rental(manager: Any, group: Any, household: Any, zone_id: int, cost: int) -> bool:
    """Undo a rental whose dialog answer was refused: release the exact
    group created for this request and return the money to the household
    that paid. Anything unverifiable is reported, never guessed."""
    if group is None:
        simsight_log.line(f'travel: rental of zone {zone_id} could not be identified for rollback')
        return False
    try:
        if not manager.destroy_travel_group_and_release_zone(group):
            simsight_log.line(f'travel: rental of zone {zone_id} could not be released')
            return False
        if cost:
            from protocolbuffers import Consts_pb2

            household.funds.add(cost, Consts_pb2.FUNDS_MONEY_VACATION, count_as_earnings=False)
        simsight_log.line(f'travel: rental of zone {zone_id} rolled back, {cost} returned')
        return True
    except Exception as ex:
        simsight_log.line(f'travel: rental rollback failed for zone {zone_id} ({ex!r})')
        return False


class _Rental:
    """What a rental needs afterwards: to answer the dialog or roll back."""

    __slots__ = ('manager', 'payer', 'service')

    def __init__(self, manager: Any, payer: Any, service: Any) -> None:
        self.manager = manager
        self.payer = payer
        self.service = service


def _rental_refusal(party: list[Any], cost: int) -> str | None:
    """The spoken term when the party may not rent, after logging why."""
    if not party:
        raise ValueError('no travelling Sims')
    for info in party:
        if _grouped(info):
            simsight_log.line(
                'travel: household of {} already has a travel group; vacation refused'.format(
                    getattr(info, 'sim_id', info)
                )
            )
            return 'action unavailable'
    funds = _funds()
    if cost and (funds is None or funds < cost):
        simsight_log.line(f'travel: vacation cost {cost} exceeds funds {funds}; refused')
        return 'cannot afford'
    return None


def _rent(pending: _Pending, zone_id: int, days: int, cost: int) -> _Rental | None:
    """Every check, the zone picked on EA's dialog, then the rental through
    EA's travel group manager. None, after speaking why, when refused."""
    dialog = pending.dialog
    party = pending.party
    try:
        import services

        refusal = _rental_refusal(party, cost)
        if refusal is not None:
            _say(refusal)
            return None
        service = _service()
        if not service.dialog_pick_result(dialog.dialog_id, [zone_id]):
            simsight_log.line(
                f'travel: zone {zone_id} refused by dialog {dialog.dialog_id} before renting; request cancelled'
            )
            _say('action unavailable')
            return None
        import clock

        now = services.time_service().sim_now
        end = now + clock.interval_in_sim_days(days) if days else None
        manager = services.travel_group_manager()
        payer = services.active_household()
        created = manager.create_travel_group_and_rent_zone(
            sim_infos=list(party),
            zone_id=zone_id,
            played=True,
            create_timestamp=now,
            end_timestamp=end,
            cost=cost,
        )
    except Exception as ex:
        simsight_log.line(f'travel: vacation setup failed ({ex!r}); request cancelled')
        _say('action unavailable')
        return None
    if not created:
        simsight_log.line(f'travel: rental of zone {zone_id} refused by the travel group manager')
        _say('action unavailable')
        return None
    return _Rental(manager, payer, service)


def _answer_ok(dialog: Any, service: Any) -> bool:
    try:
        from ui.ui_dialog import ButtonType

        return _dialog_active(dialog) and bool(
            service.dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_OK)
        )
    except Exception as ex:
        simsight_log.line(f'travel: answering dialog {dialog.dialog_id} failed ({ex!r})')
        return False

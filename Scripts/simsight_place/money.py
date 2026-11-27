"""Household funds: charging exactly the price, refunds, and refunds still owed."""

from __future__ import annotations

from typing import Any

import simsight_log

from .state import active_household, household_id, state


def funds() -> int | None:
    household = active_household()
    if household is None:
        return None
    try:
        return int(household.funds.money)
    except (AttributeError, TypeError, ValueError):  # no funds yet: unknown, never guessed
        return None


def _reason(name: str) -> int | None:
    try:
        from protocolbuffers import Consts_pb2

        value = getattr(Consts_pb2, name, None)
        return None if value is None else int(value)
    except (ImportError, TypeError, ValueError) as ex:
        simsight_log.line(f'place: no funds reason {name} ({ex!r})')
        return None


def charge(amount: Any) -> bool:
    """Take exactly amount. True only when the balance fell by exactly that.
    A partial change (an exception half way) is given back; a change we
    cannot see is treated as not charged only when the balance is unknown
    and the call raised."""
    amount = int(amount or 0)
    if amount <= 0:
        return True
    household = active_household()
    reason = _reason('FUNDS_OBJECT_BUY')
    if household is None or reason is None:
        return False
    before = funds()
    error = None
    try:
        taken = bool(household.funds.try_remove(amount, reason, None))
    except Exception as ex:  # EA's funds code: the balance below tells what happened
        simsight_log.error('place: charge')
        taken, error = False, ex
    after = funds()
    if before is not None and after is not None:
        taken_now = before - after
        ok = taken_now == amount
        if not ok and taken_now > 0:
            simsight_log.line(f'place: partial charge {taken_now} of {amount}; giving it back')
            refund(taken_now)
    else:
        ok = taken and error is None
    simsight_log.line(f'place: charge {amount} -> {ok} ({before} to {after}, error {error!r})')
    return ok


def refund(amount: Any) -> bool:
    """Give amount back. Whatever could not be paid is kept as a debt of
    this household that its next build action pays first (settle_owed)."""
    amount = int(amount or 0)
    if amount <= 0:
        return True
    household = active_household()
    reason = _reason('FUNDS_OBJECT_SELL')
    paid = 0
    error = None
    before = funds()
    if household is not None and reason is not None:
        try:
            household.funds.add(amount, reason, None, count_as_earnings=False)
        except Exception as ex:  # EA's funds code: the balance below tells what happened
            simsight_log.error('place: refund')
            error = ex
        after = funds()
        if before is not None and after is not None:
            paid = max(0, min(amount, after - before))
        elif error is None:
            paid = amount
    else:
        after = before
    owner = household_id()
    owed = state.owed
    if paid < amount and owner is not None:
        owed[owner] = owed.get(owner, 0) + amount - paid
    simsight_log.line(
        f'place: refund {amount} paid {paid} ({before} to {after}, error {error!r}, owed {owed.get(owner, 0)})'
    )
    return paid == amount


def settle_owed() -> int:
    """Pay this household any refund that failed earlier. Returns the
    amount paid now. Another household's debt is never touched."""
    owner = household_id()
    owed = state.owed.pop(owner, 0) if owner is not None else 0
    if owed <= 0:
        return 0
    if refund(owed):
        simsight_log.line(f'place: settled {owed} owed to household {owner}')
        return owed
    return 0

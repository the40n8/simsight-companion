"""Vacation rental prices: the lot value, EA's price modifiers and the
client's own answer (simsight.lotprice_enc)."""

from __future__ import annotations

from typing import Any

import sims4.commands

from simsight_commands import real_annotations
import simsight_log

from .state import VACATION, _Pending, _state, _ui


_MAX_DAILY_PRICE = 2**31 - 1  # the client reports an unsigned int; anything larger is not a price


def _lot_values(zone_id: int) -> list[tuple[str, Any]]:
    """Candidate furnished lot values for a zone that may not be loaded, as
    (source, value) pairs. EA's own code reads an unloaded lot through the
    zone manager's uninstantiated zone data (the home lot while away); the
    build-buy call is only valid for a loaded lot and returns a sentinel
    otherwise, so it comes second and only counts when positive. A failure
    is recorded as the value: this list is also the lot value diagnostic."""
    found: list[tuple[str, Any]] = []
    try:
        import services

        zone = services.get_zone(zone_id, allow_uninstantiated_zones=True)
        lot = getattr(zone, 'lot', None)
        if lot is not None:
            found.append(('zone', lot.furnished_lot_value))
    except Exception as ex:
        found.append(('zone', f'error {ex!r}'))
    try:
        import build_buy

        found.append(('build_buy', build_buy.get_lot_value(zone_id, True)[0]))
    except Exception as ex:
        found.append(('build_buy', f'error {ex!r}'))
    return found


def _positive(value: Any) -> float | None:
    """A finite positive number, or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # NaN fails every comparison, so it is refused here too.
    if not 0 < number < float('inf'):
        return None
    return number


def _request_client_price(pending: _Pending, zone_id: int) -> bool:
    """Ask the client for the lot's rental price (the map's own numbers:
    GetNeighborhoodLotData and GetLotRentalPriceCalculator). The answer
    arrives on simsight.lotprice_enc with this request's generation and is kept
    on the request only, never across vacations."""
    return _ui(f'A11y|lotprice|{int(zone_id)}|{pending.generation}')


def _daily_price(zone_id: int, pending: _Pending | None = None) -> int | None:
    """EA's rental panel price. The client's answer for this request is used
    when it has arrived; otherwise the furnished lot value times the tuned
    modifiers, truncated. None when no positive value is available, so the
    row stays unpriced and picking it hands the request to the native map."""
    if pending is not None:
        cached = _positive(pending.prices.get(int(zone_id)))
        if cached is not None:
            return int(cached)
    try:
        from world.rentable_lot_tuning import RentableZoneTuning

        modifiers = RentableZoneTuning.PRICE_MODIFIERS
    except Exception:
        simsight_log.error('travel: rental price modifiers')
        return None
    for _source, value in _lot_values(zone_id):
        number = _positive(value)
        if number is None:
            continue
        price = _positive(number * modifiers.multiply + modifiers.add)
        if price is not None:
            return int(price)
    return None


def _client_price(payload: str) -> bool:
    try:
        parts = str(payload).split('|')
        if len(parts) != 3:
            return False
        zone_id = int(parts[0])
        value = _positive(float(parts[1]))
        generation = int(parts[2])
    except (TypeError, ValueError):
        return False
    if value is not None and not 1 <= int(value) <= _MAX_DAILY_PRICE:
        value = None
    pending = _state.pending
    if pending is None or pending.mode != VACATION or generation != pending.generation:
        simsight_log.line(
            f'travel: client price for zone {zone_id} ignored (generation {generation})'
        )
        return False
    if value is None:
        pending.prices.pop(zone_id, None)
        simsight_log.line(f'travel: client price for zone {zone_id} unusable ({payload!r})')
        return False
    pending.prices[zone_id] = int(value)
    return True


@sims4.commands.Command(
    'simsight.lotprice_enc', 'a11y.lotprice_enc', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_lotprice_enc(payload: str = '', _connection: Any = None) -> bool:
    """Client bridge reply: <zone id>|<per-day price>|<generation> as the map
    computes it. Only the open vacation request that asked (same
    generation) may use it; anything else is dropped."""
    try:
        return _client_price(payload)
    except Exception:
        simsight_log.error('travel: simsight.lotprice_enc')
        return False


@sims4.commands.Command(
    'simsight.lot_value', 'a11y.lot_value', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_lot_value(zone: str = '0', _connection: Any = None) -> bool:
    """Log every lot value source for a zone and the price the list would use."""
    try:
        output = sims4.commands.CheatOutput(_connection)
        try:
            zone_id = int(zone, 0)
        except (TypeError, ValueError):
            output('a11y lot value: usage simsight.lot_value <zone id>')
            return False
        line = f'travel lot value: zone {zone_id} sources {_lot_values(zone_id)} price {_daily_price(zone_id)}'
        simsight_log.line(line)
        output(line)
        return True
    except Exception:
        simsight_log.error('travel: simsight.lot_value')
        return False

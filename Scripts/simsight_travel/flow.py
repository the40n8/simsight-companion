"""The World, place, unit and Stay windows for EA's map picker request."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


import simsight_lang as lang
import simsight_log
import simsight_menu as menu

from .state import (
    TRAVEL,
    VACATION,
    _Pending,
    _Place,
    _dialog_active,
    _funds,
    _say,
    _state,
    _zone_loaded,
    enabled,
)
from .prices import _daily_price
from .places import _buildings, _collect, _find, _needs_native_map, _units_of, _worlds
from .outcomes import _cancel, _cancel_dialog, _commit, _fallback, _native_map
from .window import _later, _open_window, _reply
from .rental import _answer_ok, _created_group, _party, _rent, _rollback_rental

if TYPE_CHECKING:
    from .state import Row


WORLD, PLACE, UNIT = 'world', 'place', 'unit'
_MAP_ROW = 'map'
_BACK_ROW = 'back'

_STAY_ROW = 'stay'
_MAX_STAY_DAYS = 7  # GERentalPanelTunables.MAX_RENTAL_DAYS


def _request_mode(dialog: Any) -> str | None:
    try:
        modes = type(dialog).MapViewMode
        mode = dialog.map_view_mode.mode
    except AttributeError:
        return None  # not a map view request this list knows
    if mode == modes.TRAVEL:
        return TRAVEL
    if mode == modes.VACATION:
        return VACATION
    return None


def _detail(place: _Place) -> Any:
    parts = []
    if place.units:
        parts.append(menu.raw_text(lang.t('units count').format(len(place.units))))
    if place.venue is not None:
        parts.append(place.venue)
    if place.household and place.label() == place.name:
        parts.append(menu.raw_text(str(place.household)))
    if place.residents:
        parts.append(menu.raw_text(lang.t('residents').format(', '.join(place.residents))))
    if place.price is not None:
        parts.append(menu.raw_text(lang.t('price per day').format(place.price)))
    return menu.join_localized(*parts) if parts else None


def _map_row() -> Row:
    return (menu.raw_text(lang.t('open the map instead')), None, (_MAP_ROW, 0))


def _show_worlds(pending: _Pending) -> bool:
    rows: list[Row] = [
        (
            label if label is not None else menu.raw_text(name),
            menu.raw_text(lang.t('places count').format(count)),
            (WORLD, key),
        )
        for key, name, count, label in pending.worlds
    ]
    rows.append(_map_row())
    return _open_window(pending, 'travel destinations', 'choose a world', rows, _on_world)


def _on_world(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _MAP_ROW:
        _later(pending, _fallback)
    else:
        _later(pending, _show_places, key)


def _show_places(pending: _Pending, world_key: int) -> bool:
    places = [p for p in pending.places if p.world_key == world_key]
    if not places:
        return _show_worlds(pending)
    if pending.mode == VACATION:
        for place in places:
            place.price = _daily_price(place.zone_id, pending)
    rows: list[Row] = [(menu.raw_text(lang.t('back to worlds')), None, (_BACK_ROW, 0))]
    rows.extend(
        (menu.raw_text(entry.label()), _detail(entry), (PLACE, entry.zone_id))
        for entry in sorted(_buildings(places), key=lambda p: p.label().lower())
    )
    rows.append(_map_row())
    return _open_window(pending, 'travel destinations', 'choose a place', rows, _on_place)


def _on_place(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _BACK_ROW:
        _later(pending, _show_worlds)
    elif kind == _MAP_ROW:
        _later(pending, _fallback)
    elif _units_of(pending, key):
        _later(pending, _show_units, key)
    else:
        _choose(pending, _find(pending, key))


def _show_units(pending: _Pending, master: int) -> bool:
    units = _units_of(pending, master)
    if not units:
        return _show_worlds(pending)
    rows: list[Row] = [
        (menu.raw_text(lang.t('back to places')), None, (_BACK_ROW, units[0].world_key))
    ]
    rows.extend(
        (menu.raw_text(place.label()), _detail(place), (UNIT, place.zone_id))
        for place in sorted(units, key=lambda p: p.label().lower())
    )
    return _open_window(pending, 'travel destinations', 'choose a unit', rows, _on_unit)


def _on_unit(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _BACK_ROW:
        _later(pending, _show_places, key)
    else:
        _choose(pending, _find(pending, key))


def _choose(pending: _Pending, place: _Place | None) -> None:
    if place is None:
        _cancel(pending)
        return
    if pending.mode == VACATION:
        if place.price is None:
            place.price = _daily_price(place.zone_id, pending)  # the client answer may have arrived
        if place.rental is True and place.price is not None:
            _later(pending, _show_stay, place.zone_id)
        else:
            # No usable price: the native panel decides, before anything is created.
            simsight_log.line(f'travel: zone {place.zone_id} has no rental price; native map')
            _later(pending, _fallback)
        return
    if _needs_native_map(place):
        simsight_log.line(f'travel: zone {place.zone_id} needs the native business selector')
        _say('map required here')
        _later(pending, _fallback)
        return
    _later(pending, _commit, place.zone_id)


def _stay_row(zone_id: int, price: int, days: int, funds: int | None) -> Row:
    cost = int(price * days)
    label = (
        lang.t('stay one day').format(cost) if days == 1 else lang.t('stay days').format(days, cost)
    )
    detail = menu.raw_text(lang.t('cannot afford')) if funds is not None and cost > funds else None
    return (menu.raw_text(label), detail, (_STAY_ROW, (zone_id, days, cost)))


def _show_stay(pending: _Pending, zone_id: int) -> bool:
    place = _find(pending, zone_id)
    if place is not None:
        place.price = _daily_price(zone_id, pending)  # the client may have answered since
    if place is None or place.price is None:
        return _fallback(pending)
    price = place.price
    funds = _funds()
    rows: list[Row] = [
        (menu.raw_text(lang.t('back to places')), None, (_BACK_ROW, place.world_key))
    ]
    rows.extend(_stay_row(zone_id, price, days, funds) for days in range(1, _MAX_STAY_DAYS + 1))
    return _open_window(pending, 'travel destinations', 'choose a stay', rows, _on_stay)


def _on_stay(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _BACK_ROW:
        _later(pending, _show_places, key)
    else:
        zone_id, days, quoted = key
        _later(pending, _rent_and_commit, zone_id, days, quoted)


def _rent_and_commit(pending: _Pending, zone_id: int, days: int, quoted: int | None = None) -> bool:
    """Validate the answer, rent the lot through EA's travel group manager
    exactly as the native rental panel does, then answer the dialog. Every
    check happens before the rental; a refused answer after the rental is
    rolled back, and there is never a native fallback after a rental."""
    if _state.pending is not pending or pending.rented:
        return False
    place = _find(pending, zone_id)
    dialog = pending.dialog
    if place is None or not _dialog_active(dialog):
        _say('action unavailable')
        return _cancel(pending)
    # The client's answer for this request may have arrived, changed or been
    # withdrawn since the stay was quoted: read it again before any sum.
    price = place.price = _daily_price(zone_id, pending)
    if days and price is None:
        simsight_log.line(f'travel: zone {zone_id} has no usable price at commit; native map')
        _say('map required here')
        return _fallback(pending)
    cost = int(price * days) if days and price is not None else 0
    if quoted is not None and cost != quoted:
        # The price moved after the stay was quoted: never charge an amount
        # the player has not heard; show the stay window again instead.
        simsight_log.line(
            f'travel: quoted {quoted} but current cost {cost} for zone {zone_id}; re-quoting'
        )
        _say('price changed')
        return _show_stay(pending, zone_id)
    rental = _rent(pending, zone_id, days, cost)
    if rental is None:
        return _cancel(pending)
    pending.rented = True
    group = _created_group(rental.manager, pending.party, zone_id)
    simsight_log.line(
        'travel: zone {} rented for {} days at {} (group {})'.format(
            zone_id, days, cost, getattr(group, 'id', 'unverified')
        )
    )
    _state.pending = None
    if _answer_ok(dialog, rental.service):
        simsight_log.line(f'travel: dialog {dialog.dialog_id} answered with zone {zone_id}')
        return True
    simsight_log.line(
        f'travel: dialog {dialog.dialog_id} did not accept OK after the rental; rolling back'
    )
    _rollback_rental(rental.manager, group, rental.payer, zone_id, cost)
    _say('action unavailable')
    _cancel_dialog(dialog)
    return False


def _prepare(pending: _Pending) -> None:
    """Collect the request's destinations; raises when the list cannot be
    offered, so the native map takes it."""
    dialog = pending.dialog
    vacation = pending.mode == VACATION
    if vacation:
        pending.party = _party(dialog)
        if not pending.party:
            raise ValueError('no travelling Sims')
        import services

        services.travel_group_manager()  # raises when vacations are unavailable
        if _funds() is None:
            raise ValueError('household funds unavailable')
        _state.price_generation += 1
        pending.generation = _state.price_generation
    pending.places = _collect(dialog, vacation=vacation, pending=pending)
    _state.last_rows = list(pending.places)
    pending.worlds = _worlds(pending.places)
    if not pending.worlds:
        raise ValueError('no destinations')


def _distribute(dialog: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    mode = _request_mode(dialog)
    if not (enabled() and mode is not None and _zone_loaded()):
        simsight_log.line(
            'travel: dialog {} to the native map (list {}, mode {}, zone {})'.format(
                getattr(dialog, 'dialog_id', '?'),
                'on' if enabled() else 'off',
                mode,
                'loaded' if _zone_loaded() else 'none',
            )
        )
        return _native_map(dialog, msg_type, dialog_msg, kwargs)
    if _state.pending is not None:
        # A second request while one list is open: the older one is
        # cancelled natively so no invisible interaction stays behind.
        simsight_log.line(
            f'travel: dialog {_state.pending.dialog.dialog_id} superseded by dialog {dialog.dialog_id}'
        )
        _cancel(_state.pending)
    pending = _Pending(dialog, msg_type, dialog_msg, kwargs, mode)
    try:
        _prepare(pending)
    except ValueError as ex:  # _prepare's own reasons
        simsight_log.line(f'travel: list unavailable ({ex!r}), native map instead')
        return _native_map(dialog, msg_type, dialog_msg, kwargs)
    except Exception:
        simsight_log.error('travel: list unavailable, native map instead')
        return _native_map(dialog, msg_type, dialog_msg, kwargs)
    _state.pending = pending
    if _show_worlds(pending):
        simsight_log.line(
            f'travel: dialog {dialog.dialog_id} listed as {len(pending.places)} places in {len(pending.worlds)} worlds'
        )
    return None

"""Placing the product: the checks before anything exists, the request to
the client, then creating, positioning, registering and paying for the object."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any, Callable

import simsight_lang as lang
import simsight_log

from .announce import announce_exact, announce_placed
from .catalog import forget, product, product_name
from .money import charge, funds, settle_owed
from .needs import NEEDS, requirement
from .slots import free_slot, put_in_slot
from .spot import destroy_object, drop_native_hand, good_location, register, yaw
from .state import (
    ACK_SECONDS,
    MAX_HISTORY,
    REPEAT_GUARD_SECONDS,
    Placement,
    Purchase,
    household_id,
    in_build_mode,
    last_placement,
    lot_zone_id,
    pending,
    say,
    say_text,
    state,
    tile_key,
)

if TYPE_CHECKING:
    from objects.game_object import GameObject

    from simsight_chair_snap import ChairRequest

    from .area import LotArea
    from .state import TileKey


def _repeated(item: dict[str, Any], key: TileKey, guard: bool) -> bool:
    """A second Enter on the tile just placed: asks for a third (spoken)."""
    if not (
        guard
        and key in state.repeat_keys
        and time.monotonic() - state.repeat_time < REPEAT_GUARD_SECONDS
    ):
        return False
    state.repeat_time = 0.0
    say(
        [
            lang.text(
                'Already placed here. Press Enter again to place another ',
                'Już umieszczono tutaj. Naciśnij Enter ponownie, aby umieścić kolejny: ',
            ),
            item['name'] or product_name(),
            '.',
        ]
    )
    return True


def _catalog_price(item: dict[str, Any]) -> int | None:
    """The product's price, or None (spoken, product forgotten) when it is gone."""
    try:
        import services

        definition = services.definition_manager().get(item['id'], get_fallback_definition_id=False)
        price = int(getattr(definition, 'price', 0) or 0)
    except Exception:  # EA loads the definition's tuning: treated as a product that is gone
        simsight_log.error('place: catalog price')
        definition, price = None, 0
    if definition is None:
        forget()
        say_text(
            'That catalog object is no longer available.',
            'Ten obiekt z katalogu nie jest już dostępny.',
        )
        return None
    return price


def _point_refused(position: Any, level: int, water: bool) -> bool:
    """The point is off the lot or in a pool (unless water is wanted); spoken."""
    try:
        import build_buy
        import services

        if not services.active_lot().is_position_on_lot(position):
            say_text('That point is not on this lot.', 'Ten punkt nie należy do tej parceli.')
            return True
        if build_buy.is_location_pool(position, level) and not water:
            say_text('That point is in a pool.', 'Ten punkt jest w basenie.')
            return True
    except Exception:  # EA's lot and pool checks: refused, never guessed
        simsight_log.error('place: tile check')
        say_text(
            'This point could not be checked, so nothing was bought.',
            'Nie udało się sprawdzić tego punktu, więc nic nie kupiono.',
        )
        return True
    return False


def _checks(
    position: Any, level: int, key: TileKey, guard: bool, exact: bool = False
) -> tuple[dict[str, Any], int, str | None] | None:
    """Everything that can refuse before an object exists. Speaks the refusal;
    returns (item, price, need) or None."""
    item = product()
    if item is None:
        say_text(
            'Choose an object in the Build catalog first.',
            'Najpierw wybierz obiekt w katalogu trybu budowania.',
        )
        return None
    if not in_build_mode():
        say_text('Enter Build mode first.', 'Najpierw włącz tryb budowania.')
        return None
    if _repeated(item, key, guard):
        return None
    need = requirement(item['id'])
    # Bare ground, water, ceiling and fence objects go through here only from
    # the Build cursor's experimental placement (simsight_surfaces), with
    # exact=True, so the game's own placement test still decides.
    import simsight_surfaces as surfaces

    experimental = exact and surfaces.handles(need)
    if need in NEEDS and not experimental:
        say_text(*NEEDS[need])
        return None
    price = _catalog_price(item)
    if price is None:
        return None
    money = funds()
    if money is not None and price > money:
        say_text(
            f'Not enough money. It costs {price} and you have {money}.',
            f'Za mało pieniędzy. Kosztuje {price}, a masz {money}.',
        )
        return None
    if _point_refused(position, level, water=experimental and need == 'water'):
        return None
    return item, price, need


def _start_ack_timer(serial: int) -> None:
    try:
        import simsight_pump as pump
        import simsight_steps as steps

        steps.after(ACK_SECONDS, pump.post, _ack_timeout, serial)
    except Exception:  # without the timer, the late answer is refused by acknowledged()
        simsight_log.error('place: acknowledgement timer')


def place(  # noqa: PLR0913, PLR0917 - its callers (grid, surfaces, chairs, furnishing) pass these
    position: Any,
    level: int,
    routing_surface: Any,
    angle: float = 0.0,
    exact: bool = False,
    surface_id: Any = None,
    chair_request: ChairRequest | None = None,
    area: LotArea | None = None,
) -> bool:
    """Ctrl+Alt+Enter on the grid: ask the client to put down EA's held
    product; the object is bought and placed when it answers (simsight.buildhand).
    exact (the Build cursor): put it at position and angle as they are,
    after the game's own placement test, instead of searching nearby.
    True when the request went out."""
    key = tile_key(position, level)
    now = time.monotonic()
    if pending.placement is not None and now - pending.at < ACK_SECONDS:
        say_text('Still placing the last one.', 'Trwa umieszczanie poprzedniego obiektu.')
        return False
    settle_owed()
    if _checks(position, level, key, guard=area is None, exact=exact) is None:
        return False
    pending.serial += 1
    serial = pending.serial
    pending.at = now
    pending.placement = Placement(position, level, routing_surface, angle, key, exact, area)
    pending.zone = lot_zone_id()
    pending.product = product()
    pending.household = household_id()
    pending.surface_id = surface_id
    pending.chair_request = chair_request
    if not drop_native_hand(serial):
        pending.placement = None
        say_text(
            'The Build tool is not answering. Nothing was bought.',
            'Narzędzie budowania nie odpowiada. Nic nie kupiono.',
        )
        return False
    _start_ack_timer(serial)
    return True


def _ack_timeout(serial: int) -> None:
    if serial == pending.serial and pending.placement is not None:
        pending.placement = None
        simsight_log.line(f'place: no buildhand acknowledgement for {serial}')
        say_text(
            'The Build tool did not answer. Nothing was bought.',
            'Narzędzie budowania nie odpowiedziało. Nic nie kupiono.',
        )


def cancel_pending() -> None:
    """A mode, product or grid change: an unanswered placement is dropped."""
    pending.placement = None


def acknowledged(serial: int, ok: bool) -> bool:
    """The client answered drop request serial. Place only when it put the
    product down (ok) and nothing changed meanwhile: same zone, still Build
    mode, same product."""
    placement = pending.placement
    if serial != pending.serial or placement is None:
        return False
    pending.placement = None
    if time.monotonic() - pending.at > ACK_SECONDS:
        # A backlogged pump may run this before the timer's own turn.
        simsight_log.line(f'place: acknowledgement {serial} arrived too late')
        say_text(
            'The Build tool did not answer. Nothing was bought.',
            'Narzędzie budowania nie odpowiedziało. Nic nie kupiono.',
        )
        return False
    if not ok:
        say_text(
            'Press Escape to put down the current Build tool, then try again. Nothing was bought.',
            'Naciśnij Escape, aby odłożyć bieżące narzędzie budowania, i spróbuj ponownie. Nic nie kupiono.',
        )
        return False
    item = product()
    if (
        pending.zone != lot_zone_id()
        or not in_build_mode()
        or item is not pending.product
        or household_id() != pending.household
    ):
        simsight_log.line(f'place: acknowledgement {serial} arrived after a change; dropped')
        return False
    return _execute(placement)


def _execute(p: Placement) -> bool:
    checked = _checks(p.position, p.level, p.key, guard=False, exact=p.exact)
    if checked is None:
        return False
    item, price, need = checked
    try:
        from objects.system import create_object

        obj = create_object(item['id'])
    except Exception:  # EA creates and initialises every component
        simsight_log.error('place: create_object')
        obj = None
    if obj is None:
        say_text('The game could not create that object.', 'Gra nie mogła utworzyć tego obiektu.')
        return False
    try:
        return _finish(obj, item, price, need, p)
    except Exception:  # anything before payment: the unpaid object is removed
        simsight_log.error('place: placement')
        simsight_log.line('place: placement raised; removing the object')
        destroy_object(obj)
        say_text(
            'The game could not place that object, so nothing was bought.',
            'Gra nie mogła umieścić tego obiektu, więc nic nie kupiono.',
        )
        return False


def _area_location(obj: GameObject, area: LotArea, level: int, routing_surface: Any) -> Any:
    import build_buy
    import services
    import sims4.math

    lot = services.active_lot()
    # Spread the attempts across the area when the sampling grid found many
    # anchors. Try the most central first and still search near its edges.
    step = max(1, math.ceil(len(area.points) / 9))
    for point in area.points[::step]:
        if not lot.is_position_on_lot(point) or build_buy.is_location_pool(point, level):
            continue
        for turn in range(4):
            spot = good_location(obj, point, yaw(turn * sims4.math.PI / 2), routing_surface)
            if (
                spot is not None
                and area.contains(spot[0])
                and lot.is_position_on_lot(spot[0])
                and not build_buy.is_location_pool(spot[0], level)
                and (level == 0 or build_buy.has_floor_at_location(spot[0], level))
            ):
                return spot
    return None


def _refuse(obj: GameObject, en: str, pl: str) -> None:
    """Remove the unpaid object and say why."""
    destroy_object(obj)
    say_text(en, pl)


def _chosen_slot(obj: GameObject, item: dict[str, Any], p: Placement) -> tuple[Any, Any] | None:
    """The chair seat or focused-surface slot the request named, rechecked."""
    chair_request = pending.chair_request
    if chair_request is not None:
        import simsight_chair_snap as chair_snap

        found = chair_snap.validate(chair_request, obj, item, p.position, p.level)
        if found is None:
            _refuse(
                obj,
                'The chair seat changed or the game refused it. Nothing was bought.',
                'Miejsce na krzesło zmieniło się albo gra je odrzuciła. Nic nie kupiono.',
            )
        return found
    import simsight_layers as layers

    found = layers.selected_surface_slot(pending.surface_id, obj, p.level)
    if found is None:
        _refuse(
            obj,
            'The focused surface changed or has no compatible free slot. Nothing was bought.',
            'Wybrana powierzchnia zmieniła się albo nie ma zgodnego wolnego miejsca. Nic nie kupiono.',
        )
    return found


def _chair_attached(surface: Any, slot: Any, obj: GameObject) -> bool:
    """No chair request, or the chair sits in its seat."""
    if pending.chair_request is None:
        return True
    import simsight_chair_snap as chair_snap

    return chair_snap.attached(surface, slot, obj)


def _into_slot(
    obj: GameObject, p: Placement, found: tuple[Any, Any] | None
) -> tuple[Any, Any] | None:
    """Put obj into the named slot, or a free one nearby (in the area)."""
    if found is None:
        found = (
            free_slot(obj, p.position, p.level)
            if p.area is None
            else free_slot(obj, p.position, p.level, accepts=p.area.contains)
        )
    if found is None or not put_in_slot(obj, found[0], found[1]):
        if p.area is None:
            _refuse(
                obj,
                'It needs a free spot on a desk or table. Move the cursor onto one.',
                'To wymaga wolnego miejsca na biurku lub stole. Przesuń kursor na taki mebel.',
            )
        else:
            _refuse(
                obj,
                'No compatible free surface in that area. Nothing was bought.',
                'Brak pasującej wolnej powierzchni w tym obszarze. Nic nie kupiono.',
            )
        return None
    if not _chair_attached(found[0], found[1], obj):
        _refuse(
            obj,
            'The chair did not attach to its seat. Nothing was bought.',
            'Krzesło nie zostało przypięte do miejsca. Nic nie kupiono.',
        )
        return None
    return found


def _move(obj: GameObject, what: str, move: Callable[[], Any]) -> bool:
    try:
        move()
    except Exception:  # EA's move runs the object's location callbacks
        simsight_log.error(f'place: {what}')
        _refuse(
            obj, 'The game could not position that object.', 'Gra nie mogła ustawić tego obiektu.'
        )
        return False
    return True


def _exactly(obj: GameObject, item: dict[str, Any], p: Placement) -> bool:
    """At the Build cursor's position and angle, if the game's test agrees."""
    import simsight_tiles as tiles

    ok, why = tiles.test_fit(obj, item['id'], p.position, p.angle, p.level)
    if ok is False:
        _refuse(
            obj,
            f'It no longer fits there ({why}), so nothing was bought.',
            f'Już się tam nie mieści ({why}), więc nic nie kupiono.',
        )
        return False
    return _move(
        obj,
        'exact move_to',
        lambda: obj.move_to(
            translation=p.position, orientation=yaw(p.angle), routing_surface=p.routing_surface
        ),
    )


def _searched(obj: GameObject, p: Placement) -> bool:
    """Where the game finds room near the cursor, or anywhere in the area."""
    spot = (
        good_location(obj, p.position, yaw(p.angle), p.routing_surface)
        if p.area is None
        else _area_location(obj, p.area, p.level, p.routing_surface)
    )
    if spot is None:
        if p.area is None:
            _refuse(
                obj,
                'No room for it here. Try another point.',
                'Brak tu miejsca. Wybierz inny punkt.',
            )
        else:
            _refuse(
                obj,
                'It could not fit in that area. Nothing was bought.',
                'Obiekt nie mieści się w tym obszarze. Nic nie kupiono.',
            )
        return False
    return _move(
        obj,
        'move_to',
        lambda: obj.move_to(
            translation=spot[0], orientation=spot[1], routing_surface=p.routing_surface
        ),
    )


def _position(
    obj: GameObject, item: dict[str, Any], need: str | None, p: Placement
) -> tuple[Any, Any] | None:
    """Put obj in place: (surface, slot) for a slot, (None, None) elsewhere;
    None when refused (obj removed, refusal spoken)."""
    found = None
    if pending.chair_request is not None or pending.surface_id is not None:
        found = _chosen_slot(obj, item, p)
        if found is None:
            return None
        need = 'slot'
    if need == 'slot':
        return _into_slot(obj, p, found)
    placed = _exactly(obj, item, p) if p.exact else _searched(obj, p)
    return (None, None) if placed else None


def _finish(
    obj: GameObject, item: dict[str, Any], price: int, need: str | None, p: Placement
) -> bool:
    """Put a freshly created obj in place, then own, register and pay for
    it. Any refusal removes it; an exception is handled by the caller."""
    placed = _position(obj, item, need, p)
    if placed is None:
        return False
    surface, slot = placed
    owner = household_id()
    if owner is None:
        _refuse(
            obj,
            'No active household to own it, so nothing was bought.',
            'Brak aktywnej rodziny, która mogłaby go posiadać, więc nic nie kupiono.',
        )
        return False
    obj.set_household_owner_id(owner)
    if not register(obj):
        _refuse(
            obj,
            'The lot would not accept it, so nothing was bought.',
            'Parcela go nie przyjęła, więc nic nie kupiono.',
        )
        return False
    if not _chair_attached(surface, slot, obj):
        _refuse(
            obj,
            'The game did not retain the chair attachment. Nothing was bought.',
            'Gra nie zachowała przypięcia krzesła. Nic nie kupiono.',
        )
        return False
    if price and not charge(price):
        _refuse(
            obj,
            'Payment failed, so nothing was bought.',
            'Płatność nie powiodła się, więc nic nie kupiono.',
        )
        return False
    # Paid: from here on the object is the household's. Nothing below may
    # raise to the caller, whose clean-up would remove a paid object.
    _after_payment(obj, item, price, owner, surface, p)
    return True


def _record(obj: GameObject, item: dict[str, Any], price: int, owner: Any, key: TileKey) -> None:
    state.history.append(Purchase(obj.id, price, item['name'], owner, lot_zone_id()))
    del state.history[:-MAX_HISTORY]
    last_placement.update(obj_id=obj.id, household=owner, zone=lot_zone_id(), position=None)
    state.repeat_keys, state.repeat_time = (key,), time.monotonic()


def _after_payment(
    obj: GameObject, item: dict[str, Any], price: int, owner: Any, surface: Any, p: Placement
) -> None:
    try:
        _record(obj, item, price, owner, p.key)
    except Exception:  # paid: nothing may raise past this point
        simsight_log.error('place: placed, but recording it failed')
    simsight_log.line(
        'place: placed {} {!r} for {}'.format(getattr(obj, 'id', '?'), item.get('name'), price)
    )
    try:
        if pending.chair_request is not None and surface is not None:
            import simsight_chair_snap as chair_snap

            chair_snap.announce(obj, item, price, surface)
        elif p.exact and surface is None:
            announce_exact(obj, item, price, p.level, p.key)
        else:
            announce_placed(obj, item, price, surface, p)
    except Exception:  # paid: nothing may raise past this point
        simsight_log.error('place: placed, but the announcement failed')
        try:
            say_text('Placed.', 'Umieszczono.')
        except Exception:
            simsight_log.error('place: placed announcement')

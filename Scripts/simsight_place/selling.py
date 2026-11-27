"""Turning, undoing and selling: the last placed object, the history, and
objects on the lot the household may sell."""

from __future__ import annotations

import time
from typing import Any, Callable

import simsight_lang as lang
import simsight_log

from .announce import name_parts
from .money import refund, settle_owed
from .spot import destroy_object, good_location, yaw
from .state import (
    SELL_CONFIRM_SECONDS,
    build_only,
    household_id,
    last_placement,
    lot_zone_id,
    say,
    say_text,
    state,
)


def last_position() -> Any:
    return last_placement['position']


def _last_object() -> Any:
    obj_id = last_placement['obj_id']
    if (
        obj_id is None
        or last_placement['household'] != household_id()
        or last_placement['zone'] != lot_zone_id()
    ):
        return None
    try:
        import services

        return services.object_manager().get(obj_id)
    except AttributeError:  # no object manager while the game loads
        return None


def rotate(step: int) -> bool:
    """Turn the last placed object a quarter turn (step -1 or 1), where it fits."""
    if not build_only():
        return False
    obj = _last_object()
    if obj is None:
        say_text(
            'Place an object first, then rotate it.', 'Najpierw umieść obiekt, a potem go obróć.'
        )
        return False
    if getattr(obj, 'parent', None) is not None:
        say_text(
            'It sits on a surface and keeps its direction.',
            'Stoi na powierzchni i zachowuje swój kierunek.',
        )
        return False
    import sims4.math

    try:
        angle = sims4.math.yaw_quaternion_to_angle(obj.orientation) + step * sims4.math.PI / 2
        spot = good_location(obj, obj.position, yaw(angle), obj.routing_surface, ignore=(obj.id,))
        if spot is None:
            say_text('No room to turn it here.', 'Brak miejsca, aby go tu obrócić.')
            return False
        obj.move_to(translation=spot[0], orientation=spot[1])
        last_placement['position'] = obj.position
    except Exception:  # EA's footprint search and move
        simsight_log.error('place: rotate')
        say_text('Could not rotate it.', 'Nie udało się go obrócić.')
        return False
    say_text(
        'Rotated a quarter turn {}.'.format('left' if step < 0 else 'right'),
        'Obrócono o ćwierć obrotu w {}.'.format('lewo' if step < 0 else 'prawo'),
    )
    return True


def _say_removed(name: Any, price: int, paid: bool) -> None:
    if price and not paid:
        say(
            [
                name,
                lang.text(
                    ' removed. The refund failed and will be retried with your next Build action.',
                    ' usunięto. Zwrot pieniędzy nie powiódł się i zostanie ponowiony przy następnej akcji.',
                ),
            ]
        )
    else:
        say(
            [
                name,
                lang.text(
                    f' removed, {price} simoleons refunded.',
                    f' usunięto, zwrócono {price} simoleonów.',
                ),
            ]
        )


def _undo_purchase(index: int, obj: Any) -> bool:
    """Remove the purchase at history index and refund it."""
    purchase = state.history[index]
    if _occupied(obj):
        say_text(*CANNOT_SELL['occupied'])
        return False
    if not destroy_object(obj):
        say_text('Could not remove it.', 'Nie udało się go usunąć.')
        return False
    del state.history[index]
    paid = refund(purchase.price)
    if last_placement['obj_id'] == purchase.obj_id:
        last_placement['obj_id'] = None
    state.repeat_keys = ()
    _say_removed(purchase.name, purchase.price, paid)
    return True


def undo() -> bool:
    """Remove the most recent placement and give the money back."""
    if not build_only():
        return False
    import services

    settle_owed()
    owner, zone = household_id(), lot_zone_id()
    history = state.history
    for index in range(len(history) - 1, -1, -1):
        purchase = history[index]
        if purchase.household != owner or purchase.zone != zone:
            continue  # another family's or another lot's purchase
        obj = services.object_manager().get(purchase.obj_id)
        if obj is None:
            del history[index]  # sold or removed some other way
            continue
        return _undo_purchase(index, obj)
    say_text('Nothing to undo.', 'Nie ma czego cofnąć.')
    return False


def object_value(obj: Any) -> int:
    """What the game says obj is worth now. Its current value when it has
    one, zero included (a worthless object sells for nothing); the catalog
    price only when the object reports no value at all."""
    getters: tuple[Callable[[], Any], ...] = (
        lambda: obj.current_value,
        lambda: obj.definition.price,
    )
    for getter in getters:
        try:
            value = getter()
        except AttributeError:  # no such value: try the next
            continue
        except Exception:  # a value property can fail any way: try the next
            simsight_log.error('place: object value')
            continue
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):  # not a number: try the next
            continue
    return 0


CANNOT_SELL = {
    'sim': ('Sims cannot be sold.', 'Simów nie można sprzedać.'),
    'offlot': ('That is not on this lot.', 'To nie znajduje się na tej parceli.'),
    'hidden': ('That object cannot be sold from here.', 'Tego obiektu nie można stąd sprzedać.'),
    'protected': (
        'The game does not allow deleting that object.',
        'Gra nie pozwala usunąć tego obiektu.',
    ),
    'foreign': ('That belongs to another household.', 'To należy do innej rodziny.'),
    'occupied': (
        'Something is on it, inside it or using it. Remove that first.',
        'Coś na nim stoi, jest w nim albo ktoś go używa. Najpierw to usuń.',
    ),
}


def _occupied(obj: Any) -> bool:
    """True when removing obj would take something else with it for obj's
    price only (live 2026-09-26: X offered a desk with a computer on it):
    any child, hidden ones included (an object in a slot, a Sim using it),
    or anything in its own inventory, which EA purges with its owner
    (InventoryComponent.on_remove; shared inventories survive)."""
    try:
        if getattr(obj, 'children', None):
            return True
        inventory = getattr(obj, 'inventory_component', None)
        if inventory is not None and not inventory.is_shared_inventory:
            return any(True for _item in inventory)
    except Exception:  # EA's children and inventory: cannot tell
        simsight_log.error('place: occupancy check')
        return True  # cannot tell: never risk what it holds
    return False


def _refusal(obj: Any) -> str | None:
    if getattr(obj, 'is_sim', False):
        return 'sim'
    if obj.is_in_inventory() or obj.is_hidden():
        return 'hidden'
    if getattr(obj, 'non_deletable', False) or getattr(obj, 'non_deletable_by_user', False):
        return 'protected'
    if _occupied(obj):
        return 'occupied'
    import services

    if not services.active_lot().is_position_on_lot(obj.position):
        return 'offlot'
    owner = obj.get_household_owner_id()
    if owner and owner != household_id():
        return 'foreign'
    return None


def sell_refusal(obj: Any) -> str | None:
    """Why obj may not be sold here, or None. Uses the game's own flags."""
    try:
        return _refusal(obj)
    except Exception:  # EA's object flags: refused, never guessed
        simsight_log.error('place: sell check')
        return 'hidden'


def _say_sold(name: Any, value: int, paid: bool) -> None:
    if value and not paid:
        say(
            [
                name,
                lang.text(
                    ' removed. The money did not come back yet and will be retried with your next Build action.',
                    ' usunięto. Pieniądze jeszcze nie wróciły; zwrot zostanie ponowiony przy następnej akcji.',
                ),
            ]
        )
    else:
        say(
            [
                name,
                lang.text(
                    f' sold for {value} simoleons.',
                    f' sprzedano za {value} simoleonów.',
                ),
            ]
        )


def sell(obj: Any) -> bool:
    """Sell obj; asks for a second press within SELL_CONFIRM_SECONDS."""
    if not build_only():
        state.sell_id = None
        return False
    settle_owed()
    if obj is None:
        say_text('Nothing here to sell.', 'Nie ma tu nic do sprzedania.')
        return False
    refusal = sell_refusal(obj)
    if refusal is not None:
        state.sell_id = None
        say_text(*CANNOT_SELL[refusal])
        return False
    now = time.monotonic()
    value = object_value(obj)
    name = name_parts(obj)
    if state.sell_id != obj.id or now - state.sell_time > SELL_CONFIRM_SECONDS:
        state.sell_id, state.sell_time = obj.id, now
        say(
            [
                lang.text('Sell ', 'Sprzedać: '),
                name,
                lang.text(
                    f' for {value} simoleons? Press again to confirm.',
                    f' za {value} simoleonów? Naciśnij ponownie, aby potwierdzić.',
                ),
            ]
        )
        return False
    state.sell_id = None
    if not destroy_object(obj):
        say_text('Could not sell it.', 'Nie udało się tego sprzedać.')
        return False
    paid = refund(value)
    if last_placement['obj_id'] == obj.id:
        last_placement['obj_id'] = None
    _say_sold(name, value, paid)
    return True

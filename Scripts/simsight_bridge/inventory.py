"""Inventory from the phone: what the controlled Sim carries and where
each item can go."""

from __future__ import annotations


from typing import TYPE_CHECKING, Any

import simsight_log

from .state import _distance, _logged, _on_active_lot, _sim_and_client
from .text import classify, object_name


if TYPE_CHECKING:
    from sims.sim import Sim

    from .state import Answer, Json


# ---------------------------------------------------------------- inventory
# What the controlled Sim carries, and where each item can go (fridge,
# bookcase...): the game's drag and drop between inventories, in one tap.
# A whole stack moves; the destination's own rules decide what it accepts.

MAX_DESTINATIONS = 5


def _stack_count(obj: Any) -> int:
    try:
        return int(obj.stack_count())
    except (AttributeError, TypeError, ValueError):
        return 1  # not a stack


def _inventory_items(sim: Sim) -> list[Any]:
    seen, out = set(), []
    for obj in list(sim.inventory_component or ()):
        try:
            stack = obj.inventoryitem_component.get_stack_id()
        except AttributeError:
            stack = obj.id  # no inventory item component: on its own
        if stack in seen:
            continue
        seen.add(stack)
        out.append(obj)
    return out


def _destination(holder: Any, item: Any, d: float) -> Json | None:
    """The holder as a place for the item, or None when it does not take it."""
    row = None
    with _logged('destination'):
        if holder.inventory_component.can_add(item):
            group, kind, _keywords = classify(holder)
            row = {
                'id': str(holder.id),
                'name': object_name(holder),
                'group': group,
                'kind': kind,
                'distance': round(d),
            }
    return row


def _destinations(item: Any, holders: list[tuple[float, Any]]) -> list[Json]:
    out = []
    for d, holder in holders:
        row = _destination(holder, item, d)
        if row is not None:
            out.append(row)
        if len(out) >= MAX_DESTINATIONS:
            break
    return out


def _holder_distance(obj: Any, sim: Sim) -> float | None:
    """How far an object with an inventory is, or None when it does not count."""
    distance = None
    with _logged('inventory holder'):
        if (
            not getattr(obj, 'is_sim', False)
            and not obj.is_hidden()
            and getattr(obj, 'inventory_component', None) is not None
            and _on_active_lot(obj, True)
        ):
            distance = _distance(obj.position, sim.position)
    return distance


def _inventory_row(item: Any, holders: list[tuple[float, Any]]) -> Json | None:
    try:
        group, kind, _keywords = classify(item)
        return {
            'item': {
                'id': str(item.id),
                'name': object_name(item),
                'group': group,
                'kind': kind,
            },
            'count': _stack_count(item),
            'destinations': _destinations(item, holders),
        }
    except Exception:
        simsight_log.error('bridge: inventory item')
        return None


def list_inventory() -> Answer:
    import services

    sim, _client = _sim_and_client()
    if sim is None or getattr(sim, 'inventory_component', None) is None:
        return False, 'sim_unavailable'
    holders = []
    for obj in services.object_manager().get_valid_objects_gen():
        d = _holder_distance(obj, sim)
        if d is not None:
            holders.append((d, obj))
    holders.sort(key=lambda pair: pair[0])
    items = []
    for item in _inventory_items(sim):
        row = _inventory_row(item, holders)
        if row is not None:
            items.append(row)
    return True, '', {'inventory': items}


def move_item(item_id: Any, destination_id: Any) -> Answer:
    import services

    sim, _client = _sim_and_client()
    if sim is None:
        return False, 'sim_unavailable'
    source = sim.inventory_component
    item = next((o for o in _inventory_items(sim) if str(o.id) == str(item_id)), None)
    if item is None:
        return False, 'target_unavailable'
    try:
        holder = services.object_manager().get(int(destination_id))
    except (TypeError, ValueError):
        holder = None
    if holder is None or getattr(holder, 'inventory_component', None) is None:
        return False, 'target_unavailable'
    target = holder.inventory_component
    if not target.can_add(item):
        return False, 'not_accepted'
    if not source.try_remove_object_by_id(item.id, count=_stack_count(item)):
        return False, 'refused'
    if target.player_try_add_object(item):
        return True, ''
    # Put it back rather than lose it.
    source.player_try_add_object(item)
    return False, 'not_accepted'

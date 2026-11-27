"""Surfaces near the cursor with a free slot the game accepts for an object."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import simsight_log

from .state import household_id

if TYPE_CHECKING:
    from objects.game_object import GameObject
    from objects.slots import RuntimeSlot


SLOT_SEARCH = 1.2  # metres around the cursor to look for surfaces


# Surfaces ranked as in glaroc's build: a computer on a desk works, the same
# computer on a decorative island slot has no usable posture.
SURFACE_RANK = (
    (('desk',), 0),
    (('table',), 1),
    (('counter', 'island'), 3),
    (('shelf', 'bookcase', 'dresser', 'endtable', 'nightstand'), 4),
    (('lamp', 'light'), 9),
)


_DEFAULT_RANK = 2


def _surface_rank(obj: Any) -> int:
    try:
        name = str(obj.definition.name or '').lower()
    except AttributeError:  # no definition: rank by its class
        name = type(obj).__name__.lower()
    for words, rank in SURFACE_RANK:
        if any(word in name for word in words):
            return rank
    return _DEFAULT_RANK


def _accepts_object(slot: RuntimeSlot, obj: GameObject) -> bool:
    try:
        return not slot.is_deco_slot() and slot.empty and bool(slot.is_valid_for_placement(obj=obj))
    except Exception:  # EA's native slot check can raise anything: not this slot
        simsight_log.error('place: native slot check')
        return False


def _free_slot_on(surface: Any, obj: GameObject) -> RuntimeSlot | None:
    try:
        slots = list(surface.get_runtime_slots_gen())
    except Exception:  # EA's native slot query: a surface without usable slots
        simsight_log.error('place: runtime slots')
        return None
    for slot in slots:
        if _accepts_object(slot, obj):
            return slot
    return None


def free_slot(
    obj: GameObject, position: Any, level: int, accepts: Callable[[Any], bool] | None = None
) -> tuple[Any, Any] | None:
    """(surface, slot) near the cursor that the game accepts for this object."""
    import services

    lot = services.active_lot()
    household = household_id()

    def entry(surface: Any) -> tuple[int, float, Any] | None:
        """(rank, distance squared, surface) when surface may take obj."""
        try:
            if surface is obj or getattr(surface, 'is_sim', False) or surface.is_in_inventory():
                return None
            if surface.is_hidden() or not lot.is_position_on_lot(surface.position):
                return None
            owner = surface.get_household_owner_id()
            if owner and owner != household:
                return None
            if int(surface.level) != int(level):
                return None
            dx = float(surface.position.x) - float(position.x)
            dz = float(surface.position.z) - float(position.z)
            if accepts is None and (abs(dx) > SLOT_SEARCH or abs(dz) > SLOT_SEARCH):
                return None
            if accepts is not None and not accepts(surface.position):
                return None
            return _surface_rank(surface), dx * dx + dz * dz, surface
        except Exception:  # an object in an odd state is simply not a candidate
            simsight_log.error('place: surface candidate')
            return None

    nearby = [row for row in map(entry, list(services.object_manager().values())) if row]
    nearby.sort(key=lambda item: (item[0], item[1]))
    for _rank, _distance, surface in nearby:
        slot = _free_slot_on(surface, obj)
        if slot is not None:
            return surface, slot
    return None


def put_in_slot(obj: GameObject, surface: Any, slot: RuntimeSlot) -> bool:
    ways: tuple[tuple[str, Callable[[], Any]], ...] = (
        ('add_child', lambda: slot.add_child(obj)),
        ('slot_object', lambda: surface.slot_object(parent_slot=slot, slotting_object=obj)),
    )
    for label, call in ways:
        try:
            call()
        except Exception:  # EA's slotting: try the other way
            simsight_log.error(f'place: {label}')
            continue
        if getattr(obj, 'parent', None) is not None:
            return True
    return False

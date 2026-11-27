"""EA's placement calls: finding a good location, creating the rotation,
registering and destroying an object, asking the client to drop its tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from objects.game_object import GameObject
    from collections.abc import Sequence


SEARCH_RADIUS = 2.5  # metres: "place here" means near here


def good_location(
    obj: Any, position: Any, orientation: Any, routing_surface: Any, ignore: Sequence[Any] = ()
) -> tuple[Any, Any] | None:
    """(translation, orientation) found by the game near position, or None."""
    try:
        import placement

        start = placement.create_starting_location(
            position=position, orientation=orientation, routing_surface=routing_surface
        )
        context = placement.create_fgl_context_for_object(
            start,
            obj,
            test_buildbuy_allowed=True,
            max_distance=SEARCH_RADIUS,
            ignored_object_ids=list(ignore) or None,
        )
        result = placement.find_good_location(context)
    except Exception:  # EA's footprint search: no location
        simsight_log.error('place: find_good_location')
        return None
    if result and result[0] is not None:
        simsight_log.line(f'place: fgl chose {result[0]}')
        return result[0], result[1]
    simsight_log.line(
        f'place: fgl found nothing: {result[2] if result and len(result) > 2 else result}'
    )
    return None


def yaw(angle: float) -> Any:
    import sims4.math

    return sims4.math.angle_to_yaw_quaternion(angle)


def destroy_object(obj: Any) -> bool:
    try:
        obj.destroy(source=obj, cause='SimSight placement undo')
    except Exception:  # EA's removal runs every component's clean-up
        simsight_log.error('place: destroy')
        return False
    return True


def register(obj: GameObject) -> bool:
    try:
        import build_buy

        build_buy.add_object_to_buildbuy_system(obj.id)
    except Exception:  # EA's native registration
        simsight_log.error('place: add_object_to_buildbuy_system')
        return False
    return True


def drop_native_hand(serial: int) -> bool:
    """Ask the client to put down whatever EA's own placement tool holds, so
    the same product is never bought twice (here, and by a later native
    click). The object is created only after the client answers."""
    try:
        import distributor.ops
        import distributor.system

        distributor.system.Distributor.instance().add_op_with_no_owner(
            distributor.ops.SendUIMessage(f'A11y|buildhand|drop|{serial}')
        )
    except Exception:  # EA's distributor
        simsight_log.error('place: could not ask the client to drop its tool')
        return False
    return True

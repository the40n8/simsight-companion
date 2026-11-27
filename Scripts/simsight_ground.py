"""Explicit native floor pick; never choose or enqueue a cleaning action."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    from sims.sim import Sim
    from world.lot import Lot


def open_floor_menu() -> bool:
    """Simulation thread, after the accessibility picker has closed."""
    import services
    import simsight_place as place

    try:
        sim: Sim | None = services.get_active_sim()
        lot: Lot | None = services.active_lot()
        if sim is None or lot is None or place.in_build_mode():
            speech.speak('Floor actions require an active Sim in Live Mode.', explicit=True)
            return False
        pos, x, z = _sim_point(sim)
        if not lot.is_position_on_lot(pos):
            speech.speak('Move the active Sim onto the active lot first.', explicit=True)
            return False
        return _request_floor_menu(sim, lot, x, z)
    except Exception:
        simsight_log.error('ground: floor actions request')
        speech.speak('The floor menu is unavailable here.', explicit=True)
        return False


def _sim_point(sim: Sim) -> tuple[Any, float, float]:
    """(position, x, z) of the Sim; ValueError when not finite."""
    pos = sim.position
    x, z = float(pos.x), float(pos.z)
    if not all(math.isfinite(v) for v in (x, z)):
        raise ValueError('non-finite floor position')
    return pos, x, z


def _request_floor_menu(sim: Sim, lot: Lot, x: float, z: float) -> bool:
    """Ask the client for the game's own floor pie menu at (x, z)."""
    import routing
    import services
    import simsight_scanner as scanner
    import terrain
    from server.pick_info import PickType

    level, lot_id = int(sim.level), int(lot.lot_id)
    if lot_id < 0:
        raise ValueError('invalid lot ID')
    # The existing Flash bridge carries a uint. Native floor resolution
    # accepts zero as the current-lot sentinel; never truncate a wider ID.
    if lot_id > 0xFFFFFFFF:
        lot_id = 0
    surface = routing.SurfaceIdentifier(
        services.current_zone_id(), level, routing.SurfaceType.SURFACETYPE_WORLD
    )
    # Read the actual floor height, including foundations/platforms;
    # the Sim may currently be sitting above it on furniture.
    y = float(terrain.get_terrain_height(x, z, routing_surface=surface))
    if not math.isfinite(y):
        raise ValueError('floor height unavailable')
    # A zero target is a native surface pick, not an object pick. The
    # game creates TerrainPoint on this world's routing surface/level.
    # Reuse the client's request counter and normal pie-menu lifecycle.
    sent = scanner.send_ui(
        f'A11y|pick|0|{int(PickType.PICK_FLOOR)}|{lot_id}||{x:.3f}|{y:.3f}|{z:.3f}|{level}'
    )
    simsight_log.line(
        f'floor actions: sim={sim.id} zone={services.current_zone_id()} lot={lot_id} level={level} point=({x:.3f},{y:.3f},{z:.3f}) sent={sent}'
    )
    if not sent:
        speech.speak('The floor menu could not be requested.', explicit=True)
    return bool(sent)

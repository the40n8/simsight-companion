"""Where Sims are (rooms as the phone's House tab names them), visitors,
bills and the camera."""

from __future__ import annotations


import time
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_names as names

from .state import _logged, _on_active_lot, _sim_and_client, _state


if TYPE_CHECKING:
    from server.client import Client
    from sims.household import Household
    from sims.sim import Sim

    from .state import Answer, Json


# ---------------------------------------------------------------- where Sims are
# The house map (rooms named as on the phone's House tab), rebuilt now and
# then: it scans the lot, so not every second.

HOUSE_INTERVAL = 20.0


def _house() -> dict[str, Json]:
    now = time.monotonic()
    if now - _state.house_at >= HOUSE_INTERVAL:
        _state.house_at = now
        try:
            import simsight_housemap as housemap

            house = housemap.build()
            _state.house_rooms = {r['id']: r for r in (house or {}).get('rooms', [])}
        except Exception:
            simsight_log.error('bridge: house map')
            _state.house_rooms = {}
    return _state.house_rooms


def _room_ref(obj: Any) -> Json | None:
    """The room an object or Sim is in, as the House tab names it, or None."""
    rooms = _house()
    if not rooms:
        return None
    room = None
    with _logged('room of'):
        import simsight_plus_rooms as plus_rooms

        if not _on_active_lot(obj, True):
            return None
        level = int(obj.level)
        rid = plus_rooms.room_id_at(obj.position.x, obj.position.z, level, obj.position.y)
        room = rooms.get(f'{rid}:{level}') or rooms.get('outside:0')
    if room is None:
        return None
    return {
        'id': room['id'],
        'kind': room['kind'],
        'number': room.get('number', 0),
        'customName': room.get('customName', ''),
        'floor': room.get('floor', 0),
    }


def _visitor(sim: Sim, members: set[Any]) -> Json | None:
    """A Sim on the lot who is not in the household, or None."""
    entry: Json | None = None
    with _logged('visitor'):
        info = sim.sim_info
        if info.sim_id not in members and _on_active_lot(sim, False):
            entry = {'simId': str(info.sim_id), 'name': names.full_name(info).strip()}
            room = _room_ref(sim)
            if room is not None:
                entry['room'] = room
    return entry


def _visitors(household: Household) -> list[Json]:
    """Sims on the lot who are not in the household."""
    import services

    members = {info.sim_id for info in household.sim_info_gen()}
    out = []
    for sim in services.sim_info_manager().instanced_sims_gen():
        entry = _visitor(sim, members)
        if entry is not None:
            out.append(entry)
    return out[:10]


def _bills_owed(household: Household) -> int:
    try:
        owed = household.bills_manager.current_payment_owed
        owed = owed() if callable(owed) else owed
        return int(owed or 0)
    except (AttributeError, TypeError, ValueError):
        return 0  # no bills manager (some lots)


def _camera_follows() -> str:
    try:
        import camera
    except ImportError:
        return ''  # outside the game (offline checks)
    # The camera module keeps who it follows in private globals only.
    followed = getattr(camera, '_sim_id', None)
    if getattr(camera, '_follow_mode', False) and followed:
        return str(followed)
    return ''


def _close_camera(sim: Sim, client: Client | None) -> None:
    """Put the camera close behind the Sim's back."""
    import camera
    import sims4.math

    pos, fwd = sim.position, sim.forward
    eye = sims4.math.Vector3(pos.x + fwd.x * 3.0, pos.y + 1.6, pos.z + fwd.z * 3.0)
    camera.focus_on_object_from_position(obj_position=pos, camera_position=eye, client=client)


def _set_camera(sim_id: Any, mode: str) -> Answer:
    """Follow a Sim with the game camera, close up, or let it go."""
    import camera
    import services

    _sim_unused, client = _sim_and_client()
    try:
        info = services.sim_info_manager().get(int(sim_id))
    except (TypeError, ValueError):
        info = None
    sim = info.get_sim_instance() if info is not None else None
    if sim is None:
        return False, 'sim_unavailable'
    if mode == 'CAMERA_MODE_FREE':
        camera.cancel_focus(object=sim)
        return True, ''
    if mode == 'CAMERA_MODE_CLOSE':
        with _logged('close camera'):
            _close_camera(sim, client)
    camera.focus_on_sim(sim=sim, follow=True, client=client)
    return True, ''

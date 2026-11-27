"""Use EA's native follow-Sim camera while scanner mode owns Live gameplay.

No camera coordinates, input simulation or polling thread. Flash reports Live
camera eligibility independently of scanner/menu focus. The client callback
retargets on Sim selection; native camera reports repair a lost follow target.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_patch
import simsight_settings as settings

if TYPE_CHECKING:
    from types import ModuleType

    from server.client import Client
    from sims.sim import Sim


class _State:
    """What this module knows about the Live camera and our follow."""

    def __init__(self) -> None:
        self.live = False  # Flash: the Live camera is eligible
        self.client: Client | None = None  # the client our Sim-change callback is on
        self.followed: Sim | None = None  # the Sim we asked the camera to follow
        self.last_request = 0.0
        self.was_following = False  # the player's own follow mode before ours
        self.baseline_known = False
        # The camera module whose update we wrapped (update() runs every tick).
        self.hooked_camera: ModuleType | None = None
        self.repair_pending = False


_state = _State()


def _configured() -> bool:
    return bool(
        settings.get('keyboard', True)
        and settings.get('scanner', True)
        and settings.get('scanner_mode', False)
    )


def _enabled() -> bool:
    return _state.live and _configured()


def _release() -> None:
    # Restore manual camera behavior only while still in the Live camera.
    # Never send a camera operation into Build/CAS during their transition.
    if _state.live and _state.followed is not None and not _state.was_following:
        try:
            import camera

            camera.cancel_focus(object=_state.followed)
        except Exception:
            simsight_log.error('scanner camera: release')
    if _state.client is not None:
        try:
            _state.client.unregister_active_sim_changed(_active_sim_changed)
        except Exception:
            simsight_log.error('scanner camera: unregister')
    _state.followed = None
    _state.client = None
    if not _configured():
        _state.was_following = False
        _state.baseline_known = False
    _state.repair_pending = False


def set_live(live: object) -> None:
    """Simulation thread, from the independent Flash Live-camera report."""
    if _state.live and not live and not _enabled():
        # Setting disabled while still in Live: release our follow before
        # clearing eligibility. A Build/CAS transition keeps settings enabled
        # and therefore never takes this path.
        _release()
    _state.live = bool(live)
    update()


def _active_sim_changed(_old_sim: Sim | None, new_sim: Sim | None) -> None:
    # Client.notify_active_sim_changed calls it with (old_sim, new_sim).
    try:
        if _enabled():
            _follow(new_sim)
    except Exception:
        simsight_log.error('scanner camera: active Sim changed')


def _follow(sim: Sim | None, repair: bool = False) -> None:
    if not _enabled() or sim is None:
        return
    now = time.monotonic()
    if sim is _state.followed and not repair:
        return
    # A client can send multiple stale reports before processing our operation.
    if repair and sim is _state.followed and now - _state.last_request < 0.25:
        _state.repair_pending = True
        return
    try:
        import camera

        camera.focus_on_sim(sim=sim, follow=True, client=_state.client)
        _state.followed = sim
        _state.last_request = now
        _state.repair_pending = False
        simsight_log.line(f'scanner camera: native follow sim {sim.id}')
    except Exception:
        simsight_log.error('scanner camera: follow')


def _camera_follow(camera: ModuleType) -> tuple[Any, Any]:
    """(follow mode, followed Sim id) of the native camera: EA keeps them in
    private module globals of `camera`, with no public getter."""
    return getattr(camera, '_follow_mode', False), getattr(camera, '_sim_id', None)


def _repair_after_report(camera: ModuleType) -> None:
    """A native camera report: follow the active Sim again if the camera lost it."""
    try:
        import services

        sim = services.get_active_sim()
        follow_mode, sim_id = _camera_follow(camera)
        if sim is not None and (not follow_mode or sim_id != sim.id):
            _follow(sim, repair=True)
        else:
            _state.repair_pending = False
    except Exception:
        simsight_log.error('scanner camera: report repair')


def _install_report_hook(camera: ModuleType) -> None:
    if _state.hooked_camera is camera:
        return

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def update_report(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            if _enabled():
                _repair_after_report(camera)
            return result

        return update_report

    simsight_patch.wrap(camera, 'update', make)
    _state.hooked_camera = camera


def update() -> None:
    """Simulation heartbeat: bind the native callback, repair missed Sim changes.

    An unchanged active Sim sends no operation, so walking is followed natively
    without repeatedly snapping the camera to new coordinates.
    """
    if not _enabled():
        _release()
        return
    try:
        import camera
        import services

        client = services.client_manager().get_first_client()
        if client is None:
            return
        if client is not _state.client:
            _release()
            # Keep the pre-scanner state through Build/CAS suspension. A
            # native report after returning may describe our own follow.
            if not _state.baseline_known:
                _state.was_following = bool(_camera_follow(camera)[0])
                _state.baseline_known = True
            _state.client = client
            client.register_active_sim_changed(_active_sim_changed)
        _install_report_hook(camera)
        _follow(services.get_active_sim(), repair=_state.repair_pending)
    except Exception:
        simsight_log.error('scanner camera: update')


def reset() -> None:
    """Zone changed: forget old Sim/client references without moving the camera."""
    _state.live = False
    _release()
    _state.baseline_known = False
    _state.was_following = False

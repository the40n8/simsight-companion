"""Use EA's native follow-Sim camera while scanner mode owns Live gameplay.

No camera coordinates, input simulation or polling thread. Flash reports Live
camera eligibility independently of scanner/menu focus. The client callback
retargets on Sim selection; native camera reports repair a lost follow target.
"""

from __future__ import annotations

import time
from typing import Any

import simsight_log
import simsight_settings as settings


class _State:
    """What this module knows about the Live camera and our follow."""

    def __init__(self) -> None:
        self.live = False  # Flash: the Live camera is eligible
        self.client: Any = None  # the client our Sim-change callback is on
        self.followed: Any = None  # the Sim we asked the camera to follow
        self.last_request = 0.0
        self.was_following = False  # the player's own follow mode before ours
        self.baseline_known = False
        self.hooked_camera: Any = None  # the camera module whose update we wrap
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


def _active_sim_changed(old_sim: Any, new_sim: Any) -> None:
    try:
        if _enabled():
            _follow(new_sim)
    except Exception:
        simsight_log.error('scanner camera: active Sim changed')


def _follow(sim: Any, repair: bool = False) -> None:
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


def _repair_after_report(camera: Any) -> None:
    """A native camera report: follow the active Sim again if the camera lost it."""
    try:
        import services

        sim = services.get_active_sim()
        if sim is not None and (not camera._follow_mode or camera._sim_id != sim.id):
            _follow(sim, repair=True)
        else:
            _state.repair_pending = False
    except Exception:
        simsight_log.error('scanner camera: report repair')


def _install_report_hook(camera: Any) -> None:
    if _state.hooked_camera is camera:
        return
    original = camera.update

    def update_report(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if _enabled():
            _repair_after_report(camera)
        return result

    camera.update = update_report
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
                _state.was_following = bool(getattr(camera, '_follow_mode', False))
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

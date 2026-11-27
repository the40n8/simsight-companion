"""The simsight.scan command, and the reset at every zone load."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_availability as availability
from simsight_commands import console_command
import simsight_log
import simsight_patch
import simsight_pump as pump

from .state import _loc_lock, _state, enabled, loc_cache
from .loc import send_ui
from .scan import _bind_availability_client, _unbind_availability_client
from .refresh import _start_refresh
from .intents import _dispatch

if TYPE_CHECKING:
    from zone import Zone


def _stats_text() -> str:
    stats = dict(_state.scan_stats)
    stats['mean_ms'] = stats['total_ms'] / max(1, stats['scans'])
    text = (
        'scanner: {scans} scans; last {last_ms:.2f} ms, mean {mean_ms:.2f} ms, '
        'max {max_ms:.2f} ms; last objects {examined}, in range {in_range}; '
        'classification hits {cache_hits}, misses {cache_misses}'
    ).format(**stats)
    return text + '; refresh {}'.format('active' if _state.refresh_requested else 'idle')


def _scan_command(action: str, output: Any) -> None:
    if action.lower() == 'stats':
        text = _stats_text()
        output(text)
        simsight_log.line(text)
        return
    if not enabled():
        output('simsight: scanner is off (cfg [features] scanner)')
        return
    _dispatch(action.lower())
    output(f'simsight: scanner {action} -> {len(_state.groups)} categories, pump {pump.status()}')


@console_command('simsight.scan', 'a11y.scan', command_type=sims4.commands.CommandType.Live)
def a11y_scan(action: str = 'rescan', _connection: Any = None) -> bool:
    """simsight.scan [rescan|next|prev|nextcat|prevcat|nextsub|prevsub|open|back|
    where] runs one scanner action from the console (same as the chords)."""
    try:
        _scan_command(action, sims4.commands.CheatOutput(_connection))
    except Exception:
        simsight_log.error('scanner: scan command')
    return True


def _reset_for_new_zone() -> None:
    """A lot finished loading: forget the old lot's lists, caches and names."""
    _state.by_group = {}
    _state.groups = []
    _state.tree_level = 'groups'
    _state.tree_groups.clear()
    _state.tree_items.clear()
    availability.reset()
    _unbind_availability_client()
    _bind_availability_client()
    import simsight_camera as camera_follow

    camera_follow.reset()
    _state.menu_open = False
    _state.last_scan = 0.0
    _state.refresh_requested = False
    _state.last_activity = 0.0
    _state.class_cache.clear()
    for key in _state.scan_stats:
        _state.scan_stats[key] = 0
    try:
        _start_refresh()
    except Exception:
        simsight_log.error('scanner: refresh alarm')
    with _loc_lock:
        loc_cache.clear()
        _state.loc_waiting.clear()
    # Re-assert the Flash game-message hook after every load.
    send_ui('A11yHook')


def _install_zone_hook() -> None:
    import zone

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def a11y_scanner_on_loading_screen_animation_finished(
            self: Zone, *args: Any, **kwargs: Any
        ) -> Any:
            result = original(self, *args, **kwargs)
            try:
                _reset_for_new_zone()
            except Exception:
                simsight_log.error('scanner: zone load reset')
            return result

        return a11y_scanner_on_loading_screen_animation_finished

    simsight_patch.wrap(zone.Zone, 'on_loading_screen_animation_finished', make)


def _queue_extensions() -> None:
    # Install only the remaining local extensions after module initialization,
    # on the simulation pump. Upstream owns the integrated contribution controls.
    try:
        import simsight_extensions
        import simsight_plus_core

        simsight_plus_core.post(simsight_extensions.install)
    except Exception:
        simsight_log.error('scanner: local extensions could not be queued')

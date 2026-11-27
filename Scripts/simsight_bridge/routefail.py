"""Blocked Sims: the game's route failures reported to the phone."""

from __future__ import annotations


import time
from typing import Any

import simsight_log
import simsight_names as names

from .pipe import send
from .state import _logged, _state, log
from .text import _pretty, object_name, text_of


# ---------------------------------------------------------------- blocked Sims
# When a Sim cannot reach where an action happens (no door, no stairs,
# something in the way), the game cancels the action and only shows a
# "blocked" bubble. interactions.utils.route_fail.route_failure plays it:
# report it to the phone and the Mac voice with the game's reason.

ROUTE_REPEAT = 10.0  # seconds before the same Sim and action is reported again


def _install_route_fail() -> None:
    from interactions.utils import route_fail

    original = route_fail.route_failure
    if getattr(original, '_simsight_phone', False):
        return

    def route_failure(
        sim: Any,
        interaction: Any,
        failure_reason: Any,
        failure_object_id: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            _report_route_fail(sim, interaction, failure_reason, failure_object_id)
        except Exception:
            simsight_log.error('bridge: route failure report')
        return original(sim, interaction, failure_reason, failure_object_id, *args, **kwargs)

    setattr(route_failure, '_simsight_phone', True)  # noqa: B010 - marker on a function
    route_fail.route_failure = route_failure


def _action_name(interaction: Any) -> str:
    action = ''
    with _logged('route failure action'):
        action = text_of(interaction.get_name(), '')
    if not action:
        action = _pretty(
            getattr(getattr(interaction, 'affordance', None), '__name__', ''), ('sim-', 'si_')
        )
    return action


def _failure_object_name(failure_object_id: Any) -> str:
    """What was in the way: an object's name or a Sim's name, or ''."""
    import services

    if not failure_object_id:
        return ''
    obj = services.object_manager().get(failure_object_id)
    if obj is None:
        return ''
    if not getattr(obj, 'is_sim', False):
        return object_name(obj)
    return names.full_name(obj.sim_info).strip()


def _report_route_fail(
    sim: Any, interaction: Any, failure_reason: Any, failure_object_id: Any
) -> None:
    import services

    household = services.active_household()
    info = getattr(sim, 'sim_info', None)
    if info is None or household is None or info.household is not household:
        return
    action = _action_name(interaction)
    now = time.monotonic()
    key = (info.sim_id, action)
    if now - _state.route_seen.get(key, -1e9) < ROUTE_REPEAT:
        return
    _state.route_seen[key] = now
    reason = getattr(failure_reason, 'name', '') or str(failure_reason or '')
    obj_name = _failure_object_name(failure_object_id)
    log(f'route failure: {info.first_name} {action} ({reason}, {obj_name})')
    send(
        {
            'event': {
                'kind': 'EVENT_KIND_ROUTE_FAIL',
                'simId': str(info.sim_id),
                'simName': info.first_name,
                'subject': action,
                'detail': reason,
                'objectName': obj_name,
            }
        }
    )

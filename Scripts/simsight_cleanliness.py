"""Read-only scanner suffixes for native dirt/dust states verified in-game.

Only reads current state values at announcement time. No interaction tests,
state writes, scan refreshes, object filtering or cached dirtiness.
"""

from __future__ import annotations

from itertools import islice
from typing import Any

import simsight_lang as lang
import simsight_log

_DIRTY_STATE = 15129  # DirtyState: the visual state
_DIRTY_STATE_DIRTY = 15131
_DIRTINESS = 15128  # the dirtiness commodity's state
_DIRTINESS_DIRTY = 15133
_DUST = 255045
_DUST_DUSTY = 255050
_WATCHED = (_DIRTY_STATE, _DIRTINESS, _DUST)
_MAX_STATES = 64  # bound the read on objects with many states


def _current_states(component: Any) -> dict[int, Any]:
    """Watched state id -> current value id, from an object's state component."""
    current: dict[int, Any] = {}
    for value in islice(component.values(), _MAX_STATES):
        state_id = getattr(getattr(value, 'state', None), 'guid64', None)
        if state_id in _WATCHED:
            current[state_id] = getattr(value, 'guid64', None)
    return current


def _is_dirty(current: dict[int, Any]) -> bool:
    # DirtyState is the visual state. Prefer it over the linked dirtiness
    # commodity's state when both exist, including transient disagreement.
    if _DIRTY_STATE in current:
        return current[_DIRTY_STATE] == _DIRTY_STATE_DIRTY
    return current.get(_DIRTINESS) == _DIRTINESS_DIRTY


def terms_for(current: dict[int, Any]) -> list[str]:
    """The string keys to say for these states, in speaking order."""
    terms = []
    if _is_dirty(current):
        terms.append('scanner dirty')
    if current.get(_DUST) == _DUST_DUSTY:
        terms.append('scanner dusty')
    return terms


def suffix(entry: Any) -> str:
    """', dirty' and/or ', dusty' for a scanned object, '' for none or a Sim."""
    if entry.is_sim:
        return ''
    try:
        import services

        obj = services.object_manager().get(entry.obj_id)
        component = getattr(obj, 'state_component', None)
        if component is None:
            return ''
        return ''.join(', ' + lang.t(key) for key in terms_for(_current_states(component)))
    except Exception:
        # A malformed/removed object must not interrupt scanner navigation.
        simsight_log.error('cleanliness: state read failed')
        return ''

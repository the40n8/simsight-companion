"""Things to deal with on the lot (fire, puddles, broken objects, spoiled
or left food), for the state."""

from __future__ import annotations


import contextlib
import time
from typing import TYPE_CHECKING, Any

import simsight_log

from .state import _distance, _logged, _on_active_lot, _state
from .text import object_name


if TYPE_CHECKING:
    from .state import Json


# ---------------------------------------------------------------- things to deal with
# Puddles, broken objects, spoiled food, fire on the lot: listed for the
# phone's "Needs attention" card. Scanning every object takes a moment, so
# it runs every few seconds and the state reuses the last answer.

PROBLEM_INTERVAL = 4.0
_PROBLEM_ORDER = {'FIRE': 0, 'BROKEN': 1, 'PUDDLE': 2, 'SPOILED': 3, 'FOOD': 4}


def _problems() -> list[Json]:
    now = time.monotonic()
    if now - _state.problems_at < PROBLEM_INTERVAL:
        return _state.problems
    _state.problems_at = now
    try:
        _state.problems = _scan_problems()
    except Exception:
        simsight_log.error('bridge: problems')
        _state.problems = []
    return _state.problems


class _ProblemTypes:
    """The game's classes and states that make an object a problem; each is
    missing (None or empty) when this game version does not have it."""

    def __init__(self) -> None:
        self.puddle: Any = None
        self.fire: Any = None
        self.broken_states: tuple[Any, ...] = ()
        self.spoiled: Any = None
        with contextlib.suppress(ImportError, AttributeError):
            from objects.puddles.puddle import Puddle

            self.puddle = Puddle
        with contextlib.suppress(ImportError, AttributeError):
            from objects.fire.fire import Fire

            self.fire = Fire
        with contextlib.suppress(ImportError, AttributeError, TypeError):
            from objects.components.state import StateComponent

            self.broken_states = tuple(StateComponent.BROKEN_STATE_SET or ())
        with contextlib.suppress(ImportError, AttributeError):
            from crafting.crafting_tunable import CraftingTuning

            self.spoiled = CraftingTuning.SPOILED_STATE_VALUE


def _has_state(obj: Any, value: Any) -> bool:
    if value is None:
        return False
    try:
        return bool(obj.has_state(value.state) and obj.get_state(value.state) is value)
    except Exception:
        simsight_log.error('bridge: object state')
        return False


def _state_active(obj: Any, value: Any) -> bool:
    try:
        return bool(obj.state_value_active(value))
    except Exception:
        simsight_log.error('bridge: object state value')
        return False


def _problem_listable(obj: Any) -> bool:
    """A visible object on the lot, not a Sim and not in an inventory."""
    if getattr(obj, 'is_sim', False) or obj.is_hidden():
        return False
    if not _on_active_lot(obj, True):
        return False
    in_inventory = getattr(obj, 'is_in_inventory', None)
    return in_inventory is None or not in_inventory()


def _problem_kind(obj: Any, types: _ProblemTypes) -> str | None:
    if types.fire is not None and isinstance(obj, types.fire):
        return 'FIRE'
    if types.puddle is not None and isinstance(obj, types.puddle):
        return 'PUDDLE'
    if _has_state(obj, types.spoiled):
        return 'SPOILED'
    if types.broken_states and any(_state_active(obj, v) for v in types.broken_states):
        return 'BROKEN'
    if getattr(obj, 'consumable_component', None) is not None:
        parent = getattr(obj, 'parent', None)
        if parent is not None and getattr(parent, 'is_sim', False):
            return None  # in a Sim's hands: being eaten
        return 'FOOD'
    return None


def _problem(obj: Any, sim: Any, types: _ProblemTypes) -> Json | None:
    entry: Json | None = None
    with _logged('problem object'):
        kind = _problem_kind(obj, types) if _problem_listable(obj) else None
        if kind is not None:
            entry = {'kind': kind, 'targetId': str(obj.id), 'name': object_name(obj)}
            if sim is not None:
                entry['distance'] = int(round(_distance(obj.position, sim.position)))
    return entry


def _scan_problems() -> list[Json]:
    import services

    sim = services.get_active_sim()
    types = _ProblemTypes()
    out = []
    for obj in services.object_manager().get_valid_objects_gen():
        entry = _problem(obj, sim, types)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda e: (_PROBLEM_ORDER[e['kind']], e.get('distance', 0)))
    # Fire burns in several pieces: one line is enough.
    fires = [e for e in out if e['kind'] == 'FIRE']
    food = [e for e in out if e['kind'] == 'FOOD'][:3]
    rest = [e for e in out if e['kind'] not in ('FIRE', 'FOOD')][:12]
    return fires[:1] + rest + food

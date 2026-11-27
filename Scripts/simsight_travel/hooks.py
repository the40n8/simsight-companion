"""The hooks on EA's map and Sim pickers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


import simsight_log
import simsight_patch

from .state import _state
from .outcomes import _native_map
from .flow import _distribute
from .front import _native_sim_picker, _sim_distribute

if TYPE_CHECKING:
    from simsight_patch import Function


def _map_hook(self: Any, msg_type: Any, dialog_msg: Any, *more: Any, **kwargs: Any) -> Any:
    """UiMapViewPicker.distribute_dialog. A failure before the list owns the
    request leaves it to the native map, so EA's dialog is never lost."""
    if more:  # a call this list does not know (EA passes only keywords): EA's own
        return _native_map(self, msg_type, dialog_msg, kwargs, more)
    try:
        return _distribute(self, msg_type, dialog_msg, **kwargs)
    except Exception:
        simsight_log.error('travel: map picker hook')
        owned = _state.pending is not None and _state.pending.dialog is self
        return None if owned else _native_map(self, msg_type, dialog_msg, kwargs)


def _sim_picker_hook(self: Any, msg_type: Any, dialog_msg: Any, *more: Any, **kwargs: Any) -> Any:
    """UiSimPicker.distribute_dialog; any failure shows EA's picker as always."""
    if more:  # a call this hook does not know (EA passes only keywords): EA's own
        return _native_sim_picker(self, msg_type, dialog_msg, kwargs, more)
    try:
        return _sim_distribute(self, msg_type, dialog_msg, **kwargs)
    except Exception:
        simsight_log.error('travel: companion picker hook')
        return _native_sim_picker(self, msg_type, dialog_msg, kwargs)


def _make_map(original: Function) -> Function:
    _state.original_distribute = original

    def a11y_travel_distribute_dialog(*args: Any, **kwargs: Any) -> Any:
        return _map_hook(*args, **kwargs)

    return a11y_travel_distribute_dialog


def _make_sim_picker(original: Function) -> Function:
    _state.original_sim_distribute = original

    def a11y_travel_sim_distribute_dialog(*args: Any, **kwargs: Any) -> Any:
        return _sim_picker_hook(*args, **kwargs)

    return a11y_travel_sim_distribute_dialog


def install() -> bool:
    if _state.original_distribute is not None:
        return True
    from ui.ui_dialog_picker import UiMapViewPicker

    simsight_patch.wrap(UiMapViewPicker, 'distribute_dialog', _make_map)
    try:
        from ui.ui_dialog_picker import UiSimPicker

        simsight_patch.wrap(UiSimPicker, 'distribute_dialog', _make_sim_picker)
    except Exception:
        simsight_log.error('travel: companion picker hook unavailable')
    return True

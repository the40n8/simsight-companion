"""The hooks on EA's map and Sim pickers."""

from __future__ import annotations

from typing import Any


import simsight_log

from .state import _state
from .outcomes import _native_map
from .flow import _distribute
from .front import _native_sim_picker, _sim_distribute


def _map_hook(self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    """UiMapViewPicker.distribute_dialog. A failure before the list owns the
    request leaves it to the native map, so EA's dialog is never lost."""
    try:
        return _distribute(self, msg_type, dialog_msg, **kwargs)
    except Exception:
        simsight_log.error('travel: map picker hook')
        owned = _state.pending is not None and _state.pending.dialog is self
        return None if owned else _native_map(self, msg_type, dialog_msg, kwargs)


def _sim_picker_hook(self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    """UiSimPicker.distribute_dialog; any failure shows EA's picker as always."""
    try:
        return _sim_distribute(self, msg_type, dialog_msg, **kwargs)
    except Exception:
        simsight_log.error('travel: companion picker hook')
        return _native_sim_picker(self, msg_type, dialog_msg, kwargs)


def install() -> bool:
    if _state.original_distribute is not None:
        return True
    from ui.ui_dialog_picker import UiMapViewPicker

    _state.original_distribute = UiMapViewPicker.distribute_dialog

    def a11y_travel_distribute_dialog(
        self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any
    ) -> Any:
        return _map_hook(self, msg_type, dialog_msg, **kwargs)

    UiMapViewPicker.distribute_dialog = a11y_travel_distribute_dialog
    try:
        from ui.ui_dialog_picker import UiSimPicker

        _state.original_sim_distribute = UiSimPicker.distribute_dialog

        def a11y_travel_sim_distribute_dialog(
            self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any
        ) -> Any:
            return _sim_picker_hook(self, msg_type, dialog_msg, **kwargs)

        UiSimPicker.distribute_dialog = a11y_travel_sim_distribute_dialog
    except Exception as ex:
        simsight_log.line(f'travel: companion picker hook unavailable ({ex!r})')
    return True

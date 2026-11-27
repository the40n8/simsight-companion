"""How a request ends: answered with a zone, cancelled natively, or handed
to the native map."""

from __future__ import annotations

from typing import Any


import simsight_log
import simsight_menu as menu

from .state import _Pending, _dialog_active, _say, _service, _state


def _clear_picks(dialog: Any) -> None:
    # EA's picker listener pushes continuations from the picked results
    # without checking the response, so a cancel must never carry picks.
    try:
        dialog.picked_results = []
    except Exception:
        simsight_log.error('travel: clearing picks')


def _commit(pending: _Pending, zone_id: int) -> bool:
    """Answer EA's dialog with the chosen zone; EA does the rest."""
    if _state.pending is not pending:
        return False
    _state.pending = None
    dialog = pending.dialog
    if not _dialog_active(dialog):
        simsight_log.line(f'travel: dialog {dialog.dialog_id} no longer active, nothing sent')
        _say('action unavailable')
        return False
    from ui.ui_dialog import ButtonType

    service = _service()
    if not service.dialog_pick_result(dialog.dialog_id, [zone_id]):
        simsight_log.line(
            f'travel: zone {zone_id} refused by dialog {dialog.dialog_id}; request cancelled'
        )
        _say('action unavailable')
        _cancel_dialog(dialog)
        return False
    if service.dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_OK):
        simsight_log.line(f'travel: dialog {dialog.dialog_id} answered with zone {zone_id}')
        return True
    simsight_log.line(f'travel: dialog {dialog.dialog_id} did not accept OK; request cancelled')
    _say('action unavailable')
    _cancel_dialog(dialog)
    return False


def _cancel_dialog(dialog: Any) -> bool:
    """Cancel EA's dialog natively if it is still active, with no picks."""
    if not _dialog_active(dialog):
        return False
    _clear_picks(dialog)
    try:
        from ui.ui_dialog import ButtonType

        return bool(_service().dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_CANCEL))
    except Exception as ex:
        simsight_log.line(f'travel: cancelling dialog {dialog.dialog_id} failed ({ex!r})')
        return False


def _cancel(pending: _Pending) -> bool:
    """The request ends without a destination: cancel EA's dialog natively."""
    if _state.pending is not pending:
        return False
    _state.pending = None
    if _state.window_id is not None:
        menu.cancel_dialog(_state.window_id)
        _state.window_id = None
    simsight_log.line(f'travel: dialog {pending.dialog.dialog_id} cancelled from the list')
    result = _cancel_dialog(pending.dialog)
    _say('travel cancelled')
    return result


def _native_map(dialog: Any, msg_type: Any, dialog_msg: Any, kwargs: dict[str, Any]) -> Any:
    """EA's own map for this request."""
    original = _state.original_distribute
    if original is None:
        return None  # never installed: nothing replaced EA's map
    return original(dialog, msg_type, dialog_msg, **kwargs)


def _fallback(pending: _Pending) -> bool:
    """Hand the same request to the native map, once."""
    if _state.pending is not pending or pending.fallback_used or pending.rented:
        return False
    _state.pending = None
    pending.fallback_used = True
    if not _dialog_active(pending.dialog):
        _say('action unavailable')
        return False
    simsight_log.line(f'travel: dialog {pending.dialog.dialog_id} handed to the native map')
    _native_map(pending.dialog, pending.msg_type, pending.dialog_msg, pending.kwargs)
    return True

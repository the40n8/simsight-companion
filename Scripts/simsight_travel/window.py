"""Native picker windows for one request: showing, the reply gate and the
next step on the pump."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


import simsight_lang as lang
import simsight_log
import simsight_menu as menu
import simsight_pump as pump

from .state import _Pending, _state, enabled
from .outcomes import _cancel

if TYPE_CHECKING:
    from collections.abc import Callable
    from .state import Row, Step


def _icon() -> Any:
    try:
        import services
        from distributor.shared_messages import IconInfoData

        sim = services.get_active_sim()
        if sim is not None:
            return IconInfoData(obj_instance=sim)
    except Exception:
        simsight_log.error('travel: window icon')
    return None


def _fence_input() -> None:
    """Keys still down from opening a window must not answer it."""
    try:
        import simsight_keys as keys

        keys.fence_opening_input()
    except Exception:
        simsight_log.error('travel: input fence')


def _show_picker(dialog: Any, rows: list[Row] | tuple[Row, ...]) -> None:
    """Fill a native picker with rows and show it."""
    from ui.ui_dialog_picker import ObjectPickerRow

    icon_info = _icon()
    for index, (name, detail, tag) in enumerate(rows):
        dialog.add_row(
            ObjectPickerRow(
                option_id=index,
                name=name,
                row_description=detail,
                row_tooltip=None,
                icon=None,
                icon_info=icon_info,
                tag=tag,
                is_selected=False,
            )
        )


def _listener(on_pick: Step, pending: _Pending) -> Callable[[Any], None]:
    """EA calls this with the reply; errors stay here."""

    def heard(reply: Any) -> None:
        try:
            on_pick(pending, reply)
        except Exception:
            simsight_log.error('travel: window reply')

    return heard


def _open_window(pending: _Pending, title: str, text: str, rows: list[Row], on_pick: Step) -> bool:
    """rows: (localized name, localized description or None, tag). A window
    that cannot open cancels the request rather than leaving EA's dialog
    open with nothing on screen."""
    if _state.pending is not pending:
        return False
    try:
        menu.cancel_dialog(_state.window_id)
        _state.window_id = None
        dialog = menu.picker(lang.t(title), lang.t(text))
        if dialog is None:
            raise ValueError('no active Sim')
        _show_picker(dialog, rows)
        dialog.add_listener(_listener(on_pick, pending))
        _fence_input()
        dialog.show_dialog()
        _state.window_id = dialog.dialog_id
        return True
    except Exception as ex:
        simsight_log.line(f'travel: window failed ({ex!r}); request cancelled')
        _cancel(pending)
        return False


def _reply(pending: _Pending, dialog: Any) -> Any:
    """Common picker-reply gate: this request, our current window, one
    answer only, settings still on."""
    if _state.pending is not pending or dialog.dialog_id != _state.window_id:
        return None
    _state.window_id = None
    if not dialog.accepted or not enabled():
        _cancel(pending)
        return None
    try:
        tags = list(dialog.get_result_tags())
    except Exception as ex:
        simsight_log.line(f'travel: pick result failed: {ex!r}')
        tags = []
    if len(tags) != 1:
        _cancel(pending)
        return None
    return tags[0]


def _later(pending: _Pending, fn: Step, *args: Any) -> None:
    """Continue on the simulation pump after EA has finished handling the
    reply. A dead pump cancels the request instead of running inline. The
    scanner keeps quiet meanwhile: our next window is on its way."""
    try:
        import simsight_keys as keys

        keys.hold_scanner_resume()
    except Exception:
        simsight_log.error('travel: scanner hold')

    def step(owner: _Pending = pending) -> None:
        if _state.pending is not owner:
            return
        if enabled():
            fn(owner, *args)
        else:
            _cancel(owner)  # switched off between the reply and this step

    if not pump.post(step):
        simsight_log.line('travel: pump unavailable; request cancelled')
        _cancel(pending)

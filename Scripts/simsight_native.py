"""Native answers and refusal reasons shared by the simplified flows.

A picker is only ever answered through EA's dialog service: the pick goes
through the dialog's own pick_results (row membership) and the answer
through respond, so EA's listener runs as if the player had clicked. The
selection limits are checked first. Nothing here sets picker attributes or
turns on the service's automatic answering. A refused interaction reports
the game's own reason: the client fills the text of a native notification,
which the notification narration reads.
"""

from __future__ import annotations

from typing import Any, Iterable

import simsight_lang as lang
import simsight_log
import simsight_speech as speech


def _service() -> Any:
    import services

    return services.ui_dialog_service()


def dialog_active(dialog: Any) -> bool:
    """True while EA still holds this exact dialog as active."""
    try:
        service = _service()
        lookup = getattr(service, 'get_dialog', None)
        if lookup is not None:
            return lookup(dialog.dialog_id) is dialog
        return service._active_dialogs.get(dialog.dialog_id) is dialog
    except Exception:
        simsight_log.error('native: dialog lookup')
        return False


def _max_selectable(dialog: Any) -> int | None:
    # EA's max_selectable is a policy object (get_max_selectable); only a
    # plain number is checked here.
    maximum = getattr(dialog, 'max_selectable', None)
    if not isinstance(maximum, int) or isinstance(maximum, bool):
        return None
    return maximum


def _within_limits(dialog: Any, count: int) -> bool:
    minimum = getattr(dialog, 'min_selectable', 0) or 0
    maximum = _max_selectable(dialog)
    if count < minimum or (maximum is not None and maximum > 0 and count > maximum):
        simsight_log.line(
            f'native: {count} picked is outside dialog {dialog.dialog_id} limits ({minimum} to {maximum})'
        )
        return False
    return True


def _pick_and_confirm(dialog: Any, picked: list[Any]) -> bool:
    from ui.ui_dialog import ButtonType

    service = _service()
    if not service.dialog_pick_result(dialog.dialog_id, picked_results=picked):
        simsight_log.line(f'native: dialog {dialog.dialog_id} refused the pick {picked}')
        return False
    if not service.dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_OK):
        simsight_log.line(f'native: dialog {dialog.dialog_id} refused OK')
        return False
    return True


def answer_picker(dialog: Any, picked_results: Iterable[Any]) -> bool:
    """Answer an open EA picker with these row option ids and OK.

    False when the dialog is gone, the count is outside its limits, or EA
    refuses the pick or the answer. Nothing is changed in the first three
    cases; when EA accepts the pick but refuses OK, the dialog stays open
    holding that pick.
    """
    picked = list(picked_results)
    if not dialog_active(dialog):
        simsight_log.line(
            'native: dialog {} is gone; nothing answered'.format(getattr(dialog, 'dialog_id', '?'))
        )
        return False
    if not _within_limits(dialog, len(picked)):
        return False
    try:
        return _pick_and_confirm(dialog, picked)
    except Exception as ex:
        simsight_log.line(f'native: answering dialog {dialog.dialog_id} failed ({ex!r})')
        return False


def show_reason(tooltip: Any, sim: Any) -> bool:
    """EA's own reason text (a localized string factory) as a native
    notification. True when shown."""
    if tooltip is None or sim is None:
        return False
    try:
        from ui.ui_dialog_notification import UiDialogNotification

        notification = UiDialogNotification.TunableFactory().default(
            sim, text=lambda *_, **__: tooltip(sim)
        )
        notification.show_dialog()
        return True
    except Exception as ex:
        simsight_log.line(f'native: reason unavailable ({ex!r})')
        return False


def say_refusal(result: Any, sim: Any) -> bool:
    """A refused push: the spoken line first, then the game's reason when
    its test gives one."""
    speech.speak(lang.t('action unavailable'), explicit=True)
    tooltip = getattr(getattr(result, 'test_result', None), 'tooltip', None)
    if show_reason(tooltip, sim):
        simsight_log.line('native: refusal reason shown')
        return True
    return False

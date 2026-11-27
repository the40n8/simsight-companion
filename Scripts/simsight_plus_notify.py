"""SimSight Plus notification history (Alt+L).

Keeps the last 30 pop-up notifications (promotions, bills, invitations,
skill gains...) so they can be read again later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

import simsight_log
import simsight_patch
import simsight_plus_core as core

if TYPE_CHECKING:
    from date_and_time import DateAndTime
    from collections.abc import Callable

MAX = 30


class _Notification(NamedTuple):
    when: str  # day and clock text, or '' when the time was unavailable
    title: Any  # LocalizedString copy or None
    text: Any  # LocalizedString copy or None


_history: list[_Notification] = []  # newest last


def _copy(ls: Any) -> Any:
    try:
        from protocolbuffers.Localization_pb2 import LocalizedString

        out = LocalizedString()
        out.CopyFrom(ls)
    except Exception:  # an odd message part is left out, the rest is kept
        simsight_log.error('plus notify: copy text')
        return None
    return out


def _now_text() -> str:
    try:
        import services
        import simsight_plus_time as t

        now: DateAndTime = services.time_service().sim_now
        return f'{t.day_text(now)} {t.clock_text(now)}'
    except Exception:  # the notification is kept without its time
        simsight_log.error('plus notify: time')
        return ''


def _record(dialog_msg: Any) -> None:
    title = _copy(dialog_msg.title) if dialog_msg.HasField('title') else None
    text = _copy(dialog_msg.text) if dialog_msg.HasField('text') else None
    if title is None and text is None:
        return
    _history.append(_Notification(_now_text(), title, text))
    del _history[:-MAX]
    core.log(f'notification recorded ({len(_history)} kept)')


def _hook(original: Callable[..., Any]) -> Callable[..., Any]:
    """distribute_dialog(self, dialog_type, dialog_msg, ...): record, then show."""

    def a11yplus_distribute_dialog(*args: Any, **kwargs: Any) -> Any:
        try:
            _record(kwargs['dialog_msg'] if 'dialog_msg' in kwargs else args[2])
        except Exception:  # a game hook: the notification still shows
            simsight_log.error('plus notify: record notification')
        return original(*args, **kwargs)

    return a11yplus_distribute_dialog


def _install() -> None:
    from ui.ui_dialog_notification import UiDialogNotification

    simsight_patch.wrap(UiDialogNotification, 'distribute_dialog', _hook)


try:
    _install()
except Exception:
    simsight_log.error('plus notify: install notification hook')


def _row(n: int, note: _Notification) -> tuple[Any, Any, int]:
    label = note.text if note.text is not None else note.title
    if note.text is not None and note.title is not None:
        return (label, note.title, n)
    return (label, f'Received {note.when}', n)


@core.hotkey(ord('L'), 'last notifications')
def open_history() -> None:
    if not _history:
        core.speak('No notifications yet since the game was loaded.')
        return
    rows = [_row(n, note) for n, note in enumerate(reversed(_history))]
    core.choose(
        'Notifications',
        f'Newest first. {len(_history)} kept.',
        rows,
        lambda _value: None,
        tag='notify',
    )

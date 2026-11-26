"""Game windows and notifications for the phone companion (macOS).

- Dialogs: every window the game waits on (a question with buttons, a picker
  such as the recipe list, a text box) is listed in the state with its words,
  buttons and rows, and the phone answers it the way the window's own
  buttons do (UiDialogService.dialog_pick_result / dialog_text_input /
  dialog_respond).
- Notifications: the corner messages are sent to the phone as events once
  their words are known.

The game's own copy of each dialog message is kept when the game builds it
(UiDialog.build_msg), so the button texts are the ones the game shows.
Runs on the game tick only (from simsight_bridge), no threads.
"""

from __future__ import annotations

import contextlib
import sys
import time
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_names as names

if TYPE_CHECKING:
    from collections.abc import Iterable

_msgs: dict[int, Any] = {}  # dialog id -> UiDialogMessage the game sent
_notes: list[list[Any]] = []  # [deadline, title proto, text proto] waiting for their words
NOTE_WAIT = 4.0  # seconds to wait for a notification's words
_OK, _CANCEL = 10001, 10002
_MARK = '_simsight_phone'  # marks our wrappers on EA's functions


def _bridge() -> Any:
    return sys.modules['simsight_bridge']


def _text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return _bridge().text_of(value)


def _log(message: str) -> None:
    simsight_log.line('bridge: dialogs: ' + message)


def install() -> None:
    from ui.ui_dialog import UiDialog
    from ui.ui_dialog_notification import UiDialogNotification

    build = UiDialog.build_msg
    if not getattr(build, _MARK, False):

        def build_msg(self: Any, *args: Any, **kwargs: Any) -> Any:
            msg = build(self, *args, **kwargs)
            try:
                if not isinstance(self, UiDialogNotification):
                    _msgs[int(msg.dialog_id)] = msg
            except Exception:
                simsight_log.error('dialogs: build_msg')
            return msg

        setattr(build_msg, _MARK, True)
        UiDialog.build_msg = build_msg

    distribute = UiDialogNotification.distribute_dialog
    if not getattr(distribute, _MARK, False):

        def distribute_dialog(self: Any, dialog_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
            try:
                _note(dialog_msg)
            except Exception:
                simsight_log.error('dialogs: notification skipped')
            return distribute(self, dialog_type, dialog_msg, **kwargs)

        setattr(distribute_dialog, _MARK, True)
        UiDialogNotification.distribute_dialog = distribute_dialog


# ---------------------------------------------------------------- notifications


def _copy(msg: Any, field: str) -> Any:
    from protocolbuffers.Localization_pb2 import LocalizedString

    if not msg.HasField(field):
        return None
    copy = LocalizedString()
    copy.CopyFrom(getattr(msg, field))
    return copy


def _note(msg: Any) -> None:
    title, text = _copy(msg, 'title'), _copy(msg, 'text')
    if title is None and text is None:
        return
    for loc in (title, text):
        _text(loc)  # ask for the words now
    _notes.append([time.monotonic() + NOTE_WAIT, title, text])


def _resolved(loc: Any) -> bool:
    return loc is None or not int(getattr(loc, 'hash', 0) or 0) or bool(_text(loc))


def _send_note(title: Any, text: Any) -> None:
    subject, detail = _text(title), _text(text)
    if subject or detail:
        _bridge().send(
            {
                'event': {
                    'kind': 'EVENT_KIND_NOTIFICATION',
                    'subject': subject or detail,
                    'detail': detail if subject else '',
                }
            }
        )


def flush_notes() -> None:
    """Send the notifications whose words have arrived (or waited enough)."""
    now = time.monotonic()
    keep = []
    for entry in _notes:
        deadline, title, text = entry
        if (_resolved(title) and _resolved(text)) or now >= deadline:
            _send_note(title, text)
        else:
            keep.append(entry)
    _notes[:] = keep


# ---------------------------------------------------------------- dialogs


def _service() -> Any:
    import services

    return services.ui_dialog_service()


def _limit(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Where each kind of picker keeps its rows in the dialog message; these rows
# wrap the common fields in base_data.
_ROW_KINDS = (
    'recipe_picker_data',
    'sim_picker_data',
    'object_picker_data',
    'outfit_picker_data',
    'shop_picker_data',
    'lot_picker_data',
    'odd_job_picker_data',
)
# Lookups of a Sim or an object by id that can fail on a stale or odd id.
_LOOKUP_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


def _sim_name(sim_id: Any) -> str:
    try:
        import services

        info = services.sim_info_manager().get(int(sim_id))
        if info is not None:
            return names.full_name(info).strip()
    except _LOOKUP_ERRORS:
        pass  # no name: the row keeps its own words
    return ''


def _object_label(object_id: Any) -> str:
    try:
        import services

        obj = services.object_manager().get(int(object_id))
        if obj is not None:
            return _bridge()._object_name(obj)
    except _LOOKUP_ERRORS:
        pass  # no name: the row keeps its own words
    return ''


def _msg_row_pairs(picker: Any) -> list[tuple[Any, Any]]:
    """(row, the row's common fields) for the picker's kind of rows."""
    for kind in _ROW_KINDS:
        if picker.HasField(kind):
            rows = [(row, row.base_data) for row in getattr(picker, kind).row_data]
            if rows:
                return rows
            break
    return [(row, row) for row in getattr(picker, 'row_picker_data', None) or ()]


def _msg_row_name(row: Any, base: Any) -> str:
    name = _text(base.name) if base.HasField('name') else ''
    if not name and getattr(row, 'sim_id', None):
        name = _sim_name(row.sim_id)
    if not name and getattr(row, 'object_id', None):
        name = _object_label(row.object_id)
    if (
        not name
        and getattr(row, 'customer_name', None) is not None
        and row.HasField('customer_name')
    ):
        name = _text(row.customer_name)
    return name


def _with_price(description: str, price: Any) -> str:
    if not price or '§' in description:
        return description
    return f'§{price} · {description}' if description else f'§{price}'


def _msg_row(row: Any, base: Any) -> dict[str, Any]:
    enabled = bool(base.is_enable)
    description = _text(base.description) if base.HasField('description') else ''
    entry: dict[str, Any] = {
        'id': str(base.option_id),
        'name': _msg_row_name(row, base),
        'description': _with_price(
            description, getattr(row, 'price', 0) or getattr(row, 'cost', 0)
        ),
        'enabled': enabled,
        'selected': bool(base.is_selected),
    }
    if not enabled and base.HasField('tooltip'):
        entry['reason'] = _text(base.tooltip)
    return entry


def _msg_rows(msg: Any) -> list[dict[str, Any]]:
    """Rows as the game sent them to its own screen: names already built.
    Sim pickers show faces, not names, so the Sim's name is filled in."""
    if msg is None or not msg.HasField('picker_data'):
        return []
    return [_msg_row(row, base) for row, base in _msg_row_pairs(msg.picker_data)]


def _object_row(row: Any) -> dict[str, Any]:
    """A row from a dialog's own row object (mod pickers)."""
    enabled = bool(getattr(row, 'is_enable', True))
    entry: dict[str, Any] = {
        'id': str(row.option_id),
        'name': _text(getattr(row, 'name', None)),
        'description': _text(getattr(row, 'row_description', None)),
        'enabled': enabled,
        'selected': bool(getattr(row, 'is_selected', False)),
    }
    if not enabled:
        entry['reason'] = _text(getattr(row, 'row_tooltip', None))
    if not entry['name']:
        entry['name'] = _text(getattr(row, 'display_name', None))
    if not entry['name'] and getattr(row, 'sim_id', None):
        entry['name'] = _sim_name(row.sim_id)
    return entry


def _rows(dialog: Any, msg: Any = None) -> list[dict[str, Any]]:
    """Picker rows: from the game's own message when it has them, else from
    the dialog's row objects (mod pickers)."""
    rows = _msg_rows(msg)
    if rows:
        return rows
    out = []
    for row in list(getattr(dialog, 'picker_rows', None) or ()):
        entry = _object_row_or_none(row)
        if entry is not None:
            out.append(entry)
    return out


def _object_row_or_none(row: Any) -> dict[str, Any] | None:
    try:
        return _object_row(row)
    except Exception as ex:
        _log(f'row skipped: {ex!r}')
        return None


def _choices(msg: Any) -> list[dict[str, Any]]:
    choices = []
    for choice in msg.choices:
        cid = int(choice.choice_id)
        if cid >= 2**31:
            cid -= 2**32  # uint32 on the wire, ButtonType can be -1
        c: dict[str, Any] = {
            'id': cid,
            'text': _text(choice.text) if choice.HasField('text') else '',
        }
        if choice.HasField('disabled_text'):
            c['reason'] = _text(choice.disabled_text)
        choices.append(c)
    return choices


def _add_picker(entry: dict[str, Any], msg: Any, rows: list[dict[str, Any]]) -> None:
    entry['rows'] = rows
    picker = msg.picker_data if msg.HasField('picker_data') else None
    entry['minSelectable'] = _limit(getattr(picker, 'min_selectable', 1), 1) if picker else 1
    entry['maxSelectable'] = _limit(getattr(picker, 'max_selectable', 1), 1) if picker else 1
    if not entry['title'] and picker is not None and picker.HasField('title'):
        entry['title'] = _text(picker.title)


def _add_text_input(entry: dict[str, Any], msg: Any) -> None:
    entry['textInput'] = msg.text_input.text_input_name or 'value'
    if msg.text_input.HasField('initial_value'):
        entry['textDefault'] = _text(msg.text_input.initial_value)


def _dialog(dialog_id: Any, dialog: Any, msg: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        'id': str(dialog_id),
        'title': _text(msg.title) if msg.HasField('title') else '',
        'text': _text(msg.text) if msg.HasField('text') else '',
    }
    choices = _choices(msg)
    rows = _rows(dialog, msg)
    if rows:
        _add_picker(entry, msg, rows)
        if not choices:
            choices = [{'id': _OK}, {'id': _CANCEL}]
    if msg.HasField('text_input'):
        _add_text_input(entry, msg)
        if not choices:
            choices = [{'id': _OK}, {'id': _CANCEL}]
    if not choices:
        choices = [{'id': _OK}]
    entry['choices'] = choices
    return entry


def active() -> list[dict[str, Any]]:
    """The windows waiting for an answer, for the state."""
    service = _service()
    if service is None:
        return []
    live = dict(getattr(service, '_active_dialogs', {}) or {})
    for gone in [k for k in _msgs if k not in live]:
        del _msgs[gone]
    out = []
    for dialog_id in sorted(live):
        msg = _msgs.get(int(dialog_id))
        if msg is None:
            continue  # notifications and windows shown before we started
        entry = _dialog_or_none(dialog_id, live[dialog_id], msg)
        if entry is not None:
            out.append(entry)
    return out


def _dialog_or_none(dialog_id: Any, dialog: Any, msg: Any) -> dict[str, Any] | None:
    try:
        return _dialog(dialog_id, dialog, msg)
    except Exception as ex:
        _log(f'dialog {dialog_id} unreadable: {ex!r}')
        return None


def _row_ids(row_ids: Iterable[Any]) -> list[Any]:
    """Row ids as the game's option ids (ints), any other id as it came."""
    return [_option_id(r) for r in row_ids]


def _option_id(row_id: Any) -> Any:
    try:
        return int(row_id)
    except ValueError:
        return row_id


def _is_recipe_picker(msg: Any) -> bool:
    return (
        msg is not None
        and msg.HasField('picker_data')
        and msg.picker_data.HasField('recipe_picker_data')
    )


def _pick(service: Any, key: int, row_ids: Iterable[Any]) -> None:
    picked = _row_ids(row_ids)
    if _is_recipe_picker(_msgs.get(key)):
        # Like the game's recipe window: "Use Fresh Ingredients" is ticked by
        # default; without it the cook action cancels itself.
        service.dialog_pick_result(
            key, picked_results=picked, ingredient_check=True, prepped_ingredient_check=False
        )
    else:
        service.dialog_pick_result(key, picked_results=picked)


def _text_input_name(key: int) -> str:
    msg = _msgs.get(key)
    name = (
        msg.text_input.text_input_name
        if msg is not None and msg.HasField('text_input')
        else 'value'
    )
    return name or 'value'


def _clear_pick(dialog: Any) -> None:
    # Pickers ignore a cancel otherwise; any other window has nothing picked.
    with contextlib.suppress(AttributeError):
        dialog.picked_results = []


def respond(dialog_id: Any, choice_id: Any, row_ids: Any, text: str) -> tuple[bool, str]:
    """Answer a window like its own buttons: (ok, reason)."""
    service = _service()
    try:
        key = int(dialog_id)
    except ValueError:
        return False, 'dialog_closed'
    dialog = service.get_dialog(key) if service is not None else None
    if dialog is None:
        return False, 'dialog_closed'
    choice = int(choice_id)
    if choice in (_CANCEL, -1):
        _clear_pick(dialog)
    elif row_ids:
        _pick(service, key, row_ids)
    if text and choice != _CANCEL:
        service.dialog_text_input(key, _text_input_name(key), text)
    service.dialog_respond(key, choice)
    still_open = service.get_dialog(key) is not None
    _log(
        f'answered {key} ({type(dialog).__name__}) with {choice} rows {list(row_ids)}; '
        f'still open: {still_open}'
    )
    return True, ''

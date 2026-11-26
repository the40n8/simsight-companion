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

import sys
import time

_msgs = {}  # dialog id -> UiDialogMessage the game sent
_notes = []  # [deadline, title proto, text proto] waiting for their words
NOTE_WAIT = 4.0  # seconds to wait for a notification's words
_OK, _CANCEL = 10001, 10002


def _bridge():
    return sys.modules['simsight_bridge']


def _text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return _bridge().text_of(value)


def _log(message):
    try:
        _bridge().log('dialogs: ' + message)
    except Exception:
        pass


def install():
    from ui.ui_dialog import UiDialog
    from ui.ui_dialog_notification import UiDialogNotification

    build = UiDialog.build_msg
    if not getattr(build, '_simsight_phone', False):

        def build_msg(self, *args, **kwargs):
            msg = build(self, *args, **kwargs)
            try:
                if not isinstance(self, UiDialogNotification):
                    _msgs[int(msg.dialog_id)] = msg
            except Exception:
                pass
            return msg

        build_msg._simsight_phone = True
        UiDialog.build_msg = build_msg

    distribute = UiDialogNotification.distribute_dialog
    if not getattr(distribute, '_simsight_phone', False):

        def distribute_dialog(self, dialog_type, dialog_msg, **kwargs):
            try:
                _note(dialog_msg)
            except Exception as ex:
                _log('notification skipped: {!r}'.format(ex))
            return distribute(self, dialog_type, dialog_msg, **kwargs)

        distribute_dialog._simsight_phone = True
        UiDialogNotification.distribute_dialog = distribute_dialog


# ---------------------------------------------------------------- notifications


def _copy(msg, field):
    from protocolbuffers.Localization_pb2 import LocalizedString

    if not msg.HasField(field):
        return None
    copy = LocalizedString()
    copy.CopyFrom(getattr(msg, field))
    return copy


def _note(msg):
    title, text = _copy(msg, 'title'), _copy(msg, 'text')
    if title is None and text is None:
        return
    for loc in (title, text):
        _text(loc)  # ask for the words now
    _notes.append([time.monotonic() + NOTE_WAIT, title, text])


def _resolved(loc):
    return loc is None or not int(getattr(loc, 'hash', 0) or 0) or bool(_text(loc))


def flush_notes():
    """Send the notifications whose words have arrived (or waited enough)."""
    now = time.monotonic()
    keep = []
    for entry in _notes:
        deadline, title, text = entry
        if (_resolved(title) and _resolved(text)) or now >= deadline:
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
        else:
            keep.append(entry)
    _notes[:] = keep


# ---------------------------------------------------------------- dialogs


def _service():
    import services

    return services.ui_dialog_service()


def _limit(value, default):
    try:
        return int(value)
    except Exception:
        return default


# Where each kind of picker keeps its rows in the dialog message, and whether
# a row wraps the common fields in base_data.
_ROW_KINDS = (
    'recipe_picker_data',
    'sim_picker_data',
    'object_picker_data',
    'outfit_picker_data',
    'shop_picker_data',
    'lot_picker_data',
    'odd_job_picker_data',
)


def _sim_name(sim_id):
    try:
        import services

        info = services.sim_info_manager().get(int(sim_id))
        if info is not None:
            return '{} {}'.format(info.first_name, info.last_name).strip()
    except Exception:
        pass
    return ''


def _object_label(object_id):
    try:
        import services

        obj = services.object_manager().get(int(object_id))
        if obj is not None:
            return _bridge()._object_name(obj)
    except Exception:
        pass
    return ''


def _msg_rows(msg):
    """Rows as the game sent them to its own screen: names already built.
    Sim pickers show faces, not names, so the Sim's name is filled in."""
    if msg is None or not msg.HasField('picker_data'):
        return []
    picker = msg.picker_data
    rows = []
    for kind in _ROW_KINDS:
        if picker.HasField(kind):
            rows = [(row, row.base_data) for row in getattr(picker, kind).row_data]
            break
    if not rows:
        rows = [(row, row) for row in getattr(picker, 'row_picker_data', None) or ()]
    out = []
    for row, base in rows:
        name = _text(base.name) if base.HasField('name') else ''
        if not name and getattr(row, 'sim_id', 0):
            name = _sim_name(row.sim_id)
        if not name and getattr(row, 'object_id', 0):
            name = _object_label(row.object_id)
        if (
            not name
            and getattr(row, 'customer_name', None) is not None
            and row.HasField('customer_name')
        ):
            name = _text(row.customer_name)
        enabled = bool(base.is_enable)
        entry = {
            'id': str(base.option_id),
            'name': name,
            'description': _text(base.description) if base.HasField('description') else '',
            'enabled': enabled,
            'selected': bool(base.is_selected),
        }
        price = getattr(row, 'price', 0) or getattr(row, 'cost', 0)
        if price and '§' not in entry['description']:
            entry['description'] = (
                '§{} · {}'.format(price, entry['description'])
                if entry['description']
                else '§{}'.format(price)
            )
        if not enabled and base.HasField('tooltip'):
            entry['reason'] = _text(base.tooltip)
        out.append(entry)
    return out


def _rows(dialog, msg=None):
    """Picker rows: from the game's own message when it has them, else from
    the dialog's row objects (mod pickers)."""
    rows = _msg_rows(msg)
    if rows:
        return rows
    out = []
    for row in list(getattr(dialog, 'picker_rows', None) or ()):
        try:
            enabled = bool(getattr(row, 'is_enable', True))
            entry = {
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
            out.append(entry)
        except Exception as ex:
            _log('row skipped: {!r}'.format(ex))
    return out


def _dialog(dialog_id, dialog, msg):
    entry = {
        'id': str(dialog_id),
        'title': _text(msg.title) if msg.HasField('title') else '',
        'text': _text(msg.text) if msg.HasField('text') else '',
    }
    choices = []
    for choice in msg.choices:
        cid = int(choice.choice_id)
        if cid >= 2**31:
            cid -= 2**32  # uint32 on the wire, ButtonType can be -1
        c = {'id': cid, 'text': _text(choice.text) if choice.HasField('text') else ''}
        if choice.HasField('disabled_text'):
            c['reason'] = _text(choice.disabled_text)
        choices.append(c)
    rows = _rows(dialog, msg)
    if rows:
        entry['rows'] = rows
        picker = msg.picker_data if msg.HasField('picker_data') else None
        entry['minSelectable'] = _limit(getattr(picker, 'min_selectable', 1), 1) if picker else 1
        entry['maxSelectable'] = _limit(getattr(picker, 'max_selectable', 1), 1) if picker else 1
        if not entry['title'] and picker is not None and picker.HasField('title'):
            entry['title'] = _text(picker.title)
        if not choices:
            choices = [{'id': _OK}, {'id': _CANCEL}]
    if msg.HasField('text_input'):
        entry['textInput'] = msg.text_input.text_input_name or 'value'
        if msg.text_input.HasField('initial_value'):
            entry['textDefault'] = _text(msg.text_input.initial_value)
        if not choices:
            choices = [{'id': _OK}, {'id': _CANCEL}]
    if not choices:
        choices = [{'id': _OK}]
    entry['choices'] = choices
    return entry


def active():
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
        try:
            out.append(_dialog(dialog_id, live[dialog_id], msg))
        except Exception as ex:
            _log('dialog {} unreadable: {!r}'.format(dialog_id, ex))
    return out


def respond(dialog_id, choice_id, row_ids, text):
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
    if choice == _CANCEL or choice == -1:
        try:
            dialog.picked_results = []  # pickers ignore a cancel otherwise
        except Exception:
            pass
    elif row_ids:
        picked = []
        for r in row_ids:
            try:
                picked.append(int(r))
            except ValueError:
                picked.append(r)
        msg = _msgs.get(key)
        if (
            msg is not None
            and msg.HasField('picker_data')
            and msg.picker_data.HasField('recipe_picker_data')
        ):
            # Like the game's recipe window: "Use Fresh Ingredients" is ticked by
            # default; without it the cook action cancels itself.
            service.dialog_pick_result(
                key, picked_results=picked, ingredient_check=True, prepped_ingredient_check=False
            )
        else:
            service.dialog_pick_result(key, picked_results=picked)
    if text and choice != _CANCEL:
        msg = _msgs.get(key)
        name = (
            msg.text_input.text_input_name
            if msg is not None and msg.HasField('text_input')
            else 'value'
        )
        service.dialog_text_input(key, name or 'value', text)
    service.dialog_respond(key, choice)
    _log(
        'answered {} ({}) with {} rows {}; still open: {}'.format(
            key, type(dialog).__name__, choice, list(row_ids), service.get_dialog(key) is not None
        )
    )
    return True, ''

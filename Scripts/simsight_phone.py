"""Announce waiting household calls; open the recipient's existing phone UI.

No automatic responses, timeout changes or background-thread game calls.

Simplified menus add the phone list: the same actions the phone offers now,
built by EA's own phone menu code (the phone's disable tests, its choice
menu, tests, visibility, names and categories), shown in two picker
windows (categories, then actions). Choosing an action runs it exactly as
the phone would (test_and_execute), so it opens the game's own career
picker, event planner, move screen and so on. Travel goes through the
travel list's own Go alone / Choose who comes window.
"""

from __future__ import annotations

import functools
from typing import Any, Callable

import sims4.commands
import simsight_lang as lang
import simsight_log
import simsight_settings as settings
import simsight_speech as speech

_CLOSE_WAIT = 1.0  # seconds a chosen action waits for our window to close
_MAX_WAITING = 32  # calls and texts remembered


class _State:
    def __init__(self) -> None:
        self.waiting: list[tuple[int, int]] = []  # (dialog_id, owner_id), oldest first
        self.list_id: int | None = None  # our open phone window
        self.phone_list: _PhoneList | None = None  # the list behind it


def _recipient(dialog: Any) -> tuple[int, Any]:
    owner = getattr(dialog, 'owner', None)
    info = getattr(owner, 'sim_info', owner)
    return getattr(info, 'sim_id', 0), info


def _note(dialog: Any, phone_ring_type: Any) -> None:
    from ui.ui_dialog import PhoneRingType
    import services

    if phone_ring_type == PhoneRingType.NO_RING or not settings.get('keyboard', True):
        return
    owner_id, info = _recipient(dialog)
    if not owner_id:
        return
    client = services.client_manager().get_first_client()
    if client is None or info not in client.selectable_sims:
        return
    token = (dialog.dialog_id, owner_id)
    if token in _state.waiting:
        return
    _state.waiting.append(token)
    del _state.waiting[:-_MAX_WAITING]
    name = ' '.join(
        part for part in (getattr(info, 'first_name', ''), getattr(info, 'last_name', '')) if part
    )
    speech.speak(f'Phone for {name}. Control Alt P opens the waiting call or text.', source='event')


def open_waiting(list_when_none: bool = False) -> bool:
    """Runs only on the simulation pump or a game command callback. With
    list_when_none (Control Alt P) and Simplified menus on, the phone list
    opens when no call or text is waiting; a waiting one always comes first."""
    import services
    import distributor.ops
    import distributor.system

    service = services.ui_dialog_service()
    client = services.client_manager().get_first_client()
    if service is None or client is None:
        return False
    active = getattr(service, '_active_dialogs', {})
    while _state.waiting:
        dialog_id, owner_id = _state.waiting[0]
        dialog = active.get(dialog_id)
        info = services.sim_info_manager().get(owner_id)
        if dialog is None or info not in client.selectable_sims:
            _state.waiting.pop(0)
            continue
        if client.active_sim_info is not info and not client.set_active_sim_by_id(owner_id):
            speech.speak('The recipient is not available.', explicit=True)
            return False
        # Flash waits for ActiveSimId to match before clicking the phone.
        op = distributor.ops.SendUIMessage(f'A11y|phone|{owner_id}')
        distributor.system.Distributor.instance().add_op_with_no_owner(op)
        return True
    if list_when_none and simplified():
        return open_list()
    speech.speak('No waiting call or text.', explicit=True)
    return False


# ------------------------------------------------------------- phone list


def simplified() -> bool:
    return bool(settings.get('simplified_menus', True))


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


class _Entry:
    """One phone action: EA's aop with the name and texts the phone shows."""

    __slots__ = ('aop', 'name', 'enabled', 'tip', 'detail', 'affordance_id')

    def __init__(self, aop: Any, name: Any, enabled: bool, tip: Any, detail: Any) -> None:
        self.aop = aop
        self.name = name
        self.enabled = enabled
        self.tip = tip
        self.detail = detail
        self.affordance_id = getattr(aop.affordance, 'guid64', None)


class _PhoneList:
    def __init__(
        self, sim_id: int, groups: list[tuple[Any, list[_Entry]]], direct: list[_Entry]
    ) -> None:
        self.sim_id = sim_id
        self.groups = groups  # [(localized label, [_Entry])], in phone order
        self.direct = direct  # [_Entry] with no category


_state = _State()


def _root(category: Any) -> Any:
    depth = 0
    while category is not None and getattr(category, '_parent', None) is not None and depth < 8:
        category = category._parent
        depth += 1
    return category


def _entry(sim: Any, item: Any, categories: Any) -> tuple[Any, _Entry] | None:
    """(root category or None, entry) for one choice, or None when the
    pie menu modifiers hide it."""
    aop = item.aop
    affordance = aop.affordance
    # As EA's create_pie_menu_message (1.128.90 bytecode, lines 881-884):
    # the pie menu modifiers decide visibility only, and their tooltip is
    # replaced by the forbid-by-object-tags one.
    visible, _suppression = sim.test_pie_menu_modifiers(affordance)
    if not visible:
        return None
    modifier_tooltip = sim.apply_forbid_by_object_tags_pie_menu_modifiers(aop)
    name = affordance.get_name(aop.target, item.context, **aop.interaction_parameters)
    tooltip = modifier_tooltip or item.result.tooltip
    enabled = bool(item.result) and not modifier_tooltip
    tip = None
    if tooltip is not None:
        tip = affordance.create_localized_string(
            tooltip, context=item.context, target=aop.target, **aop.interaction_parameters
        )
    category = categories.get(item.category_key) if item.category_key else None
    root = _root(category)
    detail = None
    if category is not None and category is not root:
        detail = category._display_name(sim)
    elif tip is None:
        detail = affordance.get_display_tooltip(
            context=item.context, target=aop.target, **aop.interaction_parameters
        )
    return root, _Entry(aop, name, enabled, tip, detail)


def _priority(root: Any) -> float:
    value = getattr(root, '_display_priority', 0)
    return value if isinstance(value, (int, float)) else 0


def _collect(sim: Any, client: Any) -> _PhoneList:
    """EA's own phone menu (interactions.phone_choices and
    create_pie_menu_message): the same aops, tests, visibility, names and
    reasons the phone would show."""
    import services
    import sims4.resources
    from interactions.choices import ChoiceMenu

    context = client.create_interaction_context(sim)
    choice_menu = ChoiceMenu(sim)
    choice_menu.add_potential_aops(None, context, sim.potential_phone_interactions(context), None)
    categories = services.get_instance_manager(sims4.resources.Types.PIE_MENU_CATEGORY)
    tutorial = services.get_tutorial_service()
    groups: dict[Any, list[_Entry]] = {}
    order: list[Any] = []
    direct: list[_Entry] = []
    for _option_id, item in choice_menu:
        if tutorial is not None and not tutorial.is_affordance_visible(item.aop.affordance):
            continue
        found = _entry(sim, item, categories)
        if found is None:
            continue
        root, entry = found
        if root is None:
            direct.append(entry)
            continue
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(entry)
    order.sort(key=_priority)  # stable: equal priorities keep the phone's order
    return _PhoneList(
        sim.sim_id, [(root._display_name(sim), groups[root]) for root in order], direct
    )


def _phone_refused(sim: Any) -> bool:
    """The phone's own disable tests (with EA's reason) and the full
    queue, as interactions.phone_choices checks them. True when refused."""
    from event_testing.resolver import SingleSimResolver
    from sims.phone_tuning import PhoneTuning
    import simsight_native as native

    resolver = SingleSimResolver(sim.sim_info)
    for phone_test in PhoneTuning.DISABLE_PHONE_TESTS:
        if resolver(phone_test.test):
            _say('phone unavailable')
            native.show_reason(phone_test.tooltip, sim)
            return True
    if sim.queue is not None and not sim.queue.can_queue_visible_interaction():
        _say('action queue full')
        return True
    return False


def _current_sim(phone_list: _PhoneList) -> tuple[Any, Any]:
    """The active Sim and client, only while Simplified menus are on and
    the list still belongs to that Sim; otherwise says why and gives None."""
    import services

    if not simplified():
        _say('action unavailable')
        return None, None
    sim = services.get_active_sim()
    client = services.client_manager().get_first_client()
    if sim is None or client is None or sim.sim_id != phone_list.sim_id:
        _say('sim unavailable')
        return None, None
    return sim, client


def open_list() -> bool:
    """Simulation pump or a command callback: the phone list for the
    active Sim, or the phone's own reason why there is none."""
    import services

    sim = services.get_active_sim()
    client = services.client_manager().get_first_client()
    if sim is None or client is None:
        _say('load a household first')
        return False
    try:
        if _phone_refused(sim):
            return False
        phone_list = _collect(sim, client)
    except Exception:
        simsight_log.error('phone: list unavailable')
        _say('action unavailable')
        return False
    if not phone_list.groups and not phone_list.direct:
        _say('phone empty')
        return False
    simsight_log.line(
        f'phone: list for sim {sim.sim_id}: {len(phone_list.groups)} categories, {len(phone_list.direct)} other actions'
    )
    return _show_top(phone_list)


def _row(
    option_id: int,
    name: Any,
    detail: Any,
    tag: tuple[Any, ...],
    enabled: bool = True,
    tip: Any = None,
) -> Any:
    from ui.ui_dialog_picker import ObjectPickerRow

    return ObjectPickerRow(
        option_id=option_id,
        name=name,
        row_description=detail,
        row_tooltip=(lambda *_, text=tip: text) if tip is not None else None,
        icon=None,
        icon_info=_icon(),
        tag=tag,
        is_selected=False,
        is_enable=enabled,
    )


def _icon() -> Any:
    """The active Sim's thumbnail for a row, or None."""
    try:
        import services
        from distributor.shared_messages import IconInfoData

        sim = services.get_active_sim()
        if sim is not None:
            return IconInfoData(obj_instance=sim)
    except Exception:
        simsight_log.error('phone: row icon')
    return None


def _keys_call(name: str) -> None:
    """simsight_keys.<name>(); the phone window works without it."""
    try:
        import simsight_keys as keys

        getattr(keys, name)()
    except Exception:
        simsight_log.error(f'phone: keys.{name}')


def _picked(reply: Any, owner: _PhoneList) -> None:
    """EA's reply to our phone window: the chosen row goes to the pump."""
    import simsight_pump as pump

    if reply.dialog_id != _state.list_id or _state.phone_list is not owner:
        return
    _state.list_id = None
    if not reply.accepted:
        return
    try:
        tags = list(reply.get_result_tags())
    except Exception:
        simsight_log.error('phone: reply tags')
        tags = []
    if len(tags) != 1:
        return
    if tags[0][0] in ('group', 'back'):
        _keys_call('hold_scanner_resume')  # the next phone window follows
    if not pump.post(_chosen, owner, tags[0]):
        _say('action unavailable')


def _open(phone_list: _PhoneList, title: Any, text: str, rows: list[Any]) -> bool:
    """One phone window at a time; its reply goes to the pump. title: None
    for the phone itself, or a category's game string."""
    import simsight_menu as menu

    menu._cancel_dialog(_state.list_id)
    _state.list_id = None
    dialog = menu._picker(lang.t('phone'), lang.t(text), localized_title=title)
    if dialog is None:
        _say('load a household first')
        return False
    for row in rows:
        dialog.add_row(row)

    def picked(reply: Any, owner: _PhoneList = phone_list) -> None:
        try:
            _picked(reply, owner)
        except Exception:
            simsight_log.error('phone: window reply')

    dialog.add_listener(picked)
    _state.phone_list = phone_list
    _keys_call('fence_opening_input')
    dialog.show_dialog()
    _state.list_id = dialog.dialog_id
    return True


def _entry_rows(entries: list[_Entry], start: int) -> list[Any]:
    return [
        _row(start + index, entry.name, entry.detail, ('do', entry), entry.enabled, entry.tip)
        for index, entry in enumerate(entries)
    ]


def _show_top(phone_list: _PhoneList) -> bool:
    import simsight_menu as menu

    rows = []
    for index, (label, entries) in enumerate(phone_list.groups):
        term = 'phone group count one' if len(entries) == 1 else 'phone group count'
        count = menu._raw(lang.t(term).format(len(entries)))
        rows.append(_row(index, label, count, ('group', index)))
    rows.extend(_entry_rows(phone_list.direct, len(rows)))
    return _open(phone_list, None, 'phone list instructions', rows)


def _show_group(phone_list: _PhoneList, index: int) -> bool:
    import simsight_menu as menu

    label, entries = phone_list.groups[index]
    rows = [_row(0, menu._raw(lang.t('back to phone')), None, ('back',))]
    rows.extend(_entry_rows(entries, 1))
    return _open(phone_list, label, 'phone group instructions', rows)


def _chosen(phone_list: _PhoneList, tag: tuple[Any, ...]) -> bool:
    """Pump, after EA has finished with the window. The setting and the
    Sim are checked again: either can change while a window is open."""
    if _state.phone_list is not phone_list:
        return False
    sim, _client = _current_sim(phone_list)
    if sim is None:
        _state.phone_list = None
        return False
    kind = tag[0]
    if kind == 'group':
        return _show_group(phone_list, tag[1])
    if kind == 'back':
        return _show_top(phone_list)
    if kind == 'do':
        return _run(phone_list, tag[1])
    return False


def _refused_now(sim: Any, entry: _Entry) -> bool:
    """The phone's own checks and the pie menu modifiers again (they can
    change while the window is open). True, said aloud, when refused."""
    import simsight_native as native

    try:
        if _phone_refused(sim):
            return True
        visible, _suppression = sim.test_pie_menu_modifiers(entry.aop.affordance)
        forbidden = sim.apply_forbid_by_object_tags_pie_menu_modifiers(entry.aop)
    except Exception:
        simsight_log.error('phone: checks failed')
        _say('action unavailable')
        return True
    if not visible or forbidden:
        _say('action unavailable')
        native.show_reason(forbidden, sim)
        return True
    return False


def _travel_window(sim: Any, entry: _Entry) -> bool:
    """The travel list's own window for the phone's travel action. True when shown."""
    try:
        import simsight_travel as travel

        return bool(
            entry.affordance_id == travel._TRAVEL_AFFORDANCE
            and travel.enabled()
            and travel.simplified()
            and travel._show_front(sim)
        )
    except Exception:
        simsight_log.error('phone: travel window unavailable')
        return False


def _run(phone_list: _PhoneList, entry: _Entry) -> bool:
    """The chosen phone action, as the phone runs it (ChoiceMenu.select):
    checks again, then a fresh context, EA's tests again, then execute."""
    _state.phone_list = None
    sim, _client = _current_sim(phone_list)
    if sim is None:
        return False
    if not entry.enabled:
        _say('action unavailable')
        return False
    if _refused_now(sim, entry):
        return False
    if _travel_window(sim, entry):
        return True
    if not _when_window_closed(_execute, phone_list, entry):
        _say('action unavailable')
        return False
    return True


def _when_window_closed(fn: Callable[..., object], *args: Any) -> bool:
    """Pump: run fn(*args) once the client reports that no menu or dialog
    owns the keys, meaning our phone window has really closed. A native
    screen from another movie sent sooner opens behind the closing window
    (live 2026-09-26: Purchase Gifts left a dim, empty modal). If a window
    still owns the keys after the wait, nothing runs and the player hears
    why. False when the pump is unavailable."""
    import time
    import simsight_pump as pump

    deadline = time.monotonic() + _CLOSE_WAIT

    def step() -> object:
        try:
            import simsight_keys as keys

            waiting = bool(keys._modal_active)
        except (ImportError, AttributeError):
            waiting = False  # no key tracking: nothing known to own the keys
        if not waiting:
            return fn(*args)
        if time.monotonic() < deadline and pump.post(step):
            return None
        simsight_log.line('phone: a window still owns the keys; action not run')
        _say('action unavailable')
        return False

    return pump.post(step)


def _execute(phone_list: _PhoneList, entry: _Entry) -> bool:
    """Pump, once our window has closed: the Sim and the setting again, a
    fresh context, EA's tests again, then execute."""
    import simsight_native as native

    sim, client = _current_sim(phone_list)
    if sim is None:
        return False
    try:
        context = client.create_interaction_context(sim)
        result = entry.aop.test_and_execute(context)
    except Exception:
        simsight_log.error('phone: action failed')
        _say('action unavailable')
        return False
    if not result:
        simsight_log.line(f'phone: action refused: {result}')
        native.say_refusal(result, sim)
        return False
    simsight_log.line(f'phone: ran {entry.affordance_id}')
    return True


@sims4.commands.Command(
    'simsight.phonelist', 'a11y.phonelist', command_type=sims4.commands.CommandType.Live
)
def a11y_phonelist(_connection: Any = None) -> bool:
    try:
        return open_list()
    except Exception:
        simsight_log.error('phone: phonelist command')
        return False


@sims4.commands.Command(
    'simsight.phone', 'a11y.phone', command_type=sims4.commands.CommandType.Live
)
def a11y_phone(_connection: Any = None) -> bool:
    try:
        return open_waiting()
    except Exception:
        simsight_log.error('phone: phone command')
        return False


def _install() -> None:
    from ui.ui_dialog_service import UiDialogService

    original = UiDialogService.dialog_show
    if getattr(original, '_a11y_phone_hook', False):
        return

    @functools.wraps(original)
    def dialog_show(self: Any, dialog: Any, phone_ring_type: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, dialog, phone_ring_type, *args, **kwargs)
        try:
            if not self.is_phone_silenced and dialog.dialog_id in self._active_dialogs:
                _note(dialog, phone_ring_type)
        except Exception:
            simsight_log.error('phone: announcement failed')
        return result

    setattr(dialog_show, '_a11y_phone_hook', True)  # noqa: B010 - a marker EA's function type does not declare
    UiDialogService.dialog_show = dialog_show


try:
    _install()
except Exception:
    simsight_log.error('phone: hook unavailable')

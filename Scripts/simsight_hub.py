"""Common accessibility actions in one native, keyboard-navigable picker.

All callbacks run on the simulation thread. Rows retain real Sim ids and are
revalidated on selection; closing a window never selects a default action.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

import sims4.commands
import simsight_contacts as contacts
import simsight_lang as lang
from simsight_commands import console_command
import simsight_log
import simsight_names as names
import simsight_menu as menu
import simsight_pump as pump
import simsight_speech as speech

if TYPE_CHECKING:
    from sims.household import Household
    from sims.sim import Sim

    # A window row: (localized name, localized description, callback). The
    # callback is called with no arguments; Callable[...] lets rows bind
    # their values as lambda defaults.
    Row = tuple[Any, Any, Callable[..., object]]
    # An accessibility menu row: (lang term, callback).
    Action = tuple[str, Callable[..., object]]


class _State:
    def __init__(self) -> None:
        self.dialog_id: int | None = None  # our open hub window
        self.sell_factory: Any = None  # UiSellPicker factory, made once


_state = _State()

# Native UI messages verified in SimInfoTrayMain. They retain EA's enabled
# checks and panel setup. The hub is closed before one of these is sent.
PANELS = (
    ('needs', 'A11yNeeds'),
    ('mood and moodlets', 'A11yMood'),
    ('wants and fears', 'A11yWants'),
    ('aspirations', 'OpenSimInfoAspirationPanel'),
    ('skills', 'OpenSimInfoSkillPanel'),
    ('career', 'OpenSimInfoCareerPanel'),
    ('inventory', 'OpenSimInfoInventoryPanel'),
    ('relationships', 'OpenSimInfoRelationshipPanel'),
    ('simology', 'OpenSimInfoSummaryPanel'),
    ('social groups', 'OpenSimInfoClubPanel'),
    ('owned businesses', 'OpenHouseholdInfoVenuePanel'),
    ('calendar', 'ShowCalendar'),
    ('photography controls', 'A11yPhotography'),
)


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


def _client() -> Any:
    import services

    return services.client_manager().get_first_client()


def send_ui(message: str) -> None:
    import distributor.ops
    import distributor.system

    distributor.system.Distributor.instance().add_op_with_no_owner(
        distributor.ops.SendUIMessage(message)
    )


def row_icon() -> Any:
    """The active Sim's thumbnail for a window row (its icon_info), or None."""
    import services

    try:
        from distributor.shared_messages import IconInfoData

        sim = services.get_active_sim()
        if sim is not None:
            return IconInfoData(obj_instance=sim)
    except Exception:
        simsight_log.error('hub: row icon')
    return None


def _selected(reply: Any, callbacks: dict[int, Callable[..., object]]) -> None:
    """EA's reply to our window: the chosen row's callback goes to the pump."""
    if reply.dialog_id != _state.dialog_id:
        return
    _state.dialog_id = None
    if not reply.accepted:
        return
    tags = list(reply.get_result_tags())
    if len(tags) != 1 or tags[0] not in callbacks:
        return
    # Defer until EA finishes dismissing this dialog. Never fall back to
    # executing on an arbitrary caller thread if the pump has stopped.
    if not pump.post(callbacks[tags[0]]):
        _say('action unavailable')


def show_window(
    title: str, description: str, rows: Sequence[Row], localized_title: Any = None
) -> bool:
    """Rows are (localized name, localized description, callback)."""
    from ui.ui_dialog_picker import ObjectPickerRow

    menu.cancel_dialog(_state.dialog_id)
    _state.dialog_id = None
    dialog = menu.picker(lang.t(title), lang.t(description), localized_title=localized_title)
    if dialog is None:
        _say('load a household first')
        return False
    # Text-only item rows drew a broken-image placeholder in every row
    # (reported 2026-09-15). Object rows with the active Sim's own thumbnail
    # ask the client for a real icon, like the scanner window does.
    icon_info = row_icon()
    callbacks: dict[int, Callable[..., object]] = {}
    for index, (name, detail, callback) in enumerate(rows):
        callbacks[index] = callback
        dialog.add_row(
            ObjectPickerRow(
                option_id=index,
                name=name,
                row_description=detail,
                row_tooltip=None,
                icon=None,
                icon_info=icon_info,
                tag=index,
                is_selected=False,
            )
        )

    def selected(reply: Any) -> None:
        try:
            _selected(reply, callbacks)
        except Exception:
            simsight_log.error('hub: window reply')

    dialog.add_listener(selected)
    _state.dialog_id = dialog.dialog_id
    # The console Enter that opened this menu belongs to the console.
    # Retire queued activations before the new picker can receive them.
    import simsight_keys as keys

    keys.fence_opening_input()
    dialog.show_dialog()
    return True


def select_sim(sim_id: int) -> bool:
    import services

    client = _client()
    info = services.sim_info_manager().get(sim_id)
    if client is None or info is None or info not in client.selectable_sims:
        _say('sim unavailable')
        return False
    if client.active_sim_info is not info and not client.set_active_sim_by_id(sim_id):
        _say('sim unavailable')
        return False
    speech.speak(names.full_name(info), explicit=True)
    return True


def away_actions() -> bool:
    import simsight_away as away

    return away.open_away_actions()


@console_command('simsight.away', 'a11y.away', command_type=sims4.commands.CommandType.Live)
def a11y_away(_connection: Any = None) -> bool:
    return _command('away', away_actions)


def household() -> bool:
    client = _client()
    if client is None:
        _say('load a household first')
        return False
    rows: list[Row] = []
    for info in client.selectable_sims:
        sim_id = info.sim_id
        detail = 'active sim' if info is client.active_sim_info else 'select this sim'
        rows.append(
            (
                menu.raw_text(names.full_name(info)),
                menu.raw_text(lang.t(detail)),
                lambda ident=sim_id: select_sim(ident),
            )
        )
    if not rows:
        _say('load a household first')
        return False
    return show_window('household sims', 'choose a sim', rows)


def funds_text(household: Household | None = None) -> str:
    """'Household funds: 12,345 simoleons' for the active household."""
    if household is None:
        import services

        household = services.active_household()
    if household is None:
        return ''
    return '{}: {:,} {}'.format(
        lang.t('household funds'), household.funds.money, lang.t('simoleons')
    )


def funds() -> bool:
    import services

    household = services.active_household()
    if household is None:
        _say('load a household first')
        return False
    speech.speak(funds_text(household), explicit=True)
    return True


def read_clock() -> bool:
    """The client's clock text, the same request as Control Alt C. The hub
    picker has already closed when a row callback runs."""
    import simsight_keys as keys

    return keys.request_clock()


def scanner_help() -> bool:
    # Retain the callback name for callers of older hub versions.
    import simsight_shortcuts as shortcuts

    return shortcuts.speak()


def floor_interactions() -> bool:
    import simsight_ground as ground

    return ground.open_floor_menu()


def self_interactions() -> bool:
    import services
    import simsight_scanner as scanner

    sim = services.get_active_sim()
    if sim is None or not scanner.enabled() or not scanner.rescan():
        _say('action unavailable')
        return False
    if not scanner.select_item(scanner.YOURSELF, sim.id):
        _say('action unavailable')
        return False
    scanner.open_pie_menu()
    return True


def queue_entries(sim: Sim) -> list[tuple[Any, Any, str]]:
    """The icons of EA's interaction queue, from the same UIManager records
    the client draws: running (super, group and posture) first, then queued
    in order. Each is (info, interaction or None, state)."""
    from protocolbuffers import Sims_pb2

    infos = [info for info in sim.ui_manager.get_interactions_gen() if not info.canceled]
    running = [info for info in infos if info.ui_state != Sims_pb2.IQ_QUEUED]
    queued = [info for info in infos if info.ui_state == Sims_pb2.IQ_QUEUED]
    return [
        (info, _queue_interaction(sim, info.interaction_id), state)
        for state, group in (('running', running), ('queued', queued))
        for info in group
    ]


def _queue_interaction(sim: Sim, interaction_id: int) -> Any:
    # interactions.cancel_si looks an id up the same two ways.
    interaction = sim.find_interaction_by_id(interaction_id)
    return interaction if interaction is not None else sim.find_continuation_by_id(interaction_id)


def _queue_detail(info: Any, interaction: Any, state: str, position: int, total: int) -> str:
    parts = []
    if info.is_visual_type_posture():
        parts.append(lang.t('queue posture'))
    elif state == 'running':
        parts.append(lang.t('queue running'))
    else:
        parts.append(lang.t('queue waiting').format(position, total))
    if interaction is not None and interaction.is_autonomous:
        parts.append(lang.t('queue autonomous'))
    elif interaction is not None and interaction.is_user_directed:
        parts.append(lang.t('queue chosen by you'))
    parts.append(
        lang.t('queue enter cancels') if info.user_cancelable else lang.t('queue cannot cancel')
    )
    return '. '.join(parts)


def action_queue() -> bool:
    """Control Alt Q and the accessibility menu row: the active Sim's action
    queue as a native picker. Enter on a row cancels that action the way
    clicking its queue icon does."""
    import services

    sim = services.get_active_sim()
    if sim is None:
        _say('load a household first')
        return False
    try:
        entries = queue_entries(sim)
    except Exception:
        simsight_log.error('hub: queue read failed')
        _say('action unavailable')
        return False
    sim_id = sim.id
    rows: list[Row] = []
    cancelable = [info.interaction_id for info, _i, _s in entries if info.user_cancelable]
    if len(cancelable) > 1:
        rows.append(
            (
                menu.raw_text(lang.t('cancel all actions')),
                None,
                lambda ids=tuple(cancelable): cancel_actions(sim_id, ids),
            )
        )
    queued_total = sum(1 for _info, _i, state in entries if state == 'queued')
    position = 0
    for info, interaction, state in entries:
        if state == 'queued':
            position += 1
        name = info.display_name
        if name is None and interaction is not None:
            name = interaction.get_name()
        if name is None:
            continue
        detail = menu.raw_text(_queue_detail(info, interaction, state, position, queued_total))
        rows.append(
            (name, detail, lambda value=info.interaction_id: cancel_actions(sim_id, (value,)))
        )
    if not rows:
        rows.append((menu.raw_text(lang.t('queue empty')), None, open_hub))
    return show_window('action queue', 'action queue instructions', rows)


def cancel_actions(sim_id: int, interaction_ids: Sequence[int]) -> bool:
    """Cancel on the simulation thread after the picker closes. Everything is
    looked up again: the Sim or the action may have changed meanwhile."""
    import services

    sim = services.get_active_sim()
    if sim is None or sim.id != sim_id:
        _say('sim unavailable')
        return False
    canceled = refused = 0
    for interaction_id in interaction_ids:
        interaction = _queue_interaction(sim, interaction_id)
        if interaction is None:
            continue
        if interaction.user_cancelable and interaction.cancel_user(
            cancel_reason_msg='SimSight action queue'
        ):
            canceled += 1
        else:
            refused += 1
    if canceled:
        _say('action canceled')
    elif refused:
        _say('queue cannot cancel')
    else:
        _say('action already finished')
    return canceled > 0


# Enter on an item of the active Sim's inventory (TooltipManager
# A11yInventoryMenu posts simsight.invitem_enc) opens this item menu instead of
# EA's pie menu. Item actions asks Flash to replay the original click.
# Selling follows EA's own paths (game 1.128.90): the inventory panel's
# sell request (inventory.sim_inventory_sell_multiple, items with id and
# count) for one or all, and the native sell picker for an amount.


def _currency() -> int:
    """GameplayStateData.CurrentRegionCurrencyBucksType on the server side:
    the region's bucks type; INVALID (0) means simoleons."""
    import services

    region = services.current_region()
    return int(getattr(region, 'region_currency_bucks_type', 0) or 0) if region is not None else 0


def _price_text(amount: int, currency: int) -> str:
    if currency == 0:
        unit = lang.t('simoleons')
    else:
        try:
            from bucks.bucks_enums import BucksType

            name = BucksType(currency).name.lower()
        except (ImportError, AttributeError, KeyError, ValueError):
            name = ''  # no bucks module, or a type this game does not know: no unit
        unit = lang.t('galactic credits') if 'galactic' in name else ''
    return f'{amount:,} {unit}'.strip()


def _inventory_item(obj_id: int) -> tuple[Any, Any]:
    """(active Sim, object) when the object is in that Sim's own inventory."""
    import services

    sim = services.get_active_sim()
    obj = services.inventory_manager().get(obj_id)
    inventory = sim.inventory_component if sim is not None else None
    if inventory is None or obj is None or obj not in inventory:
        return sim, None
    return sim, obj


def _sellable(obj: Any) -> bool:
    # The panel's two gates: sim_inventory_sell_multiple skips
    # non_deletable_by_user, and the item info carries
    # is_sell_disabled_from_component (InventoryStorage).
    if getattr(obj, 'non_deletable_by_user', False):
        return False
    check = getattr(obj, 'is_sell_disabled_from_component', None)
    return not (check is not None and check())


def _sell_price(sim: Sim, obj: Any, count: int) -> int:
    # sim_inventory_sell_multiple: current_value * count * sell modifier, rounded.
    return round(obj.current_value * count * sim.sim_info.get_item_sell_price_modifier(obj))


def _sell_rows(sim: Sim, obj: Any, obj_id: int) -> list[Row]:
    """The selling rows of the item menu."""
    sim_id = sim.id
    if not _sellable(obj):
        return [(menu.raw_text(lang.t('cannot be sold')), None, lambda: _say('cannot be sold'))]
    count = obj.stack_count()
    currency = _currency()
    one = _price_text(_sell_price(sim, obj, 1), currency)
    if count <= 1:
        return [
            (
                menu.raw_text(lang.t('sell for').format(one)),
                None,
                lambda: sell_inventory_item(sim_id, obj_id, 1),
            )
        ]
    every = _price_text(_sell_price(sim, obj, count), currency)
    return [
        (
            menu.raw_text(lang.t('sell one for').format(one)),
            None,
            lambda: sell_inventory_item(sim_id, obj_id, 1),
        ),
        (
            menu.raw_text(lang.t('sell all for').format(count, every)),
            None,
            lambda: confirm_sell_all(sim_id, obj_id),
        ),
        (
            menu.raw_text(lang.t('choose how many to sell')),
            None,
            lambda: sell_window(sim_id, obj_id),
        ),
    ]


def inventory_item_menu(obj_id: int) -> bool:
    sim, obj = _inventory_item(obj_id)
    if obj is None:
        # Not the active Sim's own inventory: EA's menu as usual.
        send_ui('A11yInvItemActions')
        return False
    from sims4.localization import LocalizationHelperTuning

    rows: list[Row] = [
        (menu.raw_text(lang.t('item actions')), None, lambda: send_ui('A11yInvItemActions'))
    ]
    rows.extend(_sell_rows(sim, obj, obj_id))
    return show_window(
        'inventory item',
        'inventory item instructions',
        rows,
        localized_title=LocalizationHelperTuning.get_object_name(obj),
    )


def _checked_item(sim_id: int, obj_id: int) -> tuple[Any, Any]:
    """Look everything up again on the simulation thread after a menu."""
    sim, obj = _inventory_item(obj_id)
    if sim is None or sim.id != sim_id:
        _say('sim unavailable')
        return None, None
    if obj is None:
        _say('item no longer in inventory')
        return None, None
    if not _sellable(obj):
        _say('cannot be sold')
        return None, None
    return sim, obj


def confirm_sell_all(sim_id: int, obj_id: int) -> bool:
    sim, obj = _checked_item(sim_id, obj_id)
    if obj is None:
        return False
    from sims4.localization import LocalizationHelperTuning

    count = obj.stack_count()
    every = _price_text(_sell_price(sim, obj, count), _currency())
    return show_window(
        'inventory item',
        'sell all instructions',
        [
            (
                menu.raw_text(lang.t('yes sell all').format(count, every)),
                None,
                lambda: sell_inventory_item(sim_id, obj_id, count),
            ),
            (menu.raw_text(lang.t('no keep them')), None, lambda: _say('nothing sold')),
        ],
        localized_title=LocalizationHelperTuning.get_object_name(obj),
    )


def sell_inventory_item(sim_id: int, obj_id: int, count: int) -> bool:
    """EA's inventory panel sell request for count items of one object."""
    import services

    sim, obj = _checked_item(sim_id, obj_id)
    if obj is None:
        return False
    count = min(int(count), obj.stack_count())
    if count < 1:
        _say('item no longer in inventory')
        return False
    currency = _currency()
    amount = _sell_price(sim, obj, count)
    household = services.active_household()
    before = household.funds.money if household is not None and currency == 0 else None
    from google.protobuf import text_format
    from protocolbuffers import UI_pb2
    from server_commands import inventory_commands

    request = UI_pb2.InventorySellRequest()
    request.sim_id = sim.id
    request.currency_type = currency
    item = request.items.add()
    item.id = obj.id
    item.count = count
    client = services.client_manager().get_first_client()
    inventory_commands.sim_inventory_sell_multiple(
        text_format.MessageToString(request), _connection=client.id if client is not None else None
    )
    if before is not None and household.funds.money <= before:
        _say('sale failed')
        return False
    speech.speak(lang.t('sold for').format(_price_text(amount, currency)), explicit=True)
    return True


def _sold(reply: Any, currency: int) -> None:
    """The sell picker's reply: remove what was sold and say the total."""
    import services

    if not reply.accepted:
        return
    triples = reply.ids_and_amounts_and_price
    source = reply.source_inventory
    if not triples or source is None:
        simsight_log.line('hub: sell window reply without amounts')
        return
    manager = services.inventory_manager()
    removed = paid = 0
    for item_id, amount, price in zip(triples[::3], triples[1::3], triples[2::3]):
        item = manager.get(item_id)
        if item is None:
            continue
        count = min(amount, item.stack_count())
        if count > 0 and source.try_destroy_object(item, count):
            removed += count
            paid += price * count
    if removed:
        speech.speak(lang.t('sold for').format(_price_text(paid, currency)), explicit=True)


def sell_window(sim_id: int, obj_id: int) -> bool:
    """EA's native sell picker (quantity, price and total) for one item.
    Set up as SellPickerInteraction._setup_dialog does; the client's Sell
    runs inventory.sell_picker_response_by_ids, which pays and stores the
    amounts on the dialog; this listener then removes what was sold, as
    SellPickerInteraction._remove_sold_objects_from_inventory does."""
    import simsight_keys as keys

    sim, obj = _checked_item(sim_id, obj_id)
    if obj is None:
        return False
    from sims4.localization import LocalizationHelperTuning
    from ui.ui_dialog_picker import PurchasePickerRow, UiSellPicker

    if _state.sell_factory is None:
        _state.sell_factory = UiSellPicker.TunableFactory()
    name = LocalizationHelperTuning.get_object_name(obj)
    dialog = _state.sell_factory.default(
        sim,
        title=lambda *_args, **_kwargs: name,
        text=lambda *_args, **_kwargs: menu.raw_text(lang.t('sell window instructions')),
    )
    dialog.inventory_object_id = sim.id
    dialog.use_dialog_pick_response = False
    dialog.add_row(
        PurchasePickerRow(
            def_id=obj.definition.id,
            is_enable=True,
            num_available=obj.stack_count(),
            tags=obj.definition.build_buy_tags,
            custom_price=_sell_price(sim, obj, 1),
            objects={obj},
        )
    )
    currency = _currency()

    def sold(reply: Any) -> None:
        try:
            _sold(reply, currency)
        except Exception:
            simsight_log.error('hub: sell window reply')

    dialog.add_listener(sold)
    keys.fence_opening_input()
    dialog.show_dialog()
    return True


# Events (Ctrl+Alt+V and the accessibility menu row): the user-facing
# situations the game shows (parties, dates, holidays, career events), with
# EA's own ending and leaving paths (game 1.128.90):
#   * SituationManager.get_user_facing_situations_gen() lists them;
#   * ending is what situations.pre_destroy_user_facing_situation and
#     situations.destroy_user_facing_situation do:
#     pre_destroy_situation_by_id(id), then destroy_situation_by_id(id);
#   * sending a guest home is SituationManager.make_sim_leave(sim), the
#     game's normal leave (it falls back to make_sim_leave_now_must_run
#     itself when the leave situation is missing).
# Household Sims are never sent away. Everything is looked up again when a
# row is chosen: the event may have ended meanwhile.


def _situation_manager() -> Any:
    import services

    return services.get_zone_situation_manager()


def _events() -> list[Any]:
    manager = _situation_manager()
    return list(manager.get_user_facing_situations_gen()) if manager is not None else []


def _is_guest(sim: Any, home: Any) -> bool:
    try:
        info = sim.sim_info
    except AttributeError:
        return False  # a Sim being taken apart: not a guest
    return not (
        getattr(info, 'household_id', None) == home or getattr(info, 'is_selectable', False)
    )


def _guests(situation: Any) -> list[Any]:
    """Sims in the event who are not in the active household."""
    import services

    household = services.active_household()
    home = getattr(household, 'id', None)
    return [sim for sim in list(situation.all_sims_in_situation_gen()) if _is_guest(sim, home)]


def _event_detail(situation: Any) -> str:
    parts = [lang.t('event guests').format(len(_guests(situation)))]
    try:
        # As Situation._get_remaining_time_in_minutes: None without an end time.
        span = situation.get_remaining_time()
        minutes = int(span.in_minutes()) if span is not None else 0
        if minutes > 0:
            parts.append(lang.t('event minutes left').format(minutes))
    except (AttributeError, TypeError, ValueError):
        pass  # an event without an end time: the row shows the guests only
    except Exception:
        simsight_log.error('hub: event time left')
    return '. '.join(parts)


def events_window() -> bool:
    """The running events; Enter on one opens what can be done with it."""
    try:
        events = _events()
    except Exception:
        simsight_log.error('hub: events unavailable')
        _say('action unavailable')
        return False
    if not events:
        _say('no events')
        return False
    rows: list[Row] = []
    for situation in events:
        name = getattr(situation, 'display_name', None)
        if name is None:
            continue
        rows.append(
            (
                name,
                menu.raw_text(_event_detail(situation)),
                lambda value=situation.id: event_actions(value),
            )
        )
    if not rows:
        _say('no events')
        return False
    return show_window('events', 'events instructions', rows)


def event_situation(situation_id: int) -> tuple[Any, Any]:
    manager = _situation_manager()
    situation = manager.get(situation_id) if manager is not None else None
    if situation is None or not getattr(situation, 'is_user_facing', False):
        _say('event already over')
        return None, None
    return manager, situation


def event_actions(situation_id: int) -> bool:
    _manager, situation = event_situation(situation_id)
    if situation is None:
        return False
    rows: list[Row] = [
        (menu.raw_text(lang.t('current event goals')), None, lambda: event_goals(situation_id)),
        (
            menu.raw_text(lang.t('send guests home and end event')),
            None,
            lambda: end_event(situation_id, send_home=True, end=True),
        ),
        (
            menu.raw_text(lang.t('end event')),
            None,
            lambda: end_event(situation_id, send_home=False, end=True),
        ),
        (
            menu.raw_text(lang.t('send guests home')),
            None,
            lambda: end_event(situation_id, send_home=True, end=False),
        ),
    ]
    return show_window(
        'events',
        'event actions instructions',
        rows,
        localized_title=getattr(situation, 'display_name', None),
    )


def event_goals(situation_id: int) -> bool:
    import simsight_event_goals as goals

    return goals.open_goals(situation_id)


def _send_guests_home(manager: Any, situation: Any) -> int:
    """How many guests were sent home with the game's normal leave."""
    sent = 0
    for sim in _guests(situation):
        try:
            manager.make_sim_leave(sim)
            sent += 1
        except Exception:  # noqa: PERF203 - one guest refusing must not keep the others
            simsight_log.error('hub: could not send a guest home')
    return sent


def _end_situation(manager: Any, situation_id: int) -> bool:
    try:
        manager.pre_destroy_situation_by_id(situation_id)
        manager.destroy_situation_by_id(situation_id)
    except Exception:
        simsight_log.error('hub: could not end the event')
        return False
    return True


def end_event(situation_id: int, send_home: bool, end: bool) -> bool:
    manager, situation = event_situation(situation_id)
    if situation is None:
        return False
    sent = _send_guests_home(manager, situation) if send_home else 0
    ended = _end_situation(manager, situation_id) if end else False
    parts = []
    if send_home:
        parts.append(lang.t('guests sent home').format(sent))
    if end:
        parts.append(lang.t('event ended') if ended else lang.t('event not ended'))
    speech.speak(' '.join(parts), explicit=True)
    return ended or sent > 0


@console_command('simsight.events', 'a11y.events', command_type=sims4.commands.CommandType.Live)
def a11y_events(_connection: Any = None) -> bool:
    return _command('events', events_window)


@console_command(
    'simsight.invitem_enc', 'a11y.invitem_enc', command_type=sims4.commands.CommandType.Live
)
def a11y_invitem_enc(text: str = '', _connection: Any = None) -> bool:
    try:
        obj_id = int(text.strip())
    except ValueError:
        return False
    return _command('invitem_enc', lambda: inventory_item_menu(obj_id))


# Build cursor actions reachable without holding a chord. This window is a
# modal, so opening it retires the native cursor; a 'menu:' row asks the
# client to bring the cursor back where it was and perform that one action
# after the window has closed, under the cursor's own guards. On, off, undo,
# redo and the grid aim are the ordinary requests. Native feedback is what
# the player hears.
BUILD_ACTIONS = (
    ('build cursor on', 'on'),
    ('build cursor off', 'off'),
    ('move left', 'menu:left'),
    ('move right', 'menu:right'),
    ('move up', 'menu:up'),
    ('move down', 'menu:down'),
    ('fine step', 'menu:fine'),
    ('coarse step', 'menu:coarse'),
    ('center cursor', 'menu:center'),
    ('aim at grid point', 'grid'),
    ('select or place', 'menu:click'),
    ('cancel tool', 'menu:cancel'),
    ('rotate left', 'menu:rotateleft'),
    ('rotate right', 'menu:rotateright'),
    ('undo', 'undo'),
    ('redo', 'redo'),
    ('cursor status', 'menu:status'),
)


def _build_action(action: str) -> object:
    import simsight_build as build

    if action.startswith('menu:'):
        return build.request_menu(action[5:])
    return build.request(action)


def build_actions() -> bool:
    import simsight_build as build

    if not build.controls_enabled():
        return build.refuse_disabled()
    if build.build_zone() is None:
        # simsight_build's own voice (simsight_plus_build may replace it).
        build._say('Enter Build mode first.', 'Najpierw włącz tryb budowania.')  # noqa: SLF001
        return False
    rows: list[Row] = [
        (menu.raw_text(lang.t(label)), None, lambda value=action: _build_action(value))
        for label, action in BUILD_ACTIONS
    ]
    return show_window('build actions', 'build actions instructions', rows)


def _rows(actions: Sequence[Action]) -> list[Row]:
    return [(menu.raw_text(lang.t(label)), None, callback) for label, callback in actions]


def _group(title: str, actions: Sequence[Action]) -> Action:
    def open_group() -> bool:
        back: Action = ('back to accessibility menu', open_hub)
        return show_window(
            title,
            'accessibility menu instructions',
            _rows([*actions, back]),
        )

    return title, open_group


def _nearby_objects() -> bool:
    import simsight_scanner as scanner

    if scanner.enabled():
        return menu.show_scanner_window(group=scanner.OBJECTS)
    _say('enable the scanner first')
    return False


def _travel() -> bool:
    import services
    import simsight_travel as travel

    sim = services.get_active_sim()
    if sim is None or sim.zone_id != services.current_zone_id():
        _say('sim unavailable')
        return False
    if travel.enabled() and travel.simplified():
        if travel.show_front(sim):
            return True
    else:
        result = travel.push_travel(sim)
        if result == 'ok':
            return True
        travel.say_refusal(result, sim)
        return False
    _say('action unavailable')
    return False


def _panel(label: str) -> Action:
    """A row opening the native panel PANELS names for label."""
    message = dict(PANELS)[label]
    return label, lambda: send_ui(message)


def _yourself_rows() -> list[Action]:
    import simsight_simology as simology

    rows: list[Action] = [('self interactions', self_interactions), _panel('simology')]
    if simology.milestones_available():
        rows.append(('milestones', simology.milestones_window))
    if simology.values_available():
        rows.append(('character values', simology.values_window))
    return rows


def _tool_rows() -> tuple[list[Action], list[Action]]:
    """(build tools, home and lot rows): the build tools only in Build mode,
    the read-only lot grid only outside it, both only with build controls on."""
    import simsight_build as build
    import simsight_business as business
    import simsight_grid as grid
    import simsight_place as place

    building = place.in_build_mode()
    build_tools: list[Action] = []
    lot_tools: list[Action] = [
        ('household funds', funds),
        _panel('owned businesses'),
        ('businesses', business.businesses_window),
        ('Floor actions here', floor_interactions),
    ]
    if build.controls_enabled():
        if building:
            build_tools = [
                ('guided placement', place.guided_placement),
                ('build placement', grid.placement_toggle),
                ('undo last accessible placement', place.undo),
            ]
        else:
            lot_tools.append(('read-only lot grid', grid.toggle))
    return build_tools, lot_tools


def _other_rows() -> list[Action]:
    import simsight_phone as phone

    rows: list[Action] = [
        ('waiting call or text', phone.open_waiting),
        _panel('photography controls'),
    ]
    if phone.simplified():
        rows.append(('phone', phone.open_list))
    rows.append(('keyboard shortcuts', scanner_help))
    rows.append(('settings menu', menu.show_settings_window))
    return rows


def open_hub() -> bool:
    yourself = _yourself_rows()
    build_tools, lot_tools = _tool_rows()
    groups: list[Action] = [
        _group('yourself', yourself),
        _panel('needs'),
        _panel('mood and moodlets'),
        _group(
            'Sims',
            (
                ('household sims', household),
                ('away actions', away_actions),
                ('known contacts', contacts.open_contacts),
            ),
        ),
        ('nearby objects', _nearby_objects),
        _panel('inventory'),
        _group(
            'places',
            (('travel', _travel), _panel('calendar'), ('events', events_window)),
        ),
        _group('home and lot', lot_tools),
        _panel('skills'),
        _group('wishes and aspirations', (_panel('wants and fears'), _panel('aspirations'))),
        _group('relationships', (_panel('relationships'), _panel('social groups'))),
        _panel('career'),
        _group('now', (('action queue', action_queue), ('day and time', read_clock))),
    ]
    other = _other_rows()
    # Mac: the phone companion first, the row people come here for most.
    return show_window(
        'accessibility menu',
        'accessibility menu instructions',
        _rows(_companion_rows() + build_tools + groups + other),
    )


def _companion_rows() -> list[Action]:
    """The phone companion's QR code page and the setup page (macOS, Windows)."""
    import sys

    bridge = sys.modules.get('simsight_bridge')
    if sys.platform not in ('darwin', 'win32') or bridge is None:
        return []
    return [
        ('phone companion', bridge.open_pairing_page),
        ('companion setup', bridge.open_setup_page),
    ]


def _command(name: str, run: Callable[[], bool]) -> bool:
    """A console command's body: a failure is logged, never raised into the game."""
    try:
        return run()
    except Exception:
        simsight_log.error(f'hub: {name} command')
        return False


@console_command('simsight.hub', 'a11y.hub', command_type=sims4.commands.CommandType.Live)
def a11y_hub(_connection: Any = None) -> bool:
    return _command('hub', open_hub)


@console_command(
    'simsight.buildmenu', 'a11y.buildmenu', command_type=sims4.commands.CommandType.Live
)
def a11y_buildmenu(_connection: Any = None) -> bool:
    return _command('buildmenu', build_actions)


@console_command(
    'simsight.household', 'a11y.household', command_type=sims4.commands.CommandType.Live
)
def a11y_household(_connection: Any = None) -> bool:
    return _command('household', household)


@console_command('simsight.funds', 'a11y.funds', command_type=sims4.commands.CommandType.Live)
def a11y_funds(_connection: Any = None) -> bool:
    return _command('funds', funds)

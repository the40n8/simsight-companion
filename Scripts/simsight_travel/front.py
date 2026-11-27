"""EA's Travel for the active Sim: the Go alone / Choose who comes window,
the push, and the companion picker answered with nobody for Go alone."""

from __future__ import annotations

from typing import Any


import simsight_lang as lang
import simsight_log
import simsight_menu as menu
import simsight_pump as pump

from .state import _dialog_active, _say, _state, _zone_loaded, enabled, simplified
from .window import _fence_input, _show_picker


_ALONE, _WITH = 'alone', 'with'

TRAVEL_AFFORDANCE = 423375  # self_SimPicker_TravelWith (Sim menu Travel)


def _away(sim: Any) -> bool:
    """The Sim is on another lot than the loaded zone."""
    import services

    try:
        return sim.zone_id != services.current_zone_id()
    except Exception:
        simsight_log.error('travel: active Sim zone')
        return False  # unknown: EA's own tests decide


def _note_alone(result: Any) -> None:
    """After a Go alone push: remember a queued Travel, which shows its
    companion picker later.

    EA never picks a companion by itself here (1.128.90 bytecode of
    SimPickerInteraction._run_interaction_gen): a single eligible Sim is
    preselected only for tuning with single_choice_display_name, which 423375
    does not set, and nothing is passed in picked_item_ids. With nobody
    eligible (min_selectable 0) EA travels alone without a picker. Otherwise
    its picker is shown and answered by _sim_distribute."""
    if _state.alone_claimed:
        return  # already claimed inside the push
    interaction = getattr(result, 'interaction', None)
    if interaction is not None:
        _state.alone = interaction
    else:
        simsight_log.line('travel: no interaction to match; the companion picker will show')


def push_travel(sim: Any, alone: bool = False) -> str:
    """EA's own Travel (the companion picker, then the map picker this
    module lists). 'ok', 'refused' or 'error'; a refusal speaks EA's reason
    when its test gives one."""
    import services

    _state.alone = None  # a new Travel replaces any earlier Go alone
    _state.alone_claimed = False
    try:
        import sims4.resources
        from interactions.context import InteractionContext
        from interactions.priority import Priority

        affordance = services.get_instance_manager(sims4.resources.Types.INTERACTION).get(
            TRAVEL_AFFORDANCE
        )
        if affordance is None:
            raise ValueError('travel affordance missing')
        client = services.client_manager().get_first_client()
        context = InteractionContext(
            sim, InteractionContext.SOURCE_PIE_MENU, Priority.High, client=client, pick=None
        )
        # 423375 is an ImmediateSuperInteraction: it runs, and EA shows its
        # companion picker, inside this push (2026-09-26 live, BFF household:
        # the picker appeared before the push returned and was not claimed).
        _state.alone_push = sim.sim_id if alone else None
        try:
            result = sim.push_super_affordance(affordance, sim, context)
        finally:
            _state.alone_push = None
        if not result:
            simsight_log.line(f'travel: keyboard entry refused: {result}')
            say_refusal(result, sim)
            return 'refused'
        if alone:
            _note_alone(result)
        return 'ok'
    except Exception as ex:
        simsight_log.line(f'travel: keyboard entry failed: {ex!r}')
        _say('action unavailable')
        return 'error'


def say_refusal(result: Any, sim: Any) -> Any:
    """The game's own reason for a refused Travel (for example a social
    event in progress), after the spoken line."""
    import simsight_native as native

    return native.say_refusal(result, sim)


def _front_picked(reply: Any, sim_id: int) -> None:
    """The Go alone / Choose who comes window answered."""
    if reply.dialog_id != _state.front_id:
        return
    _state.front_id = None
    if not reply.accepted:
        return
    try:
        tags = list(reply.get_result_tags())
    except Exception:
        simsight_log.error('travel: front window result')
        tags = []
    if len(tags) != 1 or tags[0] not in (_ALONE, _WITH):
        return
    # After EA has finished with this window; never inline.
    if not pump.post(_front_chosen, sim_id, tags[0] == _ALONE):
        _say('action unavailable')


def show_front(sim: Any) -> bool:
    """Simplified menus: Go alone or Choose who comes, before EA's own
    companion picker. Escape closes it and nothing is pushed."""
    try:
        menu.cancel_dialog(_state.front_id)
        _state.front_id = None
        dialog = menu.picker(lang.t('travel'), lang.t('travel front instructions'))
        if dialog is None:
            return False
        rows = (
            ('travel alone', 'travel alone detail', _ALONE),
            ('travel with others', 'travel with others detail', _WITH),
        )
        _show_picker(
            dialog,
            [
                (menu.raw_text(lang.t(name)), menu.raw_text(lang.t(detail)), tag)
                for name, detail, tag in rows
            ],
        )
        sim_id = sim.sim_id

        def picked(reply: Any) -> None:
            try:
                _front_picked(reply, sim_id)
            except Exception:
                simsight_log.error('travel: front window reply')

        dialog.add_listener(picked)
        _fence_input()
        dialog.show_dialog()
        _state.front_id = dialog.dialog_id
        return True
    except Exception as ex:
        simsight_log.line(f'travel: front window failed ({ex!r})')
        return False


def _front_chosen(sim_id: int, alone: bool) -> bool:
    """Pump: the same checks as the M entry, then EA's Travel."""
    import services

    if not enabled() or not _zone_loaded():
        _say('action unavailable')
        return False
    sim = services.get_active_sim()
    if sim is None or sim.sim_id != sim_id or _away(sim):
        _say('sim unavailable')
        return False
    outcome = push_travel(sim, alone=alone)
    simsight_log.line(
        'travel: {} chosen, Travel {}'.format('alone' if alone else 'with others', outcome)
    )
    return outcome == 'ok'


def _listeners(dialog: Any) -> list[Any]:
    try:
        return list(getattr(dialog, '_listeners', ()) or ())
    except TypeError:
        return []  # not a list of listeners: not ours


def _ours(dialog: Any, interaction: Any) -> bool:
    """EA's companion picker belongs to our Go alone push: its listener is
    the pushed interaction's own _on_picker_selected."""
    return any(
        getattr(listener, '__self__', None) is interaction for listener in _listeners(dialog)
    )


def _ours_in_push(dialog: Any, sim_id: int) -> bool:
    """Inside our own Go alone push: the picker's listener belongs to a
    Travel (423375) interaction of that same Sim."""
    for listener in _listeners(dialog):
        owner = getattr(listener, '__self__', None)
        if owner is None or getattr(owner, 'guid64', None) != TRAVEL_AFFORDANCE:
            continue
        if getattr(getattr(owner, 'sim', None), 'sim_id', None) == sim_id:
            return True
    return False


def _claims(dialog: Any) -> bool:
    """Whether this Sim picker is the companion picker of our Go alone:
    shown inside our push (an immediate Travel), or later by the queued
    Travel interaction we pushed."""
    if _state.alone_push is not None and _ours_in_push(dialog, _state.alone_push):
        _state.alone_push = None
        _state.alone_claimed = True
        return True
    interaction = _state.alone
    if interaction is None:
        return False
    if getattr(interaction, 'is_finishing', False) or getattr(
        interaction, 'has_been_canceled', False
    ):
        _state.alone = None
        simsight_log.line('travel: Go alone ended before the companion picker')
        return False
    if _ours(dialog, interaction):
        _state.alone = None
        return True
    return False


def _native_sim_picker(dialog: Any, msg_type: Any, dialog_msg: Any, kwargs: dict[str, Any]) -> Any:
    original = _state.original_sim_distribute
    if original is None:
        return None  # never installed: nothing replaced EA's picker
    return original(dialog, msg_type, dialog_msg, **kwargs)


def _sim_distribute(dialog: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    """EA's companion picker for our Go alone request is answered with
    nobody through EA's dialog service, only when the picker allows an
    empty choice; every other Sim picker is shown as always. EA has already
    registered the dialog when it distributes it, so the answer can wait for
    the pump."""
    if _claims(dialog):
        minimum = getattr(dialog, 'min_selectable', None)
        if (
            minimum == 0
            and enabled()
            and simplified()
            and pump.post(_answer_alone, dialog, msg_type, dialog_msg, kwargs)
        ):
            simsight_log.line(f'travel: companion picker {dialog.dialog_id} queued for nobody')
            return None
        simsight_log.line(f'travel: companion picker {dialog.dialog_id} shown (minimum {minimum})')
    return _native_sim_picker(dialog, msg_type, dialog_msg, kwargs)


def _answer_alone(dialog: Any, msg_type: Any, dialog_msg: Any, kwargs: dict[str, Any]) -> bool:
    """Pump: an empty pick, then OK, through EA's dialog service (its own
    validation and listener). If EA does not take it, the picker is shown
    after all, so no invisible dialog stays behind."""
    import simsight_native as native

    if not _dialog_active(dialog):
        simsight_log.line(f'travel: companion picker {dialog.dialog_id} already gone')
        return False
    if native.answer_picker(dialog, []):
        simsight_log.line(f'travel: companion picker {dialog.dialog_id} answered: nobody')
        return True
    simsight_log.line(f'travel: companion picker {dialog.dialog_id} did not take nobody; shown')
    if _dialog_active(dialog):
        _native_sim_picker(dialog, msg_type, dialog_msg, kwargs)
    return False

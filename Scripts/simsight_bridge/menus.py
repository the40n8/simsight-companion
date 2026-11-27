"""Playing from the phone: targets near the controlled Sim, their pie
menus as the game builds them, and the chosen row run."""

from __future__ import annotations


from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_names as names

from .state import _distance, _logged, _sim_and_client, _state, log
from .text import _pretty, classify, object_name, text_of


if TYPE_CHECKING:
    from .state import Answer, Json


# ---------------------------------------------------------------- playing from the phone
# Targets and pie menus as the game builds them for a click (EA's ChoiceMenu:
# same tests, visibility, names, greyed reasons, social sub-menus), and the
# chosen row run with test_and_execute, like the mod's phone list.

MAX_OBJECTS = 600  # nearest objects listed (a big house has a few hundred)
MAX_DISTANCE = 60.0  # metres
PHONE_ID = 'phone'  # the controlled Sim's phone, a target of its own


def _sim_targets(sim: Any) -> list[Json]:
    """The controlled Sim, its phone, then the other Sims, nearest first."""
    import services

    targets: list[Json] = [
        {
            'id': str(sim.id),
            'name': names.full_name(sim.sim_info).strip(),
            'isSim': True,
            'isSelf': True,
            'group': 'SIMS',
            'distance': 0,
        },
        {'id': PHONE_ID, 'name': 'Phone', 'group': 'PHONE'},
    ]
    others = []
    for other in services.sim_info_manager().instanced_sims_gen():
        if other is sim:
            continue
        with _logged('target Sim'):
            others.append((_distance(other.position, sim.position), other))
    for d, other in sorted(others, key=lambda pair: pair[0]):
        targets.append(
            {
                'id': str(other.id),
                'name': names.full_name(other.sim_info).strip(),
                'isSim': True,
                'group': 'SIMS',
                'distance': int(round(d)),
            }
        )
    return targets


def _target_distance(obj: Any, sim: Any) -> float | None:
    """How far an object the Sim can use is, or None when it does not count."""
    distance = None
    with _logged('target object'):
        if getattr(obj, 'is_sim', False) or getattr(obj, 'is_terrain', False) or obj.is_hidden():
            return None
        checker = getattr(obj, 'is_in_inventory', None)
        if (checker is None or not checker()) and list(obj.super_affordances()):
            d = _distance(obj.position, sim.position)
            if d <= MAX_DISTANCE:
                distance = d
    return distance


def _object_target(obj: Any, d: float) -> Json | None:
    try:
        group, kind, keywords = classify(obj)
        return {
            'id': str(obj.id),
            'name': object_name(obj),
            'group': group,
            'kind': kind,
            'keywords': keywords,
            'distance': int(round(d)),
        }
    except Exception:
        simsight_log.error('bridge: target row')
        return None


def list_targets() -> list[Json]:
    import services

    sim, _client = _sim_and_client()
    if sim is None:
        return []
    targets = _sim_targets(sim)
    objects = []
    for obj in services.object_manager().get_valid_objects_gen():
        d = _target_distance(obj, sim)
        if d is not None:
            objects.append((d, obj))
    objects.sort(key=lambda pair: pair[0])
    for d, obj in objects[:MAX_OBJECTS]:
        row = _object_target(obj, d)
        if row is not None:
            targets.append(row)
    return targets


def _target(target_id: str, sim: Any) -> Any:
    import services

    try:
        key = int(target_id)
    except ValueError:
        return None
    if key == sim.id:
        return sim
    return services.object_manager().get(key)


def _category_root(category: Any) -> Any:
    depth = 0
    while category is not None and getattr(category, '_parent', None) is not None and depth < 8:
        category = category._parent
        depth += 1
    return category


def _pick_place(obj: Any) -> tuple[int, int]:
    """(lot id, level) for a click on the object."""
    import services

    try:
        lot_id = int(services.active_lot().lot_id)
    except (AttributeError, TypeError, ValueError):
        lot_id = 0  # no active lot
    try:
        level = int(obj.level)
    except (AttributeError, TypeError, ValueError):
        level = 0  # objects without a level
    return lot_id, level


def _add_object_rows(menu: Any, sim: Any, client: Any, obj: Any) -> None:
    """What a click on the object offers (and on what it forwards clicks to)."""
    from server.pick_info import PickInfo, PickType

    parameters = client.get_interaction_parameters() or {}
    lot_id, level = _pick_place(obj)
    pick_type = PickType.PICK_SIM if getattr(obj, 'is_sim', False) else PickType.PICK_OBJECT
    targets = [obj] + [
        t
        for t in (getattr(obj, 'forwarded_pick_targets', None) or ())
        if t is not None and t is not obj
    ]
    for target in targets:
        pick = PickInfo(
            pick_type=pick_type,
            target=target,
            location=obj.position,
            routing_surface=obj.routing_surface,
            lot_id=lot_id,
            level=level,
        )
        context = client.create_interaction_context(sim, pick=pick)
        context.add_preferred_objects((obj,))
        menu.add_potential_aops(
            target, context, target.potential_interactions(context, **parameters), None
        )
        if getattr(target, 'is_sim', False) and target is not sim:
            try:
                sim.fill_choices_menu_with_si_state_aops(target, context, menu, None)
            except Exception as ex:
                log(f'social rows unavailable: {ex!r}')


def get_interactions(target_id: str) -> Answer:
    from interactions.choices import ChoiceMenu

    sim, client = _sim_and_client()
    if sim is None or client is None:
        return False, 'sim_unavailable'
    menu = ChoiceMenu(sim)
    if target_id == PHONE_ID:
        # The Sim's own phone: calls, invitations, travel, services...
        context = client.create_interaction_context(sim)
        menu.add_potential_aops(None, context, sim.potential_phone_interactions(context), None)
        return _menu_rows(target_id, sim, menu)
    obj = _target(target_id, sim)
    if obj is None:
        return False, 'target_unavailable'
    _add_object_rows(menu, sim, client, obj)
    return _menu_rows(target_id, sim, menu)


def _menu_row(sim: Any, option_id: Any, item: Any, categories: Any) -> Json | None:
    """One pie menu row as the game shows it, or None when the game hides it."""
    aop = item.aop
    affordance = aop.affordance
    visible, _suppression = sim.test_pie_menu_modifiers(affordance)
    if not visible:
        return None
    modifier_tooltip = sim.apply_forbid_by_object_tags_pie_menu_modifiers(aop)
    fallback = _pretty(getattr(affordance, '__name__', ''), ('sim-', 'si_', 'mixer_'))
    name = text_of(affordance.get_name(aop.target, item.context, **aop.interaction_parameters), '')
    tooltip = modifier_tooltip or item.result.tooltip
    reason = ''
    if tooltip is not None:
        reason = text_of(
            affordance.create_localized_string(
                tooltip,
                context=item.context,
                target=aop.target,
                **aop.interaction_parameters,
            )
        )
    category = categories.get(item.category_key) if item.category_key else None
    root = _category_root(category)
    return {
        'id': str(option_id),
        'name': name or fallback,
        'category': text_of(root._display_name(sim)) if root is not None else '',
        'enabled': bool(item.result) and not modifier_tooltip,
        'reason': reason,
        # The game's own name is not known yet (the row shows the fallback).
        'pending': not name,
    }


def _menu_rows(target_id: str, sim: Any, menu: Any) -> Answer:
    """Rows of a ChoiceMenu as the game shows them (visible, named, greyed reasons)."""
    import services
    import sims4.resources

    categories = services.get_instance_manager(sims4.resources.Types.PIE_MENU_CATEGORY)
    tutorial = services.get_tutorial_service()
    rows, options = [], {}
    pending = False
    misses = _state.loc_misses
    for option_id, item in menu:
        row = None
        with _logged('menu row'):
            if tutorial is None or tutorial.is_affordance_visible(item.aop.affordance):
                row = _menu_row(sim, option_id, item, categories)
        if row is None:
            continue
        pending = pending or row.pop('pending')
        rows.append(row)
        options[str(option_id)] = (item.aop, item.context)
    _state.menus.clear()  # only the latest menu can be run
    _state.menus[str(target_id)] = options
    return True, '', {'interactions': rows, 'namesPending': pending or _state.loc_misses != misses}


def run_interaction(target_id: str, option_id: str) -> Answer:
    entry = _state.menus.get(str(target_id), {}).get(str(option_id))
    if entry is None:
        return False, 'menu_expired'
    aop, context = entry
    try:
        result = aop.test_and_execute(context)
    except Exception:
        simsight_log.error('bridge: run interaction')
        return False, 'error'
    if not result:
        log(f'interaction refused: {result}')
        return False, 'refused'
    return True, ''

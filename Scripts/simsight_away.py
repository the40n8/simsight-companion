"""Household portrait menus through EA's native PICK_SKEWER request.

Only opens the game's menu. Never switches Sims, travels, or applies an
away action directly. Called on the simulation-thread pump by the hub.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log
import simsight_names as names
import simsight_speech as speech

if TYPE_CHECKING:
    from simsight_hub import Row


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


def _state() -> tuple[Any, Any, Any, int] | None:
    """(active Sim, household, lot, zone id) outside Build mode, when the
    active Sim belongs to the active household."""
    import services
    import simsight_place as place

    if place.in_build_mode():
        return None
    actor = services.get_active_sim()
    household = services.active_household()
    lot = services.active_lot()
    if actor is None or household is None or lot is None:
        return None
    if actor.sim_info.household is not household:
        return None
    return actor, household, lot, services.current_zone_id()


def _candidates(household: Any, actor_id: int) -> dict[int, Any]:
    result = {}
    for info in household.sim_info_gen():
        if info.sim_id == actor_id or info.is_baby:
            continue
        # Default get_sim_instance excludes hidden instances, as in EA's
        # own portrait-menu path. Work/school tones remain native choices.
        if info.get_sim_instance() is not None:
            continue
        result[int(info.sim_id)] = info
    return result


def _portrait_request(sim_id: int, actor_id: int, zone_id: int, household_id: int) -> bool:
    """Sends the portrait pick for sim_id; ValueError when the context changed."""
    import simsight_scanner as scanner
    from server.pick_info import PickType

    state = _state()
    if state is None:
        raise ValueError('no live household context')
    actor, household, lot, current_zone = state
    if actor.sim_info.sim_id != actor_id or current_zone != zone_id or household.id != household_id:
        raise ValueError('household, actor or zone changed')
    info = _candidates(household, actor_id).get(sim_id)
    if info is None:
        raise ValueError('target no longer away in this household')
    pos = actor.position
    point = tuple(float(v) for v in (pos.x, pos.y, pos.z))
    if not all(math.isfinite(v) for v in point):
        raise ValueError('invalid actor position')
    lot_id = int(lot.lot_id)
    if lot_id < 0:
        raise ValueError('invalid lot')
    if lot_id > 0xFFFFFFFF:
        lot_id = 0  # Native current-lot sentinel; never truncate an ID.
    name = names.full_name(info).replace('|', ' ')
    sent = scanner.send_ui(
        'A11y|pick|{}|{}|{}|{}|{:.3f}|{:.3f}|{:.3f}|{}'.format(
            sim_id, int(PickType.PICK_SKEWER), lot_id, name, *point, int(actor.level)
        )
    )
    simsight_log.line(
        f'away actions: portrait request sim={sim_id} actor={actor_id} zone={current_zone} sent={bool(sent)}'
    )
    return bool(sent)


def open_sim(sim_id: int, actor_id: int, zone_id: int, household_id: int) -> bool:
    try:
        sent = _portrait_request(sim_id, actor_id, zone_id, household_id)
    except ValueError as exc:
        simsight_log.line(f'away actions: request refused: {type(exc).__name__}: {exc}')
        sent = False
    except Exception:
        simsight_log.error('away: portrait request')
        sent = False
    if not sent:
        _say('action unavailable')
    return sent


def _rows(state: tuple[Any, Any, Any, int]) -> list[Row]:
    """One row per away household Sim, by name."""
    import simsight_menu as menu

    actor, household, _lot, zone_id = state
    actor_id, household_id = actor.sim_info.sim_id, household.id
    candidates = _candidates(household, actor_id)
    rows: list[Row] = []
    for ident, info in sorted(
        candidates.items(),
        key=lambda pair: (pair[1].first_name.casefold(), pair[1].last_name.casefold(), pair[0]),
    ):
        rows.append(
            (
                menu.raw_text(names.full_name(info) or str(ident)),
                None,
                lambda ident=ident: open_sim(ident, actor_id, zone_id, household_id),
            )
        )
    simsight_log.line(
        f'away actions: household={household_id} zone={zone_id} candidates={len(rows)}'
    )
    return rows


def open_away_actions() -> bool:
    try:
        import simsight_hub as hub

        state = _state()
        if state is None:
            _say('action unavailable')
            return False
        rows = _rows(state)
        if not rows:
            _say('no away household sims')
            return False
        return hub.show_window('away actions', 'away actions instructions', rows)
    except Exception:
        simsight_log.error('away: picker')
        _say('action unavailable')
        return False

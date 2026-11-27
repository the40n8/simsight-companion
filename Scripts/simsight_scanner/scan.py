"""Building the lists around the active Sim, and the active-Sim callback
that invalidates them."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import simsight_availability as availability
import simsight_lang as lang
import simsight_log
import simsight_pump as pump
import simsight_settings as settings
import simsight_speech as speech

from .state import (
    BUILD,
    Entry,
    MISC,
    OBJECTS,
    SIMS,
    YOURSELF,
    _MAX_CATCH_ALL_NOTES,
    _REFRESH_SECONDS,
    _UNRANKED,
    _current,
    _group_key,
    _state,
    _subs_in,
    enabled,
)
from .loc import speak_parts
from .categories import _SUB_RANK, classification
from .entries import _level_of, _patient_roles, entry_for
from .tree import _tree_parts, _tree_remember, _tree_restore
from .ranges import RANGE_NAMES, _apply_range, _has_active_sim, _range, _range_limit

if TYPE_CHECKING:
    from .state import Part
    from sims.sim import Sim


def cycle_range(delta: int) -> bool:
    """Ctrl+Alt+comma / period. Simulation thread."""
    names = list(RANGE_NAMES)
    index = names.index(_range()[0])
    name = names[(index + (1 if delta > 0 else -1)) % len(names)]
    settings.set('scanner_range', name)
    simsight_log.line(f'scanner: range {name}')
    if not _has_active_sim():
        speech.speak(lang.t('no lot loaded'), explicit=True)
        return False
    found = rescan(preserve=True)
    parts: list[Part] = [f'Scanner range: {name}. ']
    parts.extend(_tree_parts() if found else ['Nothing in this range. '])
    speak_parts(parts)
    return True


def apply_range_setting() -> None:
    """The settings menu changed the range: refresh a live scanner list."""
    try:
        if enabled() and _state.groups:
            rescan(preserve=True)
    except Exception:
        simsight_log.error('scanner: range refresh')


def _add_sims(services: Any, sim: Any, origin: Any) -> None:
    """List the active Sim (Yourself) and every other instanced Sim."""
    sims = [sim]
    try:
        sims.extend(
            other for other in services.sim_info_manager().instanced_sims_gen() if other is not sim
        )
    except Exception:
        simsight_log.error('scanner: sim listing')
    roles = _patient_roles()
    for other in sims:
        _add_sim(other, YOURSELF if other is sim else SIMS, origin, roles)


def _add_sim(sim: Any, group: str, origin: Any, roles: dict[int, str]) -> None:
    try:
        entry = entry_for(sim, origin, _state.origin_level, True, group, group)
        entry.role = roles.get(sim.id, '')
        _state.by_group.setdefault(group, []).append(entry)
    except Exception:
        simsight_log.error('scanner: sim entry')


def _is_listable(obj: Any) -> bool:
    """Not in an inventory and not hidden."""
    checker = getattr(obj, 'is_in_inventory', None)
    if checker is not None and checker():
        return False
    return not obj.is_hidden()


def _squared_distance(obj: Any, origin: Any) -> float:
    pos = obj.position
    dx = float(pos.x) - float(origin.x)
    dz = float(pos.z) - float(origin.z)
    return dx * dx + dz * dz


def _nearby_objects(services: Any, origin: Any, limit: float) -> tuple[list[Any], int, int]:
    """(objects to list, nearest first; objects examined; objects in range)."""
    try:
        objects = list(services.object_manager().get_valid_objects_gen())
    except Exception:
        simsight_log.error('scanner: object listing')
        objects = []
    candidates = []
    in_range = 0
    for obj in objects:
        try:
            if getattr(obj, 'is_sim', False) or getattr(obj, 'is_terrain', False):
                continue
            # Reject distant objects before walking their affordances or
            # resolving catalog metadata. Squared distance needs no sqrt.
            if _squared_distance(obj, origin) > limit * limit:
                continue
            in_range += 1
            if _is_listable(obj):
                candidates.append(obj)
        except Exception:
            simsight_log.error('scanner: object filter')
    candidates.sort(key=lambda obj: _squared_distance(obj, origin))
    return candidates, len(objects), in_range


def _catch_all_note(obj: Any, catalog_group: str, names: set[str], flags: int, sub: str) -> str:
    # Diagnostics for the two catch-alls: what did the client report, and
    # which labelled tags did the definition carry?
    labelled = sorted(name for name in names if name.startswith(('BuyCat', 'Build_', 'Func_')))
    return '{} {} flags=0x{:X} sub={} tags={}'.format(
        catalog_group, getattr(obj.definition, 'name', '?'), flags, sub, ','.join(labelled[:6])
    )


def _add_objects(
    candidates: list[Any], origin: Any, limit: float, preserve: bool
) -> tuple[int, list[str]]:
    """List the objects availability does not rule out; (count, log notes)."""
    count = 0
    notes: list[str] = []
    for obj in candidates:
        try:
            verdict = availability.state(obj)
            if verdict == 'empty':
                continue
            entry = entry_for(obj, origin, _state.origin_level, False, MISC, MISC)
            entry.availability = verdict
            if entry.distance > limit:
                continue
            entry.category, catalog_group, names, flags = classification(obj, _state.last_scan)
            entry.group = OBJECTS
            _state.by_group.setdefault(OBJECTS, []).append(entry)
            count += 1
            if (
                not preserve
                and catalog_group in (MISC, BUILD)
                and len(notes) < _MAX_CATCH_ALL_NOTES
            ):
                notes.append(_catch_all_note(obj, catalog_group, names, flags, entry.category))
        except Exception:
            simsight_log.error('scanner: object entry')
    return count, notes


def _log_scan(count: int, notes: list[str], preserve: bool) -> None:
    by_group = _state.by_group
    summary = ' '.join(
        f'{key}:{len(by_group[key])}/{len(_subs_in(by_group[key]))}' for key in _state.groups
    )
    if summary != _state.last_summary or not preserve:
        simsight_log.line(
            f'scanner: {len(by_group.get(SIMS, ()))} sims, {count} objects, {len(_state.groups)} categories (items/subcategories): {summary}'
        )
        _state.last_summary = summary
    if not preserve:
        for note in notes:
            simsight_log.line('scanner:   ' + note)


def _restore_cursor(group: str | None, index: int, previous: Entry | None) -> None:
    """Back to the thing the cursor was on before the rescan, if still listed."""
    if group not in _state.groups:
        return
    _state.group_index = _state.groups.index(group)
    entries = _state.by_group[group]
    _state.item_index = min(index, len(entries) - 1)
    if previous is not None:
        for i, entry in enumerate(entries):
            if entry.obj_id == previous.obj_id:
                _state.item_index = i
                break


def _record_stats(started: float, examined: int, in_range: int) -> None:
    elapsed = (time.perf_counter() - started) * 1000.0
    stats = _state.scan_stats
    stats['scans'] += 1
    stats['total_ms'] += elapsed
    stats['last_ms'] = elapsed
    stats['max_ms'] = max(stats['max_ms'], elapsed)
    stats['examined'] = examined
    stats['in_range'] = in_range


def rescan(preserve: bool = False) -> bool:
    """Rebuild the lists around the active Sim. Simulation thread only."""
    import services

    started = time.perf_counter()
    previous = _current() if preserve else None
    previous_group = _group_key() if preserve else None
    previous_index = _state.item_index
    if preserve:
        _tree_remember()
    _state.by_group = {}
    _state.groups = []
    _state.group_index = 0
    _state.item_index = 0
    sim = services.get_active_sim()
    if sim is None:
        availability.reset()
        return False
    _bind_availability_client()
    origin = sim.position
    _state.last_scan = time.monotonic()
    _state.last_origin = (sim.id, float(origin.x), float(origin.z))
    _state.origin_level = _level_of(sim)
    _add_sims(services, sim, origin)
    limit = _range_limit()
    candidates, examined, in_range = _nearby_objects(services, origin, limit)
    # Eligibility is evaluated incrementally on the simulation pump, never
    # synchronously on a key press. Unknown results remain discoverable.
    availability.begin(sim, candidates)
    _state.availability_revision = availability.revision()
    count, notes = _add_objects(candidates, origin, limit, preserve)
    for entries in _state.by_group.values():
        entries.sort(key=lambda e: (_SUB_RANK.get(e.category, _UNRANKED), e.distance))
    _apply_range()
    _state.groups = [key for key in (YOURSELF, SIMS, OBJECTS) if _state.by_group.get(key)]
    _log_scan(count, notes, preserve)
    _restore_cursor(previous_group, previous_index, previous)
    if preserve:
        _tree_restore()
    _record_stats(started, examined, in_range)
    return bool(_state.groups)


def _ensure_scan() -> bool:
    same_origin = True  # when the check itself fails, trust the current lists
    try:
        import services

        sim = services.get_active_sim()
        last_origin = _state.last_origin
        same_origin = (
            sim is not None
            and last_origin is not None
            and (sim.id == last_origin[0] and int(sim.level) == _state.origin_level)
        )
    except Exception:
        simsight_log.error('scanner: active Sim compare')
    if (
        _state.groups
        and same_origin
        and availability.revision() == _state.availability_revision
        and time.monotonic() - _state.last_scan < _REFRESH_SECONDS
    ):
        return True
    return rescan(preserve=True)


def _active_sim_changed(_old_sim: Sim | None, _new_sim: Sim | None) -> None:
    # The game calls this when the player switches Sims: (old_sim, new_sim).
    try:
        availability.reset()
        _state.last_scan = 0.0
        if enabled() and _state.refresh_requested:
            pump.post(rescan, True)
    except Exception:
        simsight_log.error('scanner: active Sim changed')


def _bind_availability_client() -> None:
    try:
        import services

        client = services.client_manager().get_first_client()
        if client is _state.availability_client:
            return
        _unbind_availability_client()
        if client is not None:
            client.register_active_sim_changed(_active_sim_changed)
            _state.availability_client = client
    except Exception:
        # _ensure_scan also compares the active Sim identity on every action.
        simsight_log.error('scanner: bind client')


def _unbind_availability_client() -> None:
    client = _state.availability_client
    if client is not None:
        try:
            client.unregister_active_sim_changed(_active_sim_changed)
        except Exception:  # the client may already be gone with the previous zone
            simsight_log.error('scanner: unbind client')
    _state.availability_client = None

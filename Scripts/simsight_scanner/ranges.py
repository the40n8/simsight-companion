"""Scanner range: how far the scanner looks (Ctrl+Alt+comma and period)."""

from __future__ import annotations

from typing import Any

import simsight_log
import simsight_settings as settings

from .state import Entry, _state


# Contributed by DaRainbowGirlXX (2026-10-16): Ctrl+Alt+comma and
# Ctrl+Alt+period step through these ranges; the choice is saved. Metre
# ranges still list every Sim, as the scanner always has. The lot, floor and
# room ranges keep only things on the active lot (and on the active Sim's
# floor, or in its room).
RANGES = (
    ('6 meters', 6.0, 'distance'),
    ('12 meters', 12.0, 'distance'),
    ('24 meters', 24.0, 'distance'),
    ('40 meters', 40.0, 'distance'),
    ('whole lot', None, 'lot'),
    ('this floor', None, 'floor'),
    ('this room', None, 'room'),
)
RANGE_NAMES = tuple(name for name, _limit, _kind in RANGES)
_DEFAULT_RANGE = RANGES[3]
_NO_LIMIT = 1000000.0  # metres: the lot, floor and room ranges filter later


def _range() -> tuple[str, float | None, str]:
    wanted = str(settings.get('scanner_range', '40 meters') or '').strip().lower()
    return next((mode for mode in RANGES if mode[0] == wanted), _DEFAULT_RANGE)


def _range_limit() -> float:
    limit = _range()[1]
    return limit if limit is not None else _NO_LIMIT


def _object_for_entry(entry: Entry) -> Any:
    try:
        import services

        obj = services.object_manager().get(entry.obj_id)
        if obj is not None:
            return obj
        info = services.sim_info_manager().get(entry.obj_id)
        if info is not None:
            return info.get_sim_instance()
    except Exception:
        simsight_log.error('scanner: object lookup')
    return None


def _on_active_lot(obj: Any) -> bool:
    try:
        import services

        return bool(services.active_lot().is_position_on_lot(obj.position))
    except Exception:
        simsight_log.error('scanner: active lot check')
        return False


def _room_id(obj: Any) -> Any:
    try:
        import build_buy
        import services

        return build_buy.get_room_id(
            services.current_zone_id(), obj.position, int(getattr(obj, 'level', 0) or 0)
        )
    except Exception:
        simsight_log.error('scanner: room id')
        return None


def _in_range(entry: Entry, active_room: Any) -> bool:
    _name, limit, kind = _range()
    if limit is not None:
        return bool(getattr(entry, 'is_sim', False)) or entry.distance <= limit
    obj = _object_for_entry(entry)
    if obj is None:
        return entry.distance == 0
    if not _on_active_lot(obj):
        return False
    if kind == 'lot':
        return True
    if kind == 'floor':
        return entry.level_delta == 0
    if entry.level_delta != 0 or active_room is None:
        return False
    return _room_id(obj) == active_room


def _active_room() -> Any:
    try:
        import services

        return _room_id(services.get_active_sim())
    except Exception:
        simsight_log.error('scanner: active room')
        return None


def _apply_range() -> None:
    """Filter the fresh lists to the chosen range, inside rescan, so the
    scanner's own focus restore runs on the filtered lists."""
    if _range()[1] is not None:
        return  # metre ranges were applied while collecting
    active_room = _active_room() if _range()[2] == 'room' else None
    by_group = _state.by_group
    for key in list(by_group):
        kept = [entry for entry in by_group[key] if _in_range(entry, active_room)]
        if kept:
            by_group[key] = kept
        else:
            del by_group[key]


def _has_active_sim() -> bool:
    try:
        import services

        return services.get_active_sim() is not None
    except Exception:
        simsight_log.error('scanner: active Sim check')
        return False

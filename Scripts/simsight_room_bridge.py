"""Bridge SimSight Plus room names into the standard Build grid."""

from __future__ import annotations

import time
from typing import Any

import simsight_log

_REFRESH_SECONDS = 2.0


class _Cache:
    """Room names by (room id, level) for one lot, scanned at most every 2 seconds."""

    def __init__(self) -> None:
        self.names: dict[tuple[int, int], str] = {}
        self.at = 0.0
        self.zone: object = None


_cache = _Cache()


def _room_map() -> dict[tuple[int, int], str]:
    import services

    zone = services.current_zone_id()
    if zone != _cache.zone:
        _cache.names, _cache.at, _cache.zone = {}, 0.0, zone
    now = time.monotonic()
    if now - _cache.at > _REFRESH_SECONDS:
        try:
            import simsight_plus_rooms as plus_rooms

            _cache.names = {
                (int(room.rid), int(room.level)): room.name for room in plus_rooms.scan_rooms()
            }
            _cache.at = now
        except Exception:  # keep the last names; the next call tries again
            simsight_log.error('room bridge: lookup failed')
    return _cache.names


def inferred_name(room_id: Any, level: Any) -> str | None:
    """Passive fallback to Plus room names; custom native labels take priority."""
    try:
        return _room_map().get((int(room_id), int(level)))
    except (TypeError, ValueError):
        return None

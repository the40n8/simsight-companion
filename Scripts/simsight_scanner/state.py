"""What the scanner lists and remembers: the entries, the state object,
the loc cache and the cursor."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_settings as settings


if TYPE_CHECKING:
    # What speak_parts says: plain text, or ('loc', string hash) for the
    # client to resolve in the player's language.
    Part = str | tuple[str, int]
    # (subcategory, catalog group, tag names, buy-category flags)
    Classification = tuple[str, str, set[str], int]


_LOC_TIMEOUT = 0.6  # seconds to wait for the client before speaking without a name

SIMS = 'SIMS'
YOURSELF = 'YOURSELF'
OBJECTS = 'OBJECTS'
GROUP_TERMS = {YOURSELF: 'yourself', OBJECTS: 'nearby objects'}
MISC = 'MISC'
BUILD = 'BUILD'
COMPASS = (
    'north',
    'north east',
    'east',
    'south east',
    'south',
    'south west',
    'west',
    'north west',
)

# Chords that drive the open pie menu instead of the scanner list.
MENU_KEYS = {
    'prev': 'A11yNavPrev',
    'next': 'A11yNavNext',
    'prevcat': 'A11yNavPrev',
    'nextcat': 'A11yNavNext',
    'prevsub': 'A11yNavPrev',
    'nextsub': 'A11yNavNext',
    'open': 'A11yActivate',
    'back': 'A11yHidePieMenu',
}


_REFRESH_SECONDS = 2.0
_IDLE_SECONDS = 30.0
_CLASS_CACHE_LIMIT = 1024
_CLASS_CACHE_SECONDS = 30.0
_MAX_CATCH_ALL_NOTES = 12  # log lines about Misc/Build objects per full scan
_UNRANKED = 1 << 20  # subcategories without a rank sort last


class Entry:
    """One Sim or object in the scanner lists."""

    __slots__ = (
        'availability',
        'bearing',
        'category',
        'distance',
        'first_name',
        'group',
        'is_sim',
        'level_delta',
        'name',
        'name_hash',
        'obj_id',
        'role',
    )

    def __init__(  # noqa: PLR0913, PLR0917 - one value per slot; callers pass them by position
        self,
        obj_id: int,
        is_sim: bool,
        name: str | None,
        name_hash: int,
        distance: float,
        bearing: int,
        level_delta: int,
        category: str,
        group: str,
        first_name: str = '',
    ) -> None:
        self.obj_id = obj_id
        self.is_sim = is_sim
        self.name = name  # plain text when the game has it (Sims)
        self.name_hash = name_hash  # STBL key when only the client can say it
        self.distance = distance
        self.bearing = bearing
        self.level_delta = level_delta
        self.category = category  # subcategory: a labelled tag name, SIMS or MISC
        self.group = group  # main scanner group
        self.first_name = first_name
        self.availability = 'pending'
        self.role = ''  # Sims: a spoken role such as 'patient, waiting'


class _Pending:
    """An announcement waiting for the client to resolve its loc keys."""

    def __init__(self, parts: list[Part], seq: int) -> None:
        self.parts = parts
        self.seq = seq
        self.missing: set[int] = set()
        self.timer: threading.Timer | None = None
        self.done = False


def _empty_stats() -> dict[str, float]:
    return {
        'scans': 0,
        'total_ms': 0.0,
        'last_ms': 0.0,
        'max_ms': 0.0,
        'examined': 0,
        'in_range': 0,
        'cache_hits': 0,
        'cache_misses': 0,
    }


class _State:
    """Everything the scanner remembers between keys."""

    def __init__(self) -> None:
        # main category key -> [Entry], subcategory order then nearest first
        self.by_group: dict[str, list[Entry]] = {}
        self.groups: list[str] = []  # main category keys present, in the catalog's order
        self.group_index = 0
        self.item_index = 0
        self.tree_level = 'groups'  # groups -> subcategories -> items
        self.tree_groups: dict[str, str] = {}  # group -> last selected subcategory
        # (group, subcategory) -> last selected object id
        self.tree_items: dict[tuple[str, str], int] = {}
        self.menu_open = False  # a pie menu is open (ours or a mouse click)
        self.origin_level = 0
        self.last_scan = 0.0
        self.last_origin: tuple[int, float, float] | None = None  # (sim id, x, z)
        self.last_summary: str | None = None
        self.refresh_handle: Any = None
        self.refresh_owner: Any = None
        self.refresh_requested = False
        self.last_activity = 0.0
        # id(definition) -> (definition, expiry, classification)
        self.class_cache: dict[int, tuple[Any, float, Classification]] = {}
        self.availability_revision = 0
        self.availability_client: Any = None
        self.scan_stats = _empty_stats()
        self.route_logged: dict[str, float] = {}  # reason -> when last logged
        # loc channel (guarded by _loc_lock)
        self.announce_seq = 0
        self.loc_waiting: dict[int, list[_Pending]] = {}  # hash -> announcements


_state = _State()

# hash -> text. Read by simsight_bridge (and its tests) as scanner.loc_cache.
loc_cache: dict[int, str] = {}
_loc_lock = threading.Lock()


def menu_open() -> bool:
    """A scanner menu is open (read by simsight_availability)."""
    return bool(_state.menu_open)


def _touch() -> None:
    _state.refresh_requested = True
    _state.last_activity = time.monotonic()


def enabled() -> bool:
    return bool(settings.get('scanner', True))


def _subs_in(entries: list[Entry]) -> list[str]:
    """Subcategory keys in the order the entries are listed."""
    seen: list[str] = []
    for entry in entries:
        if entry.category not in seen:
            seen.append(entry.category)
    return seen


def _group_key() -> str | None:
    if not _state.groups:
        return None
    return _state.groups[_state.group_index % len(_state.groups)]


def _entries() -> list[Entry]:
    key = _group_key()
    if key is None:
        return []
    return _state.by_group.get(key, [])


def _current() -> Entry | None:
    entries = _entries()
    if not entries:
        return None
    return entries[_state.item_index % len(entries)]

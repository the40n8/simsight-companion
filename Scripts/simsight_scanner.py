"""World scanner: keyboard access to the Sims and objects on the lot.

The UI walker can reach anything the client draws, but the world itself is
not a widget. This module lists what is around the active Sim from the
simulation's own data, lets the player step through it by category, and
opens the game's REAL pie menu on the chosen thing so the existing menu
narration and focus walker take over from there. The original scanner design
comes from glaroc's a11yscan (see A11YSCAN-REVIEW.md); the tree navigation
and integration are ours.

How it stays in the game's own words:

- Object names are catalog-name localisation keys (obj.catalog_name). Python
  cannot turn a key into text, so the hash goes to the Flash layer in a
  message name ("A11y|loc|<hash>|<hash>"), the client resolves it in the
  player's language, and the text comes back on simsight.loc_enc.
- Yourself and other Sims are separate groups. Nearby Objects are grouped
  by the object's build/buy tag or function, using the game's own labels.
  The subcategory is the
  object's build/buy tag label (Beds, Sofas, Doors, or a function label for
  spawned and crafted things), from simsight_buycat (generated from the
  tuning dump and the client's KeysBB), resolved the same way. Sims use the
  game's "Sims" string.
- Interactions are never listed here. The client asks for the real pie menu
  itself (see open_pie_menu), and from then on it is an ordinary pie menu.
- Only distance and bearing are words the game does not have; those come
  from simsight_lang like the rest of the mod's vocabulary.

Threading: keys arrive on the poller thread and go through
simsight_pump so every game call below runs on the simulation thread.
The loc replies arrive as Live commands, also on the simulation thread.

Tree keys: Up/Down previous/next sibling, Right enter a container,
Left/Backspace parent, Enter enter a container or open an object's menu.
The keyboard router provides scanner mode and equivalent Ctrl+Alt chords.
Native menus own navigation while open.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Sequence, TypeVar

import sims4.commands

import simsight_availability as availability
import simsight_buycat as buycat
import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_pump as pump
import simsight_settings as settings
import simsight_speech as speech

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

_F = TypeVar('_F')


_REFRESH_SECONDS = 2.0
_IDLE_SECONDS = 30.0
_CLASS_CACHE_LIMIT = 1024
_CLASS_CACHE_SECONDS = 30.0
_MAX_CATCH_ALL_NOTES = 12  # log lines about Misc/Build objects per full scan
_UNRANKED = 1 << 20  # subcategories without a rank sort last


class Entry:
    """One Sim or object in the scanner lists."""

    __slots__ = (
        'obj_id',
        'is_sim',
        'name',
        'name_hash',
        'distance',
        'bearing',
        'level_delta',
        'category',
        'group',
        'first_name',
        'availability',
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


def _classification(obj: Any, now: float) -> Classification:
    # Only definition metadata is cached. Position, inventory/hidden state,
    # affordances and player names are read afresh on every scan.
    definition = obj.definition
    key = id(definition)
    cache = _state.class_cache
    stats = _state.scan_stats
    cached = cache.get(key)
    if cached is not None and cached[0] is definition and now < cached[1]:
        stats['cache_hits'] += 1
        return cached[2]
    names = tag_names(obj)
    flags = _buy_flags(obj)
    category = _sub_of(names)
    result = (category, _group_of(flags, category), names, flags)
    if len(cache) >= _CLASS_CACHE_LIMIT:
        cache.clear()
    cache[key] = (definition, now + _CLASS_CACHE_SECONDS, result)
    stats['cache_misses'] += 1
    return result


def enabled() -> bool:
    return bool(settings.get('scanner', True))


# --------------------------------------------------------------- loc channel


def send_ui(name: str) -> bool:
    """Python -> Flash message. Must be called on the simulation thread."""
    try:
        import distributor.ops
        import distributor.system

        op = distributor.ops.SendUIMessage(name)
        distributor.system.Distributor.instance().add_op_with_no_owner(op)
        return True
    except Exception:
        simsight_log.error('scanner: send to the UI')
        return False


def _clean_text(pieces: list[str]) -> str:
    """Join spoken pieces; drop the gaps and leading punctuation that empty
    pieces (a name the client never resolved) leave behind."""
    text = ''.join(pieces).replace(' ,', ',').replace('  ', ' ').strip()
    while text.startswith((',', '.')):
        text = text[1:].strip()
    return text


def _finish(pending: _Pending) -> None:
    with _loc_lock:
        if pending.done:
            return
        pending.done = True
        if pending.timer is not None:
            pending.timer.cancel()
        if pending.seq != _state.announce_seq:
            return  # superseded by a newer announcement
        pieces = [
            loc_cache.get(part[1], '') if isinstance(part, tuple) else part
            for part in pending.parts
        ]
    text = _clean_text(pieces)
    if text:
        speech.speak(text, source='widget')


def _finish_late(pending: _Pending) -> None:
    # Timer thread: the client did not answer in time; speak what we have.
    try:
        _finish(pending)
    except Exception:
        simsight_log.error('scanner: speak after loc timeout')


def speak_parts(parts: Sequence[Part]) -> None:
    """Speak a list of strings and ('loc', hash) parts once the client has
    resolved every key (or the timeout passes). Simulation thread only."""
    need_send = []
    with _loc_lock:
        _state.announce_seq += 1
        pending = _Pending(list(parts), _state.announce_seq)
        for part in pending.parts:
            if isinstance(part, tuple) and part[1] and part[1] not in loc_cache:
                pending.missing.add(part[1])
        if pending.missing:
            for key in pending.missing:
                waiting = _state.loc_waiting.setdefault(key, [])
                if not waiting:
                    need_send.append(key)
                waiting.append(pending)
            pending.timer = threading.Timer(_LOC_TIMEOUT, _finish_late, (pending,))
            pending.timer.daemon = True
            pending.timer.start()
    for key in need_send:
        send_ui(f'A11y|loc|{key}|{key}')
    if not pending.missing:
        _finish(pending)


def _loc_reply(text: str) -> None:
    try:
        from simsight_narrator import decode_percent

        head, _sep, body = text.partition('|')
        key = int(head)
        resolved = decode_percent(body).strip()
    except (ImportError, TypeError, ValueError) as ex:
        simsight_log.line(f'scanner: bad loc reply {text[:60]!r}: {ex!r}')
        return
    ready = []
    with _loc_lock:
        loc_cache[key] = resolved
        for pending in _state.loc_waiting.pop(key, ()):
            pending.missing.discard(key)
            if not pending.missing:
                ready.append(pending)
    for pending in ready:
        _finish(pending)


@sims4.commands.Command(
    'simsight.loc_enc', 'a11y.loc_enc', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_loc_enc(text: str = '', _connection: Any = None) -> bool:
    """Flash reply: "<hash>|<percent-encoded localized text>"."""
    try:
        _loc_reply(text)
    except Exception:
        simsight_log.error('scanner: loc reply')
    return True


# --------------------------------------------------------------- categories


def _tags_with(prefixes: tuple[str, ...]) -> list[str]:
    return [tag for tag in buycat.ORDER if tag.startswith(prefixes)]


# Subcategory choice when a definition carries several labelled tags: the
# buy-mode function tags first (the catalog's own subcategories), then the
# build-mode tags, then the venue / off-the-grid / lot-requirement tags, then
# the room tags, then function tags (only spawned and crafted things get this
# far). Each tier keeps EA's file order.
_SUB_PRIORITY = (
    _tags_with(('BuyCatEE_', 'BuyCatSS_', 'BuyCatPA_', 'BuyCatLD_')),
    _tags_with(('Build_', 'TerrainManip_', 'TerrainPaint_')),
    _tags_with(('BuyCat_',)),
    _tags_with(('BuyCatMAG_',)),
    _tags_with(('Func_',)),
)


def _build_rank() -> dict[str, int]:
    rank: dict[str, int] = {}
    for tier in _SUB_PRIORITY:
        for tag in tier:
            rank.setdefault(tag, len(rank))
    return rank


_SUB_RANK = _build_rank()

# When the client reports several buy categories for one definition, prefer
# the one the subcategory's tag family belongs to (a bar is Activities and
# Skills, not Surfaces), then the catalog's display order.
_TAG_FAMILY = {
    'BuyCatEE_': ('ELECTRONICS', 'KIDS', 'ENTERTAINMENT', 'PETS'),
    'BuyCatSS_': ('COMFORT', 'SURFACE', 'STORAGE', 'PETS'),
    'BuyCatPA_': ('APPLIANCES', 'PLUMBING', 'PETS'),
    'BuyCatLD_': ('DECOR', 'LIGHTING'),
}
_BUILD_PREFIXES = ('Build_', 'TerrainManip_', 'TerrainPaint_', 'BuyCat_WaterObjects_BuildMode')


def _tag_name(tag: Any) -> str | None:
    name = getattr(tag, 'name', None)
    if name is not None:
        return str(name)
    try:
        from tag import Tag

        return str(Tag(tag).name)
    except (ImportError, KeyError, ValueError, AttributeError):
        return None  # a tag value this game version does not know


def tag_names(obj: Any) -> set[str]:
    try:
        tags = obj.definition.get_tags()
    except Exception:
        simsight_log.error('scanner: definition tags')
        return set()
    names = (_tag_name(tag) for tag in tags or ())
    return {name for name in names if name is not None}


def _sub_of(names: set[str]) -> str:
    for tier in _SUB_PRIORITY:
        for tag in tier:
            if tag in names:
                return tag
    return MISC


def _buy_flags(obj: Any) -> int:
    """The client's buy-category bits for the definition (0 when it has none)."""
    try:
        import build_buy

        flags = int(build_buy.get_object_buy_category_flags(obj.definition.id))
    except Exception:
        simsight_log.error('scanner: buy category flags')
        return 0
    return flags & 0x7FFFFFFF  # drop DEFAULT


# The native buy catalog can report zero in Live Mode. Definition tags remain
# available there; classify only known function-tag families, from the part
# of the tag after its family prefix.


def _live_group_ee(suffix: str) -> str:
    if suffix.startswith('Pet'):
        return 'PETS'
    if suffix.startswith('Kid') or suffix == 'MiscKids':
        return 'KIDS'
    if suffix == 'Transportation':
        return 'VEHICLE'
    if suffix == 'TVStand':
        return 'SURFACE'
    if suffix in ('Alarm', 'Audio', 'Clock', 'Computer', 'TV', 'MiscElectronics'):
        return 'ELECTRONICS'
    return 'ENTERTAINMENT'


def _live_group_ss(suffix: str) -> str:
    if any(word in suffix for word in ('Bed', 'Chair', 'Sofa', 'Seat', 'Barstool', 'Comfort')):
        return 'COMFORT'
    if any(word in suffix for word in ('Bookshelf', 'Dresser', 'Storage')):
        return 'STORAGE'
    return 'SURFACE'


def _live_group_pa(suffix: str) -> str:
    if suffix.startswith('Pet'):
        return 'PETS'
    if suffix in ('Shower', 'Sink', 'Toilet', 'Tub', 'PublicRestroom', 'MiscPlumbing'):
        return 'PLUMBING'
    return 'APPLIANCES'


def _live_group_ld(suffix: str) -> str:
    return 'LIGHTING' if 'Light' in suffix or 'Lamp' in suffix else 'DECOR'


_LIVE_GROUP: dict[str, Callable[[str], str]] = {
    'BuyCatEE_': _live_group_ee,
    'BuyCatSS_': _live_group_ss,
    'BuyCatPA_': _live_group_pa,
    'BuyCatLD_': _live_group_ld,
}


def _group_of(flags: int, sub: str) -> str:
    """The catalog group for buy-category flags and a subcategory tag."""
    candidates = [key for key in buycat.GROUP_ORDER if flags & buycat.GROUP_FLAG.get(key, 0)]
    if candidates:
        family = next((fam for prefix, fam in _TAG_FAMILY.items() if sub.startswith(prefix)), ())
        return next((key for key in family if key in candidates), candidates[0])
    if sub.startswith(_BUILD_PREFIXES):
        return BUILD
    suffix = sub.split('_', 1)[-1]
    for prefix, classify in _LIVE_GROUP.items():
        if sub.startswith(prefix):
            return classify(suffix)
    return MISC


def _label_hash(key: str) -> int:
    if key in buycat.GROUP_LABEL_HASH:
        return buycat.GROUP_LABEL_HASH[key]
    return buycat.TAG_LABEL_HASH.get(key, buycat.MISC_LABEL_HASH)


def _label_part(key: str) -> Part:
    if key in GROUP_TERMS:
        return lang.t(GROUP_TERMS[key])
    return ('loc', _label_hash(key))


# ------------------------------------------------------------------ scanning


def bearing_index(dx: float, dz: float) -> int:
    """Compass point index (0 north, clockwise) of an offset."""
    angle = math.degrees(math.atan2(dx, dz)) % 360.0
    return int(((angle + 22.5) % 360.0) // 45) % 8


def _level_of(obj: Any) -> int:
    try:
        return int(obj.level)
    except (AttributeError, TypeError, ValueError):
        return 0  # no floor level: count it as the ground floor


def _sim_name_parts(sim_info: Any) -> tuple[str | None, int]:
    """Plain names when the game stores them as text; a loc key for the few
    special Sims that only carry a name key."""
    try:
        first = str(sim_info.first_name or '')
        last = str(sim_info.last_name or '')
    except (AttributeError, TypeError):
        first, last = '', ''
    joined = ' '.join(part for part in (first, last) if part).strip()
    if joined:
        return joined, 0
    for attr in ('full_name_key', 'first_name_key'):
        try:
            key = int(getattr(sim_info, attr, 0) or 0)
        except (TypeError, ValueError):
            key = 0
        if key:
            return None, key
    return '', 0


# Get to Work hospital patients (PatientSituationBase) by visit stage: the
# situation's current state class, as EA names them in
# situations.complex.patient_situation_base and hospital_patient_situation.
PATIENT_STAGES = {
    'ArrivingState': 'patient arriving',
    'WaitingState': 'patient waiting',
    '_AdmittedState': 'patient admitted',
    '_DiagnosedState': 'patient diagnosed',
    'TreatedState': 'patient treated',
}


def _patient_roles() -> dict[int, str]:
    """Sim id -> spoken role for every patient on the lot."""
    try:
        import services
        from situations.complex.patient_situation_base import PatientSituationBase

        manager = services.get_zone_situation_manager()
        situations = list(manager.values()) if manager is not None else []
    except Exception:
        simsight_log.error('scanner: patient situations')
        return {}
    roles = {}
    for situation in situations:
        try:
            if not isinstance(situation, PatientSituationBase):
                continue
            patient = situation.get_patient()
            if patient is None:
                continue
            state = type(getattr(situation, '_cur_state', None)).__name__
            roles[patient.id] = lang.t(PATIENT_STAGES.get(state, 'patient'))
        except Exception:
            simsight_log.error('scanner: patient role')
    return roles


def _first_name(sim_info: Any) -> str:
    try:
        return str(sim_info.first_name or '')
    except (AttributeError, TypeError):
        return ''


def _object_name_parts(obj: Any) -> tuple[str | None, int]:
    """(player-given name, None) or (None, catalog name key)."""
    # A player-given name is plain text; otherwise catalog_name is the
    # string-table key itself (an int; EA's own GSI code does hex() on it).
    try:
        if obj.has_custom_name():
            custom = str(obj.custom_name or '') or None
            if custom is not None:
                return custom, 0
    except (AttributeError, TypeError):
        pass  # no custom-name support on this object: use the catalog name
    try:
        raw = obj.catalog_name
        return None, int(getattr(raw, 'hash', raw)) & 0xFFFFFFFF
    except (AttributeError, TypeError, ValueError):
        return None, 0


def entry_for(  # noqa: PLR0913, PLR0917 - used by other modules with this signature
    obj: Any, origin: Any, origin_level: int, is_sim: bool, category: str, group: str
) -> Entry:
    pos = obj.position
    dx = float(pos.x) - float(origin.x)
    dz = float(pos.z) - float(origin.z)
    distance = math.sqrt(dx * dx + dz * dz)
    level_delta = _level_of(obj) - origin_level
    if is_sim:
        name, key = _sim_name_parts(obj.sim_info)
        first = _first_name(obj.sim_info)
        return Entry(
            obj.id,
            True,
            name,
            key,
            distance,
            bearing_index(dx, dz),
            level_delta,
            category,
            group,
            first,
        )
    name, key = _object_name_parts(obj)
    return Entry(
        obj.id, False, name, key, distance, bearing_index(dx, dz), level_delta, category, group
    )


def _subs_in(entries: list[Entry]) -> list[str]:
    """Subcategory keys in the order the entries are listed."""
    seen: list[str] = []
    for entry in entries:
        if entry.category not in seen:
            seen.append(entry.category)
    return seen


# ------------------------------------------------------------ scanner range
#
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
            entry.category, catalog_group, names, flags = _classification(obj, _state.last_scan)
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


# ------------------------------------------------------- for the menu window


def ensure_scan() -> bool:
    """Scan if nothing is listed yet. Simulation thread."""
    _touch()
    return _ensure_scan()


def snapshot() -> tuple[list[tuple[str, list[Entry]]], str | None, Entry | None]:
    """(groups as [(key, [Entry])], current group key, current Entry)."""
    groups = [(key, list(_state.by_group.get(key, ()))) for key in _state.groups]
    return (groups, _group_key(), _current())


def subs_in(entries: list[Entry]) -> list[str]:
    return _subs_in(entries)


def label_hash(key: str) -> int:
    return _label_hash(key)


def place_text(entry: Entry) -> str:
    return place_part(entry)


def select_group(key: str) -> bool:
    """Move the cursor to a main category and speak it. Simulation thread."""
    if key not in _state.groups:
        return False
    _tree_remember()
    _state.group_index = _state.groups.index(key)
    _state.tree_level = 'groups'
    _tree_restore()
    _tree_speak()
    return True


def select_item(key: str, obj_id: int) -> bool:
    """Move the cursor to one thing and speak it. Simulation thread."""
    if key not in _state.groups:
        return False
    entries = _state.by_group.get(key, [])
    for index, entry in enumerate(entries):
        if entry.obj_id == obj_id:
            _tree_remember()
            _state.group_index = _state.groups.index(key)
            _state.item_index = index
            _state.tree_level = 'items'
            _tree_remember()
            _tree_speak()
            return True
    return False


# --------------------------------------------------------------- describing


def name_part(entry: Entry) -> Part:
    if entry.name:
        return entry.name
    if entry.name_hash:
        return ('loc', entry.name_hash)
    return ''


def _floor_part(delta: int) -> str:
    """', one floor up' and the like; '' on the same floor."""
    if delta == 1:
        return ', ' + lang.t('one floor up')
    if delta == -1:
        return ', ' + lang.t('one floor down')
    if delta > 1:
        return ', {} {}'.format(delta, lang.t('floors up'))
    if delta < -1:
        return ', {} {}'.format(-delta, lang.t('floors down'))
    return ''


def place_part(entry: Entry) -> str:
    metres = int(round(entry.distance))
    if metres < 1:
        where = lang.t('here')
    else:
        where = '{} {} {}'.format(metres, lang.t('meters'), lang.t(COMPASS[entry.bearing]))
    return where + _floor_part(entry.level_delta)


def _entry_parts(entry: Entry, index: int, total: int) -> list[Part]:
    import simsight_cleanliness as cleanliness

    status = ', ' + lang.t('scanner unavailable') if entry.availability == 'locked' else ''
    if entry.role:
        status = ', ' + entry.role + status
    return [
        name_part(entry),
        cleanliness.suffix(entry) + status + ', ' + place_part(entry),
        '. {} {} {}.'.format(index + 1, lang.t('of'), total),
    ]


def _sub_label_part(entries: list[Entry], entry: Entry) -> Part | None:
    """The subcategory name, or None when the main category has a single
    subcategory that is the category itself (Sims, bare Misc)."""
    if len(_subs_in(entries)) <= 1 and _label_part(entry.category) == _label_part(entry.group):
        return None
    return _label_part(entry.category)


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


def _ui_owns_input() -> bool:
    try:
        import simsight_keys as keys

        return bool(keys.status.modal_active or keys.status.editing_active)
    except (ImportError, AttributeError):
        return False  # the keyboard module is not loaded: nothing owns the keys


def _refresh() -> None:
    # After 30 seconds without scanner input, the alarm only checks these
    # flags. The next action refreshes before moving or opening an item.
    if not enabled() or not settings.get('keyboard', True):
        availability.reset()
        return
    if not _state.refresh_requested:
        return
    if _state.menu_open or _ui_owns_input():
        availability.pause()
        return
    now = time.monotonic()
    if now - _state.last_activity >= _IDLE_SECONDS:
        _state.refresh_requested = False
        availability.pause()
        return
    if now - _state.last_scan >= _REFRESH_SECONDS:
        rescan(preserve=True)


def _refresh_tick(_handle: Any = None) -> None:
    # A real-time game alarm: updates while paused, never on the key thread.
    try:
        _refresh()
    except Exception:
        simsight_log.error('scanner: refresh tick')


class _RefreshOwner:
    """The owner object a game alarm needs."""


def _start_refresh() -> None:
    import alarms
    import clock

    if _state.refresh_handle is not None:
        # The previous zone may already have destroyed its timeline.
        with contextlib.suppress(Exception):
            alarms.cancel_alarm(_state.refresh_handle)
    _state.refresh_owner = _RefreshOwner()
    _state.refresh_handle = alarms.add_alarm_real_time(
        _state.refresh_owner,
        clock.interval_in_real_seconds(_REFRESH_SECONDS),
        _refresh_tick,
        repeating=True,
    )


def _active_sim_changed(old_sim: Any, new_sim: Any) -> None:
    # The game calls this when the player switches Sims.
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
        # The client may already be gone with the previous zone.
        with contextlib.suppress(Exception):
            client.unregister_active_sim_changed(_active_sim_changed)
    _state.availability_client = None


# ------------------------------------------------------------------- intents


def _tree_remember() -> None:
    entry = _current()
    if entry is not None:
        _state.tree_groups[entry.group] = entry.category
        _state.tree_items[(entry.group, entry.category)] = entry.obj_id


def _tree_restore(subcategory: str | None = None) -> None:
    """Restore by identity, falling back within the same subcategory first."""
    entries = _entries()
    if not entries:
        _state.item_index = 0
        _state.tree_level = 'groups'
        return
    group = _group_key() or ''
    subs = _subs_in(entries)
    sub = subcategory if subcategory is not None else _state.tree_groups.get(group)
    if sub not in subs:
        sub = subs[0]
    indices = [i for i, entry in enumerate(entries) if entry.category == sub]
    wanted = _state.tree_items.get((group, sub))
    _state.item_index = next((i for i in indices if entries[i].obj_id == wanted), indices[0])
    # Do not leave a redundant Sims -> Sims container after a refresh.
    if _state.tree_level == 'subcategories' and not _tree_has_sublevel():
        _state.tree_level = 'items'


def _tree_has_sublevel() -> bool:
    entries = _entries()
    entry = _current()
    return entry is not None and _sub_label_part(entries, entry) is not None


def _kind(term: str) -> list[Part]:
    """The spoken kind before a name; verbosity brief leaves it out."""
    return [] if settings.brief() else [lang.t(term) + '. ']


def _position_text(count: int, position: int, total: int) -> str:
    """', 5 items. 2 of 3.'"""
    return ', {} {}. {} {} {}.'.format(count, lang.t('items'), position, lang.t('of'), total)


def _tree_parts() -> list[Part]:
    entry = _current()
    if entry is None:
        return [lang.t('nothing nearby')]
    entries = _entries()
    if _state.tree_level == 'groups':
        # entry.group is the current group key: lists hold their own group only.
        return [
            *_kind('scanner category'),
            _label_part(entry.group),
            _position_text(len(entries), _state.group_index + 1, len(_state.groups)),
        ]
    subs = _subs_in(entries)
    siblings = [e for e in entries if e.category == entry.category]
    if _state.tree_level == 'subcategories':
        return [
            *_kind('scanner subcategory'),
            _label_part(entry.category),
            _position_text(len(siblings), subs.index(entry.category) + 1, len(subs)),
        ]
    return _entry_parts(entry, siblings.index(entry), len(siblings))


def _tree_speak(prefix: str = '') -> None:
    parts: list[Part] = [prefix] if prefix else []
    speak_parts(parts + _tree_parts())


def enter_mode() -> None:
    """Keyboard router calls on the simulation thread after mode activation."""
    _touch()
    _ensure_scan()
    _tree_speak(lang.t('scanner') + '. ')


def resume_mode() -> None:
    """Restore the spoken location after the native menu relinquishes input."""
    enter_mode()


def set_camera_live(live: bool) -> None:
    import simsight_camera as camera_follow

    camera_follow.set_live(live)


def update_camera_follow() -> None:
    import simsight_camera as camera_follow

    camera_follow.update()


def _tree_move(step: int) -> None:
    entry = _current()
    if entry is None:
        _tree_speak()
        return
    _tree_remember()
    if _state.tree_level == 'groups':
        _state.group_index = (_state.group_index + step) % len(_state.groups)
        _tree_restore()
    elif _state.tree_level == 'subcategories':
        subs = _subs_in(_entries())
        _tree_restore(subs[(subs.index(entry.category) + step) % len(subs)])
    else:
        indices = [i for i, other in enumerate(_entries()) if other.category == entry.category]
        _state.item_index = indices[(indices.index(_state.item_index) + step) % len(indices)]
    _tree_remember()
    _tree_speak()


def _tree_enter(activate: bool = False) -> None:
    if _current() is None:
        _tree_speak()
        return
    if _state.tree_level == 'groups':
        _state.tree_level = 'subcategories' if _tree_has_sublevel() else 'items'
    elif _state.tree_level == 'subcategories':
        _state.tree_level = 'items'
    elif activate:
        open_pie_menu()
        return
    # Right on a leaf only repeats it; Enter is required for interaction.
    _tree_speak()


def _tree_back() -> None:
    _tree_remember()
    if _state.tree_level == 'items' and _tree_has_sublevel():
        _state.tree_level = 'subcategories'
    else:
        _state.tree_level = 'groups'
    _tree_speak()


def _do_rescan() -> None:
    _state.class_cache.clear()
    availability.recheck()
    if not rescan(preserve=True):
        speech.speak(lang.t('nothing nearby'), source='widget')
        return
    total = sum(len(v) for v in _state.by_group.values())
    parts: list[Part] = [
        '{} {}, {} {}. '.format(total, lang.t('items'), len(_state.groups), lang.t('categories'))
    ]
    speak_parts(parts + _tree_parts())


def _do_step_sub(step: int) -> None:
    """Optional legacy Page key jump; the tree itself needs no Page keys."""
    entry = _current()
    if entry is None:
        _tree_speak()
        return
    _tree_remember()
    subs = _subs_in(_entries())
    _tree_restore(subs[(subs.index(entry.category) + step) % len(subs)])
    _state.tree_level = 'items'
    _tree_remember()
    landed = _current() or entry  # the list is not empty: always the restored entry
    speak_parts([_label_part(landed.category), '. ', *_tree_parts()])


def _do_where() -> None:
    import services

    parts: list[Part] = []
    sim = services.get_active_sim()
    if sim is not None:
        name, key = _sim_name_parts(sim.sim_info)
        parts.append(name if name else ('loc', key))
        parts.append('. ')
    entry = _current()
    if entry is None:
        parts.append(lang.t('nothing nearby'))
    else:
        entries = _entries()
        parts.append(_label_part(entry.group))
        sub = _sub_label_part(entries, entry)
        if sub is not None:
            parts.append(', ')
            parts.append(sub)
        parts.append('. ')
        parts.extend(_entry_parts(entry, _state.item_index % len(entries), len(entries)))
    speak_parts(parts)


def _pick_message(entry: Entry, obj: Any, services: Any) -> str:
    """The A11y|pick message that asks the client for this thing's pie menu."""
    from server.pick_info import PickType

    pick_type = PickType.PICK_SIM if entry.is_sim else PickType.PICK_OBJECT
    pos = obj.position
    level = _level_of(obj)
    try:
        lot_id = int(services.active_lot().lot_id)
    except (AttributeError, TypeError, ValueError):
        lot_id = 0  # no active lot
    first = (entry.first_name or '').replace('|', ' ')
    return f'A11y|pick|{obj.id}|{int(pick_type)}|{lot_id}|{first}|{float(pos.x):.3f}|{float(pos.y):.3f}|{float(pos.z):.3f}|{level}'


def open_pie_menu() -> None:
    """Ask the client to open the game's pie menu on the current thing.

    The request goes to Flash first (A11y|pick|...): the patched PieMenuMain
    posts interactions.choices through the client's own command channel with
    its own request counter, exactly like a mouse click, so the reply is a
    menu the client asked for and it draws it. Python never calls
    generate_choices itself (v1 did, and the client discarded the reply)."""
    entry = _current()
    if entry is None:
        speech.speak(lang.t('nothing nearby'), source='widget')
        return
    import services

    obj = services.object_manager().get(entry.obj_id)
    if obj is None:
        speech.speak(lang.t('no longer here'), source='widget')
        return
    try:
        sent = send_ui(_pick_message(entry, obj, services))
    except Exception:
        simsight_log.error('scanner: open menu')
        sent = False
    simsight_log.line(
        f'scanner: menu requested for {entry.obj_id} (sim={entry.is_sim}) sent={sent}'
    )


@sims4.commands.Command(
    'simsight.menuempty', 'a11y.menuempty', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_menuempty(arg: str = '', _connection: Any = None) -> bool:
    """Flash: the requested pie menu had no options."""
    try:
        speech.speak(lang.t('no options'), source='widget')
    except Exception:
        simsight_log.error('scanner: menu empty')
    return True


_INTENTS: dict[str, Callable[[], object]] = {
    'rescan': _do_rescan,
    'next': lambda: _tree_move(1),
    'prev': lambda: _tree_move(-1),
    'nextcat': _tree_enter,
    'prevcat': _tree_back,
    'nextsub': lambda: _do_step_sub(1),
    'prevsub': lambda: _do_step_sub(-1),
    'open': lambda: _tree_enter(activate=True),
    'where': _do_where,
    'back': _tree_back,
    'scopeprev': lambda: cycle_range(-1),
    'scopenext': lambda: cycle_range(1),
}


def _dispatch(intent: str) -> None:
    _touch()
    if intent != 'rescan':
        _ensure_scan()
    action = _INTENTS.get(intent)
    if action is None:
        simsight_log.line(f'scanner: unknown intent {intent!r}')
        return
    action()


def on_key(intent: str, on_simulation_thread: bool = False) -> None:
    """Called on the KEY thread. Decides who handles the chord; never
    touches the game here unless its caller explicitly owns the simulation thread."""
    if not enabled():
        return
    if intent == 'repeat':
        speech.repeat_last()
        return
    if intent == 'help':
        import simsight_shortcuts as shortcuts

        shortcuts.speak()
        return
    if _state.menu_open and intent in MENU_KEYS:
        # A pie menu is open: the chords walk it through the Flash walker.
        _log_route('sent to the open menu', intent)
        try:
            import simsight_keys as keys

            keys.send_action(MENU_KEYS[intent])
        except Exception:
            simsight_log.error('scanner: menu key')
        return
    if on_simulation_thread:
        # The world-chord callback has already crossed onto the simulation.
        # Do not defer the same key to a second alarm turn.
        _log_route('dispatched to the scanner', intent)
        _dispatch(intent)
    elif not pump.post(_dispatch, intent):
        speech.speak(lang.t('no lot loaded'), explicit=True)


def _log_route(reason: str, intent: str) -> None:
    """One line per reason per second (fixed reasons, bounded dict)."""
    now = time.monotonic()
    if now - _state.route_logged.get(reason, 0.0) >= 1.0:
        _state.route_logged[reason] = now
        simsight_log.line(f'scanner: chord {intent} {reason}')


def note_menu_open() -> None:
    """Any pie menu opened (ours or a mouse click)."""
    if not _state.menu_open:
        simsight_log.line('scanner: menu flag set')
    _state.menu_open = True
    availability.pause()


@sims4.commands.Command(
    'simsight.menuclosed', 'a11y.menuclosed', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_menuclosed(arg: str = '', _connection: Any = None) -> bool:
    if _state.menu_open:
        simsight_log.line('scanner: menu flag cleared')
    _state.menu_open = False
    return True


def _stats_text() -> str:
    stats = dict(_state.scan_stats)
    stats['mean_ms'] = stats['total_ms'] / max(1, stats['scans'])
    text = (
        'scanner: {scans} scans; last {last_ms:.2f} ms, mean {mean_ms:.2f} ms, '
        'max {max_ms:.2f} ms; last objects {examined}, in range {in_range}; '
        'classification hits {cache_hits}, misses {cache_misses}'
    ).format(**stats)
    return text + '; refresh {}'.format('active' if _state.refresh_requested else 'idle')


def _scan_command(action: str, output: Any) -> None:
    if action.lower() == 'stats':
        text = _stats_text()
        output(text)
        simsight_log.line(text)
        return
    if not enabled():
        output('simsight: scanner is off (cfg [features] scanner)')
        return
    _dispatch(action.lower())
    output(f'simsight: scanner {action} -> {len(_state.groups)} categories, pump {pump.status()}')


@sims4.commands.Command('simsight.scan', 'a11y.scan', command_type=sims4.commands.CommandType.Live)
@real_annotations
def a11y_scan(action: str = 'rescan', _connection: Any = None) -> bool:
    """simsight.scan [rescan|next|prev|nextcat|prevcat|nextsub|prevsub|open|back|
    where] runs one scanner action from the console (same as the chords)."""
    try:
        _scan_command(action, sims4.commands.CheatOutput(_connection))
    except Exception:
        simsight_log.error('scanner: scan command')
    return True


def _reset_for_new_zone() -> None:
    """A lot finished loading: forget the old lot's lists, caches and names."""
    _state.by_group = {}
    _state.groups = []
    _state.tree_level = 'groups'
    _state.tree_groups.clear()
    _state.tree_items.clear()
    availability.reset()
    _unbind_availability_client()
    _bind_availability_client()
    import simsight_camera as camera_follow

    camera_follow.reset()
    _state.menu_open = False
    _state.last_scan = 0.0
    _state.refresh_requested = False
    _state.last_activity = 0.0
    _state.class_cache.clear()
    for key in _state.scan_stats:
        _state.scan_stats[key] = 0
    try:
        _start_refresh()
    except Exception:
        simsight_log.error('scanner: refresh alarm')
    with _loc_lock:
        loc_cache.clear()
        _state.loc_waiting.clear()
    # Re-assert the Flash game-message hook after every load.
    send_ui('A11yHook')


def _install_zone_hook() -> None:
    import zone

    original = zone.Zone.on_loading_screen_animation_finished

    def a11y_scanner_on_loading_screen_animation_finished(
        self: Any, *args: Any, **kwargs: Any
    ) -> Any:
        result = original(self, *args, **kwargs)
        try:
            _reset_for_new_zone()
        except Exception:
            simsight_log.error('scanner: zone load reset')
        return result

    zone.Zone.on_loading_screen_animation_finished = (
        a11y_scanner_on_loading_screen_animation_finished
    )


try:
    _install_zone_hook()
except Exception:
    simsight_log.error('scanner: zone hook install')


def _queue_extensions() -> None:
    # Install only the remaining local extensions after module initialization,
    # on the simulation pump. Upstream owns the integrated contribution controls.
    try:
        import simsight_extensions
        import simsight_plus_core

        simsight_plus_core.post(simsight_extensions.install)
    except Exception:
        simsight_log.error('scanner: local extensions could not be queued')


_queue_extensions()

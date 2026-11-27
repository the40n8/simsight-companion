"""Main categories and subcategories from the game's own build/buy tags and
buy-category flags (simsight_buycat)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import simsight_buycat as buycat
import simsight_lang as lang
import simsight_log

from .state import BUILD, GROUP_TERMS, MISC, _CLASS_CACHE_LIMIT, _CLASS_CACHE_SECONDS, _state

if TYPE_CHECKING:
    from .state import Classification, Part


def classification(obj: Any, now: float) -> Classification:
    """(subcategory, catalog group, tag names, buy-category flags) of an
    object (also read by simsight_bridge)."""
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

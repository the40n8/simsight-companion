"""The tree cursor (groups, subcategories, items): remembering and
restoring the place, and what it says."""

from __future__ import annotations

from typing import TYPE_CHECKING

import simsight_lang as lang
import simsight_settings as settings

from .state import _current, _entries, _group_key, _state, _subs_in
from .loc import speak_parts
from .categories import _label_part
from .entries import _entry_parts, _sub_label_part

if TYPE_CHECKING:
    from .state import Part


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

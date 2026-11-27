"""What the scanner window (simsight_menu) reads and selects."""

from __future__ import annotations

from .state import Entry, _current, _group_key, _state, _subs_in, _touch
from .categories import _label_hash
from .entries import place_part
from .tree import _tree_remember, _tree_restore, _tree_speak
from .scan import _ensure_scan


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

"""Game text: string-table keys resolved by the UI, Sims placeholders
filled ({0.SimFirstName}, {M0.he}{F0.she}), readable tuning names when
no game text is known yet, object names and their catalog classification."""

from __future__ import annotations


import contextlib
import re
import sys
import time
from typing import Any

import simsight_log

from .state import _state


LOC_PER_TICK = 20  # string-table keys requested from the UI per poll


# ---------------------------------------------------------------- localization
# Python only sees string-table keys; the patched UI resolves them in the
# player's language through the scanner's loc channel (A11y|loc / simsight.loc_enc).


def _loc_cache() -> dict[int, str]:
    scanner = sys.modules.get('simsight_scanner')
    return getattr(scanner, 'loc_cache', {}) if scanner else {}


def _request_localization() -> None:
    scanner = sys.modules.get('simsight_scanner')
    if scanner is None or not _state.loc_wanted:
        return
    cache = scanner.loc_cache
    for key in list(_state.loc_wanted)[:LOC_PER_TICK]:
        _state.loc_wanted.discard(key)
        if key in cache or key in _state.loc_requested:
            continue
        _state.loc_requested.add(key)
        scanner.send_ui(f'A11y|loc|{key}|{key}')


def _cached_text(key: int) -> str | None:
    """The game's text for a string-table key, or None (then asked of the UI)."""
    cached = _loc_cache().get(key)
    if cached is None:
        _state.loc_misses += 1
        _state.loc_wanted.add(key)
    return cached


def _loc_value(value: Any, sims: tuple[Any, ...] = ()) -> Any:
    """A LocalizedString proto from a tuned value (proto or factory). A
    factory is given the Sims the text is about, as the game does, so
    {M0.father}{F0.mother} tokens get the right Sim."""
    if value is None:
        return None
    if callable(value) and not hasattr(value, 'hash'):
        try:
            value = value(*sims)
        except TypeError:
            return None  # a factory that needs arguments (the Sims it is about)
        except Exception:
            simsight_log.error('bridge: text factory')
            return None
    return value if hasattr(value, 'hash') else None


_TOKEN = re.compile(r'\{([MF])(\d+)\.([^}]*)\}')
_PLURAL = re.compile(r'\{([SP])\.([^}]*)\}')
_FIELD = re.compile(r'\{(\d+)\.(\w+)\}')


def _number_text(t: Any, money: bool) -> str:
    n = getattr(t, 'number', 0) or 0
    text = str(int(n)) if float(n).is_integer() else f'{n:g}'
    return ('§' + text) if money else text


def _field_text(t: Any, name: str) -> str:
    """One {0.Field} placeholder filled from its token."""
    first, last = getattr(t, 'first_name', ''), getattr(t, 'last_name', '')
    if name == 'SimFirstName':
        return first
    if name == 'SimLastName':
        return last
    if name in ('SimName', 'SimFullName'):
        return f'{first} {last}'.strip()
    if name in ('Number', 'Money'):
        return _number_text(t, name == 'Money')
    return _token_text(t)


def _fill_tokens(template: str, tokens: list[Any]) -> str:
    """Fill the common Sims placeholders: {0.SimFirstName}, {M0.he}{F0.she}..."""

    def token(i: int) -> Any:
        return tokens[i] if 0 <= i < len(tokens) else None

    def gender(match: re.Match[str]) -> str:
        t = token(int(match.group(2)))
        female = bool(getattr(t, 'is_female', False)) if t is not None else False
        return match.group(3) if (match.group(1) == 'F') == female else ''

    def field(match: re.Match[str]) -> str:
        t = token(int(match.group(1)))
        return '' if t is None else _field_text(t, match.group(2))

    # {S.Cereal}{P.Bowls of Cereal}: one item, so the singular form.
    text = _PLURAL.sub(lambda m: m.group(2) if m.group(1) == 'S' else '', template)
    text = _TOKEN.sub(gender, text)
    text = _FIELD.sub(field, text)
    return re.sub(r'\{[^}]*\}', '', text).replace('  ', ' ').strip()


def _token_text(t: Any) -> str:
    """A token's own text: a nested game string, raw text, or an object's name."""
    # Not every token is a proto with a text_string field.
    with contextlib.suppress(AttributeError, ValueError):
        if t.HasField('text_string'):
            return text_of(t.text_string)
    for attr in ('raw_text', 'custom_name'):
        value = getattr(t, attr, '') or ''
        if value:
            return value
    key = getattr(t, 'catalog_name_key', 0) or 0
    return _loc_key_text(key) if key else ''


def text_of(value: Any, fallback: str = '', sims: tuple[Any, ...] = ()) -> str:
    """Game text for a localized value, or fallback until the UI resolves it.
    `sims`: the Sims a text factory is about (gendered words, names)."""
    loc = _loc_value(value, sims)
    if loc is None:
        return fallback
    try:
        key = int(loc.hash)
    except (TypeError, ValueError):
        return fallback
    if not key:
        return fallback
    cached = _cached_text(key)
    if cached is None:
        return fallback
    tokens = list(getattr(loc, 'tokens', []) or [])
    return _fill_tokens(cached, tokens) or fallback


def _pretty(tuning_name: str, prefixes: tuple[str, ...] = ()) -> str:
    """'Buff_Energized_Coffee' -> 'Energized Coffee' when no game text yet."""
    name = tuning_name or ''
    for p in prefixes:
        if name.lower().startswith(p):
            name = name[len(p) :]
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name.replace('_', ' ').replace('-', ' '))
    return ' '.join(w.capitalize() if w.islower() else w for w in name.split())


def _custom_name(obj: Any) -> str:
    """The name the player gave the object, or ''."""
    try:
        if obj.has_custom_name():
            return str(obj.custom_name or '')
    except AttributeError:
        pass  # objects that cannot be renamed
    return ''


def object_name(obj: Any) -> str:
    custom = _custom_name(obj)
    if custom:
        return custom
    fallback = _pretty(getattr(obj.definition, 'name', '') or type(obj).__name__)
    try:
        raw = obj.catalog_name
        key = int(getattr(raw, 'hash', raw)) & 0xFFFFFFFF
    except (AttributeError, TypeError, ValueError):
        key = 0  # no catalog name: the tuning name will do
    if not key:
        return fallback
    cached = _cached_text(key)
    if cached is None:
        return fallback
    return _fill_tokens(cached, []) or fallback


def _loc_key_text(key: int) -> str:
    """Game text for a string-table key, '' until the UI resolves it."""
    if not key:
        return ''
    cached = _cached_text(key)
    return '' if cached is None else _fill_tokens(cached, [])


def classify(obj: Any) -> tuple[str, str, str]:
    """(group, kind, keywords) from the scanner's catalog classification."""
    scanner = sys.modules.get('simsight_scanner')
    buycat = sys.modules.get('simsight_buycat')
    if scanner is None:
        return 'MISC', '', ''
    try:
        # simsight_scanner has no public name for it (yet).
        sub, group, names, _flags = scanner._classification(obj, time.monotonic())  # noqa: SLF001
    except Exception:
        simsight_log.error('bridge: classify')
        return 'MISC', '', ''
    kind = ''
    if buycat is not None:
        kind = _loc_key_text(buycat.TAG_LABEL_HASH.get(sub, 0))
    words = {
        _pretty(re.sub(r'^(BuyCat[A-Z]*_|Func_)', '', name))
        for name in names or ()
        if name.startswith(('BuyCat', 'Func_'))
    }
    return group, kind, ' '.join(sorted(words))

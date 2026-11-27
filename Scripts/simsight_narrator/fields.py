"""Tooltip payloads: "y|x|text" fields into reading order, labels glued to
their values, the mod's own phrases translated."""

from __future__ import annotations

import re

import simsight_lang as lang
import simsight_log

from .decode import _clean_glyphs, _norm
from .state import _state


FIELD_SEP = '\x01'

_EFFECT_RE = re.compile(r'^[A-Za-z][A-Za-z ]* \+\d+$')
_DURATION_RE = re.compile(r'^\d+ (?:minute|hour|day)s?$')
_FIELD_RE = re.compile(r'^(-?\d+)\|(-?\d+)\|(.*)$', re.DOTALL)
_ROW_TOLERANCE = 8


# EA measures text boxes with hidden ruler strings: digit runs
# ("1234567890...") and repeated wide characters ("wwwwwwww...").
_PLACEHOLDER = re.compile(r'^(?:1234567890)+\d*$')
# Authoring text left in unused TextFields: the field's own instance name
# ("txtSimName. txtPay. txtSatisfaction" in empty business employee slots,
# 2026-10-04). Game text never looks like a lowerCamel field identifier.
_FIELD_NAME = re.compile(r'^(?:txt|mc|btn|dd|cb|sl|lb|tf)[A-Z][A-Za-z0-9]*$')
_REPEATED_CHAR = re.compile(r'^(.)\1{4,}$')


def _is_placeholder(text: str) -> bool:
    stripped = re.sub(r'\s+', '', text)
    if not stripped:
        return True
    if _PLACEHOLDER.match(stripped) or _FIELD_NAME.match(stripped):
        return True
    if not re.search(r'[^\W_]', stripped, re.UNICODE):
        # Nothing but punctuation or symbols ("%", "% % %", "..."): empty
        # stat rows the game leaves in place with no value in them.
        return True
    return bool(_REPEATED_CHAR.match(stripped))


def _parse_fields(decoded: str) -> list[tuple[int, int, str]]:
    """(y, x, text) of every readable field, sorted top to bottom, left to right.
    A part without coordinates counts as (0, 0)."""
    fields = []
    for part in decoded.split(FIELD_SEP):
        match = _FIELD_RE.match(part)
        y, x, raw = (
            (int(match.group(1)), int(match.group(2)), match.group(3)) if match else (0, 0, part)
        )
        text = _clean_glyphs(raw)
        if text and not _is_placeholder(text):
            fields.append((y, x, text))
    fields.sort(key=lambda f: (f[0], f[1]))
    return fields


def _row_segments(fields: list[tuple[int, int, str]]) -> list[str]:
    """Group fields into visual rows; a row led by "Label:" reads as one segment."""
    rows: list[tuple[int, list[tuple[int, str]]]] = []
    for y, x, text in fields:
        if rows and abs(rows[-1][0] - y) <= _ROW_TOLERANCE:
            rows[-1][1].append((x, text))
        else:
            rows.append((y, [(x, text)]))
    segments = []
    for _y, cells in rows:
        # Sort by X ONLY. Sorting the whole (x, text) tuple made equal-X
        # cells fall back to comparing TEXT, which alphabetized them: a
        # composed payload whose segments carry no coordinates (they all
        # default to 0) came out in dictionary order, so the purchase
        # picker's "Increase / <item> / <count>" read as "<item> /
        # Increase / <count>". Python's sort is stable, so sorting on X
        # alone preserves the order Flash posted them in.
        cells.sort(key=lambda cell: cell[0])
        texts = [t for _x, t in cells]
        if len(texts) > 1 and texts[0].endswith(':'):
            segments.append(' '.join(texts))
        else:
            segments.extend(texts)
    return segments


def _glue_labels(segments: list[str]) -> list[str]:
    """Label above value: a bare "Label:" segment absorbs the next segment."""
    glued = []
    i = 0
    while i < len(segments):
        if (
            segments[i].endswith(':')
            and i + 1 < len(segments)
            and not segments[i + 1].endswith(':')
        ):
            glued.append(segments[i] + ' ' + segments[i + 1])
            i += 2
        else:
            glued.append(segments[i])
            i += 1
    return glued


def _fields_to_segments(decoded: str) -> list[str]:
    """Parse y|x|text fields, sort into visual rows, glue labels to values."""
    glued = _glue_labels(_row_segments(_parse_fields(decoded)))
    # drop bare header segments that duplicate a glued label
    # (e.g. "Eco Footprint" next to "Eco-Footprint: Neutral")
    label_norms = {_norm(seg.split(':', 1)[0]) for seg in glued if ':' in seg}
    return [seg for seg in glued if not (':' not in seg and _norm(seg) in label_norms)]


def _order_segments(segments: list[str]) -> list[str]:
    """Semantic ordering: moodlets read title/effect/source/duration/description;
    anything else with a (From ...) source reads it last."""
    effects = [s for s in segments if _EFFECT_RE.match(s) or s.startswith('Boosting: ')]
    sources = [s for s in segments if s.startswith(('(From', '(You cheated'))]
    durations = [s for s in segments if _DURATION_RE.match(s)]
    if effects and sources:
        special = set(effects) | set(sources) | set(durations)
        rest = [s for s in segments if s not in special]
        return rest[:1] + effects + durations + sources + rest[1:]
    if sources:
        source_set = set(sources)
        return [s for s in segments if s not in source_set] + sources
    return segments


# Flash posts bare lowercase tokens for the mod's OWN vocabulary (game text
# arrives already localized); they are translated here.
_OWN_TOKENS = frozenset(
    ('checked', 'unchecked', 'increase', 'decrease', 'phone', 'action unavailable')
)
# English lines Flash composes itself (the simplified menus position and
# selection count, the first-person camera state); game text arrives already
# localized, and none of it reads exactly like these.
_POSITION_RE = re.compile(r'^(\d+) of (\d+)$')
_SELECTED_RE = re.compile(r'^(\d+) selected$')
_OWN_PHRASES = {
    'First-person camera on.': 'first person camera on',
    'First-person camera off.': 'first person camera off',
}


def _own_phrase(seg: str) -> str:
    if seg in _OWN_TOKENS:
        return lang.t(seg)
    term = _OWN_PHRASES.get(seg)
    if term is not None:
        return lang.t(term)
    match = _POSITION_RE.match(seg)
    if match:
        return lang.t('option position').format(match.group(1), match.group(2))
    match = _SELECTED_RE.match(seg)
    if match:
        return lang.t('selected count').format(match.group(1))
    return seg


def _process(decoded: str, channel: str = '?') -> str:
    if _state.rawdiag:
        simsight_log.line(f'RAWDIAG[{channel}]: {decoded!a}')
    if FIELD_SEP not in decoded and '|' not in decoded[:12]:
        return _clean_glyphs(decoded)
    segments = _order_segments(_fields_to_segments(decoded))
    seen = set()
    ordered = []
    for seg in segments:
        if seg not in seen:
            seen.add(seg)
            ordered.append(_own_phrase(seg))
    return '. '.join(ordered)


def _process_or_raw(decoded: str, channel: str) -> str:
    """_process, or the cleaned raw text when the payload cannot be parsed."""
    try:
        return _process(decoded, channel)
    except Exception:
        simsight_log.error(f'narrator: process {channel}; speaking raw')
        return _clean_glyphs(decoded.replace(FIELD_SEP, '. '))

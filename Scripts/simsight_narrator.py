"""SimSight: console commands that receive UI text from the patched
Flash layer and speak it through a screen reader / TTS.

The patched TooltipManager (UI override package) posts:
    simsight.speak_enc <percent-encoded-utf8>
where the payload is a \\x01-separated list of "y|x|text" entries, one per
visible TextField in the tooltip. This module sorts them into visual reading
order, glues labels to values, curates known tooltip shapes, and speaks.

    simsight.mark <library-name>     (once; identifies which movie's patched
                                  TooltipManager copy won the app domain)
Manual / scripted narration can use:
    simsight.speak <free text>
Diagnostics:
    simsight.test     speak a test sentence and report the backend
    simsight.status   report backend, DLL presence and log path
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Any, Callable, Iterator, TypeVar
from urllib.parse import unquote_to_bytes

import sims4.commands

import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_patch
import simsight_settings as settings
import simsight_speech as speech

if TYPE_CHECKING:
    from zone import Zone

    Output = Callable[[str], Any]

_F = TypeVar('_F', bound=Callable[..., Any])

FIELD_SEP = '\x01'

_EFFECT_RE = re.compile(r'^[A-Za-z][A-Za-z ]* \+\d+$')
_DURATION_RE = re.compile(r'^\d+ (?:minute|hour|day)s?$')
_FIELD_RE = re.compile(r'^(-?\d+)\|(-?\d+)\|(.*)$', re.DOTALL)
_ROW_TOLERANCE = 8

# ------------------------------------------------------------------ commands


def live_command(*names: str) -> Callable[[_F], _F]:
    """sims4.commands.Command(*names, Live) with the argument types EA's
    parser needs (see simsight_commands.real_annotations)."""
    register = sims4.commands.Command(*names, command_type=sims4.commands.CommandType.Live)

    def decorate(func: _F) -> _F:
        return register(real_annotations(func))

    return decorate


@contextlib.contextmanager
def _boundary(where: str) -> Iterator[None]:
    """A console command or callback: log a failure, never raise into the game."""
    try:
        yield
    except Exception:
        simsight_log.error(where)


# ------------------------------------------------------------------ decoding


def _utf8(chunk: bytes) -> str | None:
    try:
        return chunk.decode('utf-8')
    except UnicodeDecodeError:
        return None  # not a complete UTF-8 sequence


def _decode_char(raw: bytes, i: int) -> tuple[str, int]:
    """The character at raw[i] (not ASCII) and its width: UTF-8 where it
    validates, else one Latin-1 byte."""
    for width in (2, 3, 4):
        char = _utf8(raw[i : i + width])
        if char is not None:
            return char, width
    return raw[i : i + 1].decode('latin-1'), 1


def decode_percent(token: str) -> str:
    """Decode Flash payloads. Scaleform's encodeURIComponent is broken for
    U+0080..U+00FF: those arrive as bare Latin-1 bytes while higher
    codepoints arrive as proper UTF-8, so a strict UTF-8 unquote turns every
    accented letter (and the simoleon sign) into U+FFFD. Decode byte-wise:
    UTF-8 where it validates, Latin-1 for stray single bytes."""
    try:
        raw = unquote_to_bytes(token)
    except (TypeError, ValueError):
        return token
    out = []
    i = 0
    while i < len(raw):
        if raw[i] < 0x80:
            out.append(chr(raw[i]))
            i += 1
            continue
        char, width = _decode_char(raw, i)
        out.append(char)
        i += width
    return ''.join(out)


def _direction(match: re.Match[str]) -> str:
    args = [
        arg.strip() or lang.t('transfer other household')
        for arg in match.group(2).split('\x02')[1:]
    ]
    template = lang.t('transfer ' + match.group(1))
    try:
        return template.format(*args)
    except (IndexError, KeyError, ValueError):
        return ' '.join(args)


def _amount(term: str) -> Callable[[re.Match[str]], str]:
    """Replacement for a currency regex: "<amount> <localized unit>"."""
    return lambda m: m.group(1) + ' ' + lang.t(term)


def _clean_glyphs(text: str) -> str:
    """Replace/strip game icon glyphs that TTS reads badly (localized).
    EA repurposes ordinary symbol chars via the game font: paragraph sign =
    simoleons, U+2260 = galactic credits, lozenge = satisfaction, and so on
    (found by censusing the STBLs; see HANDOFF)."""
    # EA name/description strings carry text markup (<i>, <b>) that only
    # renders inside TextFields; Flash-side COMPOSED payloads (catalog
    # cells read mData.Name raw) pass it through, so spoken text must
    # drop the tags (heard live: "The Indoor <i>and</i> Outdoor Plastic
    # Chair", household inventory 2026-08-20).
    text = re.sub(r'</?[a-zA-Z][^>]*>', ' ', text)
    # Stack-count marker from the catalog cell composer (Flash appends
    # "\x02qty\x02<n>"; keeping it a control-char marker instead of an
    # English word keeps the 17-language table in charge of the wording).
    text = re.sub('\x02qty\x02(\\d+)', lambda m: lang.t('quantity') + ' ' + m.group(1), text)
    # Transfer direction marker from the household and retail transfer
    # panels: "\x02dir\x02<kind>" then "\x02"-separated names and amounts,
    # worded here so the language table owns it. Runs before the currency
    # rule so a household's paragraph-sign amount still becomes simoleons.
    text = re.sub('\x02dir\x02(\\w+)((?:\x02[^\x01\x02]*)*)', _direction, text)
    # Business summaries and panels sign amounts as "+  §500" and
    # "-  §500" and colour them; a lone dash is often skipped by
    # screen readers, so say plus and minus (2026-10-04).
    text = re.sub(
        '(?<![\\w§])([+-])\\s*(?=§)',
        lambda m: lang.t('plus' if m.group(1) == '+' else 'minus') + ' ',
        text,
    )
    # Currency amounts: the capture must END on a digit. The old
    # [\d][\d.,]* also swallowed a trailing sentence period, so
    # "§645. Romantic" became "645. simoleons Romantic" with the unit
    # glued to the next clause (household inventory bug, 2026-08-20).
    # Interior separators ("13,709") still match.
    text = re.sub(r'[§�]\s*(\d(?:[\d.,]*\d)?)', _amount('simoleons'), text)
    text = re.sub('≠\\s*(\\d(?:[\\d.,]*\\d)?)', _amount('galactic credits'), text)
    # Eco Lifestyle recycling currencies (glyphs captured in-game 2026-08-01:
    # wallet order is credits, bits, pieces)
    text = re.sub('⬡\\s*(\\d(?:[\\d.,]*\\d)?)', _amount('bits'), text)
    text = re.sub('⬢\\s*(\\d(?:[\\d.,]*\\d)?)', _amount('pieces'), text)
    text = text.replace('�', ' ')
    text = re.sub(
        r'◊\s*(\d+)', lambda m: '. ' + m.group(1) + ' ' + lang.t('satisfaction points') + '. ', text
    )
    text = text.replace('◴', ' ' + lang.t('likes') + ': ')
    text = text.replace('◵', ' ' + lang.t('dislikes') + ': ')
    # private-use-area icon glyphs
    text = re.sub('[-]', ' ', text)
    text = re.sub(r'\s*[\r\n]+\s*', '. ', text)
    return re.sub(r'  +', ' ', text).strip()


def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', text.lower())


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


# ------------------------------------------------------------------ state

# Two-stage reading for DATA ROWS. A row that announces its own name and
# value (needs bars, skill rows, mood, the time panel) has already said the
# useful part; its tooltip is a long stock description. So the row line
# speaks at once and the description that follows within the window is held
# back by `tooltip delay` seconds, cancelled the moment the pointer moves on
# (tester request 2026-08-03: "mostly useless info"). The read key still
# reads row and description together.
#
# The trigger is deliberately "a tooltip following a real row announcement",
# NOT tooltip length: buttons and icons announce through the widgetNAME
# channel instead, so their tooltips (which carry the only text they have)
# stay immediate. A global length threshold was tried 2026-08-01 and
# rejected by the tester.
_ROW_TIP_WINDOW = 4.0
# Control Alt E reads at most this many moodlets.
_MOOD_LIMIT = 40
# An icon-only control has no text of its own, so Flash sends us its instance
# identifier. That is a poor substitute for the real tooltip ("Wall" for the
# Notification Wall button), so hold it briefly: if the control's tooltip
# arrives, it wins and the derived name is dropped.
_NAME_GRACE_SECONDS = 0.4


@dataclass
class _Moodlet:
    mood: str
    boost: bool
    weight: int
    name: str
    effect: str
    bonus: str
    reason: str
    time: str
    description: str


@dataclass
class _MoodReading:
    seq: str
    mood: str
    name: str
    description: str
    rows: list[_Moodlet] = field(default_factory=list)


class _State:
    def __init__(self) -> None:
        self.rawdiag = False  # simsight.rawdiag: log payloads before cleaning
        self.row_head: str | None = None  # last row line, for _take_row_head
        self.row_head_when = 0.0
        self.mood_reading: _MoodReading | None = None
        self.inspect_timer: threading.Timer | None = None
        # The pending derived name is shared with its timer thread: _name_lock.
        self.pending_name: str | None = None
        self.pending_name_timer: threading.Timer | None = None


_state = _State()
_name_lock = threading.Lock()


def _arm_row_head(text: str) -> None:
    """Remember a row line so the description behind it can be deferred."""
    _state.row_head = text or None
    _state.row_head_when = time.monotonic()


def _take_row_head() -> str | None:
    """The pending row line, if it is recent enough to still belong to the
    tooltip now arriving."""
    head = _state.row_head
    _state.row_head = None
    if head is None or time.monotonic() - _state.row_head_when >= _ROW_TIP_WINDOW:
        return None
    return head


def _tooltip_delay() -> float:
    try:
        return max(0.0, float(settings.get('tooltip_delay')))
    except (TypeError, ValueError):
        return 0.0


def _on_off(value: object) -> str:
    return 'on' if value else 'off'


def _cancel_pending_name() -> None:
    with _name_lock:
        _state.pending_name = None


def _flush_pending_name() -> None:
    """Timer thread: speak the derived name if no real text replaced it."""
    with _boundary('narrator: flush pending name'):
        with _name_lock:
            name = _state.pending_name
        if name:
            _cancel_pending_name()
            speech.speak(name, source='widget')


# ------------------------------------------------------------------ Flash channels


@live_command('simsight.rawdiag', 'a11y.rawdiag')
def a11y_rawdiag(mode: str = 'on', _connection: Any = None) -> bool:
    """Hidden support diagnostic: log pre-clean payloads with unicode
    escapes (for identifying unknown glyph characters). Session-only;
    needs simsight.log on to actually write anywhere."""
    with _boundary('narrator: rawdiag'):
        _state.rawdiag = mode.lower() not in ('off', 'false', '0')
        sims4.commands.CheatOutput(_connection)(
            'simsight: raw payload logging {}'.format('ON' if _state.rawdiag else 'OFF')
        )
        return True
    return False


@live_command('simsight.timewidget_enc', 'a11y.timewidget_enc')
def a11y_timewidget_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: timewidget_enc'):
        decoded = decode_percent(text)
        if _state.rawdiag:
            simsight_log.line('RAWDIAG[timewidget]: ' + ascii(decoded))
        final = _clean_glyphs(decoded)
        if not final:
            return True
        _arm_row_head(final)
        speech.speak(final, source='widget')
        return True
    return False


@live_command('simsight.clock_enc', 'a11y.clock_enc')
def a11y_clock_enc(text: str = '', _connection: Any = None) -> bool:
    """The client's answer to A11yClock (Control Alt C, the menu row): its
    clock widget's own localized day and time, with Paused when paused,
    as 'clock:<text>'. Empty text means the widget is not live; say so in
    words rather than invent a day number. Separate from the pointer path
    (simsight.timewidget_enc) so a hover can never pass for the reply."""
    with _boundary('narrator: clock_enc'):
        decoded = decode_percent(text)
        if decoded.startswith('clock:'):
            decoded = decoded[len('clock:') :]
        final = _clean_glyphs(decoded).strip() if decoded.strip() else ''
        money = _clock_funds()
        if not final:
            speech.speak(
                lang.t('clock unavailable') + (' ' + money if money else ''), explicit=True
            )
            return True
        speech.speak(final + ('. ' + money if money else ''), source='widget', explicit=True)
        return True
    return False


def _clock_funds() -> str:
    """The clock reply also reads household funds (requested 2026-09-28). Empty
    before a household loads, e.g. at the main menu, so the clock still
    answers there on its own."""
    try:
        import simsight_hub as hub

        return hub.funds_text()
    except Exception as ex:  # the clock must still answer
        simsight_log.line(f'clock: funds unavailable ({ex!r})')
        simsight_log.error('narrator: clock funds')
        return ''


# Control Alt E and the accessibility menu row read the mood panel in one
# pass. SimHub's MoodContainer posts one record per simsight.mood_enc so no
# single command carries every moodlet: M opens a reading (sequence, count,
# current mood key, mood name, mood description), B carries a moodlet
# (sequence, index, mood key, boost flag, weight, name, effect, boost,
# reason, time left, description; the description is last because Flash
# caps each record's length), and E ends it. All text is the client's own
# localized text. An empty payload means the panel is not live.


def _mood_text(value: str) -> str:
    return _clean_glyphs(value).strip() if value and value.strip() else ''


def _mood_weight(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _mood_order(current: str, rows: list[_Moodlet]) -> list[_Moodlet]:
    """BuffDisplay.BuffSort: the current mood's moodlets first, ordinary
    before boosts, then by weight and name; other moods follow grouped by
    their summed weight, then by mood key descending, the same way inside."""
    totals: dict[str, int] = {}
    for row in rows:
        totals[row.mood] = totals.get(row.mood, 0) + row.weight
    rows = sorted(rows, key=lambda row: (row.boost, -row.weight, row.name.lower()))
    rows.sort(key=lambda row: row.mood, reverse=True)
    rows.sort(key=lambda row: (0, 0) if row.mood == current else (1, -totals[row.mood]))
    return rows


def _mood_speech(reading: _MoodReading) -> str:
    parts = [reading.name, reading.description]
    rows = _mood_order(reading.mood, reading.rows)
    if not rows:
        parts.append(lang.t('no moodlets'))
    for row in rows:
        # Title, effect, duration, source, description: the order moodlet
        # tooltips already read in (_order_segments).
        parts.extend((row.name, row.effect, row.bonus, row.time, row.reason, row.description))
    return '. '.join(part.rstrip('.') for part in parts if part)


def _moodlet(fields: list[str]) -> _Moodlet:
    """A B record (at least 11 fields) as a moodlet."""
    return _Moodlet(
        mood=fields[3],
        boost=fields[4] == '1',
        weight=_mood_weight(fields[5]),
        name=_mood_text(fields[6]),
        effect=_mood_text(fields[7]),
        bonus=_mood_text(fields[8]),
        reason=_mood_text(fields[9]),
        time=_mood_text(fields[10]),
        description=_mood_text(fields[11]) if len(fields) > 11 else '',
    )


def _mood_record(text: str) -> None:
    fields = decode_percent(text).split(FIELD_SEP) if text else []
    kind = fields[0] if fields else ''
    reading = _state.mood_reading
    if kind == 'M' and len(fields) >= 6:
        _state.mood_reading = _MoodReading(
            seq=fields[1],
            mood=fields[3],
            name=_mood_text(fields[4]),
            description=_mood_text(fields[5]),
        )
    elif kind == 'B' and len(fields) >= 11:
        if reading is None or fields[1] != reading.seq or len(reading.rows) >= _MOOD_LIMIT:
            return
        moodlet = _moodlet(fields)
        if moodlet.name:
            reading.rows.append(moodlet)
    elif kind == 'E' and len(fields) >= 2:
        if reading is None or fields[1] != reading.seq:
            return
        _state.mood_reading = None
        final = _mood_speech(reading)
        speech.speak(final or lang.t('mood unavailable'), source='widget', explicit=True)
    elif not text:
        _state.mood_reading = None
        speech.speak(lang.t('mood unavailable'), explicit=True)
    else:
        simsight_log.line('mood: ignored a malformed record')


@live_command('simsight.mood_enc', 'a11y.mood_enc')
def a11y_mood_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: mood_enc'):
        _mood_record(text)
        return True
    return False


@live_command('simsight.speak_enc', 'a11y.speak_enc')
def a11y_speak_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: speak_enc'):
        # A real tooltip beats any placeholder name we derived from the control.
        _cancel_pending_name()
        final = _process_or_raw(decode_percent(text), 'tooltip')
        head = _take_row_head()
        delay = _tooltip_delay()
        if head is not None and delay > 0:
            # The row already said name and value; hold its description back.
            speech.speak_progressive(head, final, source='tooltip', delay=delay, head_spoken=True)
            return True
        speech.speak(final, source='tooltip')
        return True
    return False


@live_command('simsight.widget_enc', 'a11y.widget_enc')
def a11y_widget_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: widget_enc'):
        # A real widget line beats any derived-name placeholder still waiting
        # (e.g. the purchase picker's +/- buttons: the generic path posts their
        # bare instance name, then our cell patch posts the full
        # "Increase. <item>. <count>" line; without this, the parked name
        # flushed 0.4 s later and talked over the real announcement).
        _cancel_pending_name()
        final = _process_or_raw(decode_percent(text), 'widget')
        # This row spoke for itself, so a description arriving behind it can be
        # deferred; it also supersedes any older row still waiting.
        _arm_row_head(final)
        speech.speak(final, source='widget')
        return True
    return False


@live_command('simsight.focus_enc', 'a11y.focus_enc')
def a11y_focus_enc(text: str = '', _connection: Any = None) -> bool:
    """The settled keyboard target supersedes an older pending row."""
    with _boundary('narrator: focus_enc'):
        _cancel_pending_name()
        final = _process_or_raw(decode_percent(text), 'widget')
        _arm_row_head(final)
        speech.speak(final, source='widget', explicit=speech.modes.speech == 'hover')
        return True
    return False


@live_command('simsight.context_enc', 'a11y.context_enc')
def a11y_context_enc(text: str = '', _connection: Any = None) -> bool:
    """Log owner transitions without speaking; distinguish loaded from open.
    The ':DD' suffix is the same owner state simsight.modalstate carries; it is
    applied too, so one lost post cannot strand keyboard routing. A third
    digit is the owner's blocking bit (phone guard), applied with the
    owner as one report; the direct simsight.modalstate stays two digits."""
    with _boundary('narrator: context_enc'):
        decoded = decode_percent(text)
        simsight_log.line('UI owner: ' + decoded[:300])
        suffix = decoded.rsplit(':', 1)[-1] if ':' in decoded else ''
        if suffix[:2] in ('00', '01', '10', '11') and suffix[2:] in ('', '0', '1'):
            try:
                import simsight_keys as keys

                keys.apply_modal_state(suffix, 'context')
            except Exception:
                simsight_log.error('context: modal state not applied')
        return True
    return False


# ------------------------------------------------------------------ inspection


def _log_inventory_item(obj: Any) -> None:
    try:
        definition = getattr(obj, 'definition', None)
        simsight_log.line(
            'Inventory inspect: id={} definition={} class={} catalog={} hover={} hidden={}'.format(
                obj.id,
                getattr(definition, 'id', None),
                type(obj).__name__,
                getattr(obj, 'catalog_name', None),
                getattr(obj, 'hover_tip', None),
                obj.is_hidden(),
            )
        )
    except (AttributeError, TypeError) as ex:  # not shaped like an inventory object
        simsight_log.line(f'Inventory inspect: item unavailable {ex!r}')


@live_command('simsight.inspect_inventory', 'a11y.inspect_inventory')
def a11y_inspect_inventory(_connection: Any = None) -> bool:
    """Log read-only item identities/tooltip flags; never request an action."""
    with _boundary('narrator: inspect_inventory'):
        import services

        output = sims4.commands.CheatOutput(_connection)
        sim = services.get_active_sim()
        inventory = getattr(sim, 'inventory_component', None)
        if inventory is None:
            output('No active Sim inventory.')
            return False
        count = 0
        # Inventory iteration also includes hidden storage. Log the visibility
        # flag, and bound this diagnostic independently of stack sizes.
        for obj in islice(iter(inventory), 100):
            count += 1
            _log_inventory_item(obj)
        output(f'Inventory snapshot: {count} entries (limit 100); see SimSight log.')
        return True
    return False


@live_command('simsight.inspect_ui', 'a11y.inspect_ui')
def a11y_inspect_ui(delay: float = 0, _connection: Any = None) -> bool:
    """Read-only snapshot, optionally delayed to inspect a console-blocking modal."""
    with _boundary('narrator: inspect_ui'):
        import simsight_keys as keys

        if _state.inspect_timer is not None:
            _state.inspect_timer.cancel()
            _state.inspect_timer = None
        delay = max(0.0, min(60.0, delay))
        if delay:
            # _send only queues work; the timer never calls game APIs directly.
            timer = threading.Timer(delay, keys.send_action, args=('A11yInspect',))
            timer.daemon = True
            timer.start()
            _state.inspect_timer = timer
        else:
            keys.send_action('A11yInspect')
        sims4.commands.CheatOutput(_connection)(
            f'Keyboard focus snapshot in {delay:.0f} seconds; see SimSight log.'
        )
        return True
    return False


@live_command('simsight.inspect_enc', 'a11y.inspect_enc')
def a11y_inspect_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: inspect_enc'):
        for line in decode_percent(text)[:16000].splitlines():
            simsight_log.line('UI inspect: ' + line)
        return True
    return False


# ------------------------------------------------------------------ dialogs and names


@live_command('simsight.scope_enc', 'a11y.scope_enc')
def a11y_scope_enc(text: str = '', _connection: Any = None) -> bool:
    """Opening description and first control form one uninterrupted utterance."""
    with _boundary('narrator: scope_enc'):
        _cancel_pending_name()
        parts = decode_percent(text).split('\x02', 2)
        if len(parts) != 3:
            return False
        kind, intro, focus = parts
        intro = _process(intro, 'dialog') if intro else ''
        focus = _process(focus, 'widget') if focus else ''
        if kind == 'dialog' and not settings.brief():
            intro = '{}. {}'.format(lang.t('dialog'), intro)
        line = '. '.join(part for part in (intro, focus) if part)
        _arm_row_head(line)
        speech.speak(line, source='widget')
        return True
    return False


@live_command('simsight.dialog_enc', 'a11y.dialog_enc')
def a11y_dialog_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: dialog_enc'):
        body = _process_or_raw(decode_percent(text), 'dialog')
        # Dialogs demand an answer, so they interrupt and are labelled as such
        # (unless verbosity is brief).
        speech.speak(
            body if settings.brief() else '{}. {}'.format(lang.t('dialog'), body),
            source='widget',
        )
        return True
    return False


@live_command('simsight.menuopen', 'a11y.menuopen')
def a11y_menuopen(count: str = '0', _connection: Any = None) -> bool:
    with _boundary('narrator: menuopen'):
        try:
            n = int(count)
        except ValueError:
            n = 0
        count_text = '{} {}.'.format(n, lang.t('options'))
        speech.speak(
            count_text if settings.brief() else '{}. {}'.format(lang.t('menu open'), count_text),
            source='widget',
        )
        try:
            import simsight_scanner as scanner
        except ImportError:
            return True  # no scanner in this build: nothing to tell it
        scanner.note_menu_open()
        return True
    return False


@live_command('simsight.stop', 'a11y.stop')
def a11y_stop(
    arg: str = '',  # noqa: ARG001 - Flash may post one; the stop needs none
    _connection: Any = None,
) -> bool:
    with _boundary('narrator: stop'):
        # Posted by Flash when a touched control has no text at all. Soft stop:
        # it must not cut down a row label that just started (see request_hush).
        if _state.rawdiag:
            simsight_log.line('RAWDIAG[stop]')
        speech.request_hush()
        return True
    return False


def _control_name(decoded: str) -> str:
    """Words from a control's instance identifier ("ddProfitMargin" -> "Profit Margin")."""
    # Instance identifiers arrive camel-cased ("BackgroundColorSelector");
    # split into words. English dev names, fallback for unlabeled controls.
    decoded = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', decoded)
    # Drop the control-type prefix EA's instance names carry ("dd Profit
    # Margin" was heard for the retail markup dropdown, 2026-10-04).
    decoded = re.sub(r'^(?:txt|mc|btn|dd|cb|sl|lb)\s+(?=[A-Z])', '', decoded)
    return re.sub(r'[_\d]+', ' ', decoded).strip()


@live_command('simsight.widgetname_enc', 'a11y.widgetname_enc')
def a11y_widgetname_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: widgetname_enc'):
        # An icon-only control: its tooltip is the only text it has, so it must
        # never be deferred as if it were a data row's description.
        _state.row_head = None
        decoded = decode_percent(text).strip()
        if _state.rawdiag:
            simsight_log.line('RAWDIAG[widgetname]: ' + ascii(decoded))
        name = _control_name(decoded)
        # Touching a textless control silences STALE speech, but must not cut a
        # row label that just started (see speech.request_hush).
        speech.request_hush()
        if not name:
            return True
        with _name_lock:
            _state.pending_name = name
        if _state.pending_name_timer is not None:
            _state.pending_name_timer.cancel()
        timer = threading.Timer(_NAME_GRACE_SECONDS, _flush_pending_name)
        timer.daemon = True
        timer.start()
        _state.pending_name_timer = timer
        return True
    return False


@live_command('simsight.event_enc', 'a11y.event_enc')
def a11y_event_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: event_enc'):
        speech.speak(_process_or_raw(decode_percent(text), 'event'), source='event')
        return True
    return False


# Sim-action announcements are an optional feature: the handler always exists
# (so the Flash side can never hit an unknown command), but it only speaks
# when the option is on. Toggled with simsight.simactions, persisted in settings.
def _sim_actions_on() -> bool:
    return bool(settings.get('sim_actions'))


@live_command('simsight.simactions', 'a11y.simactions')
def a11y_simactions(mode: str = '', _connection: Any = None) -> bool:
    """simsight.simactions on|off: announce what the active Sim starts doing."""
    with _boundary('narrator: simactions'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower()
        if m in ('on', 'true', '1'):
            settings.set('sim_actions', True)
            output('simsight: Sim action announcements ON')
            speech.speak('Sim action announcements on.', explicit=True)
        elif m in ('off', 'false', '0'):
            settings.set('sim_actions', False)
            output('simsight: Sim action announcements OFF')
            speech.speak('Sim action announcements off.', explicit=True)
        else:
            output(
                'simsight: Sim action announcements are {}'.format(
                    'ON' if _sim_actions_on() else 'OFF'
                )
            )
            output('simsight: usage simsight.simactions on|off')
        return True
    return False


@live_command('simsight.simaction_enc', 'a11y.simaction_enc')
def a11y_simaction_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: simaction_enc'):
        if not _sim_actions_on():
            return True
        speech.speak(_process_or_raw(decode_percent(text), 'simaction'), source='event')
        return True
    return False


# ------------------------------------------------------------------ Flash switches


def _send_ui_message(message: str) -> None:
    import distributor.ops
    import distributor.system

    distributor.system.Distributor.instance().add_op_with_no_owner(
        distributor.ops.SendUIMessage(message)
    )


def _push_autofocus() -> None:
    """Synchronize the keyboard master switch with Flash on load and mark.
    Flash starts with capture off until Python supplies the user's setting.
    The old auto_focus cfg value is retained only for file compatibility."""
    try:
        state = bool(settings.get('keyboard', True))
        _send_ui_message('A11yKeyboardOn' if state else 'A11yKeyboardOff')
    except Exception:
        simsight_log.error('autofocus push')
    push_simplified_menus()


def push_simplified_menus() -> bool:
    """Tell every live UI copy the Simplified menus switch. Flash starts with
    it on (the default) until Python says otherwise, so it goes out with the
    keyboard switch at zone load, mark and reload, on a settings change and
    with every keyboard lease. Flash acts only when the value changes."""
    try:
        state = bool(settings.get('simplified_menus', True))
        _send_ui_message('A11y|simplifiedmenus|' + ('on' if state else 'off'))
    except Exception:
        simsight_log.error('simplified menus notice')
        return False
    return True


@live_command('simsight.autofocus', 'a11y.autofocus')
def a11y_autofocus(mode: str = '', _connection: Any = None) -> bool:
    """Legacy alias for simsight.keys; focus now follows the keyboard setting."""
    with _boundary('narrator: autofocus'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower()
        if m in ('on', 'true', '1', 'off', 'false', '0'):
            import simsight_keys as keys

            output('simsight: automatic focus is part of keyboard navigation; use simsight.keys.')
            return bool(keys.a11y_keys(m, _connection=_connection))
        output(
            'simsight: keyboard navigation and automatic focus are {}'.format(
                'ON' if settings.get('keyboard', True) else 'OFF'
            )
        )
        output('simsight: use simsight.keys on|off')
        return True
    return False


# Speech mode names from earlier builds.
_LEGACY_SPEECH_MODES = {'talk': 'hover', 'ondemand': 'manual', 'on_demand': 'manual'}


@live_command('simsight.speechmode', 'a11y.speechmode')
def a11y_speechmode(mode: str = '', _connection: Any = None) -> bool:
    """simsight.speechmode hover|manual|beeps|off (also cycled by the speech
    mode key, default Numpad 9 or Insert)."""
    with _boundary('narrator: speechmode'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower().replace('-', '_').replace(' ', '_')
        m = _LEGACY_SPEECH_MODES.get(m, m)
        if m in speech.VALID_MODES:
            speech.set_mode(m)
            output(f'simsight: speech mode {m}')
        else:
            output(f'simsight: speech mode is {speech.modes.speech}')
            output('simsight: usage simsight.speechmode hover|manual|beeps|off')
        return True
    return False


def _set_tooltip_delay(raw: str, output: Output) -> bool:
    if raw in ('off', 'none'):
        value = 0.0
    else:
        try:
            value = float(raw)
        except ValueError:
            output('simsight: usage simsight.tooltipdelay <seconds|off>')
            return False
    value = max(0.0, min(10.0, value))
    settings.set('tooltip_delay', value)
    if value <= 0:
        output('simsight: tooltip delay OFF (descriptions read immediately)')
        speech.speak('Tooltip delay off.', explicit=True)
    else:
        output(f'simsight: tooltip delay {value:g} seconds')
        speech.speak(f'Tooltip delay {value:g} seconds.', explicit=True)
    return True


def _delay_text(delay: float) -> str:
    return 'off' if delay <= 0 else f'{delay:g} seconds'


@live_command('simsight.tooltipdelay', 'a11y.tooltipdelay')
def a11y_tooltipdelay(seconds: str = '', _connection: Any = None) -> bool:
    """simsight.tooltipdelay <seconds|off>: how long a panel row (needs, skills,
    mood, the time panel) holds its description back after reading its name
    and value. Moving the pointer on cancels it; the read key still reads
    everything. off (or 0) reads descriptions immediately."""
    with _boundary('narrator: tooltipdelay'):
        output = sims4.commands.CheatOutput(_connection)
        raw = seconds.strip().lower()
        if raw:
            return _set_tooltip_delay(raw, output)
        output(f'simsight: tooltip delay is {_delay_text(_tooltip_delay())}')
        output('simsight: usage simsight.tooltipdelay <seconds|off>')
        return True
    return False


# ------------------------------------------------------------------ voice


def _voice_number(sub: str, text: str, output: Output) -> bool:
    try:
        value = int(text)
    except ValueError:
        output(f'simsight: {sub} must be a number')
        return False
    if sub == 'rate':
        value = max(-10, min(10, value))
        settings.set('sapi_rate', value)
    else:
        value = max(0, min(100, value))
        settings.set('sapi_volume', value)
    speech.restart_sapi()
    output(f'simsight: SAPI {sub} set to {value}')
    speech.speak(f'Voice {sub} {value}.', explicit=True)
    return True


def _voice_name(name: str, output: Output) -> bool:
    settings.set('sapi_voice', name)
    speech.restart_sapi()
    output(
        f'simsight: SAPI voice set to "{name}" (partial name, not case-'
        'sensitive: david, zira or hazel; default voice if nothing '
        'matches)'
    )
    speech.speak('Voice changed.', explicit=True)
    return True


def _voice_list(output: Output) -> bool:
    names = speech.list_voices()
    if not names:
        output('simsight: could not list voices')
        return True
    for name in names:
        output(f'simsight: voice: {name}')
    speech.speak('{} voices installed. {}'.format(len(names), '. '.join(names)), explicit=True)
    return True


def _voice_reset(output: Output) -> bool:
    for key in ('sapi_rate', 'sapi_volume', 'sapi_voice'):
        settings.set(key, settings.DEFAULTS[key])
    speech.restart_sapi()
    output('simsight: SAPI voice options reset')
    speech.speak('Voice reset.', explicit=True)
    return True


def _sapi_summary() -> str:
    return 'simsight: SAPI fallback voice: rate {}, volume {}, name "{}"'.format(
        settings.get('sapi_rate'),
        settings.get('sapi_volume'),
        settings.get('sapi_voice') or 'default',
    )


def _voice_usage(output: Output) -> bool:
    output(_sapi_summary())
    output(
        'simsight: these only apply without a screen reader; screen reader '
        'users set voice/rate/volume in their screen reader'
    )
    output(
        'simsight: usage simsight.voice rate <-10..10> | volume <0..100> | '
        'name <voice> | list | reset'
    )
    return True


def _voice(parts: list[str], output: Output) -> bool:
    sub = parts[0].lower() if parts else ''
    if sub in ('rate', 'volume') and len(parts) >= 2:
        return _voice_number(sub, parts[1], output)
    if sub == 'name' and len(parts) >= 2:
        return _voice_name(' '.join(parts[1:]), output)
    if sub == 'list':
        return _voice_list(output)
    if sub == 'reset':
        return _voice_reset(output)
    return _voice_usage(output)


@live_command('simsight.voice', 'a11y.voice')
def a11y_voice(*args: Any, _connection: Any = None) -> bool:
    """simsight.voice rate <-10..10> | volume <0..100> | name <voice name> |
    list | reset: SAPI fallback voice only. Screen reader users control
    voice, rate and volume in their own screen reader settings."""
    with _boundary('narrator: voice'):
        output = sims4.commands.CheatOutput(_connection)
        return _voice([str(part) for part in args], output)
    return False


# ------------------------------------------------------------------ mark and zone


@live_command('simsight.mark', 'a11y.mark')
def a11y_mark(name: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: mark'):
        simsight_log.line(f'MARK: live TooltipManager copy is from "{name}"')
        try:
            import simsight_startup as startup

            startup.note_mark(name)
        except Exception:
            simsight_log.error('narrator: startup mark note')
        _push_autofocus()
        return True
    return False


def _install_zone_hook() -> None:
    """Push Flash-side config at every zone load: the only reliably safe
    moment for SendUIMessage, and it re-applies after zone switches."""
    import zone

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def a11y_narrator_on_loading_screen_animation_finished(
            self: Zone, *args: Any, **kwargs: Any
        ) -> Any:
            result = original(self, *args, **kwargs)
            try:
                _push_autofocus()
            except Exception:
                simsight_log.error('autofocus zone push')
            return result

        return a11y_narrator_on_loading_screen_animation_finished

    simsight_patch.wrap(zone.Zone, 'on_loading_screen_animation_finished', make)


try:
    _install_zone_hook()
except Exception:
    simsight_log.error('narrator zone hook install')


# ------------------------------------------------------------------ console


@live_command('simsight.speak', 'a11y.speak')
def a11y_speak(*args: Any, _connection: Any = None) -> bool:
    with _boundary('narrator: speak'):
        text = ' '.join(str(part) for part in args).strip()
        if text:
            speech.speak(text, explicit=True)
        return True
    return False


@live_command('simsight.test', 'a11y.test')
def a11y_test(_connection: Any = None) -> bool:
    with _boundary('narrator: test'):
        output = sims4.commands.CheatOutput(_connection)
        backend = speech.init()
        speech.speak(f'Narration test successful. The active backend is {backend}.', explicit=True)
        output(f'simsight: spoke test sentence via backend "{backend}"')
        output(f'simsight: log file at {simsight_log.PATH}')
        return True
    return False


_BRAILLE_MODES = ('concise', 'full', 'only', 'off')


@live_command('simsight.braille', 'a11y.braille')
def a11y_braille(mode: str = '', _connection: Any = None) -> bool:
    """simsight.braille <concise|full|only|off>"""
    with _boundary('narrator: braille'):
        output = sims4.commands.CheatOutput(_connection)
        if mode.lower() not in _BRAILLE_MODES:
            output(f'a11y braille mode: {speech.modes.braille}')
            output(
                'a11y braille display detected: {}'.format('yes' if speech.has_braille() else 'no')
            )
            output('simsight: usage simsight.braille <{}>'.format('|'.join(_BRAILLE_MODES)))
            output('   concise = speak everything, braille the label')
            output('   full    = speak and braille everything')
            output('   only    = braille only, no speech')
            output('   off     = speech only')
            return True
        speech.modes.braille = mode.lower()
        settings.set('braille_mode', speech.modes.braille)
        output(f'simsight: braille mode set to {speech.modes.braille}')
        speech.speak(f'Braille mode {speech.modes.braille}.', explicit=True)
        return True
    return False


@live_command('simsight.log', 'a11y.log')
def a11y_log(mode: str = '', _connection: Any = None) -> bool:
    """simsight.log on|off: utterance/diagnostic log next to the ts4script."""
    with _boundary('narrator: log'):
        output = sims4.commands.CheatOutput(_connection)
        m = mode.lower()
        if m in ('on', 'true', '1'):
            simsight_log.enabled = True
            settings.set('logging', True)
            simsight_log.line('logging enabled via simsight.log')
            output(f'simsight: logging ON -> {simsight_log.PATH}')
            speech.speak('Logging on.', explicit=True)
        elif m in ('off', 'false', '0'):
            simsight_log.line('logging disabled via simsight.log')
            simsight_log.enabled = False
            settings.set('logging', False)
            output('simsight: logging OFF')
            speech.speak('Logging off.', explicit=True)
        else:
            output(
                'simsight: logging is {} (log file: {})'.format(
                    'ON' if simsight_log.enabled else 'OFF', simsight_log.PATH
                )
            )
            output('simsight: usage simsight.log on|off')
        return True
    return False


def _reload_speech(output: Output) -> None:
    """Apply the re-read speech, braille, caption and logging options."""
    if not settings.get('captions', False):
        speech.clear_caption()
    simsight_log.enabled = bool(settings.get('logging', simsight_log.enabled))
    braille = str(settings.get('braille_mode', speech.modes.braille))
    if braille in _BRAILLE_MODES:
        speech.modes.braille = braille
    mode = str(settings.get('speech_mode', 'hover'))
    if mode in speech.VALID_MODES:
        speech.modes.speech = mode
    speech.restart_sapi()
    if speech.retry_screen_reader():
        output('simsight: screen reader detected; speech now routes through it')


def _reload_modules() -> None:
    """Apply the re-read keyboard, object name, travel, Build and scanner options."""
    try:
        import simsight_keys as keys

        keys.status.enabled = bool(settings.get('keyboard', True))
        if keys.status.enabled:
            keys.start()
    except ImportError:
        pass  # module not in this build
    try:
        import simsight_objectnames as objectnames

        objectnames.enable(bool(settings.get('object_names', True)))
    except ImportError:
        pass  # module not in this build
    _push_autofocus()
    try:
        import simsight_travel as travel

        travel.apply_setting()
    except Exception:
        simsight_log.error('reload: travel list notice')
    try:
        import simsight_build as build

        build.apply_setting()
    except Exception:
        simsight_log.error('reload: build controls notice')
    try:
        import simsight_keys as keys

        keys.apply_scanner_setting()
    except Exception:
        simsight_log.error('reload: scanner mode notice')


@live_command('simsight.reload', 'a11y.reload')
def a11y_reload(_connection: Any = None) -> bool:
    """Re-read SimSight.cfg after a hand-edit and apply it live."""
    with _boundary('narrator: reload'):
        output = sims4.commands.CheatOutput(_connection)
        settings.reload()
        _reload_speech(output)
        _reload_modules()
        for err in settings.load_errors:
            output('simsight: warning: ' + err)
        output(f'simsight: settings reloaded from {settings.PATH}')
        if settings.load_errors:
            speech.speak(
                'Settings reloaded with {} warnings. {}'.format(
                    len(settings.load_errors), ' '.join(settings.load_errors)
                ),
                explicit=True,
            )
        else:
            speech.speak('Settings reloaded.', explicit=True)
        return True
    return False


@live_command('simsight.captions', 'a11y.captions')
def a11y_captions(mode: str = '', _connection: Any = None) -> bool:
    """simsight.captions [on|off] - display accepted narration on screen."""
    with _boundary('narrator: captions'):
        output = sims4.commands.CheatOutput(_connection)
        mode = mode.lower()
        if mode not in ('', 'on', 'off'):
            output('Usage: simsight.captions [on|off]')
            return False
        if mode:
            settings.set('captions', mode == 'on')
            if mode == 'off':
                speech.clear_caption()
        output('simsight: captions {}'.format('on' if settings.get('captions') else 'off'))
        return True
    return False


# simsight.settings: (spoken label, setting key, default) of the plain switches.
_SETTING_SWITCHES = (
    ('scanner', 'scanner', True),
    ('captions', 'captions', False),
    ('startup speech', 'startup', True),
    ('simplified menus', 'simplified_menus', True),
    ('travel list', 'travel_list', True),
    ('Build controls', 'build_controls', None),
    ('scanner mode', 'scanner_mode', False),
)


def _settings_lines() -> tuple[list[str], list[str]]:
    """(spoken option lines, console key binding lines) for simsight.settings."""
    lines = [f'Sim actions {_on_off(_sim_actions_on())}']
    try:
        import simsight_objectnames as objectnames

        lines.append(f'object names {_on_off(objectnames.enabled())}')
    except ImportError:
        lines.append('object names unavailable')
    lines.extend(
        f'{label} {_on_off(settings.get(key, default))}'
        for label, key, default in _SETTING_SWITCHES
    )
    keybind_lines = []
    try:
        import simsight_keys as keys

        lines.append(f'keyboard navigation {_on_off(keys.status.enabled)}')
        for label, combos in keys.BINDINGS.values():
            keybind_lines.append(
                '   {}: {}'.format(
                    label, ' or '.join(keys.describe_key(vk, alt) for (vk, alt) in combos)
                )
            )
    except ImportError:
        lines.append('keyboard navigation unavailable')
    lines.append('speech mode {}'.format(speech.modes.speech.replace('_', ' ')))
    lines.append(f'tooltip delay {_delay_text(_tooltip_delay())}')
    lines.append('auto focus {}'.format(_on_off(settings.get('auto_focus'))))
    lines.append(f'braille mode {speech.modes.braille}')
    lines.append('verbosity {}'.format(settings.get('verbosity')))
    lines.append(f'logging {_on_off(simsight_log.enabled)}')
    return lines, keybind_lines


@live_command('simsight.settings', 'a11y.settings')
def a11y_settings(_connection: Any = None) -> bool:
    """Read out every option and its current live value."""
    with _boundary('narrator: settings'):
        output = sims4.commands.CheatOutput(_connection)
        lines, keybind_lines = _settings_lines()
        for line in lines:
            output('simsight: ' + line)
        for line in keybind_lines:
            output(line)
        output(_sapi_summary())
        output(f'simsight: settings file: {settings.PATH}')
        for err in settings.load_errors:
            output('simsight: warning: ' + err)
        speech.speak('. '.join(lines) + '.', explicit=True)
        return True
    return False


@live_command('simsight.status', 'a11y.status')
def a11y_status(_connection: Any = None) -> bool:
    with _boundary('narrator: status'):
        output = sims4.commands.CheatOutput(_connection)
        output(f'a11y backend: {speech.backend_name()}')
        output(
            'a11y screen reader: {}'.format(speech.screen_reader_name() or 'none; Windows fallback')
        )
        output(
            'a11y speech pack: {}'.format(
                'installed'
                if speech.speech_pack_installed()
                else 'NOT installed (separate download; screen readers and braille '
                'need it, the Windows voice does not)'
            )
        )
        output(
            'a11y Prism DLL expected at: {} (present: {})'.format(
                speech.PRISM_DLL_PATH,
                'yes' if os.path.isfile(speech.PRISM_DLL_PATH) else 'no',
            )
        )
        output(
            'a11y braille: display {} / mode {}'.format(
                'supported by reader' if speech.has_braille() else 'not available',
                speech.modes.braille,
            )
        )
        output(
            'a11y log: {} ({})'.format('ON' if simsight_log.enabled else 'OFF', simsight_log.PATH)
        )
        return True
    return False

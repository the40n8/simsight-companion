"""The mood panel reading (Control Alt E): one record per
simsight.mood_enc."""

from __future__ import annotations

from typing import Any

import simsight_lang as lang
import simsight_log
import simsight_speech as speech

from .base import _boundary, live_command
from .decode import _clean_glyphs, decode_percent
from .state import _MOOD_LIMIT, _MoodReading, _Moodlet, _state
from .fields import FIELD_SEP


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

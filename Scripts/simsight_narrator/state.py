"""What the narrator remembers between payloads: the row line whose
description is deferred, the mood reading, the pending derived name."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import simsight_settings as settings
import simsight_speech as speech

from .base import _boundary


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

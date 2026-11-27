"""The Windows key poller (its own thread): presses, repeats and releases
of bare keys and chords."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Any

import simsight_log

from .bindings import (
    BINDINGS,
    BUILD_GRID_KEYS,
    CHORD_NAMES,
    CHORDS,
    NUMPAD_BINDINGS,
    REPEAT_DELAY,
    REPEAT_INTERVAL,
    REPEATABLE,
    VK_CONTROL,
    VK_MENU,
    VK_SHIFT,
)
from .chords import repeat_chord, run_chord
from .hook import (
    _hook,
    _hook_edges,
    _hook_state,
    _hook_trace,
    _mask_swallowed_releases,
    _swallowed_snapshot,
    _write_hook_trace,
)
from .plain import plain_message
from .scannermode import announce_ui_settings
from .send import send_action
from .state import _ACTIVATION_KEYS, _opening_keys, _post, _state, status
from .winapi import _altgr_pressed, _dlls, _game_is_foreground, _key_reader, fence_opening_input

if TYPE_CHECKING:
    KeyId = tuple[str, int]  # ('chord' or 'plain', vk) in the poller's tables

_POLL_SECONDS = 0.01


def _log_fire(intent: str, source: str) -> None:
    """One line per chord press (repeats excluded). The route line is
    throttled to one per second, which hides a double or a lost press;
    this one is not. source: edge (the hook saw the press), hook (the
    hook's key state), held (Windows' key state) or tap (a press bit)."""
    simsight_log.line(f'keys: chord {intent} fired ({source})')


@dataclass
class _Sample:
    """One poll pass: the modifiers and every key, read back to back."""

    now: float
    epoch: int  # the input epoch when the pass began
    hook_known_before: frozenset[int]  # keys the hook had reported before the read
    ctrl: bool
    alt: bool
    shift: bool
    shift_tapped: bool
    sampled: dict[int, int]  # vk -> GetAsyncKeyState, swallowed releases masked
    chord: bool  # Ctrl+Alt held, no Shift, not AltGr typing
    chord_end: bool  # diagnostic: Ctrl+Alt still held after the read
    tracing: bool
    hook_alive: bool


class _Poller:
    """The Windows key poller (its own thread): presses, repeats, releases."""

    def __init__(self, user32: Any, kernel32: Any) -> None:
        self.user32 = user32
        self.kernel32 = kernel32
        self.read_key = _key_reader(user32)
        self.was_down: dict[KeyId, bool] = {}
        self.pressed_at: dict[KeyId, float] = {}
        self.last_fire: dict[KeyId, float] = {}
        self.recovering_focus = False
        self.focus_blocked: set[int] = set()
        self.editing_prev = False
        self.last_lease = 0.0
        plain = {vk for _label, combos in BINDINGS.values() for vk, _alt in combos}
        plain.update(NUMPAD_BINDINGS)
        plain.update(BUILD_GRID_KEYS)
        plain.add(0x4E)
        self.plain = plain
        self.all_keys = plain | set(CHORDS)
        self.chord_seen_at = -10.0
        self.shift_before = False

    def run(self) -> None:
        simsight_log.line('keys: contextual polling started')
        while _state.running:
            if self._safe_poll():
                time.sleep(_POLL_SECONDS)
        simsight_log.line('keys: polling stopped')

    def _safe_poll(self) -> bool:
        """One pass; False when it already waited (the game is not in front)."""
        try:
            return self.poll()
        except Exception:
            simsight_log.error('keys: poll error')
            time.sleep(1.0)
            return True

    def poll(self) -> bool:
        if not status.enabled or not _game_is_foreground(self.user32, self.kernel32):
            self._focus_left()
            time.sleep(0.05)
            return False
        now = time.monotonic()
        # A text field that just closed (cheat console on Enter) leaves the
        # submitting key's tap latch behind; if this poll lands after Flash
        # clears the editing flag, that tap would activate whatever we focus
        # next (settings row, HUD button). Retire it once, here.
        editing_ended = self.editing_prev and not status.editing_active
        self.editing_prev = status.editing_active
        if editing_ended:
            fence_opening_input()
        self._lease(now)
        sample = self._sample(now)
        if self.recovering_focus:
            self._recover_focus(sample)
        self._hook_edges(now)
        for vk, (intent, repeatable) in CHORDS.items():
            self._chord_key(sample, vk, intent, repeatable)
        for vk in self.plain:
            self._plain_key(sample, vk, editing_ended)
        return True

    def _focus_left(self) -> None:
        """Release native guards when focus leaves the game. Never replay
        background keystrokes on return."""
        held = [
            CHORD_NAMES.get(vk, hex(vk))
            for (kind, vk), down in self.was_down.items()
            if kind == 'chord' and down
        ]
        if held and status.enabled:
            # Diagnostic: a key still held when focus returns counts
            # as a new press (an unexplained double toggle, 2026-09-26).
            simsight_log.line(
                'keys: focus left the game with chord key {} held'.format('+'.join(sorted(held)))
            )
        for vk in self.plain:
            if self.was_down.get(('plain', vk), False):
                send_action(f'A11y|keyup|{vk}')
        self.was_down.clear()
        self.pressed_at.clear()
        self.last_fire.clear()
        _hook_edges.clear()
        _hook_trace.clear()
        _opening_keys.clear()
        self.recovering_focus = True

    def _lease(self, now: float) -> None:
        if now - self.last_lease < 0.5:
            return
        send_action('A11y|lease')
        try:
            _post(announce_ui_settings)
        except Exception:
            simsight_log.error('keys: settings heartbeat')
        self.last_lease = now

    def _sample(self, now: float) -> _Sample:
        # Snapshots first (the swallow snapshot takes a lock and may
        # wait), then the modifiers and every key back to back through
        # read_key. With ~50 GIL waits a pass, a chord key's press bit
        # could be read with Ctrl+Alt already down but seen up (live
        # 2026-09-26: 2 of 10 fast Ctrl+Alt+B lost). The burst is much
        # shorter, not atomic: Windows input can still change between
        # two calls, only the window is now microseconds.
        read_key = self.read_key
        epoch = _state.input_epoch
        swallowed_before = _swallowed_snapshot()
        hook_known_before = frozenset(_hook_state)
        alt = bool(read_key(VK_MENU) & 0x8000)
        ctrl = bool(read_key(VK_CONTROL) & 0x8000)
        shift_state = read_key(VK_SHIFT)
        shift = bool(shift_state & 0x8000)
        # A fast Shift+Tab can release Shift before this pass reads Tab's
        # press bit (live 2026-09-26: the game's first-person switch also
        # opened the HUD). A key seen only as a tap counts as shifted when
        # Shift was down at the last pass or was pressed since.
        shift_tapped = shift or self.shift_before or bool(shift_state & 1)
        self.shift_before = shift
        # Read each key once: GetAsyncKeyState's low bit records a short
        # tap since the last query and must not be drained by two loops.
        raw = {vk: read_key(vk) for vk in self.all_keys}
        chord = ctrl and alt and not shift and not _altgr_pressed(self.user32)
        sampled = _mask_swallowed_releases(raw, swallowed_before)
        # Diagnostic only, and only while the log is on: one pass makes
        # about 50 ctypes calls, each releasing the GIL, so under load it
        # may span a Ctrl or Alt change (live 2026-09-26, 2 of 5 fast
        # chords lost). A second modifier read after the sample shows
        # whether a press bit read with Ctrl+Alt up had them held a
        # moment later. It decides nothing: chords use the first read.
        tracing = simsight_log.enabled
        chord_end = tracing and (
            bool(read_key(VK_CONTROL) & 0x8000)
            and bool(read_key(VK_MENU) & 0x8000)
            and not (read_key(VK_SHIFT) & 0x8000)
            and not _altgr_pressed(self.user32)
        )
        if chord or chord_end:
            self.chord_seen_at = now
        if tracing:
            _write_hook_trace()
        else:
            _hook_trace.clear()
        hook_alive = _hook.handle is not None and (now - _hook.last_event) < 1.0
        return _Sample(
            now,
            epoch,
            hook_known_before,
            ctrl,
            alt,
            shift,
            shift_tapped,
            sampled,
            chord,
            chord_end,
            tracing,
            hook_alive,
        )

    def _recover_focus(self, sample: _Sample) -> None:
        """The first sample belongs to the focus boundary: neither its tap
        bits nor queued hook events prove an in-game press. Require held
        keys to be released before accepting them again."""
        _hook_edges.clear()
        self.focus_blocked.clear()
        for vk in self.all_keys:
            sample.sampled[vk] &= 0x8000
            held = (
                bool(_hook_state[vk])
                if sample.hook_alive and vk in _hook_state
                else bool(sample.sampled[vk] & 0x8000)
            )
            if held:
                self.focus_blocked.add(vk)
        self.recovering_focus = False

    def _hook_edges(self, now: float) -> None:
        """Chord presses and releases the hook saw between two samples."""
        while _hook_edges:
            vk, down, edge_time = _hook_edges.popleft()
            key = ('chord', vk)
            if vk in self.focus_blocked:
                if not down:
                    self.focus_blocked.discard(vk)
                self.was_down[key] = down
                continue
            if (
                down
                and vk not in _opening_keys
                and (not self.was_down.get(key, False) or key not in self.pressed_at)
            ):
                _log_fire(CHORDS[vk][0], 'edge')
                run_chord(CHORDS[vk][0])
                self.pressed_at[key] = edge_time
                self.last_fire[key] = now
            self.was_down[key] = down

    def _still_blocked(self, vk: int, key: KeyId, held: bool) -> bool:
        """A key held across the return of focus stays ignored until released."""
        if vk not in self.focus_blocked:
            return False
        if not held:
            self.focus_blocked.discard(vk)
            return False
        self.was_down[key] = True
        return True

    def _repeat_due(self, key: KeyId, now: float) -> bool:
        return (
            now - self.pressed_at.get(key, now) >= REPEAT_DELAY
            and now - self.last_fire.get(key, 0) >= REPEAT_INTERVAL
        )

    def _trace_late_tap(self, sample: _Sample, vk: int, raw_tap: bool) -> None:
        if (
            sample.tracing
            and raw_tap
            and not sample.chord
            and not status.editing_active
            and sample.now - self.chord_seen_at < 0.5
        ):
            simsight_log.line(
                f'keys: trace {CHORD_NAMES.get(vk, hex(vk))} press read with Ctrl+Alt up (held after the read: {int(sample.chord_end)})'
            )

    def _chord_key(self, sample: _Sample, vk: int, intent: str, repeatable: bool) -> None:
        key = ('chord', vk)
        now = sample.now
        raw_down = bool(sample.sampled[vk] & 0x8000)
        raw_tap = bool(sample.sampled[vk] & 1)
        hook_knows_key = sample.hook_alive and vk in _hook_state
        if sample.chord and raw_down and _hook.handle is not None and not hook_knows_key:
            _hook.missed = now
        # A consumed key-up may not reach GetAsyncKeyState. Its
        # stale high bit must not restart a released scanner chord.
        held = bool(_hook_state[vk]) if hook_knows_key else raw_down
        if self._still_blocked(vk, key, held):
            return
        down = sample.chord and held
        # The transient hook may not be installed yet. Preserve a
        # complete letter/arrow tap while Ctrl+Alt are still held,
        # just as plain navigation preserves taps between polls.
        # A live hook's release wins over stale Windows state, but
        # only for a key the hook knew before this sample: a press
        # read before the hook consumed its release is still a press.
        # A fresh tap/observed hook edge can recover a stale held bit,
        # but the held bit alone cannot start a second shortcut.
        fresh_tap = raw_tap and not (hook_knows_key and vk in sample.hook_known_before)
        pressed = down or (sample.chord and fresh_tap)
        self._trace_late_tap(sample, vk, raw_tap)
        if (
            pressed
            and vk not in _opening_keys
            and (not self.was_down.get(key, False) or (fresh_tap and key not in self.pressed_at))
        ):
            _log_fire(intent, ('hook' if hook_knows_key else 'held') if down else 'tap')
            run_chord(intent)
            self.pressed_at[key] = now
            self.last_fire[key] = now
        elif down and repeatable and self._repeat_due(key, now):
            repeat_chord(intent)
            self.last_fire[key] = now
        # Modifier changes are not new letter/arrow presses. Keep
        # tracking the key while Ctrl+Alt are up; otherwise an old H
        # high bit becomes another Help shortcut on every chord.
        self.was_down[key] = held
        if not sample.chord:
            self.pressed_at.pop(key, None)
            self.last_fire.pop(key, None)

    def _plain_key_in_chord(self, sample: _Sample, vk: int, key: KeyId, held: bool) -> None:
        # Holding Enter after releasing Ctrl/Alt cannot activate twice.
        # A key released while modifiers remain held must also
        # release its native guard. A swallowed hook key-up can
        # leave GetAsyncKeyState stale, so a live hook wins.
        tapped = bool(sample.sampled[vk] & 1)
        chord_held = bool(_hook_state[vk]) if sample.hook_alive and vk in _hook_state else held
        if not chord_held and (self.was_down.get(key, False) or tapped):
            send_action(f'A11y|keyup|{vk}')
        self.was_down[key] = chord_held

    def _plain_key(self, sample: _Sample, vk: int, editing_ended: bool) -> None:
        key = ('plain', vk)
        now = sample.now
        held = bool(sample.sampled[vk] & 0x8000)
        tapped = bool(sample.sampled[vk] & 1)
        if self._still_blocked(vk, key, held):
            return
        if (
            vk in _ACTIVATION_KEYS
            and not held
            and (editing_ended or sample.epoch != _state.input_epoch)
        ):
            # Tap sampled across a surface change (editing ended, or a
            # fence bumped the epoch since sampling): retire it.
            self.was_down[key] = False
            return
        down = held or tapped
        message = plain_message(
            vk, sample.ctrl, sample.alt, sample.shift if held else sample.shift_tapped
        )
        if vk in _opening_keys:
            # Consume the opener's key and its release, then allow
            # a new physical press. Never synthesize another click.
            if not held:
                _opening_keys.discard(vk)
                send_action(f'A11y|keyup|{vk}')
            self.was_down[key] = held
            return
        if sample.chord:
            self._plain_key_in_chord(sample, vk, key, held)
            return
        if down and not self.was_down.get(key, False):
            if message:
                send_action(message)
            self.pressed_at[key] = now
            self.last_fire[key] = now
        elif held and message is not None and message in REPEATABLE and self._repeat_due(key, now):
            send_action(message)
            self.last_fire[key] = now
        if not held and (self.was_down.get(key, False) or tapped):
            send_action(f'A11y|keyup|{vk}')
        self.was_down[key] = held


def _loop() -> None:
    """The poller thread."""
    try:
        user32, kernel32 = _dlls()
        _Poller(user32, kernel32).run()
    except Exception:
        simsight_log.error('keys: poller thread')

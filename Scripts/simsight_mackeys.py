"""Keyboard on macOS: keys come from the native helper, not from ctypes.

On Windows simsight_keys reads the keyboard itself (a ctypes poller and
hook). The Mac game has no ctypes, so the speech helper (simsight-say)
captures the keys with system hot keys while The Sims 4 is the front app and
the bridge passes them here. This module feeds them into the same entry
points the Windows reader uses, on the game's tick (no threads):

- "chord": the Windows Ctrl+Alt+key (Mac: Control+Shift+key) -> keys._chord
- "needs": Ctrl+Shift+N (Mac: Control+Shift+N) -> A11yNeeds
- "plus":  the Windows Alt+key (Mac: Option+Shift+key) -> plus_core.HOTKEYS
- "plain": bare keys (arrows, Tab, Enter...) the mod owns right now
  (keys._plain_message decides, like the Windows reader) -> keys._send

The helper only takes bare keys the mod owns: grab_set() lists them and the
bridge forwards the list to the helper whenever it changes. Held keys repeat
with the Windows reader's timing, and a key-up is sent like on Windows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from typing import Callable

    HeldKey = tuple[str, int, bool]  # (family, vk, shift)

LEASE_SECONDS = 0.5

# Bare keys the mod may own (Windows VK codes), with Shift variants that
# have their own meaning (Shift+Tab, Shift+R in the Build cursor).
_CANDIDATES = [
    0x09,
    0x0D,
    0x20,
    0x08,
    0x1B,
    0x25,
    0x26,
    0x27,
    0x28,
    0x21,
    0x22,
    0x24,
    0x2E,
    0x52,
    0x4D,
    0x31,
    0x32,
    0x33,
    0x34,
    0x35,
    0x36,
]
_SHIFTED = [0x09, 0x52]


@dataclass
class _Held:
    """A held key that repeats: the message (plain) or intent (chord) it sends."""

    what: str
    pressed_at: float
    last_fire: float


@dataclass
class _Sent:
    """When the keyboard lease was last sent, and the last grab set sent."""

    lease_at: float = -1e9
    grab: list[str] | None = None


_held: dict[HeldKey, _Held] = {}
_sent = _Sent()


def reset() -> None:
    """Forget held keys and what was last sent: the next tick sends both again."""
    _held.clear()
    _sent.lease_at = -1e9
    _sent.grab = None


def _keys() -> Any:
    import simsight_keys as keys

    return keys


def _pump_alive() -> bool:
    try:
        import simsight_pump as pump

        return bool(pump.alive())
    except Exception:
        simsight_log.error('mackeys: pump state')
        return False


def _needs(keys: Any, key: HeldKey, now: float) -> bool:
    keys._send('A11yNeeds')
    return True


def _chord_key(keys: Any, key: HeldKey, now: float) -> bool:
    entry = keys.CHORDS.get(key[1])
    if entry is None:
        return False
    intent, repeats = entry
    keys._chord(intent)
    if repeats:
        _held[key] = _Held(intent, now, now)
    return True


def _plus_key(keys: Any, key: HeldKey, now: float) -> bool:
    import simsight_plus_core as core

    entry = core.HOTKEYS.get(key[1])
    if entry is None:
        return False
    fn = entry[0]
    try:
        if core._wants_shift(fn):
            fn(key[2])
        else:
            fn()
    except Exception:
        simsight_log.error(f'mackeys: {entry[1]}')
    return True


def _plain_key(keys: Any, key: HeldKey, now: float) -> bool:
    message = keys._plain_message(key[1], False, False, key[2])
    if message is None:
        return False
    keys._send(message)
    if message in keys._REPEATABLE:
        _held[key] = _Held(message, now, now)
    return True


_FAMILIES: dict[str, Callable[[Any, HeldKey, float], bool]] = {
    'needs': _needs,
    'chord': _chord_key,
    'plus': _plus_key,
    'plain': _plain_key,
}


def handle(family: str, vk: int, down: bool, shift: bool) -> bool:
    """One key event from the helper (simulation thread)."""
    keys = _keys()
    key = (family, vk, shift)
    if not down:
        _held.pop(key, None)
        if family == 'plain':
            keys._send(f'A11y|keyup|{vk}')
        return True
    handler = _FAMILIES.get(family)
    if handler is None:
        return False
    return handler(keys, key, time.monotonic())


def _repeat(now: float) -> None:
    keys = _keys()
    for (family, _vk, _shift), entry in list(_held.items()):
        if (
            now - entry.pressed_at < keys._REPEAT_DELAY
            or now - entry.last_fire < keys._REPEAT_INTERVAL
        ):
            continue
        entry.last_fire = now
        if family == 'chord':
            keys.repeat_chord(entry.what)
        else:
            keys._send(entry.what)


def grab_set() -> list[str]:
    """The bare keys the mod owns right now, as 'vk' or 'vk+shift' strings."""
    if not _pump_alive():
        return []
    keys = _keys()
    out = [
        str(vk) for vk in _CANDIDATES if keys._plain_message(vk, False, False, False) is not None
    ]
    out.extend(
        f'{vk}+shift' for vk in _SHIFTED if keys._plain_message(vk, False, False, True) is not None
    )
    return out


def _lease() -> None:
    """Keep Flash's keyboard guards on, and the UI copies' settings current."""
    keys = _keys()
    if keys.status.enabled and _pump_alive():
        keys._send('A11y|lease')
        try:
            import simsight_pump as pump

            pump.post(keys._announce_ui_settings)
        except Exception:
            simsight_log.error('mackeys: settings heartbeat')


def tick(send: Callable[[dict[str, Any]], object]) -> None:
    """Every game tick: key repeats, the keyboard lease Flash's guards need,
    and the bare-key set for the helper (sent through send when it changes)."""
    now = time.monotonic()
    if _held:
        _repeat(now)
    if now - _sent.lease_at < LEASE_SECONDS:
        return
    _sent.lease_at = now
    _lease()
    grab = grab_set()
    if grab != _sent.grab:
        _sent.grab = grab
        send({'grab': {'keys': grab}})


def focus_lost() -> None:
    """The game went to the background: release held keys (like Windows)."""
    keys = _keys()
    for family, vk, _shift in list(_held):
        if family == 'plain':
            keys._send(f'A11y|keyup|{vk}')
    _held.clear()

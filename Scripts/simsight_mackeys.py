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

import time

import simsight_speech as speech

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

_held = {}  # (family, vk, shift) -> [message or intent, pressed_at, last_fire]
_last_lease = -1e9
_last_grab = None


def _keys():
    import simsight_keys as keys

    return keys


def _log(message):
    speech.log_line('mackeys: ' + message)


def _pump_alive():
    try:
        import simsight_pump as pump

        return bool(pump.alive())
    except Exception:
        return False


def handle(family, vk, down, shift):
    """One key event from the helper (simulation thread)."""
    keys = _keys()
    key = (family, vk, shift)
    if not down:
        _held.pop(key, None)
        if family == 'plain':
            keys._send(f'A11y|keyup|{vk}')
        return True
    now = time.monotonic()
    if family == 'needs':
        keys._send('A11yNeeds')
        return True
    if family == 'chord':
        entry = keys.CHORDS.get(vk)
        if entry is None:
            return False
        intent, repeats = entry
        keys._chord(intent)
        if repeats:
            _held[key] = [intent, now, now]
        return True
    if family == 'plus':
        import simsight_plus_core as core

        entry = core.HOTKEYS.get(vk)
        if entry is None:
            return False
        fn = entry[0]
        try:
            if core._wants_shift(fn):
                fn(shift)
            else:
                fn()
        except Exception as ex:
            _log(f'{entry[1]} failed: {ex!r}')
        return True
    if family == 'plain':
        message = keys._plain_message(vk, False, False, shift)
        if message is None:
            return False
        keys._send(message)
        if message in keys._REPEATABLE:
            _held[key] = [message, now, now]
        return True
    return False


def _repeat(now):
    keys = _keys()
    for (family, _vk, _shift), entry in list(_held.items()):
        what, pressed_at, last_fire = entry
        if now - pressed_at < keys._REPEAT_DELAY or now - last_fire < keys._REPEAT_INTERVAL:
            continue
        entry[2] = now
        if family == 'chord':
            keys._chord_repeating = True
            try:
                keys._chord(what)
            finally:
                keys._chord_repeating = False
        else:
            keys._send(what)


def grab_set():
    """The bare keys the mod owns right now, as 'vk' or 'vk+shift' strings."""
    if not _pump_alive():
        return []
    keys = _keys()
    out = []
    for vk in _CANDIDATES:
        if keys._plain_message(vk, False, False, False) is not None:
            out.append(str(vk))
    for vk in _SHIFTED:
        if keys._plain_message(vk, False, False, True) is not None:
            out.append(f'{vk}+shift')
    return out


def tick(send):
    """Every game tick: key repeats, the keyboard lease Flash's guards need,
    and the bare-key set for the helper (sent through send when it changes)."""
    global _last_lease, _last_grab
    now = time.monotonic()
    if _held:
        _repeat(now)
    if now - _last_lease >= LEASE_SECONDS:
        _last_lease = now
        keys = _keys()
        if keys._enabled and _pump_alive():
            keys._send('A11y|lease')
            try:
                import simsight_pump as pump

                pump.post(keys._announce_ui_settings)
            except Exception:
                pass
        grab = grab_set()
        if grab != _last_grab:
            _last_grab = grab
            send({'grab': {'keys': grab}})


def focus_lost():
    """The game went to the background: release held keys (like Windows)."""
    keys = _keys()
    for family, vk, _shift in list(_held):
        if family == 'plain':
            keys._send(f'A11y|keyup|{vk}')
    _held.clear()

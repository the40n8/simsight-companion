"""The transient Windows hook that swallows chord keys while Ctrl+Alt is
held (its own thread), and what it reports to the poller."""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Any

import simsight_log

from .bindings import CHORD_NAMES, CHORDS, VK_CONTROL, VK_MENU, VK_SHIFT
from .state import status
from .winapi import _CTYPES_ERRORS, _altgr_pressed, _dlls, _game_is_foreground, ctypes

# --------------------------------------------------- chord key swallowing
# Polling cannot stop the game from seeing a key, so Ctrl+Alt+Down still
# panned the camera on every scanner step (first in-game test, 2026-09-06).
# A WH_KEYBOARD_LL hook can refuse a key before it reaches the message
# queue. It lives on its own thread with its own message loop (a low-level
# hook is only called while its owning thread pumps messages), blocks ONLY
# chord keys while Ctrl+Alt is held and the game is in front, and is
# removed when polling stops or the feature is off. A blocked key never
# reaches the system key state, so the hook reports chord keys to the
# poller itself (_hook_state). Windows silently drops a hook whose callback
# is too slow (the callback needs the GIL, which a busy simulation thread
# can hold); the poller notices a chord key the hook never reported and the
# hook thread reinstalls it.
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
_KEY_MESSAGES = (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
_SWALLOW_VKS = frozenset(CHORDS)


class _Hook:
    """The swallow hook's state, shared by the hook thread and the poller."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.running = False
        self.handle: int | None = None
        self.proc: Any = None  # keep the ctypes callback alive or the hook dies
        self.last_event = 0.0
        self.missed = 0.0  # poller saw a chord key the hook did not report
        self.installs = 0
        self.swallow_generation = 0


_hook = _Hook()
_hook_state: dict[int, bool] = {}  # vk -> down, as the hook last saw it
# Key-ups the hook consumed never reach Windows, so the key's async high bit
# stays set after the physical release. Kept across hook removal until
# Windows shows a real release or a new press (see _mask_swallowed_releases).
# vk -> generation of its latest consumed key-up; the hook thread records,
# the poller retires, both under _swallow_lock.
_swallowed_up: dict[int, int] = {}
_swallow_lock = threading.Lock()
# (vk, down, when): preserve complete chord taps between poll samples.
_hook_edges: deque[tuple[int, bool, float]] = deque()
# Diagnostic: (vk, down, seen down before, gate, ms late) for each chord-key
# event the hook handled; the poller writes them to the log (fast-chord
# losses, 2026-09-26). Never read by the input logic.
_hook_trace: deque[tuple[int, bool, bool, bool, int]] = deque(maxlen=64)


def _record_swallowed_up(vk: int) -> None:
    with _swallow_lock:
        _hook.swallow_generation += 1
        _swallowed_up[vk] = _hook.swallow_generation


def _clear_swallowed_up(vk: int) -> None:
    with _swallow_lock:
        _swallowed_up.pop(vk, None)


def _swallowed_snapshot() -> dict[int, int]:
    with _swallow_lock:
        return dict(_swallowed_up)


def _swallow_enabled() -> bool:
    # Always on with keyboard mode (no longer a player option); the hook
    # still needs Ctrl+Alt held, the game in front and no AltGr typing.
    return status.enabled


def _trace_hook_event(
    kernel32: Any, info: Any, vk: int, is_down: bool, previous: bool, gate: bool
) -> None:
    try:
        late = (kernel32.GetTickCount() - info[3]) & 0xFFFFFFFF
    except _CTYPES_ERRORS:
        late = -1  # diagnostic only
    _hook_trace.append((vk, is_down, previous, gate, late))


def _hook_key_event(user32: Any, kernel32: Any, info: Any, is_down: bool) -> bool:
    """One chord-key event in the hook: record it; True swallows it."""
    vk = info[0]
    previous = _hook_state.get(vk, False)
    _hook_state[vk] = is_down
    if is_down:
        _clear_swallowed_up(vk)
    if not is_down and previous:
        _hook_edges.append((vk, False, _hook.last_event))
    ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000 != 0
    alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000 != 0
    shift = user32.GetAsyncKeyState(VK_SHIFT) & 0x8000 != 0
    gate = (
        ctrl
        and alt
        and not shift
        and _swallow_enabled()
        and _game_is_foreground(user32, kernel32)
        and not _altgr_pressed(user32)
    )
    if simsight_log.enabled and not (is_down and previous):
        _trace_hook_event(kernel32, info, vk, is_down, previous, gate)
    if not gate:
        return False
    if is_down and not previous:
        _hook_edges.append((vk, is_down, _hook.last_event))
    elif not is_down:
        # Drain the old press bit so the stale high bit is never read as a
        # new press. A press this hook never saw (the key went down just
        # before the hook was installed) that the poller has not read either
        # is a whole chord tap: report it, or the drain loses a fast chord
        # (2026-09-26).
        unread = user32.GetAsyncKeyState(vk) & 1
        _record_swallowed_up(vk)
        if unread and not previous:
            _hook_edges.append((vk, True, _hook.last_event))
            _hook_edges.append((vk, False, _hook.last_event))
    return True


def _hook_callback_factory(user32: Any, kernel32: Any) -> Any:
    lresult = ctypes.c_ssize_t
    proto = ctypes.CFUNCTYPE(lresult, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
    user32.CallNextHookEx.restype = lresult
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]

    def callback(code: int, wparam: int, lparam: int) -> int:
        try:
            _hook.last_event = time.monotonic()
            if code == 0 and wparam in _KEY_MESSAGES:
                # KBDLLHOOKSTRUCT: vkCode, scanCode, flags, time (DWORDs)
                info = ctypes.cast(lparam, ctypes.POINTER(ctypes.c_ulong))
                is_down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if info[0] in _SWALLOW_VKS and _hook_key_event(user32, kernel32, info, is_down):
                    return 1
        except Exception:
            simsight_log.error('keys: swallow hook callback')
        return int(user32.CallNextHookEx(None, code, wparam, lparam))

    return proto(callback)


class _HookOwner:
    """Owns the hook and pumps messages for it, on the hook thread."""

    def __init__(self) -> None:
        user32, kernel32 = _dlls()
        self.user32 = user32
        self.kernel32 = kernel32
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_void_p]
        _hook.proc = _hook_callback_factory(user32, kernel32)
        self.module = kernel32.GetModuleHandleW(None)

        class MSG(ctypes.Structure):
            _fields_ = [
                ('hwnd', ctypes.c_void_p),
                ('message', ctypes.c_uint),
                ('wParam', ctypes.c_size_t),
                ('lParam', ctypes.c_ssize_t),
                ('time', ctypes.c_ulong),
                ('pt_x', ctypes.c_long),
                ('pt_y', ctypes.c_long),
            ]

        self.msg = MSG()

    def install(self) -> bool:
        _hook_state.clear()
        _hook.last_event = time.monotonic()
        _hook.missed = 0.0
        handle = self.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, ctypes.cast(_hook.proc, ctypes.c_void_p), self.module, 0
        )
        _hook.handle = handle
        _hook.installs += 1
        simsight_log.line(
            'keys: swallow hook {} (install #{})'.format(
                'installed' if handle else f'FAILED err={ctypes.get_last_error()}',
                _hook.installs,
            )
        )
        return bool(handle)

    def remove(self, reason: str) -> None:
        if _hook.handle:
            try:
                self.user32.UnhookWindowsHookEx(_hook.handle)
            except Exception:
                simsight_log.error('keys: swallow hook removal')
            _hook.handle = None
            _hook_state.clear()
            simsight_log.line(f'keys: swallow hook removed ({reason})')

    def _wanted(self) -> bool:
        user32 = self.user32
        return (
            _swallow_enabled()
            and bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            and bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            and not (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            and _game_is_foreground(user32, self.kernel32)
            and not _altgr_pressed(user32)
        )

    def run(self) -> None:
        # The hook is system-wide and its callback needs the GIL, so while it
        # is installed every keystroke in every program waits on the game.
        # Even ordinary typing INSIDE the game must bypass this Python hook.
        # Check modifiers on every pass; keep the hook only for scanner chords.
        user32 = self.user32
        msg = ctypes.byref(self.msg)
        last_check = time.monotonic()
        while _hook.running:
            # Pump whatever is queued (the hook callback is delivered here), then rest.
            while user32.PeekMessageW(msg, None, 0, 0, 1):
                user32.TranslateMessage(msg)
                user32.DispatchMessageW(msg)
            time.sleep(0.005)
            now = time.monotonic()
            wanted = self._wanted()
            if wanted and not _hook.handle:
                self.install()
            elif _hook.handle and not wanted:
                self.remove('scanner chord released or inactive')
            if now - last_check > 1.0:
                last_check = now
                if _hook.handle and _hook.missed > _hook.last_event + 0.5:
                    self.remove('dropped by Windows, reinstalling')
                    self.install()
        self.remove('polling stopped')


def _hook_loop() -> None:
    """The hook thread."""
    owner: _HookOwner | None = None
    try:
        owner = _HookOwner()
        owner.run()
    except Exception:
        simsight_log.error('keys: swallow hook thread')
    finally:
        # A failed message loop must not leave a hook blocking all input.
        if _hook.handle and owner is not None:
            try:
                owner.user32.UnhookWindowsHookEx(_hook.handle)
            except Exception:
                simsight_log.error('keys: swallow hook removal')
        _hook.handle = None
        _hook_state.clear()


def _mask_swallowed_releases(
    sampled: dict[int, int], before: dict[int, int] | None = None
) -> dict[int, int]:
    """Report a key as up while Windows still lacks a key-up the hook
    consumed and no new press has arrived.

    Without this, the stale high bit reads as a held key once Ctrl+Alt are
    released or the hook goes quiet, and navigation repeats until that key
    is pressed again (scanner incident 2026-09-25). Only consumed key-ups
    count: a hook Windows dropped leaves _hook_state stale, and then the
    polled state is the one to trust.

    before is the record snapshot taken just ahead of sampling. A record
    made after it belongs to a key-up that happened after (or during) the
    read, so the sample cannot retire it: a press it read still counts, and
    otherwise the consumed key-up already proves the key is up."""
    for vk, generation in _swallowed_snapshot().items():
        state = sampled.get(vk)
        if state is None:
            continue
        pressed = bool(state & 1)
        if before is not None and before.get(vk) != generation:
            if not pressed:
                sampled[vk] = 0
            continue
        if not state & 0x8000 or pressed:
            with _swallow_lock:  # retire only the record this sample saw
                if _swallowed_up.get(vk) == generation:
                    del _swallowed_up[vk]
        else:
            sampled[vk] = 0
    return sampled


def start_swallow() -> bool:
    if ctypes is None or not _swallow_enabled():
        return False
    if _hook.thread is not None and _hook.thread.is_alive():
        return True
    _hook.running = True
    _hook.thread = threading.Thread(target=_hook_loop, name='a11y_keys_hook', daemon=True)
    _hook.thread.start()
    return True


def stop_swallow() -> None:
    _hook.running = False


def swallow_status() -> str:
    if not _swallow_enabled():
        return 'off'
    if _hook.thread is None or not _hook.thread.is_alive():
        return 'not running'
    return 'installed' if _hook.handle else 'armed (hold Ctrl+Alt in the game)'


def _write_hook_trace() -> None:
    """Diagnostic: the hook's chord-key events since the last poll."""
    while _hook_trace:
        vk, down, seen, gate, late = _hook_trace.popleft()
        simsight_log.line(
            'keys: trace hook {} {} (down seen {}) gate {} late {} ms'.format(
                CHORD_NAMES.get(vk, hex(vk)),
                'down' if down else 'up',
                int(bool(seen)),
                int(bool(gate)),
                late,
            )
        )

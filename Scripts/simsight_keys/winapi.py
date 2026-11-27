"""Windows calls through ctypes (None on the Mac): the game window in
front, AltGr layouts, the poller's key reader and the opening-input fence."""

from __future__ import annotations

from typing import Any, Callable

import simsight_log

from .bindings import VK_LMENU, VK_RMENU
from .state import _ACTIVATION_KEYS, _opening_keys, _state, status

ctypes: Any = None  # None on the Mac, and on Windows when the bundled ctypes fails
_ctypes_error = 'unknown'
try:
    # NOTE: the bundled ctypes package ships only the core modules, so
    # ctypes.wintypes is NOT available here; use plain c_ulong for DWORDs.
    import ctypes as _loaded_ctypes

    ctypes = _loaded_ctypes
except Exception as _import_ex:
    _ctypes_error = repr(_import_ex)

# What a ctypes call into user32/kernel32 raises when it fails.
_CTYPES_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    AttributeError,
    TypeError,
    ValueError,
) + ((ctypes.ArgumentError,) if ctypes is not None else ())


def _dlls() -> tuple[Any, Any]:
    return (
        ctypes.WinDLL('user32', use_last_error=True),
        ctypes.WinDLL('kernel32', use_last_error=True),
    )


def fence_opening_input() -> None:
    """Retire activation input already in flight when a new surface opens.

    Snapshot only keys already down/tapped; a fresh first press stays usable.
    Windows' low bit is consumed here deliberately, before polling can
    mistake the console's completed Enter tap for a new picker activation.
    """
    _state.input_epoch += 1
    if ctypes is None or not status.enabled:
        return
    try:
        user32, kernel32 = _dlls()
        if not _game_is_foreground(user32, kernel32):
            return
        for vk in _ACTIVATION_KEYS:
            if user32.GetAsyncKeyState(vk) & 0x8001:
                _opening_keys.add(vk)
    except Exception:
        simsight_log.error('keys: opening input snapshot')


_altgr_layouts: dict[int, bool] = {}  # keyboard layout handle -> types with Ctrl+Alt


def _layout_has_altgr(user32: Any, hkl: int) -> bool:
    """True when this keyboard layout types characters with Ctrl+Alt (AltGr)."""
    if hkl in _altgr_layouts:
        return _altgr_layouts[hkl]
    found = False
    try:
        user32.VkKeyScanExW.restype = ctypes.c_short
        user32.VkKeyScanExW.argtypes = [ctypes.c_wchar, ctypes.c_void_p]
        for code in range(0x20, 0x100):
            result = user32.VkKeyScanExW(chr(code), hkl)
            if result != -1 and ((result >> 8) & 0x06) == 0x06:
                found = True
                break
    except _CTYPES_ERRORS:
        found = False  # unknown layout: treat Ctrl+Alt as a chord
    _altgr_layouts[hkl] = found
    return found


def _altgr_pressed(user32: Any) -> bool:
    """Right Alt alone on an AltGr layout: the player is typing, not chording."""
    try:
        if not (user32.GetAsyncKeyState(VK_RMENU) & 0x8000):
            return False
        if user32.GetAsyncKeyState(VK_LMENU) & 0x8000:
            return False
        hwnd = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        user32.GetKeyboardLayout.restype = ctypes.c_void_p
        hkl = user32.GetKeyboardLayout(thread_id)
        return _layout_has_altgr(user32, hkl)
    except _CTYPES_ERRORS:
        return False  # no layout to check: not AltGr


def _game_is_foreground(user32: Any, kernel32: Any) -> bool:
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return bool(pid.value == kernel32.GetCurrentProcessId())
    except _CTYPES_ERRORS:
        return False  # no foreground window to check: treat the game as behind


def _key_reader(user32: Any) -> Callable[[int], int]:
    """GetAsyncKeyState for the poller's sample through a PyDLL handle,
    which keeps the GIL during each call, so one pass reads the modifiers and
    every key in a burst of well under a millisecond instead of waiting for
    the GIL once per call (up to ~50 waits a pass under load). Only for a
    real user32 handle; the hook keeps its WinDLL calls. Falls back to the
    ordinary call."""
    try:
        if isinstance(user32, ctypes.CDLL):
            reader = ctypes.PyDLL('user32').GetAsyncKeyState
            reader.argtypes = [ctypes.c_int]
            reader.restype = ctypes.c_short
            return reader
    except Exception:
        simsight_log.error('keys: GIL-holding key reader')
    return user32.GetAsyncKeyState

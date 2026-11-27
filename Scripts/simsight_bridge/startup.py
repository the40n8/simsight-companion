"""Started once, when the package loads (the game's loading thread)."""

from __future__ import annotations

import importlib

import simsight_log

from . import pipe
from .routefail import _install_route_fail
from .state import _state
from .tick import _install_tick

# Windows reads the pipe through msvcrt and ctypes (PeekNamedPipe); the Mac
# takes its keys from the phone (simsight_mackeys). Never ctypes on the Mac.


def install() -> None:
    """The optional modules, the game hooks and the bridge process."""
    if pipe.IS_WINDOWS:
        try:
            _state.msvcrt = importlib.import_module('msvcrt')
            _state.ctypes = importlib.import_module('ctypes')
        except Exception:
            pipe.IS_WINDOWS = False  # no way to read the pipe without blocking: no companion
            simsight_log.error('bridge: companion unavailable on Windows')
    if pipe.IS_MAC:
        try:
            _state.mackeys = importlib.import_module('simsight_mackeys')
        except Exception:
            _state.mackeys = None
            simsight_log.error('bridge: keyboard unavailable')
    if pipe.IS_MAC or pipe.IS_WINDOWS:
        try:
            _install_route_fail()
        except Exception:
            simsight_log.error('bridge: route failure hook unavailable')
        try:
            _state.dialogs = importlib.import_module('simsight_dialogs')
            _state.dialogs.install()
        except Exception:
            _state.dialogs = None
            simsight_log.error('bridge: dialogs unavailable')
        try:
            _install_tick()
            pipe.start()
        except Exception:
            simsight_log.error('bridge: install')

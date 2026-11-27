"""Contextual keyboard navigation.

Tab/Shift+Tab moves between controls; arrows move within the open surface.
Enter/Space activates once; Escape backs out. EA InputAction guards consume
these physical keys while our UI owns focus. Python polling supplies presses,
repeats and releases. The transient Windows hook remains scanner-chord-only.
Ordinary typing never waits on a permanent Python keyboard hook.

This package is the keyboard's public API (keys.<name>); the submodules
hold the parts: bindings (key tables), state (shared state), winapi (ctypes),
modal (Flash's owner reports), scannermode (scanner mode, Build cursor),
send (delivering a message), chords (chord routing), plain (bare-key
messages), hook (chord swallowing), poller (the Windows poller) and
control (start, stop, simsight.keys). On the Mac keys arrive through
simsight_mackeys instead of the poller.
"""

from __future__ import annotations

# Loaded first and in this order, as before the split: the modules below
# install observers and register console commands when they load.
import sims4.commands  # noqa: F401 - loaded before our commands register

import simsight_lang  # noqa: F401 - load order
import simsight_commands  # noqa: F401 - load order
import simsight_log
import simsight_settings  # noqa: F401 - load order
import simsight_speech  # noqa: F401 - load order
import simsight_phone  # noqa: F401 - side effect: install the incoming-call observer at startup
import simsight_hub  # noqa: F401 - side effect: register the common-actions commands
import simsight_careerlog  # noqa: F401 - side effect: doctor career research log (logging only)
import simsight_modui  # noqa: F401 - side effect: mod menu census (logging only)
import simsight_rooms  # noqa: F401 - side effect: the simsight.roomlabel console command (Build cursor room labels)
import simsight_holidays  # noqa: F401 - side effect: Seasons: announce a holiday when its day starts
import simsight_plus  # noqa: F401 - side effect: SimSight Plus features (Alt keys), merged 2026-10-08

from .bindings import (
    BINDINGS,
    BUILD_GRID_KEYS,
    CHORD_NAMES,
    CHORDS,
    NUMPAD_BINDINGS,
    REPEAT_DELAY,
    REPEAT_INTERVAL,
    REPEATABLE,
    SCANNER_KEYS,
    VK_CONTROL,
    VK_LMENU,
    VK_MENU,
    VK_RMENU,
    VK_SHIFT,
)
from .state import Status, status
from .winapi import fence_opening_input
from .modal import (
    a11y_blocking,
    a11y_inputfence,
    a11y_modalstate,
    apply_modal_state,
    blocking_active,
)
from .scannermode import (
    a11y_buildgridowner,
    a11y_scannerlive,
    a11y_scannermode,
    a11y_scannerowner,
    announce_ui_settings,
    apply_scanner_setting,
    hold_scanner_resume,
    scanner_effective,
    scanner_zone_started,
    set_scanner_mode,
)
from .send import send_action
from .hook import (
    WH_KEYBOARD_LL,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    start_swallow,
    stop_swallow,
    swallow_status,
)
from .chords import repeat_chord, request_clock, run_chord
from .plain import plain_message
from .control import a11y_keyprobe, a11y_keys, describe_key, start, stop

__all__ = [
    'BINDINGS',
    'BUILD_GRID_KEYS',
    'CHORDS',
    'CHORD_NAMES',
    'NUMPAD_BINDINGS',
    'REPEATABLE',
    'REPEAT_DELAY',
    'REPEAT_INTERVAL',
    'SCANNER_KEYS',
    'VK_CONTROL',
    'VK_LMENU',
    'VK_MENU',
    'VK_RMENU',
    'VK_SHIFT',
    'WH_KEYBOARD_LL',
    'WM_KEYDOWN',
    'WM_KEYUP',
    'WM_SYSKEYDOWN',
    'WM_SYSKEYUP',
    'Status',
    'a11y_blocking',
    'a11y_buildgridowner',
    'a11y_inputfence',
    'a11y_keyprobe',
    'a11y_keys',
    'a11y_modalstate',
    'a11y_scannerlive',
    'a11y_scannermode',
    'a11y_scannerowner',
    'announce_ui_settings',
    'apply_modal_state',
    'apply_scanner_setting',
    'blocking_active',
    'describe_key',
    'fence_opening_input',
    'hold_scanner_resume',
    'plain_message',
    'repeat_chord',
    'request_clock',
    'run_chord',
    'scanner_effective',
    'scanner_zone_started',
    'send_action',
    'set_scanner_mode',
    'start',
    'start_swallow',
    'status',
    'stop',
    'stop_swallow',
    'swallow_status',
]

try:
    if status.enabled:
        start()
    else:
        simsight_log.line('keys: keyboard navigation disabled by settings')
except Exception:
    simsight_log.error('keys: autostart')

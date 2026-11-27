"""Read-only Windows/build coordinate diagnostics; never inject input or set DPI."""

from __future__ import annotations

import ctypes
import json
import math
import os
import threading
import time
from typing import Any, Callable

import sims4.commands
from simsight_commands import real_annotations
import simsight_log

# What a Win32 reading can raise here: our own refusals, a missing or failing
# function (AttributeError, OSError), an argument ctypes cannot convert.
_WIN32_ERRORS = (RuntimeError, OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError)


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int32), ('y', ctypes.c_int32)]


class RECT(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int32) for n in ('left', 'top', 'right', 'bottom')]


class _State:
    def __init__(self) -> None:
        self.active = False  # projections are logged once simsight.inspect_display ran
        self.timer: threading.Timer | None = None
        self.window_start = 0.0  # rate limit: at most 30 records a minute
        self.count = 0


_state = _State()
_lock = threading.Lock()
_OPTIONS_KEYS = frozenset(
    (
        'resolutionwidth',
        'resolutionheight',
        'resolutionrefresh',
        'fullscreen',
        'windowedfullscreen',
        'uiscale',
        'sceneresolution',
    )
)


def _win_dll(name: str) -> Any:
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]  # Windows only; mypy checks as macOS


def user32_api() -> Any:
    # Private function objects: do not overwrite prototypes cached by the
    # existing mouse worker. HWND/context return values must be pointer-wide.
    u = _win_dll('user32')
    ptr, integer = ctypes.c_void_p, ctypes.c_int32
    signatures = {
        'GetForegroundWindow': (ptr, []),
        'GetWindowThreadProcessId': (ctypes.c_uint32, [ptr, ctypes.POINTER(ctypes.c_uint32)]),
        'GetClientRect': (integer, [ptr, ctypes.POINTER(RECT)]),
        'GetWindowRect': (integer, [ptr, ctypes.POINTER(RECT)]),
        'ClientToScreen': (integer, [ptr, ctypes.POINTER(POINT)]),
        'ScreenToClient': (integer, [ptr, ctypes.POINTER(POINT)]),
        'GetCursorPos': (integer, [ctypes.POINTER(POINT)]),
        'GetPhysicalCursorPos': (integer, [ctypes.POINTER(POINT)]),
        'LogicalToPhysicalPointForPerMonitorDPI': (integer, [ptr, ctypes.POINTER(POINT)]),
        'GetDpiForWindow': (ctypes.c_uint32, [ptr]),
        'GetThreadDpiAwarenessContext': (ptr, []),
        'GetWindowDpiAwarenessContext': (ptr, [ptr]),
        'GetAwarenessFromDpiAwarenessContext': (integer, [ptr]),
        'GetSystemMetrics': (integer, [integer]),
        'MonitorFromWindow': (ptr, [ptr, ctypes.c_uint32]),
        'GetWindowLongPtrW': (ctypes.c_ssize_t, [ptr, integer]),
        'IsZoomed': (integer, [ptr]),
        'IsIconic': (integer, [ptr]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(u, name, None)
        if fn is not None:
            fn.restype, fn.argtypes = result, args
    return u


def _monitor_scale(u: Any, hwnd: Any) -> int:
    monitor = u.MonitorFromWindow(hwnd, 2)
    if not monitor:
        raise RuntimeError('monitor unavailable')
    fn = _win_dll('shcore').GetScaleFactorForMonitor
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
    fn.restype = ctypes.c_int32
    scale = ctypes.c_int32()
    if fn(monitor, ctypes.byref(scale)) != 0:
        raise RuntimeError('scale unavailable')
    return int(scale.value)


class _Snapshot:
    """Win32 readings of one game window, each recorded or its failure named."""

    def __init__(self, u: Any, hwnd: Any) -> None:
        self.u, self.hwnd = u, hwnd
        self.errors: dict[str, str] = {}
        self.result: dict[str, Any] = {
            'hwnd': int(hwnd),
            'thread': threading.current_thread().name,
            'game_ui_stage_size': None,
            'errors': self.errors,
        }

    def field(self, name: str, fn: Callable[[], Any]) -> None:
        try:
            self.result[name] = fn()
        except _WIN32_ERRORS as exc:  # a diagnostic: name what failed, keep reading
            self.errors[name] = type(exc).__name__

    def rect(self, fn: Any) -> list[int]:
        r = RECT()
        if not fn(self.hwnd, ctypes.byref(r)):
            raise RuntimeError('rectangle query failed')
        return [r.left, r.top, r.right, r.bottom]

    def point(self, fn: Any, initial: Any = (0, 0), window: bool = True) -> list[int]:
        p = POINT(*initial)
        args = (self.hwnd, ctypes.byref(p)) if window else (ctypes.byref(p),)
        if not fn(*args):
            raise RuntimeError('point query failed')
        return [p.x, p.y]

    def read_fields(self) -> None:
        u, hwnd = self.u, self.hwnd
        self.field('client_rect_reported', lambda: self.rect(u.GetClientRect))
        self.field('window_rect_reported', lambda: self.rect(u.GetWindowRect))
        self.field('client_origin_reported', lambda: self.point(u.ClientToScreen))
        self.field('cursor_reported', lambda: self.point(u.GetCursorPos, window=False))
        self.field('cursor_physical', lambda: self.point(u.GetPhysicalCursorPos, window=False))
        self.field('window_dpi', lambda: int(u.GetDpiForWindow(hwnd)))
        self.field('monitor_scale_percent', lambda: _monitor_scale(u, hwnd))
        self.field('window_style_hex', lambda: hex(u.GetWindowLongPtrW(hwnd, -16) & 0xFFFFFFFF))
        self.field('maximized', lambda: bool(u.IsZoomed(hwnd)))
        self.field('minimized', lambda: bool(u.IsIconic(hwnd)))
        self.field(
            'thread_awareness',
            lambda: int(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())),
        )
        self.field(
            'window_awareness',
            lambda: int(
                u.GetAwarenessFromDpiAwarenessContext(u.GetWindowDpiAwarenessContext(hwnd))
            ),
        )
        self.field('primary_size_reported', lambda: [u.GetSystemMetrics(0), u.GetSystemMetrics(1)])
        self.field(
            'virtual_desktop_reported', lambda: [u.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        )

    def sample(self, initial: tuple[int, int]) -> dict[str, Any]:
        """One client point converted to the screen and back."""
        u = self.u
        sample: dict[str, Any] = {'client': list(initial)}
        try:
            screen = self.point(u.ClientToScreen, initial)
            sample.update(
                screen_reported=screen, client_roundtrip=self.point(u.ScreenToClient, screen)
            )
            sample['screen_physical_for_window'] = self.point(
                u.LogicalToPhysicalPointForPerMonitorDPI, screen
            )
        except _WIN32_ERRORS as exc:  # a diagnostic: name what failed
            sample['error'] = type(exc).__name__
        return sample

    def read_samples(self) -> None:
        r = self.result.get('client_rect_reported')
        if not r:
            return
        w, h = r[2] - r[0], r[3] - r[1]
        self.result['client_size_reported'] = [w, h]
        corners = ((0, 0), (w // 2, h // 2), (max(0, w - 1), max(0, h - 1)))
        self.result['samples'] = [self.sample(initial) for initial in corners]


def windows_snapshot(u: Any = None) -> dict[str, Any]:
    u = user32_api() if u is None else u
    hwnd = u.GetForegroundWindow()
    owner = ctypes.c_uint32()
    if (
        not hwnd
        or not u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        or owner.value != os.getpid()
    ):
        return {'refused': 'foreground window is not owned by this game process'}
    snapshot = _Snapshot(u, hwnd)
    snapshot.read_fields()
    snapshot.read_samples()
    if u.GetForegroundWindow() != hwnd:
        return {'refused': 'foreground changed during capture'}
    return snapshot.result


def _game_root() -> str:
    """The game's user folder (the one holding Mods), from the log's path."""
    folder = os.path.dirname(simsight_log.PATH)
    for _ in range(3):
        if os.path.basename(folder).lower() == 'mods':
            return os.path.dirname(folder)
        folder = os.path.dirname(folder)
    raise ValueError('Mods root unavailable')


def saved_options() -> dict[str, Any]:
    # Read only display fields, with a size cap; never include unrelated
    # preferences, paths or personal data from Options.ini in the log.
    result: dict[str, Any] = {}
    try:
        with open(os.path.join(_game_root(), 'Options.ini'), encoding='utf-8-sig') as f:
            text = f.read(65536)
        for line in text.splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip().lower() in _OPTIONS_KEYS:
                result[key.strip().lower()] = int(value.strip())
    except (OSError, ValueError) as exc:
        result['error'] = type(exc).__name__
    return result


def _allowed() -> bool:
    """At most 30 records a minute."""
    with _lock:
        now = time.monotonic()
        if now - _state.window_start >= 60:
            _state.window_start, _state.count = now, 0
        if _state.count >= 30:
            return False
        _state.count += 1
        return True


def record(kind: str, data: Any = None, build: Any = None) -> bool:
    if not simsight_log.enabled or not _allowed():
        return False
    try:
        row = windows_snapshot()
        row.update(kind=kind, data=data, saved_options=saved_options())
        if build is not None and 'refused' not in row:
            try:
                row['adapter_size_reported'] = list(build.stage_size())
            except _WIN32_ERRORS as exc:  # a diagnostic: name what failed
                row['errors']['adapter_size_reported'] = type(exc).__name__
        simsight_log.line('display inspect: ' + json.dumps(row, sort_keys=True, default=str))
    except Exception:  # timer and projection threads: optional logging never stops them
        simsight_log.error('display inspect: capture')
        return False
    return 'refused' not in row


def _engine() -> Any:
    """simsight_plus_build, or None when it did not load."""
    try:
        import simsight_plus_build as build
    except ImportError:
        return None
    return build


def observe(kind: str, data: Any) -> None:
    """The build engine reports each projection; logged once simsight.inspect_display ran."""
    if not _state.active:
        return
    try:
        record(kind, data, _engine())
    except Exception:  # optional logging must never break a build
        simsight_log.error('display inspect: observe')


def install() -> None:
    _state.active = True


def _capture(build: Any) -> None:
    try:
        record('manual', build=build)
    except Exception:  # timer thread: nothing above it catches
        simsight_log.error('display inspect: manual capture')


@sims4.commands.Command(  # type: ignore[misc]  # EA's Command is untyped
    'simsight.inspect_display', 'a11y.inspect_display', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def inspect_display(delay: float = 5, _connection: Any = None) -> bool:
    output = sims4.commands.CheatOutput(_connection)
    if not math.isfinite(delay):
        return False
    if not simsight_log.enabled:
        output('Run simsight.log on first.')
        return False
    try:
        import simsight_plus_build as build

        install()
    except Exception:  # the Build helper failed to load: any error of its module code
        simsight_log.error('display inspect: adapter unavailable')
        output('Display diagnostic unavailable; see SimSight log.')
        return False
    if _state.timer is not None:
        _state.timer.cancel()
    delay = max(0, min(30, delay))
    # No game APIs on this timer: only Win32/filesystem reads. Actual builder
    # worker DPI/coordinates are captured by the passive wrappers separately.
    timer = threading.Timer(delay, _capture, (build,))
    timer.daemon = True
    timer.name = 'a11y_display_probe'
    timer.start()
    _state.timer = timer
    output(
        f'Read-only display snapshot in {delay} seconds. Close the console and keep the game focused; see SimSight log.'
    )
    return True

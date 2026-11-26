"""Measured UI -> client -> physical-screen boundary for build helpers.

No resolution presets or extra DPI multiplier. Native projection is read-only;
actual clicks/drags remain the existing helpers and the game's validation.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import math
import os
import sys
import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import sims4.commands
import simsight_display_diagnostic as display
from simsight_commands import real_annotations
import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    from collections.abc import Iterator

    # hwnd, client origin x and y, client width and height, DPI,
    # virtual desktop (left, top, width, height), window style
    Geometry = tuple[int, int, int, int, int, int, tuple[int, ...], int]

_STAGE_MODES = ('noScale', 'exactFit', 'showAll', 'noBorder')
_ALIGNMENTS = frozenset(('', 'T', 'B', 'L', 'R', 'TL', 'TR', 'BL', 'BR', 'LT', 'RT', 'LB', 'RB'))
_MOUSE_MOVE_ABSOLUTE = 0xC001  # MOUSEEVENTF_MOVE | ABSOLUTE | VIRTUALDESK
_MOUSE_LEFT_UP = 4
_SHIFT = (0x10, 0x2A)  # virtual key, scan code


class _State:
    def __init__(self) -> None:
        self.build: Any = None  # simsight_plus_build, set by install()
        self.frame: Frame | None = None  # the last measured viewport
        self.serial = 0  # the last projection request


_state = _State()
_pending: dict[int, dict[str, Any]] = {}  # serial -> projection job
_lock = threading.Lock()
# Per worker thread: .input_frame (the frame an action runs in), .down (left
# button held), .last (where the pointer was put).
_local = threading.local()


def _log(kind: str, **data: Any) -> None:
    simsight_log.line(
        'build coordinates: ' + json.dumps(dict(kind=kind, **data), sort_keys=True, default=str)
    )


def _api() -> Any:
    u = display._api()
    signatures = {
        'SetThreadDpiAwarenessContext': (ctypes.c_void_p, [ctypes.c_void_p]),
        'SetCursorPos': (ctypes.c_int32, [ctypes.c_int32, ctypes.c_int32]),
        'mouse_event': (
            None,
            [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t],
        ),
        'GetAsyncKeyState': (ctypes.c_short, [ctypes.c_int32]),
        'GetAncestor': (ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_uint32]),
        'keybd_event': (None, [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint32, ctypes.c_size_t]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(u, name)
        fn.restype, fn.argtypes = result, args
    return u


@contextlib.contextmanager
def physical_context(u: Any) -> Iterator[None]:
    """Per-monitor DPI coordinates on this thread meanwhile."""
    previous = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise RuntimeError('Per-monitor coordinate context unavailable')
    try:
        yield
    finally:
        if not u.SetThreadDpiAwarenessContext(previous):
            _log('dpi-restore-failed')
            raise RuntimeError('Thread DPI context could not be restored')


def window(u: Any) -> Geometry:
    """The focused game window's geometry; raises unless it can take input."""
    hwnd = u.GetForegroundWindow()
    pid = ctypes.c_uint32()
    if (
        not hwnd
        or not u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        or pid.value != os.getpid()
    ):
        raise RuntimeError('The game must remain focused')
    if u.GetAncestor(hwnd, 3) != hwnd:
        raise RuntimeError('Close the native game dialog before building')
    if u.IsIconic(hwnd):
        raise RuntimeError('The game window is minimized')
    rect = display.RECT()
    origin = display.POINT(0, 0)
    if not u.GetClientRect(hwnd, ctypes.byref(rect)) or not u.ClientToScreen(
        hwnd, ctypes.byref(origin)
    ):
        raise RuntimeError('Game window geometry unavailable')
    w, h = int(rect.right - rect.left), int(rect.bottom - rect.top)
    desktop = tuple(int(u.GetSystemMetrics(i)) for i in (76, 77, 78, 79))
    if min(w, h, desktop[2], desktop[3]) < 100:
        raise RuntimeError('Invalid game window dimensions')
    return (
        int(hwnd),
        origin.x,
        origin.y,
        w,
        h,
        int(u.GetDpiForWindow(hwnd)),
        desktop,
        int(u.GetWindowLongPtrW(hwnd, -16)),
    )


def capture_window() -> Geometry:
    u = _api()
    with physical_context(u):
        return window(u)


def _check_viewport(viewport: dict[str, Any]) -> None:
    """Raise ValueError unless the UI's reply describes a usable viewport."""
    width, height, mode = viewport['width'], viewport['height'], viewport['mode']
    bounds, layout = viewport['bounds'], viewport['layout']
    align = viewport.get('align', '')
    if not all(math.isfinite(v) for v in (width, height, *bounds, *layout)):
        raise ValueError('Non-finite viewport')
    if not (100 <= width <= 100000 and 100 <= height <= 100000):
        raise ValueError('Invalid viewport')
    if mode not in _STAGE_MODES:
        raise ValueError('Unverified stage scale mode: ' + mode)
    if mode in ('showAll', 'noBorder') and align not in _ALIGNMENTS:
        raise ValueError('Unknown stage alignment: ' + repr(align))
    if min(layout) < 100 or not (
        0 <= bounds[0] < bounds[2] <= layout[0] and 0 <= bounds[1] < bounds[3] <= layout[1]
    ):
        raise ValueError('Invalid build bounds')


class Frame:
    """The game UI's stage (its width and height in UI units) mapped onto
    the client area of the game window, as Flash scales and aligns it."""

    def __init__(self, viewport: dict[str, Any], geometry: Geometry) -> None:
        """viewport: the UI's reply (zone, width, height, mode, align, bounds, layout)."""
        _check_viewport(viewport)
        width, height, mode = viewport['width'], viewport['height'], viewport['mode']
        bounds, layout = viewport['bounds'], viewport['layout']
        align = viewport.get('align', '')
        self.zone, self.width, self.height, self.mode, self.geometry = (
            viewport['zone'],
            width,
            height,
            mode,
            geometry,
        )
        self.sx, self.sy = geometry[3] / width, geometry[4] / height
        self.ox = self.oy = 0.0
        if mode in ('showAll', 'noBorder'):
            self.sx = self.sy = (min if mode == 'showAll' else max)(self.sx, self.sy)
            free_x, free_y = geometry[3] - width * self.sx, geometry[4] - height * self.sy
            self.ox = 0.0 if 'L' in align else free_x if 'R' in align else free_x / 2
            self.oy = 0.0 if 'T' in align else free_y if 'B' in align else free_y / 2
        self.bounds = (
            bounds[0] * width / layout[0],
            bounds[1] * height / layout[1],
            bounds[2] * width / layout[0],
            bounds[3] * height / layout[1],
        )

    def client(self, x: float, y: float, offset: Any = (0, 0)) -> tuple[int, int]:
        """Client pixel of the UI point (x, y) moved by offset; ValueError outside."""
        x, y = float(x) + offset[0], float(y) + offset[1]
        if not (
            math.isfinite(x) and math.isfinite(y) and 0 <= x < self.width and 0 <= y < self.height
        ):
            raise ValueError('Projected point is outside the game viewport')
        result = (int(round(self.ox + x * self.sx)), int(round(self.oy + y * self.sy)))
        if not (0 <= result[0] < self.geometry[3] and 0 <= result[1] < self.geometry[4]):
            raise ValueError('Projected point is cropped from the viewport')
        return result

    def clear(self, p: Any) -> bool:
        """The UI point is inside the build area and away from the panels."""
        x, y = p
        left, top, right, bottom = self.bounds
        return (
            left <= x <= right
            and top <= y <= bottom
            and 0.02 * self.width <= x <= 0.98 * self.width
            and 0.08 * self.height <= y <= 0.70 * self.height
        )


def _viewport_reply(fields: list[str], zone: int, status: str) -> dict[str, Any] | None:
    """The projection reply's numbers, or None when one is not finite."""
    values = [float(fields[i]) for i in (3, 4, 5, 6, 9, 10, 11, 12, 13, 14)]
    if not all(math.isfinite(v) for v in values[2:]):
        return None
    if status == 'point' and not all(math.isfinite(v) for v in values[:2]):
        return None
    return {
        'status': status,
        'zone': zone,
        'point': tuple(values[:2]),
        'width': values[2],
        'height': values[3],
        'mode': fields[7],
        'align': fields[8],
        'layout': tuple(values[4:6]),
        'bounds': tuple(values[6:10]),
    }


def _receive(text: str) -> bool:
    if len(text) > 3000:
        return False
    fields = unquote(text).split('|')
    if len(fields) != 15:
        return False
    ident = int(fields[0])
    zone = int(fields[1])
    status = fields[2]
    with _lock:
        job = _pending.get(ident)
    if job is None or job['zone'] != zone:
        return False
    if status not in ('point', 'viewport'):
        job['result'] = {'status': status}
        job['event'].set()
        return True
    result = _viewport_reply(fields, zone, status)
    if result is None:
        return False
    job['result'] = result
    job['event'].set()
    return True


@sims4.commands.Command(
    'simsight.coords_enc', 'a11y.coords_enc', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def receive(text: str = '', _connection: Any = None) -> bool:
    """UI reply to a projection request: serial|zone|status|x|y|width|height|mode|align|layout|bounds."""
    try:
        return _receive(text)
    except ValueError as exc:  # a number that does not parse
        _log('reply-rejected', error=type(exc).__name__)
        return False
    except Exception as exc:
        simsight_log.error('build coordinates: projection reply')
        _log('reply-rejected', error=type(exc).__name__)
        return False


def _new_job() -> tuple[int, dict[str, Any]]:
    with _lock:
        _state.serial = (_state.serial % 2147483646) + 1
        ident = _state.serial
        job: dict[str, Any] = {'event': threading.Event(), 'zone': None, 'result': None}
        _pending[ident] = job
    return ident, job


def _measure(
    x: float, z: float, level: int, height: float = 0.0, timeout: float = 3.0
) -> tuple[dict[str, Any], Frame]:
    """Ask the UI to project the world point; (its reply, the measured frame)."""
    before = capture_window()
    ident, job = _new_job()
    build = _state.build

    def start() -> None:
        try:
            import services

            job['zone'] = int(services.current_zone_id())
            y = build.floor_height(x, z, level) + height
            build._send_ui(
                'A11y|coords|{}|{:.6f}|{:.6f}|{:.6f}|{}|{}'.format(
                    ident, x, y, z, level, job['zone']
                )
            )
        except Exception as exc:  # simulation thread: answer the waiter instead of raising
            simsight_log.error('build coordinates: projection request')
            job['result'] = {'status': 'request-error', 'error': type(exc).__name__}
            job['event'].set()

    build.core.post(start)
    try:
        if not job['event'].wait(timeout):
            raise RuntimeError('Projection measurement timed out; check paired UI packages')
        result = job['result']
        if not result or result['status'] not in ('point', 'viewport'):
            raise RuntimeError('Projection refused: ' + str(result))
        after = capture_window()
        if before != after:
            raise RuntimeError('Game window changed during projection')
        _log('raw-measure', response=result, window=after)
        frame = Frame(result, after)
        _state.frame = frame
        _log('measure', response=result, window=after)
        return result, frame
    finally:
        with _lock:
            _pending.pop(ident, None)


def project(x: float, z: float, level: int, timeout: float = 2.5, height: float = 0.0) -> Any:
    """The UI point of the world point, or None."""
    try:
        result, _frame = _measure(x, z, level, height, timeout=max(2.5, timeout))
        return result['point'] if result['status'] == 'point' else None
    except (RuntimeError, ValueError) as exc:  # refused, timed out or moved: logged, no point
        _log('project-refused', error=str(exc))
        return None
    except Exception as exc:
        simsight_log.error('build coordinates: project')
        _log('project-refused', error=str(exc))
        return None


def _current(u: Any) -> Frame:
    """The measured frame, while the window and the game's dialogs are as measured."""
    frame = getattr(_local, 'input_frame', None) or _state.frame
    if frame is None:
        raise RuntimeError('Measure the game viewport before building')
    if window(u) != frame.geometry:
        raise RuntimeError('Game focus, size, position or DPI changed; retry the operation')
    keys = sys.modules.get('simsight_keys')
    status = getattr(keys, 'status', None)
    if keys is not None and (getattr(status, 'editing_active', False) or keys.blocking_active()):
        raise RuntimeError('Close the open game dialog before building')
    return frame


def stage_size() -> tuple[float, float]:
    u = _api()
    with physical_context(u):
        f = _current(u)
        return f.width, f.height


def to_screen(x: float, y: float) -> tuple[int, int]:
    """Screen pixel of the UI point (x, y)."""
    u = _api()
    with physical_context(u):
        f = _current(u)
        cx, cy = f.client(x, y, _state.build.OFFSET)
        point = display.POINT(cx, cy)
        if not u.ClientToScreen(f.geometry[0], ctypes.byref(point)):
            raise RuntimeError('Client conversion failed')
        _log('convert', ui=[x, y], client=[cx, cy], screen=[point.x, point.y], window=f.geometry)
        return point.x, point.y


def _release(u: Any) -> None:
    if getattr(_local, 'down', False):
        u.mouse_event(_MOUSE_LEFT_UP, 0, 0, 0, 0)
        _local.down = False


def _move(u: Any, x: float, y: float) -> None:
    f = _current(u)
    g = f.geometry
    x, y = int(round(x)), int(round(y))
    if not _state.build.on_main(_safe_game, f.zone):
        raise RuntimeError('Build Mode changed during pointer operation')
    _current(u)
    if not (g[1] <= x < g[1] + g[3] and g[2] <= y < g[2] + g[4]):
        raise RuntimeError('Mouse point is outside the game window')
    left, top, w, h = g[6]
    nx = int(round((x - left) * 65535 / max(1, w - 1)))
    ny = int(round((y - top) * 65535 / max(1, h - 1)))
    if not (0 <= nx <= 65535 and 0 <= ny <= 65535):
        raise RuntimeError('Mouse point outside virtual desktop')
    if not u.SetCursorPos(x, y):
        raise RuntimeError('Pointer positioning failed')
    u.mouse_event(_MOUSE_MOVE_ABSOLUTE, nx, ny, 0, 0)
    p = display.POINT()
    if not u.GetPhysicalCursorPos(ctypes.byref(p)) or abs(p.x - x) > 2 or abs(p.y - y) > 2:
        raise RuntimeError('Physical pointer did not reach the requested point')
    _local.last = (x, y)


def move_to(x: float, y: float) -> None:
    """Put the real pointer on the screen pixel; raises (button released) when refused."""
    u = _api()
    try:
        with physical_context(u):
            _move(u, x, y)
    except Exception:
        _release(u)
        raise


def _safe_game(zone: int) -> bool:
    import services
    import simsight_keys as keys
    import simsight_place as place

    return (
        place.in_build_mode()
        and int(services.current_zone_id()) == zone
        and not keys.blocking_active()
        and not keys.status.editing_active
    )


def _press_left(u: Any, flags: int, data: int) -> None:
    f = _current(u)
    if not _state.build.on_main(_safe_game, f.zone):
        raise RuntimeError('Build context changed before click')
    p = display.POINT()
    last = getattr(_local, 'last', None)
    if (
        last is None
        or not u.GetPhysicalCursorPos(ctypes.byref(p))
        or max(abs(p.x - last[0]), abs(p.y - last[1])) > 2
    ):
        raise RuntimeError('Pointer moved before click')
    _current(u)
    u.mouse_event(flags, 0, 0, data, 0)
    _local.down = True


def mouse(flags: int, data: int = 0) -> Any:
    """A mouse button event; the left button goes down only where move_to put it."""
    u = _api()
    build = _state.build
    if flags == build.LEFT_UP:
        u.mouse_event(flags, 0, 0, data, 0)
        _local.down = False
        return None
    if flags != build.LEFT_DOWN:
        return _original_mouse(flags, data)
    try:
        with physical_context(u):
            _press_left(u, flags, data)
    except Exception:
        _release(u)
        raise
    return None


def clear_of_panels(point: Any) -> bool:
    try:
        u = _api()
        with physical_context(u):
            return _current(u).clear(point)
    except RuntimeError:  # not measured, or the window changed: not known to be clear
        return False
    except Exception:
        simsight_log.error('build coordinates: clear of panels')
        return False


def _guarded_action(original: Any, args: tuple[Any, ...], shift: bool) -> Any:
    """Run a click or drag helper in the measured frame, releasing the
    button (and Shift) whatever happens."""
    u = _api()
    with physical_context(u):
        f = _current(u)
        if shift and u.GetAsyncKeyState(_SHIFT[0]) & 0x8000:
            raise RuntimeError('Release Shift before using this helper')
        _local.input_frame = f
        try:
            return original(*args, shift=shift)
        finally:
            _release(u)
            if shift:
                u.keybd_event(_SHIFT[0], _SHIFT[1], 2, 0)
            _local.input_frame = None


def park_mouse() -> None:
    """Move the pointer out of the way, in the upper middle of the game window."""
    try:
        u = _api()
        with physical_context(u):
            g = window(u)
            u.SetCursorPos(g[1] + g[3] // 2, g[2] + g[4] // 3)
    except RuntimeError:  # the game is not focused: leave the pointer alone
        pass
    except Exception:
        simsight_log.error('build coordinates: park mouse')


def install() -> None:
    """Startup: the engine this module measures for. Idempotent."""
    if _state.build is not None:
        return
    import simsight_plus_build as build

    _state.build = build
    _log('installed')


def _original_mouse(flags: int, data: int = 0) -> Any:
    import simsight_plus_build as build

    return build._mouse_base(flags, data)


def _sim_point() -> tuple[float, float, int]:
    import services

    sim = services.get_active_sim()
    if sim is None:
        raise RuntimeError('Load a household first')
    return float(sim.position.x), float(sim.position.z), int(sim.level)


def _preflight_work() -> None:
    """Timer thread: measure at the active Sim and map that point, nothing more."""
    build = _state.build
    try:
        point = build.on_main(_sim_point)
        if point is None:
            raise RuntimeError('Game target unavailable')
        result, frame = _measure(*point)
        # Mapping only: never call move_to, mouse, click, drag or park.
        mapped = (
            to_screen(*result['point'])
            if result['status'] == 'point' and frame.clear(result['point'])
            else None
        )
        _log('preflight', response=result, physical_point=mapped, window=frame.geometry)
        text = 'Build coordinate check complete. No pointer movement or construction. See SimSight log.'
    except Exception as exc:  # timer thread: every failure is reported
        if not isinstance(exc, (RuntimeError, ValueError)):
            simsight_log.error('build coordinates: preflight')
        _log('preflight-failed', error=str(exc))
        text = 'Build coordinate check failed: ' + str(exc)
    build.core.post(lambda: speech.speak(text, explicit=True))


@sims4.commands.Command(
    'simsight.buildcoords', 'a11y.buildcoords', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def preflight(delay: float = 5, _connection: Any = None) -> bool:
    if not math.isfinite(delay):
        return False
    install()
    timer = threading.Timer(max(0, min(30, delay)), _preflight_work)
    timer.daemon = True
    timer.name = 'a11y_build_coordinate_check'
    timer.start()
    sims4.commands.CheatOutput(_connection)(
        'Read-only projection check scheduled. Close the console and keep Build Mode focused.'
    )
    return True

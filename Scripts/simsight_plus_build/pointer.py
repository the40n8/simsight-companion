"""From world points to the real mouse: projection through SimSight, the
Windows mouse and keyboard, and the guarded engine calls."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import simsight_plus_core as core

if TYPE_CHECKING:
    from collections.abc import Sequence


def send_ui(msg: str) -> None:
    import distributor.ops
    import distributor.system

    distributor.system.Distributor.instance().add_op_with_no_owner(
        distributor.ops.SendUIMessage(msg)
    )


def _new_serial() -> int:
    import simsight_build as b
    import services

    return b.next_serial(services.current_zone_id())


def floor_height(x: float, z: float, level: int) -> float:
    """Height of the floor at a point (includes the house foundation), or ground height outside."""
    import routing
    import services
    import terrain

    try:
        surface = routing.SurfaceIdentifier(
            services.current_zone_id(), int(level), routing.SurfaceType.SURFACETYPE_WORLD
        )
        return terrain.get_terrain_height(x, z, routing_surface=surface)
    except Exception:  # no floor surface here: ground height plus 3 m a floor
        return terrain.get_terrain_height(x, z) + 3.0 * level


def cursor_off() -> None:
    def go() -> None:
        send_ui(f'A11y|build|off|{_new_serial()}')

    core.post(go)
    time.sleep(0.4)


# ---------------------------------------------------------------- real mouse


def user32() -> Any:
    import ctypes

    return ctypes.windll.user32  # type: ignore[attr-defined]  # Windows only


# Measured: SimSight's projected point sits about 21 interface pixels below where the game's own
# pointer hits the ground (October 2026, 1920x1080 at 125%).
OFFSET = [0.0, 0.0]


def mouse_base(flags: int, data: int = 0) -> None:
    user32().mouse_event(flags, 0, 0, data, 0)


LEFT_DOWN, LEFT_UP, WHEEL = 0x0002, 0x0004, 0x0800


def screenshot(path: str, mark: Sequence[float] | None = None) -> bool:
    """Diagnostics: save the game window's client area as a 24-bit BMP.
    mark=(x, y) screen pixels draws a small cross there (where the mouse
    clicks). Returns True when written. Worker thread; never raises."""
    import ctypes
    import struct

    try:

        class RECT(ctypes.Structure):
            _fields_ = [
                ('l', ctypes.c_long),
                ('t', ctypes.c_long),
                ('r', ctypes.c_long),
                ('b', ctypes.c_long),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

        u, g = user32(), ctypes.windll.gdi32  # type: ignore[attr-defined]  # Windows only
        g.CreateCompatibleDC.restype = ctypes.c_void_p
        g.CreateCompatibleBitmap.restype = ctypes.c_void_p
        g.SelectObject.restype = ctypes.c_void_p
        u.GetDC.restype = ctypes.c_void_p
        hwnd = u.GetForegroundWindow()
        rc = RECT()
        u.GetClientRect(hwnd, ctypes.byref(rc))
        origin = POINT(0, 0)
        u.ClientToScreen(hwnd, ctypes.byref(origin))
        w, h = rc.r - rc.l, rc.b - rc.t
        if w <= 0 or h <= 0:
            return False
        screen = u.GetDC(None)
        mem = g.CreateCompatibleDC(ctypes.c_void_p(screen))
        bmp = g.CreateCompatibleBitmap(ctypes.c_void_p(screen), w, h)
        old = g.SelectObject(ctypes.c_void_p(mem), ctypes.c_void_p(bmp))
        g.BitBlt(
            ctypes.c_void_p(mem),
            0,
            0,
            w,
            h,
            ctypes.c_void_p(screen),
            origin.x,
            origin.y,
            0x00CC0020 | 0x40000000,
        )
        stride = (w * 3 + 3) & ~3
        header = struct.pack('<IiiHHIIiiII', 40, w, h, 1, 24, 0, stride * h, 2835, 2835, 0, 0)
        info = ctypes.create_string_buffer(header + b'\0' * 16)
        bits = ctypes.create_string_buffer(stride * h)
        g.GetDIBits(ctypes.c_void_p(mem), ctypes.c_void_p(bmp), 0, h, bits, info, 0)
        g.SelectObject(ctypes.c_void_p(mem), ctypes.c_void_p(old))
        g.DeleteObject(ctypes.c_void_p(bmp))
        g.DeleteDC(ctypes.c_void_p(mem))
        u.ReleaseDC(None, ctypes.c_void_p(screen))
        data = bytearray(bits.raw)
        if mark is not None:
            mx, my = int(mark[0]) - origin.x, int(mark[1]) - origin.y
            for d in range(-12, 13):
                for px, py in ((mx + d, my), (mx, my + d), (mx + d, my + 1), (mx + 1, my + d)):
                    if 0 <= px < w and 0 <= py < h:
                        o = (h - 1 - py) * stride + px * 3
                        data[o : o + 3] = b'\x00\x00\xff' if abs(d) > 3 else b'\x00\xff\xff'
        with open(path, 'wb') as f:
            f.write(struct.pack('<2sIHHI', b'BM', 54 + len(data), 0, 0, 54))
            f.write(header)
            f.write(bytes(data))
        core.log(f'screenshot {path} ({w}x{h}, mark {mark})')
        return True
    except Exception as ex:  # diagnostics only: never stop the job for a picture
        core.log(f'screenshot failed: {ex!r}')
        return False


def _drag_base(a: Sequence[float], b: Sequence[float], shift: bool = False) -> None:
    u = user32()
    (ax, ay), (bx, by) = to_screen(*a), to_screen(*b)
    if shift:
        u.keybd_event(0x10, 0x2A, 0, 0)
    move_to(ax, ay)
    time.sleep(0.3)
    mouse(LEFT_DOWN)
    time.sleep(0.3)
    steps = 25
    for i in range(1, steps + 1):
        move_to(ax + (bx - ax) * i / steps, ay + (by - ay) * i / steps)
        time.sleep(0.03)
    move_to(bx, by)
    time.sleep(0.4)
    mouse(LEFT_UP)
    if shift:
        u.keybd_event(0x10, 0x2A, 2, 0)
    time.sleep(0.3)


def _click_base(a: Sequence[float], shift: bool = False) -> None:
    u = user32()
    (ax, ay) = to_screen(*a)
    if shift:
        u.keybd_event(0x10, 0x2A, 0, 0)
        time.sleep(0.05)
    move_to(ax, ay)
    time.sleep(0.3)
    mouse(LEFT_DOWN)
    time.sleep(0.1)
    mouse(LEFT_UP)
    if shift:
        time.sleep(0.05)
        u.keybd_event(0x10, 0x2A, 2, 0)
    time.sleep(0.3)


def tap_scan(scan: int, extended: bool = False) -> None:
    """Press and release a key by its scan code with SendInput, the way a keyboard does
    (the game ignores some keys sent with keybd_event)."""
    import ctypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ('wVk', ctypes.c_ushort),
            ('wScan', ctypes.c_ushort),
            ('dwFlags', ctypes.c_ulong),
            ('time', ctypes.c_ulong),
            ('dwExtraInfo', ctypes.c_size_t),
        ]

    class _U(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT), ('pad', ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ('u',)
        _fields_ = [('type', ctypes.c_ulong), ('u', _U)]

    for up in (False, True):
        flags = 0x0008 | (0x0001 if extended else 0) | (0x0002 if up else 0)
        inp = INPUT(type=1)
        inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        user32().SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.08)


# ------------------------------------------------- DaRainbowGirlXX's build engine work
#
# Merged 2026-11-14 (LeonianUniverse, lead developer). These used to be
# replaced at game start by her modules; the engine now calls them directly.
# simsight_build_coordinates measures the game client, window origin and
# Windows scaling, and checks focus and pointer arrival before every click.


def _display_observe(kind: str, data: dict[str, Any]) -> None:
    """simsight.inspect_display: log projections while the diagnostic is on."""
    import simsight_display_diagnostic as display

    display.observe(kind, data)


def project(x: float, z: float, level: int, timeout: float = 2.5, height: float = 0.0) -> Any:
    """Runs in a worker thread. Returns (stage_x, stage_y) or None."""
    import simsight_build_coordinates as coordinates

    result = coordinates.project(x, z, level, timeout=timeout, height=height)
    _display_observe(
        'project', {'world_args': (x, z, level), 'height': height, 'reported_game_point': result}
    )
    return result


def to_screen(sx: float, sy: float) -> Any:
    import simsight_build_coordinates as coordinates

    result = coordinates.to_screen(sx, sy)
    _display_observe('convert', {'input': (sx, sy), 'output': result})
    return result


def stage_size() -> Any:
    import simsight_build_coordinates as coordinates

    return coordinates.stage_size()


def move_to(x: float, y: float) -> Any:
    import simsight_build_coordinates as coordinates

    return coordinates.move_to(x, y)


def mouse(flags: int, data: int = 0) -> Any:
    """Button presses are checked (focus, Build state, pointer arrival); other
    events go straight to mouse_base."""
    import simsight_build_coordinates as coordinates

    return coordinates.mouse(flags, data)


def click(a: Sequence[float], shift: bool = False) -> Any:
    import simsight_build_coordinates as coordinates

    return coordinates.guarded_action(_click_base, (a,), shift)


def drag(a: Sequence[float], b: Sequence[float], shift: bool = False) -> Any:
    import simsight_build_coordinates as coordinates

    return coordinates.guarded_action(_drag_base, (a, b), shift)


def clear_of_panels(pt: Sequence[float]) -> Any:
    import simsight_build_coordinates as coordinates

    return coordinates.clear_of_panels(pt)


def park_mouse() -> Any:
    import simsight_build_coordinates as coordinates

    return coordinates.park_mouse()


def set_floor(level: int) -> Any:
    """Floor changes checked against the game's current, lowest and highest floor."""
    import simsight_build_helpers as helpers

    return helpers.set_floor(level)

# SimSight Plus - build helper (Alt+B, in Build mode).
# Walls and rooms are drawn by the game's own tools with a real mouse drag between two points.
# SimSight projects world points to screen points (its "buildaim" message); we then move the
# real mouse there. The tool (Room Tool, Wall Tool, a door, a paint) is chosen by the player in the
# Build catalog with SimSight's keyboard navigation; we only do the pointing.
from __future__ import annotations

import math
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from simsight_commands import real_annotations
import simsight_log
import simsight_plus_core as core
import simsight_steps as steps

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from simsight_steps import Job

    Point = tuple[float, float]  # world (x, z), or screen (x, y)
    Box = tuple[int, int, int, int]  # (e0, n0, e1, n1) meters from the west and north edges
    Grid = list[list[Any]]  # grid[n][e]: room id, 'free', 'inside' or 'object'
    Row = tuple[Any, ...]  # a core.choose row
    Action = Callable[[list[Any]], object]  # gets the projected screen points

# ---------------------------------------------------------------- lot frame
# Positions are given in whole meters from the lot's west edge (x) and north edge (y).


class Frame:
    def __init__(self, lot: Any) -> None:
        xs = [c.x for c in lot.corners]
        zs = [c.z for c in lot.corners]
        self.x0, self.x1 = min(xs), max(xs)
        self.z0, self.z1 = min(zs), max(zs)
        self.width = int(round(self.x1 - self.x0))  # west to east
        self.depth = int(round(self.z1 - self.z0))  # north to south

    def world(self, e: float, n: float) -> tuple[float, float]:
        """meters from west edge, meters from north edge -> world x, z"""
        return (self.x0 + e, self.z1 - n)

    def local(self, x: float, z: float) -> tuple[float, float]:
        return (x - self.x0, self.z1 - z)


def frame() -> Frame:
    import services

    return Frame(services.active_lot())


def in_build_mode() -> bool:
    try:
        import services

        return bool(services.current_zone().is_in_build_buy)
    except AttributeError:  # no zone yet (loading)
        return False


# ---------------------------------------------------------------- tile map


def tile_map(level: int = 0) -> tuple[Frame, Grid]:
    """grid[n][e] for each 1 m tile: 'room' id (int > 0), 'free', or 'object'."""
    import services
    import build_buy
    import terrain
    import sims4.math
    import simsight_plus_rooms as rooms

    f = frame()
    grid: Grid = [[None] * f.width for _ in range(f.depth)]
    for n in range(f.depth):
        for e in range(f.width):
            (x, z) = f.world(e + 0.5, n + 0.5)
            rid = rooms.room_id_at(x, z, level)
            pos = sims4.math.Vector3(x, terrain.get_terrain_height(x, z), z)
            try:
                outside = build_buy.is_location_outside(pos, level)
            except Exception:  # the game's build data may not answer for this tile
                outside = rid == 0
            grid[n][e] = 'free' if outside else (rid or 'inside')
    for obj in services.object_manager().values():
        tile = _object_tile(f, obj, level, rooms.tag_names)
        if tile is not None:
            (e, n) = tile
            if 0 <= n < f.depth and 0 <= e < f.width and grid[n][e] == 'free':
                grid[n][e] = 'object'
    return f, grid


def _object_tile(
    f: Frame, obj: Any, level: int, tag_names: Callable[[Any], set[str]]
) -> tuple[int, int] | None:
    """(e, n) tile of a bought object on this floor, or None."""
    try:
        if obj.is_sim or int(obj.level) != level:
            return None
        if not any(t.startswith('BuyCat') for t in tag_names(obj)):
            return None
        (e, n) = f.local(obj.position.x, obj.position.z)
        return (int(e), int(n))
    except (AttributeError, TypeError, ValueError):  # objects without a place on the lot
        return None


def free_spots(grid: Grid, w: int, d: int, limit: int = 6) -> list[tuple[int, int]]:
    """Top-left corners (e, n) where a w x d rectangle is completely free, preferring spots
    that touch the house (so the new room joins it) and are near the middle."""
    rows, cols = len(grid), len(grid[0]) if grid else 0
    spots: list[tuple[int, float, int, int]] = []
    for n in range(1, rows - d):  # rooms must stay 1 m inside the lot edge
        for e in range(1, cols - w):
            if all(grid[n + j][e + i] == 'free' for j in range(d) for i in range(w)):
                touch = 0
                for j in range(-1, d + 1):
                    for i in range(-1, w + 1):
                        if (
                            0 <= n + j < rows
                            and 0 <= e + i < cols
                            and (i in (-1, w) or j in (-1, d))
                            and grid[n + j][e + i] not in ('free', 'object')
                        ):
                            touch += 1
                spots.append((-touch, abs(e + w / 2 - cols / 2) + abs(n + d / 2 - rows / 2), e, n))
    spots.sort()
    out: list[tuple[int, int]] = []
    for _touch, _dist, e, n in spots:
        if all(abs(e - e2) >= max(2, w // 2) or abs(n - n2) >= max(2, d // 2) for (e2, n2) in out):
            out.append((e, n))
        if len(out) >= limit:
            break
    return out


def _touch_text(grid: Grid, e: int, n: int, w: int, d: int) -> str:
    sides = []
    rows, cols = len(grid), len(grid[0])

    def solid(r: int, c: int) -> bool:
        return 0 <= r < rows and 0 <= c < cols and grid[r][c] not in ('free', 'object')

    if any(solid(n - 1, e + i) for i in range(w)):
        sides.append('north')
    if any(solid(n + d, e + i) for i in range(w)):
        sides.append('south')
    if any(solid(n + j, e - 1) for j in range(d)):
        sides.append('west')
    if any(solid(n + j, e + w) for j in range(d)):
        sides.append('east')
    if not sides:
        return 'stands on its own'
    return 'joins the house on its {} side'.format(' and '.join(sides))


# ---------------------------------------------------------------- projection (via SimSight)


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


# ---------------------------------------------------------------- doing a pointing job


busy = [False]  # [True] while a pointing job runs (other modules read and set it)


VIEW_FROM = {'north': (0.0, -1.0), 'south': (0.0, 1.0), 'east': (-1.0, 0.0), 'west': (1.0, 0.0)}


def _focus_camera(
    x: float,
    z: float,
    level: int,
    view: tuple[float, float] | None = None,
    focus_height: float | None = None,
) -> None:
    """Center the camera on a point. view=(dx, dz): put the camera on that side of the point,
    looking at it, so a wall there is seen from the front and is not cut away.
    focus_height: look at that height above the floor instead of the ground (hanging wall
    objects: the click is the screen centre, which must be on the wall). The camera
    itself stays where it was, above the walls."""
    import camera
    import terrain
    import sims4.math

    y = terrain.get_terrain_height(x, z) + 3.0 * level
    target = sims4.math.Vector3(x, y, z)
    if focus_height is not None:
        target = sims4.math.Vector3(x, floor_height(x, z, level) + focus_height, z)
    if view is None:
        camera.focus_on_position(target)
        return
    (dx, dz) = view
    eye = sims4.math.Vector3(x + dx * 7.0, y + 6.0, z + dz * 7.0)
    camera.focus_on_object_from_position(obj_position=target, camera_position=eye)


def _zoom_out(notches: int = 2) -> None:
    u = user32()
    w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    u.SetCursorPos(w // 2, h // 2)
    for _ in range(notches):
        mouse(WHEEL, -120)
        time.sleep(0.15)


def overview_camera(points_world: Sequence[Point], level: int = 0) -> None:
    """Look down from high above the street side (north) at a group of points, so a whole house fits
    on screen. Aimed at the near (north) edge: points close to the camera drop off the bottom otherwise."""
    import camera
    import terrain
    import sims4.math

    cx = sum(p[0] for p in points_world) / len(points_world)
    zmax = max(p[1] for p in points_world)
    span = max(
        max(p[0] for p in points_world) - min(p[0] for p in points_world),
        zmax - min(p[1] for p in points_world),
    )
    tz = zmax - 1.0
    y = terrain.get_terrain_height(cx, tz) + 3.0 * level
    up = 14.0 + span * 1.1
    camera.focus_on_object_from_position(
        obj_position=sims4.math.Vector3(cx, y, tz),
        camera_position=sims4.math.Vector3(cx, y + up, tz + up * 0.3),
    )


def point_and_act_base(  # noqa: PLR0913, PLR0917 - signature other modules call
    points_world: list[Point],
    level: int,
    action: Action,
    describe: str,
    height: float = 0.0,
    view: tuple[float, float] | None = None,
    overview: bool = False,
    focus_height: float | None = None,
) -> bool:
    """Worker thread only. Center the camera, project the points, do the mouse action.
    Returns True when the action was performed."""
    cx = sum(p[0] for p in points_world) / len(points_world)
    cz = sum(p[1] for p in points_world) / len(points_world)
    if overview:
        core.post(overview_camera, points_world, level)
    else:
        core.post(_focus_camera, cx, cz, level, view, focus_height)
    time.sleep(1.8)
    screen = None
    for attempt in range(4):
        pts: list[Any] = []
        for x, z in points_world:
            p = project(x, z, level, height=height)
            if p is None:
                break
            pts.append(p)
        if len(pts) == len(points_world) and all(clear_of_panels(pt) for pt in pts):
            screen = pts
            break
        core.log(
            f'build: projection attempt {attempt + 1} failed or under a panel ({pts}), zooming out'
        )
        cursor_off()
        if overview:
            core.post(overview_camera, points_world + [(cx, cz + 3.0 * (attempt + 1))], level)
        else:
            _zoom_out(3)
            core.post(_focus_camera, cx, cz, level, view, focus_height)
        time.sleep(1.5)
    cursor_off()
    if screen is None:
        core.post(core.speak, 'I could not get that spot on screen. Try a smaller size.')
        return False
    core.log(f'build: {describe} at screen {screen}')
    action(screen)
    park_mouse()
    time.sleep(1.0)
    return True


def on_main(fn: Callable[..., Any], *args: Any) -> Any:
    """Worker thread only: run fn on the game thread and wait for its result."""
    return steps.on_main(fn, *args)


NO_MOUSE_TEXT = 'This build step moves the mouse, which SimSight cannot do on the Mac yet.'


def no_mouse() -> bool:
    """True, and says so, where the real mouse cannot be moved (the Mac: no
    ctypes, no simsight_build_coordinates). The pointing workers would only
    fail there, and on a thread, which the Mac game does not survive."""
    if not steps.IS_MAC:
        return False
    core.speak(NO_MOUSE_TEXT)
    return True


def undo() -> None:
    send_ui(f'A11y|build|undo|{_new_serial()}')


def run_job(  # noqa: PLR0913, PLR0917 - signature other modules call
    points_world: list[Point],
    level: int,
    action: Action,
    describe: str,
    verify: Callable[[], object] | None = None,
    height: float = 0.0,
    view: tuple[float, float] | None = None,
    focus_height: float | None = None,
) -> None:
    """Do one pointing job in a background thread so the game keeps drawing frames."""
    if no_mouse():
        return
    if busy[0]:
        core.speak('Still working on the last build step.')
        return
    busy[0] = True

    def work() -> None:
        try:
            if (
                point_and_act(
                    points_world, level, action, describe, height, view, focus_height=focus_height
                )
                and verify is not None
            ):
                core.post(verify)
        except Exception:
            core.log_exc('build job')
            core.post(core.speak, 'Sorry, the build step failed.')
        finally:
            busy[0] = False

    threading.Thread(target=work, name='simsight_plus_build', daemon=True).start()


# ---------------------------------------------------------------- selected tool / product

_product: dict[str, Any] = {'id': None, 'name': ''}  # the chosen catalog item


def _note_product(instance_text: Any, name: Any) -> None:
    """simsight_place's product listener: remember the chosen catalog item."""
    try:
        text = str(instance_text or '').strip()
        _product['id'] = int(text) if text.isdigit() and int(text) else None
        _product['name'] = str(name or '').strip()
        core.log('build: selected product {} {!r}'.format(_product['id'], _product['name']))
    except Exception:  # noting the product must never stop SimSight choosing it
        simsight_log.error('plus build: note selected product')


def watch_product() -> None:
    """Learn each product the Build catalog selects from now on."""
    try:
        import simsight_place as place
    except ImportError:
        return
    place.on_product(_note_product)


def tool_name() -> str:
    return _product['name'] or ''


def _tool_is(*words: str) -> bool:
    name = tool_name().lower()
    return any(w in name for w in words)


# ---------------------------------------------------------------- user features


def whole_numbers(text: str | None) -> list[int]:
    return [int(v) for v in re.findall(r'\d+', text or '')]


def build_room() -> None:
    if not in_build_mode():
        core.speak('Go to Build mode first with F2.')
        return
    if no_mouse():
        return
    if not _tool_is('room tool'):
        core.speak(
            'First choose the Room Tool in the Build catalog. Tab to the catalog, open Walls and Empty Rooms, '
            'choose Room Tool with Enter, press Escape, then Alt+B again.'
        )
        return

    def got_size(text: str) -> None:
        nums = whole_numbers(text)
        if len(nums) < 2 or not (1 <= nums[0] <= 30 and 1 <= nums[1] <= 30):
            core.speak('Please type two numbers, for example 4 5.')
            return
        (w, d) = nums[0], nums[1]
        f, grid = tile_map()
        spots = free_spots(grid, w, d)
        rows: list[Row] = [
            (
                f'{e} from west, {n} from north',
                f'This room {_touch_text(grid, e, n, w, d)}.',
                (e, n),
            )
            for e, n in spots
        ]
        rows.append(
            (
                'Type a position',
                'Type the meters from the west edge and from the north edge.',
                'type',
            )
        )
        if not spots:
            core.speak(f'No free space fits {w} by {d} meters. You can still type a position.')

        def place_at(value: Any) -> None:
            if value == 'type':
                core.ask_text(
                    'Room position',
                    'Type meters from the west edge, then meters from the north edge, for example 2 3.',
                    '',
                    lambda t: _do_room(w, d, *(whole_numbers(t) + [None, None])[:2]),
                )
            else:
                _do_room(w, d, value[0], value[1])

        core.choose(
            'Where to build',
            f'Free places for a {w} by {d} meter room. The position is the north west corner.',
            rows,
            place_at,
            tag='build',
        )

    core.ask_text(
        'Room size',
        'Type the width from west to east, then the depth from north to south, in meters. For example 4 5.',
        '',
        got_size,
    )


def _new_room_box(before: set[tuple[Box, int]]) -> Box | None:
    """Box (e0, n0, e1, n1) of rooms that did not exist before, or None. Rooms are compared by their
    box, because the game may renumber a room when a new one is built next to it."""
    import simsight_plus_rooms as rooms

    new = [room_box(r) for r in rooms.scan_rooms() if (room_box(r), r.level) not in before]
    if not new:
        return None
    return (
        min(b[0] for b in new),
        min(b[1] for b in new),
        max(b[2] for b in new),
        max(b[3] for b in new),
    )


def room_ids() -> set[tuple[Box, int]]:
    import simsight_plus_rooms as rooms

    return {(room_box(r), r.level) for r in rooms.scan_rooms()}


def _do_room(w: int, d: int, e: int | None, n: int | None) -> None:
    if e is None or n is None:
        core.speak('I need two numbers.')
        return
    f = frame()
    if e < 1 or n < 1 or e + w > f.width - 1 or n + d > f.depth - 1:
        core.speak(
            f'That does not fit. Rooms must stay at least 1 meter inside the lot edge. The lot is {f.width} by {f.depth} meters.'
        )
        return
    if no_mouse():
        return
    if busy[0]:
        core.speak('Still working on the last build step.')
        return
    busy[0] = True
    want = (e, n, e + w, n + d)
    core.speak(f'Building a {w} by {d} meter room. Please wait.')

    def work() -> None:
        try:
            box = room_worker(want)
            if box in (None, 'nomoney', 'open', 'noscreen'):
                if box in ('nomoney', None):
                    core.post(
                        core.speak,
                        'The room was not built. Something may be in the way, or there is not enough money.',
                    )
                elif box == 'open':
                    core.post(
                        core.speak, 'The walls were built, but they did not make a closed room.'
                    )
                return
            (bw, bd) = (box[2] - box[0], box[3] - box[1])
            if (bw, bd) == (w, d) and box[:2] == want[:2]:
                core.post(
                    core.speak,
                    f'Built a {w} by {d} meter room at {e} from west, {n} from north.',
                )
            else:
                core.post(
                    core.speak,
                    f'Built a room, but it came out {bw} by {bd} meters, at {box[0]} from west, {box[1]} from north. '
                    'You can undo it from the SimSight build actions.',
                )
        except Exception:
            core.log_exc('room job')
            core.post(core.speak, 'Sorry, building the room failed.')
        finally:
            busy[0] = False

    threading.Thread(target=work, name='a11yplus_room', daemon=True).start()


def room_worker_base(
    want: Sequence[int], overview: bool = False, tries: int = 4, level: int = 0
) -> Any:
    """Worker thread only. Draw a room with the Room Tool (already chosen) from corner (want[0], want[1])
    to (want[2], want[3]), in meters from the west and north edges. The room tool snaps to the nearest
    grid corner; where a side meets an existing wall the game sometimes stops a meter short, so check
    the result and redraw with a correction. Returns the built box, or "nomoney", "open", "noscreen"."""
    f = frame()
    fix = [0.0, 0.0, 0.0, 0.0]
    box: Box | None = None
    if not set_floor(level):
        return 'nofloor'
    for attempt in range(tries):
        before = on_main(room_ids) or set()
        money = on_main(funds)
        a = f.world(want[0] - fix[0], want[1] - fix[1])
        b = f.world(want[2] - fix[2], want[3] - fix[3])
        if not point_and_act(
            [a, b],
            level,
            lambda s: drag(s[0], s[1]),
            f'room {want} try {attempt + 1}',
            overview=overview,
        ):
            return 'noscreen'
        # 0.7.1: slower computers take longer to report a new room (upstairs especially).
        for _wait in range(10):
            time.sleep(0.5)
            box = on_main(_new_room_box, before)
            if box is not None:
                break
        core.log(f'build: wanted {want} got {box} (fix {fix})')
        if box is None:
            spent = (money or 0) - (on_main(funds) or 0)
            if spent <= 0:
                return 'nomoney'
            if attempt == tries - 1:
                return 'open'
            # 0.7.1: walls were built but did not close a room: take them back and draw again.
            core.log('build: walls did not close, undo and try again')
            on_main(undo)
            time.sleep(2.0)
            if not set_floor(level):
                return 'nofloor'
            continue
        err = [box[i] - want[i] for i in range(4)]
        if err == [0, 0, 0, 0]:
            break
        if attempt == tries - 1 or any(abs(v) > 2 for v in err):
            break
        on_main(undo)
        time.sleep(1.5)
        # Half a meter at a time: the pointer lands between grid corners.
        fix = [fix[i] + 0.5 * err[i] for i in range(4)]
    return box


def build_wall() -> None:
    if not in_build_mode():
        core.speak('Go to Build mode first with F2.')
        return
    if no_mouse():
        return
    # The Wall Tool has its own game key, B; press it for the player.
    u = user32()
    u.keybd_event(0x42, 0x30, 0, 0)
    u.keybd_event(0x42, 0x30, 2, 0)
    _product.update(id=None, name='Wall Tool')

    def got(text: str) -> None:
        nums = whole_numbers(text)
        if len(nums) < 3:
            core.speak('Please type three numbers: from west, from north, and length.')
            return
        (e, n, length) = nums[:3]
        rows = [
            ('East', None, (1, 0)),
            ('South', None, (0, 1)),
            ('West', None, (-1, 0)),
            ('North', None, (0, -1)),
        ]

        def go(direction: tuple[int, int]) -> None:
            f = frame()
            (de, dn) = direction
            e2, n2 = e + de * length, n + dn * length
            if not (
                0 <= e <= f.width
                and 0 <= n <= f.depth
                and 0 <= e2 <= f.width
                and 0 <= n2 <= f.depth
            ):
                core.speak('That wall would leave the lot.')
                return
            core.speak(f'Drawing a {length} meter wall. Please wait.')
            run_job(
                [f.world(e, n), f.world(e2, n2)],
                0,
                lambda s: drag(s[0], s[1]),
                'wall',
                cost_check(
                    'Built the wall', 'The wall was not built. Something may be in the way.'
                ),
            )

        core.choose(
            'Wall direction',
            'Which way does the wall go from the start point?',
            rows,
            go,
            tag='build',
        )

    core.ask_text(
        'Wall',
        'Type the start point as meters from the west edge and from the north edge, then the length. For example 2 3 5.',
        '',
        got,
    )


def funds() -> int | None:
    try:
        import services

        return int(services.active_household().funds.money)
    except (AttributeError, TypeError, ValueError):  # no active household
        return None


def cost_check(done_text: str, fail_text: str) -> Callable[[], None]:
    """A verify callback: compares household money before and after the job."""
    before = funds()
    started = time.monotonic()

    def verify() -> None:
        after = funds()
        if before is None or after is None:
            core.speak(done_text + '.')
        elif after < before:
            core.speak(f'{done_text}. It cost {before - after:,} simoleons.')
        elif after > before:
            core.speak(
                f'{done_text}. You got {after - before:,} simoleons back for what it replaced.'
            )
        else:
            reason = last_error['text'] if last_error['at'] > started else ''
            core.speak(f'Not done. The game says: {reason}' if reason else fail_text)

    return verify


def room_rows() -> list[Row]:
    import simsight_plus_rooms as rooms

    return [
        (r.name, f'{r.size_text()}, {rooms.floor_name(r.level)}', r) for r in rooms.scan_rooms()
    ]


def room_box(room: Any) -> Box:
    f = frame()
    es = [f.local(t[0], t[1])[0] for t in room.tiles]
    ns = [f.local(t[0], t[1])[1] for t in room.tiles]
    return (
        math.floor(min(es)),
        math.floor(min(ns)),
        math.floor(max(es)) + 1,
        math.floor(max(ns)) + 1,
    )


def wall_point(room: Any, side: str, where: float = 0.5) -> tuple[float, float]:
    """World (x, z) on the middle of a room wall, a little inside the room so the click hits it."""
    f = frame()
    (e0, n0, e1, n1) = room_box(room)
    inset = 0.0
    if side == 'north':
        return f.world(e0 + (e1 - e0) * where, n0 + inset)
    if side == 'south':
        return f.world(e0 + (e1 - e0) * where, n1 - inset)
    if side == 'west':
        return f.world(e0 + inset, n0 + (n1 - n0) * where)
    return f.world(e1 - inset, n0 + (n1 - n0) * where)


def room_tiles(room: Any) -> set[tuple[int, int]]:
    f = frame()
    return {
        (int(math.floor(f.local(t[0], t[1])[0])), int(math.floor(f.local(t[0], t[1])[1])))
        for t in room.tiles
    }


# side -> (tile step to the outside neighbor, how to read "left to right" when facing that wall from inside)
_SIDES: dict[str, tuple[tuple[int, int], Callable[[int, int], int]]] = {
    'north': ((0, -1), lambda e, n: e),
    'south': ((0, 1), lambda e, n: -e),
    'west': ((-1, 0), lambda e, n: -n),
    'east': ((1, 0), lambda e, n: n),
}


def room_walls(room: Any) -> list[dict[str, Any]]:
    """List of walls: dict(side, cells=[(e, n), ...] left to right). Each cell is the 1 m wall piece
    on that side of the room tile (e, n)."""
    tiles = room_tiles(room)
    walls: list[dict[str, Any]] = []
    for side, ((de, dn), order) in _SIDES.items():
        cells = sorted(
            [t for t in tiles if (t[0] + de, t[1] + dn) not in tiles],
            key=lambda t: order(*t),
        )
        if cells:
            walls.append({'side': side, 'cells': cells})
    return walls


def wall_cell_point(side: str, cell: tuple[int, int]) -> tuple[float, float]:
    """World (x, z) at the middle of the wall piece on `side` of tile `cell`."""
    f = frame()
    (e, n) = cell
    if side == 'north':
        return f.world(e + 0.5, n)
    if side == 'south':
        return f.world(e + 0.5, n + 1)
    if side == 'west':
        return f.world(e, n + 0.5)
    return f.world(e + 1, n + 0.5)


def wall_things() -> list[tuple[float, float, str]]:
    """Doors and windows on the lot: list of (e, n, kind)."""
    import services
    import simsight_plus_rooms as rooms

    f = frame()
    out = []
    for obj in services.object_manager().values():
        thing = _wall_thing(f, obj, rooms.tag_names(obj))
        if thing is not None:
            out.append(thing)
    return out


def _wall_thing(f: Frame, obj: Any, names: set[str]) -> tuple[float, float, str] | None:
    kind = (
        'door'
        if ('Build_Door' in names or 'Build_Arch' in names)
        else ('window' if 'Build_Window' in names else None)
    )
    if not kind:
        return None
    try:
        (e, n) = f.local(obj.position.x, obj.position.z)
    except (AttributeError, TypeError):  # objects without a place on the lot
        return None
    return (e, n, kind)


def _cell_status(
    side: str, cell: tuple[int, int], things: list[tuple[float, float, str]]
) -> str | None:
    (x, z) = wall_cell_point(side, cell)
    (e, n) = frame().local(x, z)
    for te, tn, kind in things:
        if abs(te - e) < 0.75 and abs(tn - n) < 0.75:
            return kind
    return None


def reselect() -> None:
    """Choose the catalog item again right before using it (the game sometimes drops it)."""
    index = catalog_state.get('chosen')
    if index is not None:
        on_main(send_ui, f'A11y|plus|select|{index}')
        time.sleep(0.7)


def place_on_wall() -> None:
    if not in_build_mode():
        core.speak('Go to Build mode first with F2.')
        return
    if no_mouse():
        return
    if not tool_name():
        core.speak('First choose a door or window with Alt+B, Catalog.')
        return
    rows = room_rows()
    if not rows:
        core.speak('There are no rooms yet.')
        return

    def got_room(room: Any) -> None:
        walls = room_walls(room)
        things = wall_things()
        wrows = []
        for w in walls:
            used = [_cell_status(w['side'], c, things) for c in w['cells']]
            note = ', '.join(
                '{} {}{}'.format(used.count(k), k, '' if used.count(k) == 1 else 's')
                for k in ('door', 'window')
                if k in used
            )
            wrows.append(
                (
                    '{} wall, {} meters'.format(w['side'].capitalize(), len(w['cells'])),
                    ('Has ' + note + '.') if note else 'No doors or windows yet.',
                    w,
                )
            )

        def got_wall(w: dict[str, Any]) -> None:
            srows = []
            for i, cell in enumerate(w['cells']):
                st = _cell_status(w['side'], cell, things)
                srows.append(
                    (
                        f'Meter {i + 1} from the left',
                        f'A {st} is here already.' if st else 'Free.',
                        cell,
                    )
                )

            def got_cell(cell: tuple[int, int]) -> None:
                (x, z) = wall_cell_point(w['side'], cell)
                core.speak(
                    'Placing {} on the {} wall of the {}. Please wait.'.format(
                        tool_name(), w['side'], room.name
                    )
                )

                def act(s: list[Any]) -> None:
                    reselect()
                    click(s[0])

                run_job(
                    [(x, z)],
                    room.level,
                    act,
                    'wall item',
                    height=0.15,
                    view=VIEW_FROM[w['side']],
                    verify=cost_check(
                        f'Placed {tool_name()}',
                        'It was not placed. There may be a door, window or furniture in the way, or the wall is too short.',
                    ),
                )

            core.choose(
                'Where on the {} wall'.format(w['side']),
                'Meters are counted from the left as you face the wall from inside the room.',
                srows,
                got_cell,
                tag='build',
            )

        core.choose('Which wall', f'Walls of the {room.name}.', wrows, got_wall, tag='build')

    core.choose(f'Place {tool_name()}', 'Choose the room.', rows, got_room, tag='build')


def paint_room(part: str) -> None:
    if not in_build_mode():
        core.speak('Go to Build mode first with F2.')
        return
    if no_mouse():
        return
    if not tool_name():
        core.speak(
            'First choose a wall pattern or a floor pattern in the Build catalog, press Escape, then Alt+B again.'
        )
        return
    rows = room_rows()

    def got_room(room: Any) -> None:
        target = wall_point(room, 'north') if part == 'walls' else room.center
        core.speak(f'Painting the {part} of the {room.name}. Please wait.')
        run_job(
            [target],
            room.level,
            lambda s: click(s[0], shift=True),
            'paint ' + part,
            cost_check(f'Painted the {part} of the {room.name}', 'Nothing was painted.'),
            height=0.15 if part == 'walls' else 0.0,
            view=VIEW_FROM['north'] if part == 'walls' else None,
        )

    core.choose(
        f'Paint {part}',
        'Choose the room. The whole room gets the chosen pattern.',
        rows,
        got_room,
        tag='build',
    )


# ---------------------------------------------------------------- accessible catalog
# Uses the SimSight Plus bridge added to SimSight's interface file: "A11y|plus|catalog|type|tags|serial"
# lists a catalog category, "A11y|plus|select|index" picks an item exactly as clicking it would.

# (menu name, product type, comma separated catalog tag names, what to do next)
BUILD_CATALOG: tuple[tuple[str, int, str, str], ...] = (
    ('Doors', 2, 'BUILDTAG_DOOR', 'wall'),
    ('Windows', 2, 'BUILDTAG_WINDOW', 'wall'),
    ('Wall patterns, paint and wallpaper', 7, '', 'paintwalls'),
    ('Floor patterns', 5, '', 'paintfloor'),
    ('Stairs', 14, '', 'none'),
    ('Fences', 10, '', 'none'),
    ('Gates', 2, 'BUILDTAG_GATE', 'wall'),
    ('Columns', 2, 'BUILDTAG_COLUMN', 'none'),
    ('Roofs', 4, '', 'none'),
    ('Rooms and wall tools', 16, 'BUILDTAG_BLOCK_WALLTOOL,BUILDTAG_BLOCK_WITH_WALLS', 'tool'),
    ('Plants, bushes and trees', 2, 'BUILDTAG_SHRUB,BUILDTAG_FLOWER,BUILDTAG_TREE', 'object'),
)
STYLED_ROOMS: tuple[tuple[str, str], ...] = (
    ('Bathrooms', 'BUYCAT_MAG_BATHROOM'),
    ('Bedrooms', 'BUYCAT_MAG_BEDROOM'),
    ('Kitchens', 'BUYCAT_MAG_KITCHEN'),
    ('Living rooms', 'BUYCAT_MAG_LIVINGROOM'),
    ('Dining rooms', 'BUYCAT_MAG_DINNINGROOM'),
    ('Studies', 'BUYCAT_MAG_STUDY'),
    ('Kids rooms', 'BUYCAT_MAG_KIDS'),
    ('Outdoor areas', 'BUYCAT_MAG_OUTDOOR'),
)
BUY_CATALOG: tuple[tuple[str, str], ...] = (
    ('Beds', 'BUYCAT_SS_BED'),
    ('Sofas and loveseats', 'BUYCAT_SS_SOFA,BUYCAT_SS_LOVESEAT'),
    ('Living chairs', 'BUYCAT_SS_LIVINGCHAIR'),
    ('Dining tables', 'BUYCAT_SS_DININGTABLE'),
    ('Dining chairs', 'BUYCAT_SS_DININGCHAIR'),
    ('Desks', 'BUYCAT_SS_DESK'),
    ('Desk chairs', 'BUYCAT_SS_DESKCHAIR'),
    ('Coffee and end tables', 'BUYCAT_SS_COFFEETABLE,BUYCAT_SS_ENDTABLE'),
    ('Counters', 'BUYCAT_SS_COUNTER'),
    ('Dressers', 'BUYCAT_SS_DRESSER'),
    ('Bookshelves', 'BUYCAT_SS_BOOKSHELF'),
    ('Refrigerators', 'BUYCAT_PA_REFRIGERATOR'),
    ('Stoves and ovens', 'BUYCAT_PA_STOVE'),
    ('Small appliances', 'BUYCAT_PA_SMALLAPPLIANCE,BUYCAT_PA_MICROWAVE,BUYCAT_PA_COFFEE_MAKER'),
    ('Sinks', 'BUYCAT_PA_SINK'),
    ('Toilets', 'BUYCAT_PA_TOILET'),
    ('Showers', 'BUYCAT_PA_SHOWER'),
    ('Bathtubs', 'BUYCAT_PA_TUB'),
    ('Trash cans', 'BUYCAT_PA_DISPOSABLE,BUYCAT_PA_DISPOSAL_INDOOR'),
    ('Televisions', 'BUYCAT_EE_TV'),
    ('Computers', 'BUYCAT_EE_COMPUTER'),
    ('Music and audio', 'BUYCAT_EE_AUDIO'),
    ('Creative activities, easels and instruments', 'BUYCAT_EE_CREATIVEACTIVITY'),
    ('Knowledge activities, chess and telescopes', 'BUYCAT_EE_KNOWLEDGEACTIVITY'),
    ('Exercise and active', 'BUYCAT_EE_ACTIVEACTIVITY'),
    ('Kids and toddlers', 'BUYCAT_EE_KIDFURNITURE,BUYCAT_EE_KIDTOY,BUYCAT_EE_TODDLERS'),
    ('Ceiling lights', 'BUYCAT_LD_CEILINGLIGHT'),
    ('Table and floor lamps', 'BUYCAT_LD_TABLELAMP,BUYCAT_LD_FLOORLAMP'),
    ('Wall lights', 'BUYCAT_LD_WALLLIGHT'),
    ('Outdoor lights', 'BUYCAT_LD_OUTDOORLIGHT'),
    ('Mirrors', 'BUYCAT_LD_MIRROR'),
    ('Rugs', 'BUYCAT_LD_RUG'),
    ('Plants for indoors', 'BUYCAT_LD_PLANT'),
    ('Wall decorations and paintings', 'BUYCAT_LD_WALLDECORATION'),
    ('Other decorations', 'BUYCAT_LD_MISCDECORATION,BUYCAT_LD_SCULPTURE'),
    (
        'Outdoor seating and tables',
        'BUYCAT_SS_OUTDOORSEATING,BUYCAT_SS_OUTDOORTABLES,BUYCAT_SS_OUTDOORCHAIR',
    ),
    ('Grills and outdoor cooking', 'BUYCAT_PA_OUTDOORCOOKING'),
)

# The last listed catalog category; other modules read 'chosen' and 'items'.
catalog_state: dict[str, Any] = {
    'serial': 0,
    'items': [],
    'total': 0,
    'name': '',
    'next': '',
    'event': None,
}
_bridge = {'pong': 0.0}
last_error: dict[str, Any] = {'text': '', 'at': 0.0}  # the game's last build error


def flash_reply_base(encoded: str = '') -> bool:
    from urllib.parse import unquote

    text = unquote(str(encoded or ''))
    parts = text.split('|')
    kind = parts[0]
    if kind == 'pong':
        _bridge['pong'] = time.monotonic()
        core.log('bridge: pong')
    elif kind == 'cat' and len(parts) >= 5 and parts[1] == str(catalog_state['serial']):
        catalog_state['total'] = int(parts[3])
        for item in parts[4].split('^'):
            bits = item.split('~')
            if len(bits) >= 2:
                catalog_state['items'].append(
                    (
                        unquote(bits[0]),
                        int(float(bits[1] or 0)),
                        int(bits[2] or 1) if len(bits) > 2 else 1,
                    )
                )
    elif kind == 'catend' and len(parts) >= 2 and parts[1] == str(catalog_state['serial']):
        if catalog_state.get('quiet') is not None:
            catalog_state['quiet'].set()
        else:
            core.post(_show_catalog)
    elif kind in ('wallsup', 'wallsrestored'):
        core.log('bridge: {} (wall view mode {})'.format(kind, parts[1] if len(parts) > 1 else '?'))
    elif kind == 'selected':
        name = unquote(parts[2]) if len(parts) > 2 else 'item'
        _product['name'] = name
        if (
            not catalog_state.get('silent_select') and not busy[0]
        ):  # quiet while a build job picks things itself
            core.speak('Chosen: {}. {}'.format(name, NEXT_HINT.get(catalog_state['next'], '')))
    elif kind == 'builderror':
        reason = unquote(parts[1]) if len(parts) > 1 else ''
        last_error.update(text=reason, at=time.monotonic())
        core.log('game build error: ' + reason)
    elif kind == 'error':
        core.log('bridge error: ' + text)
        core.speak('The catalog said: ' + '|'.join(parts[1:]))
    return True


NEXT_HINT = {
    'wall': 'Now use Alt+B, Put the chosen door or window on a wall.',
    'paintwalls': 'Now use Alt+B, Paint the walls of a room.',
    'paintfloor': 'Now use Alt+B, Paint the floor of a room.',
    'object': 'Now use Alt+B, Put the chosen object in a room.',
    'tool': 'Now use Alt+B to build with it.',
    'room': 'Now use Alt+B, Place the chosen furnished room.',
    'none': 'Placing this needs the build cursor. See the SimSight build controls.',
}


def bridge_ok() -> bool:
    return time.monotonic() - _bridge['pong'] < 600


def _register_flash_command() -> None:
    import sims4.commands

    @sims4.commands.Command('a11yplus.flash', command_type=sims4.commands.CommandType.Live)
    @real_annotations
    def a11yplus_flash(encoded: str = '', _connection: Any = None) -> bool:
        try:
            return _flash_reply(encoded)
        except Exception:
            core.log_exc('flash reply')
            return False


try:
    _register_flash_command()
except Exception:
    core.log_exc('register a11yplus.flash')


def request_catalog(name: str, product_type: int, tags: str, nxt: str) -> None:
    if not in_build_mode():
        core.speak('Go to Build mode first with F2.')
        return
    catalog_state['serial'] += 1
    catalog_state.update(items=[], total=0, name=name, next=nxt, quiet=None)
    core.speak(f'Opening {name}.')
    send_ui('A11y|plus|catalog|{}|{}|{}'.format(product_type, tags, catalog_state['serial']))

    def timeout(serial: int = catalog_state['serial']) -> None:
        if catalog_state['serial'] == serial and not catalog_state['items'] and not bridge_ok():
            core.speak(
                'The catalog did not answer. The SimSight Plus part of the SimSight interface file may be missing.'
            )

    steps.after(4.0, core.post, timeout, daemon=False)


def fetch_catalog_steps(
    product_type: int, tags: str, nxt: str, timeout: float = 6.0
) -> Job[list[tuple[str, int, int]]]:
    """Job steps: list a catalog category without showing it. Returns [(name, price, colors)]."""
    ev = threading.Event()

    def start() -> None:
        catalog_state['serial'] += 1
        catalog_state.update(items=[], total=0, name='', next=nxt, quiet=ev)
        send_ui('A11y|plus|catalog|{}|{}|{}'.format(product_type, tags, catalog_state['serial']))

    core.post(start)
    yield steps.WaitEvent(ev, timeout)
    catalog_state['quiet'] = None
    return list(catalog_state['items'])


def fetch_catalog(
    product_type: int, tags: str, nxt: str, timeout: float = 6.0
) -> list[tuple[str, int, int]]:
    """Worker thread only: list a catalog category without showing it. Returns [(name, price, colors)]."""
    return steps.run(fetch_catalog_steps(product_type, tags, nxt, timeout))


def select_item_steps(index: int, name: str) -> Job[None]:
    """Job steps: choose catalog item `index` of the last fetched list, as clicking it would."""
    catalog_state['chosen'] = index
    _product['name'] = name
    yield steps.OnMain(send_ui, f'A11y|plus|select|{index}')
    yield steps.Sleep(0.8)


def select_item(index: int, name: str) -> None:
    """Worker thread only: choose catalog item `index` of the last fetched list, as clicking it would."""
    steps.run(select_item_steps(index, name))


def _show_catalog() -> None:
    items = catalog_state['items']
    if not items:
        core.speak('{} is empty.'.format(catalog_state['name']))
        return
    rows = []
    for i, (name, price, variations) in enumerate(items):
        desc = f'{price:,} simoleons'
        if variations > 1:
            desc += f', {variations} colors'
        rows.append((name, desc, i))
    core.choose(
        catalog_state['name'],
        f'{len(items)} items. Enter chooses one.',
        rows,
        _choose_item,
        tag='catalog',
    )


def _choose_item(i: int) -> None:
    catalog_state['chosen'] = i
    send_ui(f'A11y|plus|select|{i}')


def open_catalog() -> None:
    rows: list[Row] = [
        (name, None, ('build', name, t, tags, nxt)) for (name, t, tags, nxt) in BUILD_CATALOG
    ]
    rows.insert(
        0,
        (
            'Furnished rooms',
            'Ready-made rooms with walls, floor, door and furniture: bathrooms, bedrooms, kitchens and more.',
            ('styled',),
        ),
    )
    rows.append(
        (
            'Furniture and other objects',
            'Beds, fridges, toilets, lights and more, by kind.',
            ('buy',),
        )
    )

    def picked(value: tuple[Any, ...]) -> None:
        if value[0] == 'styled':
            srows = [(name, None, (name, tags)) for (name, tags) in STYLED_ROOMS]
            core.choose(
                'Furnished rooms',
                'Choose a kind of room.',
                srows,
                lambda v: request_catalog('Furnished ' + v[0].lower(), 23, v[1], 'room'),
                tag='catalog',
            )
        elif value[0] == 'buy':
            brows = [(name, None, (name, tags)) for (name, tags) in BUY_CATALOG]
            core.choose(
                'Furniture and objects',
                'Choose a kind of object.',
                brows,
                lambda v: request_catalog(v[0], 2, v[1], 'object'),
                tag='catalog',
            )
        else:
            request_catalog(value[1], value[2], value[3], value[4])

    core.choose('Catalog', 'What do you want to buy or build with?', rows, picked, tag='catalog')


def place_object_in_room() -> Any:
    import simsight_place as place

    return place.guided_placement()


def place_styled_room() -> None:
    if not in_build_mode():
        core.speak('Go to Build mode first with F2.')
        return
    if no_mouse():
        return
    if catalog_state.get('next') != 'room' or catalog_state.get('chosen') is None:
        core.speak('First choose a furnished room with Alt+B, Catalog, Furnished rooms.')
        return
    f, grid = tile_map()
    spots = free_spots(grid, 4, 4) or free_spots(grid, 3, 3)
    rows: list[Row] = [
        (
            f'Around {e + 2} from west, {n + 2} from north',
            'Free space. ' + _touch_text(grid, e, n, 4, 4).capitalize() + '.',
            (e + 2, n + 2),
        )
        for (e, n) in spots
    ]
    rows.append(
        (
            'Type a position',
            'Type the meters from the west edge and from the north edge for the middle of the room.',
            'type',
        )
    )

    def go(center: Sequence[int]) -> None:
        (ce, cn) = center
        name = tool_name()
        before = room_ids()
        money = cost_check('Placed ' + name, 'The room was not placed. It may not fit there.')

        def verify_and_describe() -> None:
            box = _new_room_box(before)
            if box is None:
                money()
                return
            g = tile_map()[1]
            (w, d) = (box[2] - box[0], box[3] - box[1])
            core.speak(
                f'Placed {name}. It is {w} by {d} meters, from {box[0]} to {box[2]} west, {box[1]} to {box[3]} north. It {_touch_text(g, box[0], box[1], w, d)}. '
                'Use Alt+R to hear what is inside.'
            )

        x, z = f.world(ce, cn)
        core.speak(f'Placing {name}. Please wait.')

        def act(s: list[Any]) -> None:
            reselect()
            click(s[0])

        run_job([(x, z)], 0, act, 'styled room', verify_and_describe)

    def picked(value: Any) -> None:
        if value == 'type':
            core.ask_text(
                'Room position',
                'Type meters from the west edge, then from the north edge, for example 3 6.',
                '',
                lambda t: go(tuple((whole_numbers(t) + [0, 0])[:2])),
            )
        else:
            go(value)

    core.choose(
        f'Where to put {tool_name()}',
        'The room is placed with its middle at the spot you choose.',
        rows,
        picked,
        tag='build',
    )


def _undo_spoken() -> None:
    undo()
    core.speak('Undone.')


@core.on_zone_started
def _start() -> None:
    watch_product()


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


def point_and_act(  # noqa: PLR0913, PLR0917 - signature other modules call
    points_world: list[Point],
    level: int,
    action: Action,
    describe: str,
    height: float = 0.0,
    view: tuple[float, float] | None = None,
    overview: bool = False,
    focus_height: float | None = None,
) -> Any:
    """Worker thread only. Overview jobs (whole rooms) use the house planner's
    widening room framing; everything else centers the camera as before."""
    if overview:
        import simsight_house_planner as planner

        return planner.overview_point_and_act(
            points_world, level, action, describe, height=height, overview=True
        )
    return point_and_act_base(
        points_world,
        level,
        action,
        describe,
        height=height,
        view=view,
        overview=False,
        focus_height=focus_height,
    )


def room_worker(want: Sequence[int], overview: bool = False, tries: int = 4, level: int = 0) -> Any:
    """Basement rooms (level below 0) go through the game's Basement tool."""
    import simsight_basement as basement

    return basement.room_worker(want, overview=overview, tries=tries, level=level)


def _flash_reply(encoded: str = '') -> Any:
    import simsight_basement as basement

    return basement.reply(encoded)


@core.hotkey(ord('B'), 'build helper')
def open_menu() -> None:
    """Alt+B. DaRainbowGirlXX's house, roof, basement and lot tools replace
    Michael Maslo's Build my house, Put a roof on my house and Describe the lot
    (Leo, 2026-11-14); his other tools stay."""
    watch_product()
    if not in_build_mode():
        core.speak('The build helper works in Build mode. Press F2 first, on a laptop Fn+F2.')
        return
    tool = tool_name() or 'nothing'
    import simsight_plus_house as house
    import simsight_plus_furnish as furnish
    import simsight_plus_decor as decor
    import simsight_plus_draw as draw
    import simsight_house_workflow as workflow
    import simsight_detached as detached
    import simsight_basement as basement
    import simsight_roof_tools as roofs
    import simsight_lot_grid as lot_grid
    import simsight_house_planner as planner

    rows: list[tuple[str, str | None, Callable[[], object]]] = [
        (
            'Build my house',
            'Ready-made or custom layouts, stories, basement and individual room names.',
            workflow.open_builder,
        ),
        (
            'Build a detached small structure',
            'Shed, garage, enclosed winter garden, party house or custom one/two-room building at the current grid tile.',
            detached.open_builder,
        ),
        (
            'Basement assistant (experimental)',
            'Build a basement room or inspect native floors and existing rooms.',
            basement.open_assistant,
        ),
        (
            'Accessible roof builder',
            'Flat, gabled or hipped roofs over the whole top floor or a named room.',
            roofs.roof_builder,
        ),
        (
            'Describe roof level',
            'Rooms covered and fireplace alignment points below the roof.',
            roofs.describe_roof_level,
        ),
        (
            'Chimney assistant',
            'Finds fireplace-aligned roof positions without forcing invalid placement.',
            roofs.chimney_assistant,
        ),
        (
            'Lot planning grid',
            'Arrow through buildings, rooms, pools, landscaping and apparently open ground.',
            lot_grid.open_lot_grid,
        ),
        ('Turn lot planning descriptions off', None, lot_grid.close_lot_grid),
        (
            'Describe the lot and buildings',
            'Lot size, building positions, pools and cautiously inferred exterior areas.',
            roofs.describe_lot,
        ),
        (
            'Describe detected buildings',
            'Neutral footprints and room, door and window counts for gallery and existing lots.',
            lot_grid.describe_buildings,
        ),
        (
            'Describe this build',
            'Geometry, floors, room shapes, doors, connections and major furniture.',
            roofs.describe_build,
        ),
        (
            'Name the building here',
            'Give the detected building at the grid cursor a custom name.',
            lot_grid.label_current_building,
        ),
    ]
    if draw.is_on():
        rows += [
            ('Choose the drawing tool', 'Wall, room or roof.', draw.choose_tool),
            ('Turn the drawing cursor off', None, draw.stop),
        ]
    else:
        rows.append(
            (
                'Draw with a cursor, like sighted players',
                'Move a cursor around the lot with Alt+arrows and draw walls, rooms and roofs from corner to corner.',
                draw.start,
            )
        )
    rows += [
        (
            'Door between two rooms',
            'Choose two rooms, or a room and the yard. The door goes in the middle of the wall between them.',
            house.door_between_rooms,
        ),
        (
            'Remove a door or window',
            'Lists every door and window and where it is.',
            house.remove_door_or_window,
        ),
        (
            'Furnish a room with the basics',
            'Say what kind of room it is: the bed, toilet, fridge and so on are bought and placed.',
            furnish.furnish_room,
        ),
        (
            'Decorate a room',
            'Choose a room, walls or floor, then a paint, wallpaper or floor from a list. The whole room is done at once.',
            decor.decorate_room,
        ),
        (
            'Sell things in a room',
            'Lists everything in a room with what it sells for. Check the ones to sell.',
            decor.sell_in_room,
        ),
        ('Name a room', "Give a room a name, like Kitchen or Shay's bedroom.", house.name_a_room),
        (
            'Catalog',
            'Doors, windows, wall and floor patterns, furniture and more, as lists with prices.',
            open_catalog,
        ),
        (
            'Place the chosen furnished room',
            'After choosing one in Catalog, Furnished rooms. You choose a free spot.',
            place_styled_room,
        ),
        (
            'Guided placement',
            'After choosing furniture in the Catalog, choose a floor and a room or lot area.',
            place_object_in_room,
        ),
        (
            'Build a room',
            'Needs the Room Tool chosen in the catalog. You give the size and choose a place.',
            build_room,
        ),
        (
            'Draw a wall',
            'Picks the Wall Tool for you. You give a start point, a direction and a length.',
            build_wall,
        ),
        (
            'Put the chosen door or window on a wall',
            'Choose a door or window in the catalog first.',
            place_on_wall,
        ),
        (
            'Paint the walls of a room',
            'Choose a wall pattern in the catalog first.',
            lambda: paint_room('walls'),
        ),
        (
            'Paint the floor of a room',
            'Choose a floor pattern in the catalog first.',
            lambda: paint_room('floor'),
        ),
        (
            'Undo the last build step',
            'Takes back the last thing built, with a refund.',
            _undo_spoken,
        ),
        (
            'Stair-shape diagnostics',
            'Logs the stair catalog for future shaped-stair support; places nothing.',
            planner.shaped_stair_diagnostics,
        ),
    ]
    core.choose(
        'Build helper',
        f'Chosen in the catalog now: {tool}.',
        rows,
        lambda fn: fn(),
        tag='build',
    )

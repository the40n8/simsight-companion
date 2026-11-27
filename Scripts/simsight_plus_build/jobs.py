"""Pointing jobs (camera, projection, action, undo), the selected tool and
product, and the checks after a job."""

from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_plus_core as core
import simsight_steps as steps

from .state import _product, busy, last_error
from .pointer import (
    WHEEL,
    _new_serial,
    clear_of_panels,
    cursor_off,
    floor_height,
    mouse,
    park_mouse,
    project,
    send_ui,
    user32,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .state import Action, Point


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
            core.post(overview_camera, [*points_world, (cx, cz + 3.0 * (attempt + 1))], level)
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
            simsight_log.error(core.log_exc('build job'))
            core.post(core.speak, 'Sorry, the build step failed.')
        finally:
            busy[0] = False

    threading.Thread(target=work, name='simsight_plus_build', daemon=True).start()


# ---------------------------------------------------------------- selected tool / product


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


def whole_numbers(text: str | None) -> list[int]:
    return [int(v) for v in re.findall(r'\d+', text or '')]


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

"""Stacked-house pieces for simsight_house_workflow: room and hallway layout of
each level, floor limits, stair placement, room framing and the roof follow-up."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from simsight_plus_house import Plan
    from simsight_steps import Job

    Point = tuple[float, float]
    Tile = tuple[int, int]

_FRONT = 4  # depth of the street-facing rooms, metres
_HALL = 2  # depth of the hallway between front and back rooms, metres


def _room_overview(points: Sequence[Point], level: int, attempt: int) -> None:
    """Simulation thread: widen the view without moving the room bounds."""
    import camera
    import sims4.math
    import terrain

    xs, zs = [p[0] for p in points], [p[1] for p in points]
    cx = (min(xs) + max(xs)) / 2.0
    tz = max(zs) - 1.0
    span = max(max(xs) - min(xs), max(zs) - min(zs))
    y = terrain.get_terrain_height(cx, tz) + 3.0 * level
    up = (14.0 + span * 1.1) * (1.0 + 0.5 * attempt)
    camera.focus_on_object_from_position(
        obj_position=sims4.math.Vector3(cx, y, tz),
        camera_position=sims4.math.Vector3(cx, y + up, tz + up * 0.3),
    )


def overview_point_and_act(  # noqa: PLR0913, PLR0917 - same signature as build.point_and_act
    points_world: list[Point],
    level: int,
    action: Callable[[list[Any]], object],
    describe: str,
    height: float = 0.0,
    view: Any = None,
    overview: bool = False,
) -> bool:
    """Worker thread only. Overview jobs widen the camera until every room corner
    projects clear of the interface; other jobs use the build helper's pointing."""
    import simsight_plus_build as build

    if not overview:
        return build.point_and_act_base(
            points_world, level, action, describe, height=height, view=view, overview=False
        )
    points = tuple(points_world)
    if not points:
        return False
    try:
        for attempt in range(5):
            build.cursor_off()
            # Wait for the simulation-thread camera call before allowing its
            # animation to settle and asking the UI for fresh projections.
            build.on_main(_room_overview, points, level, attempt)
            time.sleep(1.8)
            projected = [build.project(x, z, level, height=height) for x, z in points]
            clear = all(p is not None and build.clear_of_panels(p) for p in projected)
            simsight_log.line(
                f'house planner: room framing attempt={attempt + 1} level={level} bounds={points} screen={projected} stage={build.stage_size()} clear={clear}'
            )
            if clear:
                build.cursor_off()
                action(projected)
                time.sleep(1.0)
                return True
        build.core.post(
            build.core.speak,
            'The room corners could not be framed clear of the interface. Construction stopped before drawing this room.',
        )
        return False
    finally:
        build.park_mouse()


def floor_limits() -> tuple[int, int]:
    """(lowest, highest) floor the game allows here; (-1, 3) when it cannot say."""
    try:
        import build_buy

        return int(build_buy.get_lowest_level_allowed()), int(build_buy.get_highest_level_allowed())
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as ex:
        simsight_log.line(f'house planner: floor limits unavailable: {ex!r}')
        return -1, 3


def _room(kind: str, name: str, uv: tuple[int, int, int, int], level: int) -> dict[str, Any]:
    return {'kind': kind, 'name': name, 'uv': uv, 'level': level}


def _level_rooms(
    names: Sequence[str], level: int, width: int, depth: int, layout: str
) -> list[dict[str, Any]]:
    """One level's rooms: front room(s), the hallway, back room(s)."""
    front, hall = _FRONT, _HALL
    hallway = _room(
        'hall',
        '{} hallway'.format('Basement' if level < 0 else f'Floor {level + 1}'),
        (0, front, width, front + hall),
        level,
    )
    if layout == 'two':
        return [
            _room('room', names[0], (0, 0, width, front), level),
            hallway,
            _room('room', names[1], (0, front + hall, width, depth), level),
        ]
    split = width // 2
    return [
        _room('room', names[0], (0, 0, split, front), level),
        _room('room', names[1], (split, 0, width, front), level),
        hallway,
        _room('room', names[2], (0, front + hall, split, depth), level),
        _room('room', names[3], (split, front + hall, width, depth), level),
    ]


def _door(what: str, a: Tile, b: Tile, front: bool, level: int) -> dict[str, Any]:
    return {'what': what, 'a': a, 'b': b, 'front': front, 'level': level}


def _hall_doors(rooms: Sequence[dict[str, Any]], level: int) -> list[dict[str, Any]]:
    """A door from each front room into the hallway, then from the hallway to each back room."""
    front, hall = _FRONT, _HALL
    hallroom = next(r for r in rooms if r['kind'] == 'hall')
    doors = []
    for r in rooms:
        if r['uv'][1] == 0:
            mid = (r['uv'][0] + r['uv'][2]) // 2
            what = 'Door from {} to {}'.format(r['name'], hallroom['name'])
            doors.append(_door(what, (mid, front - 1), (mid, front), False, level))
    for r in rooms:
        if r['uv'][1] > front:
            mid = (r['uv'][0] + r['uv'][2]) // 2
            what = 'Door from {} to {}'.format(hallroom['name'], r['name'])
            doors.append(_door(what, (mid, front + hall - 1), (mid, front + hall), False, level))
    return doors


def build_plan(
    width: int, depth: int, levels: Sequence[int], layout: str, names_by_level: dict[int, list[str]]
) -> Plan:
    """A house.Plan of stacked identical levels with a hallway and straight stairs."""
    import simsight_plus_build as build
    import simsight_plus_house as house

    p = house.Plan()
    p.side = house.street_side()
    p.width = width
    p.depth = depth
    p.floors = len([v for v in levels if v >= 0])
    if depth < _FRONT + _HALL + 3:
        p.problem = (
            'The footprint is too shallow for rooms, a hallway and verified straight stairs.'
        )
        return p
    for level in levels:
        rooms = _level_rooms(names_by_level[level], level, width, depth, layout)
        p.rooms += rooms
        p.doors += _hall_doors(rooms, level)
    ground = next(r for r in p.rooms if r['level'] == 0 and r['uv'][1] == 0)
    mid = (ground['uv'][0] + ground['uv'][2]) // 2
    p.doors.append(_door('Front door into {}'.format(ground['name']), (mid, 0), (mid, -1), True, 0))
    frame = build.frame()
    along = frame.width if p.side in ('north', 'south') else frame.depth
    across = frame.depth if p.side in ('north', 'south') else frame.width
    if width > along - 2 or depth > across - house.SETBACK - 1:
        p.problem = (
            f'That {width} by {depth} house does not fit this lot with the required setbacks.'
        )
        return p
    house.map_plan(p, frame, along)
    top = (width - 2, _FRONT)
    transitions = [
        (
            house.tile_to_lot(p, frame, (along - width) // 2, top),
            house.dir_to_lot(p, (1, 0)),
            lower,
            lower + 1,
        )
        for lower in levels[:-1]
    ]
    if transitions:
        p.stairs = {'top_lot': transitions[0][0], 'climb_lot': transitions[0][1]}
    p.transitions = transitions
    return p


def _select_stairs_silently(build: Any, pick: Sequence[Any]) -> None:
    build.catalog_state['silent_select'] = True
    try:
        build.select_item(*pick)
    finally:
        build.catalog_state['silent_select'] = False


def _click_stairs(screen: list[Any]) -> None:
    import simsight_plus_build as build

    build.move_to(*build.to_screen(*screen[0]))
    time.sleep(0.8)
    build.click(screen[0])


def place_stair_at(top: Tile, climb: Tile, level: int) -> bool | str:
    """Worker thread only. Straight stairs whose highest step is lot tile `top`, climbing
    `climb`, from `level` up. True when placed where wanted, else a failure word."""
    import simsight_plus_build as build
    import simsight_plus_house as house

    pick = house.pick_stairs()
    if not pick:
        return 'nocatalog'
    if not build.set_floor(level):
        return 'nofloor'
    frame = build.frame()
    want = (
        top[0] + 0.5 - (house.STAIR_LEN - 0.5) * climb[0],
        top[1] + 0.5 - (house.STAIR_LEN - 0.5) * climb[1],
    )
    shift = [0.0, 0.0]
    turn = False
    for attempt in range(10):
        before = set(build.on_main(house.stair_ids) or [])
        _select_stairs_silently(build, pick)
        if turn:
            build.tap_scan(52)
            time.sleep(0.5)
            turn = False
        point = frame.world(top[0] + 0.5 + shift[0], top[1] + 0.5 + shift[1])
        if not build.point_and_act(
            [point], level, _click_stairs, f'stacked stairs level {level} attempt {attempt + 1}'
        ):
            return 'noscreen'
        time.sleep(0.5)
        new = [oid for oid in build.on_main(house.stair_ids) or [] if oid not in before]
        if not new:
            simsight_log.line(
                'house planner: stairs level {} attempt {} rejected: {}'.format(
                    level, attempt + 1, build.last_error.get('text')
                )
            )
            turn = True
            continue
        info = build.on_main(house.stair_info, new[0])
        simsight_log.line(
            f'house planner: stairs level {level} placed {info} wanted {want} climb {climb}'
        )
        if info is None:
            continue
        e, n, got = info
        if got == tuple(climb) and abs(e - want[0]) < 0.3 and abs(n - want[1]) < 0.3:
            return True
        build.on_main(build.undo)
        time.sleep(1.5)
        turn = got != tuple(climb)
        shift = [shift[0] - (e - want[0]), shift[1] - (n - want[1])]
    return 'failed'


def _built_completely(plan: Plan) -> bool:
    """After a build: does every planned room exist, with its box and level?"""
    try:
        import simsight_plus_build as build
        import simsight_plus_rooms as rooms

        actual = {(build.room_box(r), int(r.level)) for r in rooms.scan_rooms()}
        complete = all((r['box'], int(r.get('level', 0))) in actual for r in plan.rooms)
    except Exception as ex:  # the room scan runs EA's native room queries (logged)
        simsight_log.line(f'house planner: post-build completeness failed: {ex!r}')
        simsight_log.error('house planner: post-build completeness')
        return False
    simsight_log.line(
        'house planner: post-build completeness {} actual={} wanted={}'.format(
            complete, actual, [(r['box'], r.get('level', 0)) for r in plan.rooms]
        )
    )
    return complete


def build_with_followup(plan: Plan) -> Any:
    """Build the plan; when a stacked house is complete, offer the roof builder."""
    result = _original_build(plan)
    complete = _built_completely(plan)
    if plan.roof_prompt and complete:
        try:
            import simsight_plus_core as core
            import simsight_roof_tools as roofs

            core.post(
                core.speak,
                'The stacked-house build has finished. Choose the roof shape and overhang next.',
            )
            core.post(roofs.roof_builder)
        except Exception as ex:
            simsight_log.line(f'house planner: roof follow-up failed: {ex!r}')
            simsight_log.error('house planner: roof follow-up')
    return result


def shaped_stair_diagnostics() -> None:
    import simsight_plus_build as build
    import simsight_plus_core as core
    import simsight_steps as steps

    def work() -> Job[None]:
        items = (yield from build.fetch_catalog_steps(14, '', 'none')) or []
        simsight_log.line(
            f'house planner: stair catalog discovery {[(str(n), p, c) for n, p, c in items]}'
        )
        core.post(
            core.speak,
            f'Logged {len(items)} stair catalog entries and swatch counts. No shaped, flared, L, U or spiral stair was placed.',
        )

    steps.start('a11y_stair_discovery', work())


def _original_build(plan: Plan) -> Any:
    import simsight_plus_house as house

    return house.build_base(plan)

"""Configurable, conservative stacked-house plans for SimSight Plus."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    Point = tuple[float, float]
    Tile = tuple[int, int]
    # (top step lot tile, climb direction, lower level, upper level)
    Transition = tuple[Any, Any, int, int]

# Stair runs the approved plan still has to place. simsight_build_helpers
# rebinds this attribute (planner._pending_stairs = []): keep it a module list.
_pending_stairs: list[Transition] = []

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
        return build._point_and_act_base(
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
            clear = all(p is not None and build._clear_of_panels(p) for p in projected)
            simsight_log.line(
                f'house planner: room framing attempt={attempt + 1} level={level} bounds={points} screen={projected} stage={build._stage_size()} clear={clear}'
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


def _limits() -> tuple[int, int]:
    """(lowest, highest) floor the game allows here; (-1, 3) when it cannot say."""
    try:
        import build_buy

        return int(build_buy.get_lowest_level_allowed()), int(build_buy.get_highest_level_allowed())
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as ex:
        simsight_log.line(f'house planner: floor limits unavailable: {ex!r}')
        return -1, 3


def _clean_names(text: str | None, count: int, level: int, attic: bool = False) -> list[str]:
    """Exactly `count` room names from a comma list, defaults filling the gaps;
    repeated names get a number."""
    names = [' '.join(v.strip().split())[:50] for v in (text or '').split(',') if v.strip()]
    if attic:
        defaults: tuple[str, ...] = ('Attic room', 'Storage', 'Attic bedroom', 'Attic bathroom')
    elif level == 0:
        defaults = ('Living room', 'Kitchen', 'Bedroom', 'Bathroom')
    else:
        defaults = ('Bedroom', 'Bedroom', 'Study', 'Bathroom')
    while len(names) < count:
        names.append(defaults[len(names) % len(defaults)])
    names = names[:count]
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        seen[name] = seen.get(name, 0) + 1
        if names.count(name) > 1:
            names[i] = f'{name} {seen[name]}'
    return names


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
    hallroom = [r for r in rooms if r['kind'] == 'hall'][0]
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


def _build_plan(
    width: int, depth: int, levels: Sequence[int], layout: str, names_by_level: dict[int, list[str]]
) -> Any:
    """A house.Plan of stacked identical levels with a hallway and straight stairs."""
    import simsight_plus_build as build
    import simsight_plus_house as house

    p: Any = house.Plan()
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
    ground = [r for r in p.rooms if r['level'] == 0 and r['uv'][1] == 0][0]
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
    house._map_plan(p, frame, along)
    top = (width - 2, _FRONT)
    transitions = [
        (
            house._tile_to_lot(p, frame, (along - width) // 2, top),
            house._dir_to_lot(p, (1, 0)),
            lower,
            lower + 1,
        )
        for lower in levels[:-1]
    ]
    if transitions:
        p.stairs = {'top_lot': transitions[0][0], 'climb_lot': transitions[0][1]}
    p._a11y_transitions = transitions
    return p


def _select_stairs_silently(build: Any, pick: Sequence[Any]) -> None:
    build._cat['silent_select'] = True
    try:
        build.select_item(*pick)
    finally:
        build._cat['silent_select'] = False


def _click_stairs(screen: list[Any]) -> None:
    import simsight_plus_build as build

    build.move_to(*build._to_screen(*screen[0]))
    time.sleep(0.8)
    build.click(screen[0])


def _place_stair_at(top: Tile, climb: Tile, level: int) -> bool | str:
    """Worker thread only. Straight stairs whose highest step is lot tile `top`, climbing
    `climb`, from `level` up. True when placed where wanted, else a failure word."""
    import simsight_plus_build as build
    import simsight_plus_house as house

    pick = house._pick_stairs()
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
        before = set(build.on_main(house._stair_ids) or [])
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
        new = [oid for oid in build.on_main(house._stair_ids) or [] if oid not in before]
        if not new:
            simsight_log.line(
                'house planner: stairs level {} attempt {} rejected: {}'.format(
                    level, attempt + 1, build._last_error.get('text')
                )
            )
            turn = True
            continue
        info = build.on_main(house._stair_info, new[0])
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


def _built_completely(plan: Any) -> bool:
    """After a build: does every planned room exist, with its box and level?"""
    try:
        import simsight_plus_build as build
        import simsight_plus_rooms as rooms

        actual = {(build._room_box(r), int(r.level)) for r in rooms.scan_rooms()}
        complete = all((r['box'], int(r.get('level', 0))) in actual for r in plan.rooms)
        simsight_log.line(
            'house planner: post-build completeness {} actual={} wanted={}'.format(
                complete, actual, [(r['box'], r.get('level', 0)) for r in plan.rooms]
            )
        )
        return complete
    except Exception as ex:
        simsight_log.line(f'house planner: post-build completeness failed: {ex!r}')
        simsight_log.error('house planner: post-build completeness')
        return False


def _build_with_followup(plan: Any) -> Any:
    """Build the plan; when a stacked house is complete, offer the roof builder."""
    result = _original_build(plan)
    complete = _built_completely(plan)
    if getattr(plan, '_a11y_roof_prompt', False) and complete:
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


def _review(plan: Any, description: str) -> None:
    import simsight_plus_build as build
    import simsight_plus_core as core
    import simsight_plus_house as house

    if plan.problem:
        core.speak(plan.problem)
        return
    estimate = (
        house.wall_meters(plan) * house.WALL_PRICE
        + len(plan.doors) * 100
        + len(plan._a11y_transitions) * house.STAIR_PRICE
    )
    rows = [
        (
            'Build it',
            'Every room, door and stair is checked; construction stops on the first failed room.',
            True,
        ),
        ('Cancel', None, False),
    ]

    def chosen(go: bool) -> None:
        if not go:
            return
        # In place: the module attribute stays the list other modules see.
        _pending_stairs[:] = list(plan._a11y_transitions)
        simsight_log.line(
            'house planner: approved plan levels={} size={}x{} rooms={} stairs={}'.format(
                sorted({r['level'] for r in plan.rooms}),
                plan.width,
                plan.depth,
                [(r['level'], r['name'], r['box']) for r in plan.rooms],
                _pending_stairs,
            )
        )
        house.start_build(plan)

    core.choose(
        'Configurable stacked house',
        description
        + f' Estimated structural cost {estimate:,}; household funds {build.funds() or 0:,}.',
        rows,
        chosen,
        tag='build',
    )


class _StackedHouseChoices:
    """The planner's dialogs, one after the other: stories, basement, footprint,
    layout, top-floor use, then the room names of each level."""

    def __init__(self, low: int, high: int) -> None:
        import simsight_plus_core as core

        self.core: Any = core
        self.low = low
        self.high = high
        self.stories = 1
        self.basement = False
        self.size = (10, 10)
        self.layout = 'two'
        self.attic = False
        self.levels: list[int] = []
        self.count = 2
        self.names: dict[int, list[str]] = {}

    def start(self) -> None:
        floor_rows = [
            ('{} above-ground stor{}'.format(n, 'y' if n == 1 else 'ies'), None, n)
            for n in range(1, max(1, self.high + 1) + 1)
        ]
        self.core.choose(
            'Number of stories',
            f'The game currently reports support through floor {self.high}. Choose an above-ground count.',
            floor_rows,
            self.floors_chosen,
            tag='build',
        )

    def floors_chosen(self, stories: int) -> None:
        self.stories = stories
        basement_rows: list[tuple[str, str | None, bool]] = [('No basement', None, False)]
        if self.low < 0:
            basement_rows.append(
                (
                    'One basement level',
                    'Experimental negative-level room and stair placement; every step is logged and verified.',
                    True,
                )
            )
        self.core.choose(
            'Basement',
            'A basement counts as level minus one and receives the same footprint.',
            basement_rows,
            self.basement_chosen,
            tag='build',
        )

    def basement_chosen(self, basement: bool) -> None:
        self.basement = basement
        sizes = [
            ('Compact square', '10 by 10 metres.', (10, 10)),
            ('Standard rectangle', '10 by 11 metres.', (10, 11)),
            ('Large rectangle', '12 by 14 metres.', (12, 14)),
        ]
        self.core.choose(
            'House footprint',
            'Every level uses exactly this perimeter.',
            sizes,
            self.size_chosen,
            tag='build',
        )

    def size_chosen(self, size: tuple[int, int]) -> None:
        self.size = size
        layouts = [
            ('Two rooms per floor', 'One front room, hallway, one back room.', 'two'),
            ('Four rooms per floor', 'Two front rooms, hallway, two back rooms.', 'four'),
        ]
        self.core.choose(
            'Room layout',
            'The same proven rectangular partition pattern is used on every level.',
            layouts,
            self.layout_chosen,
            tag='build',
        )

    def layout_chosen(self, layout: str) -> None:
        self.layout = layout
        self.levels = list(range(-1 if self.basement else 0, self.stories))
        self.count = 2 if layout == 'two' else 4
        self.names = {}
        self.attic = False
        attic_rows = [
            ('Normal top floor', None, False),
            (
                'Attic-style top floor',
                'A full-height normal story with attic and storage defaults, followed by the roof chooser.',
                True,
            ),
        ]
        self.core.choose(
            'Top-floor use',
            'This does not create unsafe sloped rooms; it changes the intended room naming and roof follow-up.',
            attic_rows,
            self.attic_chosen,
            tag='build',
        )

    def attic_chosen(self, value: bool) -> None:
        self.attic = value
        self.ask(0)

    def ask(self, index: int) -> None:
        """The room names of level number `index`; after the last level, the review."""
        if index >= len(self.levels):
            self.review()
            return
        level = self.levels[index]
        count = self.count
        where = 'basement' if level < 0 else f'floor {level + 1}'
        order = 'front, back' if count == 2 else 'front west, front east, back west, back east'
        is_attic = self.attic and level == max(self.levels)

        def named(text: str) -> None:
            self.names[level] = _clean_names(text, count, level, is_attic)
            self.ask(index + 1)

        self.core.ask_text(
            f'Rooms on {where}',
            'Type {} room names separated by commas, in this order: {}.{}'.format(
                count,
                order,
                ' Leave blank for attic and storage defaults.' if is_attic else '',
            ),
            '',
            named,
        )

    def review(self) -> None:
        size, levels = self.size, self.levels
        plan = _build_plan(size[0], size[1], levels, self.layout, self.names)
        plan._a11y_roof_prompt = True
        desc = '{} by {} metre {} house; levels {}; {} rooms plus a hallway per level; straight aligned stairs.'.format(
            size[0],
            size[1],
            'square' if size[0] == size[1] else 'rectangular',
            ', '.join(str(v) for v in levels),
            self.count,
        )
        _review(plan, desc)


def open_planner() -> None:
    import simsight_plus_build as build
    import simsight_plus_core as core

    if not build.in_build_mode():
        core.speak('Enter Build mode first.')
        return
    low, high = _limits()
    _StackedHouseChoices(low, high).start()


def shaped_stair_diagnostics() -> None:
    import simsight_plus_build as build
    import simsight_plus_core as core

    def work() -> None:
        items = build.fetch_catalog(14, '', 'none') or []
        simsight_log.line(
            f'house planner: stair catalog discovery {[(str(n), p, c) for n, p, c in items]}'
        )
        core.post(
            core.speak,
            f'Logged {len(items)} stair catalog entries and swatch counts. No shaped, flared, L, U or spiral stair was placed.',
        )

    threading.Thread(target=work, name='a11y_stair_discovery', daemon=True).start()


def _original_build(plan: Any) -> Any:
    import simsight_plus_house as house

    return house._build_base(plan)

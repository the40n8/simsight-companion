"""Unified house planning and fresh Build-mode room geometry."""

from __future__ import annotations

import copy
import threading
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Callable


class _State:
    """The modules install() wires in, and the game's simulation thread."""

    def __init__(self) -> None:
        self.build: Any = None
        self.core: Any = None
        self.house: Any = None
        self.planner: Any = None
        self.installed = False
        self.main_ident: int | None = None


_state = _State()

ROLES = (
    ('Bedroom', 'bedroom'),
    ("Children's bedroom", 'bedroom'),
    ('Nursery', 'bedroom'),
    ('Bathroom', 'bathroom'),
    ('Kitchen', 'kitchen'),
    ('Living room', 'living'),
    ('Dining room', 'dining'),
    ('Study', 'study'),
    ('Storage', 'room'),
    ('Hobby room', 'room'),
)


def _log(kind: str, **data: Any) -> None:
    import json

    simsight_log.line(
        'house workflow: ' + json.dumps(dict(kind=kind, **data), sort_keys=True, default=str)
    )


def _record_main() -> None:
    _state.main_ident = threading.get_ident()


def _scan_main() -> Any:
    import services

    lot = services.active_lot()
    if lot is not None and _state.build.in_build_mode():
        old = (getattr(lot, '_min_level', None), getattr(lot, '_max_level', None))
        # Native Lot caches these properties until Zone.on_build_buy_exit.
        # This only invalidates Python's two cached values, not the building.
        lot.clear_cached_level_range()
        fresh = (int(lot.min_level), int(lot.max_level))
        if old != fresh:
            _log('room-floor-range', cached=old, fresh=fresh)
    return _original_scan()


def scan_rooms() -> Any:
    """Room scan on the simulation thread, from any thread (replaces rooms.scan_rooms)."""
    if _state.main_ident is None:
        raise RuntimeError('The room-scan simulation thread is not ready')
    if threading.get_ident() == _state.main_ident:
        return _scan_main()
    result = _state.build.on_main(_scan_main)
    if result is None:
        raise RuntimeError('The room-scan simulation thread did not answer')
    return result


def _context(plan: Any) -> Any:
    import services

    plan._a11y_zone = int(services.current_zone_id())
    plan._a11y_household = int(services.active_household_id())
    return plan


def _valid(plan: Any) -> bool:
    import services

    return (
        _state.build.in_build_mode()
        and int(services.current_zone_id()) == plan._a11y_zone
        and int(services.active_household_id()) == plan._a11y_household
    )


def _floor(level: int) -> str:
    return 'Basement' if level == -1 else 'Ground floor' if level == 0 else f'Floor {level + 1}'


def _position(plan: Any, room: dict[str, Any]) -> str:
    level = room.get('level', 0)
    boxes = [r['box'] for r in plan.rooms if r.get('level', 0) == level]
    west, north, east, south = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    a, b, c, d = room['box']
    x, z = (a + c) / 2.0, (b + d) / 2.0
    sides = []
    if z < (north + south) / 2.0 - 0.1:
        sides.append('north')
    elif z > (north + south) / 2.0 + 0.1:
        sides.append('south')
    if x < (west + east) / 2.0 - 0.1:
        sides.append('west')
    elif x > (west + east) / 2.0 + 0.1:
        sides.append('east')
    relation = (
        'connecting hallway'
        if room['kind'] == 'hall'
        else 'street-facing room'
        if room['uv'][1] == 0
        else 'rear room'
    )
    return '{}; {}; {}; {} by {} metres'.format(
        _floor(level), relation, ' '.join(sides) + ' side' if sides else 'center', c - a, d - b
    )


def _room_at(plan: Any, tile: tuple[int, int], level: int) -> str | None:
    """Name of the planned room on `level` that holds lot tile `tile`."""
    return next(
        (
            r['name']
            for r in plan.rooms
            if r.get('level', 0) == level
            and r['box'][0] <= tile[0] < r['box'][2]
            and r['box'][1] <= tile[1] < r['box'][3]
        ),
        None,
    )


def _door_names(plan: Any) -> None:
    """Name each door after the rooms on its two sides."""
    for door in plan.doors:
        level = door.get('level', 0)
        a, b = _room_at(plan, door['tile_a'], level), _room_at(plan, door['tile_b'], level)
        either = a or b
        if door['front'] and either:
            door['what'] = 'Front door into ' + either
        elif a and b:
            door['what'] = f'Door between {a} and {b}'


def _check_story_count(stories: object, basement: bool) -> None:
    low, high = _state.planner.floor_limits()
    if isinstance(stories, bool) or not isinstance(stories, int) or not 1 <= stories <= high + 1:
        raise ValueError('That story count is outside the native game limits')
    if basement and low >= 0:
        raise ValueError('The game does not allow a basement here')


def _copy_level(
    original: Any, templates: dict[int, list[dict[str, Any]]], level: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rooms and doors of `level`: its own template, else the ground floor's for a
    basement, else the highest floor's (renamed for this floor)."""
    source = (
        level if level in templates else 0 if level < 0 else max(v for v in templates if v >= 0)
    )
    rooms = []
    for room in templates[source]:
        r = copy.deepcopy(room)
        r['level'] = level
        if source != level:
            base = room.get('_a11y_template_name', room['name'])
            r['name'] = _floor(level) + ' ' + base
            r['_a11y_template_name'] = base
        rooms.append(r)
    doors = []
    for door in original.doors:
        if door.get('level', 0) != source or (door['front'] and level != 0):
            continue
        d = copy.deepcopy(door)
        d['level'] = level
        doors.append(d)
    return rooms, doors


def _extend(plan: Any, stories: int, basement: bool) -> Any:
    """Keep each existing floor template; repeat the highest template for additions."""
    original = copy.deepcopy(plan)
    templates: dict[int, list[dict[str, Any]]] = {}
    for room in original.rooms:
        level = room.get('level', 0)
        templates.setdefault(level, []).append(room)
    if not templates or 0 not in templates:
        raise ValueError('The plan has no ground-floor template')
    _check_story_count(stories, basement)
    levels = list(range(-1 if basement else 0, stories))
    p = copy.deepcopy(original)
    p.rooms = []
    p.doors = []
    for level in levels:
        rooms, doors = _copy_level(original, templates, level)
        p.rooms.extend(rooms)
        p.doors.extend(doors)
    p.floors = stories
    p.stairs = None
    halls = [r for r in p.rooms if r['level'] == 0 and r['kind'] == 'hall']
    if len(levels) > 1:
        if len(halls) != 1 or p.width < 5:
            raise ValueError('This layout has no verified straight-stair hallway')
        hall = halls[0]
        top = (p.width - 2, hall['uv'][1])
        p.stairs = {'top': top, 'climb': (1, 0)}
    frame = _state.build.frame()
    along = frame.width if p.side in ('north', 'south') else frame.depth
    _state.house.map_plan(p, frame, along)
    p._a11y_transitions = []
    if p.stairs:
        for lower in levels[:-1]:
            p._a11y_transitions.append(
                (p.stairs['top_lot'], p.stairs['climb_lot'], lower, lower + 1)
            )
    _door_names(p)
    return p


def _summary(plan: Any) -> str:
    levels = sorted({r.get('level', 0) for r in plan.rooms})
    counts = ', '.join(
        '{}: {} rooms'.format(
            _floor(level),
            sum(r['kind'] != 'hall' and r.get('level', 0) == level for r in plan.rooms),
        )
        for level in levels
    )
    return '{} by {} metres. Street-facing side: {}. {}. {} connecting stair runs.'.format(
        plan.width, plan.depth, plan.side, counts, len(getattr(plan, '_a11y_transitions', ()))
    )


def _edit_rooms(plan: Any) -> None:
    rows = [(r['name'], _position(plan, r), i) for i, r in enumerate(plan.rooms)] + [
        ('Back to house plan', None, None)
    ]

    def selected(index: int | None) -> None:
        if index is None:
            _review(plan)
            return
        room = plan.rooms[index]
        options: list[tuple[str, str | None, Any]] = [
            ('Keep ' + room['name'], _position(plan, room), None)
        ]
        if room['kind'] != 'hall':
            options += [(name, None, (name, kind)) for name, kind in ROLES]
        options += [
            (
                'Give this room a custom name',
                'Changes the label; keeps its geometry and current room type.',
                'custom',
            )
        ]

        def changed(value: Any) -> None:
            if value == 'custom':

                def named(typed: str) -> None:
                    text = ' '.join((typed or '').split())[:60]
                    if text:
                        room['name'] = text
                        room['_a11y_template_name'] = text
                    _door_names(plan)
                    _edit_rooms(plan)

                _state.core.ask_text(
                    'Name this room',
                    _position(plan, room)
                    + '. Type one room name. Leave blank to keep '
                    + room['name']
                    + '.',
                    '',
                    named,
                )
                return
            if value is not None:
                room['name'], room['kind'] = value
                room['_a11y_template_name'] = room['name']
            _door_names(plan)
            _edit_rooms(plan)

        _state.core.choose(
            'Room use and name',
            room['name'] + '. ' + _position(plan, room),
            options,
            changed,
            tag='build',
        )

    _state.core.choose(
        'Rooms and positions',
        'Select any room to choose its use or give it a custom name. Each room is edited separately.',
        rows,
        selected,
        tag='build',
    )


def _stories(plan: Any) -> None:
    low, high = _state.planner.floor_limits()

    def count_chosen(count: int) -> None:
        options: list[tuple[str, str | None, bool]] = [('No basement', None, False)]
        if low < 0:
            options.append(
                (
                    'One basement level',
                    'Experimental native basement drawing, checked before continuing.',
                    True,
                )
            )

        def below(value: bool) -> None:
            try:
                p = _extend(plan, count, value)
            except ValueError as exc:
                _state.core.speak(str(exc))
                return
            _review(p)

        _state.core.choose(
            'Basement', 'Keep the same perimeter below ground.', options, below, tag='build'
        )

    rows = [
        ('{} above-ground {}'.format(n, 'story' if n == 1 else 'stories'), None, n)
        for n in range(1, high + 2)
    ]
    _state.core.choose(
        'Stories',
        'Existing floor layouts are preserved. Extra stories repeat the highest floor layout; names can be changed separately.',
        rows,
        count_chosen,
        tag='build',
    )


def _review(plan: Any) -> None:
    if getattr(plan, 'problem', None):
        _state.core.speak(plan.problem)
        return
    if not _valid(plan):
        _state.core.speak('The lot or household changed. Open Build my house again.')
        return
    estimate = (
        _state.house.wall_meters(plan) * _state.house.WALL_PRICE
        + len(plan.doors) * 100
        + len(getattr(plan, '_a11y_transitions', ())) * _state.house.STAIR_PRICE
    )
    funds = _state.build.funds() or 0
    rows = [
        (
            'Build this house' if estimate <= funds else 'Estimated cost exceeds household funds',
            'Uses the existing native construction and placement checks.',
            'build' if estimate <= funds else None,
        ),
        (
            'Rooms, positions and custom names',
            'Choose each room separately; no comma-separated list.',
            'rooms',
        ),
        ('Stories and basement', 'Repeat layouts without changing the perimeter.', 'stories'),
        ('Cancel', None, None),
    ]

    def selected(action: str | None) -> None:
        if action == 'rooms':
            _edit_rooms(plan)
        elif action == 'stories':
            _stories(plan)
        elif action == 'build':
            if not _valid(plan):
                _state.core.speak('The lot or household changed. Nothing was built.')
                return
            if _state.build.busy[0]:
                _state.core.speak('Still working on the last build step.')
                return
            _door_names(plan)
            plan._a11y_roof_prompt = True
            _log(
                'approved',
                summary=_summary(plan),
                rooms=[(r.get('level', 0), r['kind'], r['name'], r['box']) for r in plan.rooms],
                stairs=getattr(plan, '_a11y_transitions', ()),
            )
            _state.house.start_build(plan)

    _state.core.choose(
        'Build my house: review',
        _summary(plan) + f' Estimated structural cost {estimate:,}; household funds {funds:,}.',
        rows,
        selected,
        tag='build',
    )


def _preset(value: tuple[str, Any]) -> None:
    try:
        plan = (
            _state.house.make_two_floor_plan(value[1])
            if value[0] == 'two'
            else _state.house.make_plan(list(value[1]))
        )
        if getattr(plan, 'problem', None):
            _state.core.speak(plan.problem)
            return
        # Also creates an explicit stair scope for the existing two-floor presets.
        plan = _extend(plan, plan.floors, False)
        _review(_context(plan))
    except ValueError as exc:  # a plan the game limits do not allow: say why
        _log('plan-failed', error=str(exc))
        _state.core.speak('The house plan could not be prepared. ' + str(exc))
    except Exception as exc:
        _log('plan-failed', error=str(exc))
        simsight_log.error('house workflow: prepare plan')
        _state.core.speak('The house plan could not be prepared. ' + str(exc))


def _custom() -> None:
    sizes = [
        ('Compact square', '10 by 10 metres.', (10, 10)),
        ('Standard rectangle', '10 by 11 metres.', (10, 11)),
        ('Large rectangle', '12 by 14 metres.', (12, 14)),
    ]

    def size_chosen(size: tuple[int, int]) -> None:
        def layout_chosen(layout: str) -> None:
            defaults = (
                ['Living room', 'Kitchen', 'Bedroom', 'Bathroom']
                if layout == 'four'
                else ['Living room', 'Bedroom']
            )
            plan = _state.planner.build_plan(size[0], size[1], [0], layout, {0: defaults})
            if getattr(plan, 'problem', None):
                _state.core.speak(plan.problem)
                return
            _stories(_context(plan))

        rows = [
            (
                'Front and back rooms',
                'One street-facing room, a hallway and one rear room per floor.',
                'two',
            ),
            (
                'Four rooms around a hallway',
                'Two street-facing rooms and two rear rooms per floor.',
                'four',
            ),
        ]
        _state.core.choose(
            'Room layout',
            'The footprint stays aligned between floors. Review every room by its actual compass position afterward.',
            rows,
            layout_chosen,
            tag='build',
        )

    _state.core.choose(
        'House footprint', 'Choose the rectangular perimeter.', sizes, size_chosen, tag='build'
    )


def _ready_made() -> None:
    rows = [(label, None, ('one', list(kinds))) for label, kinds in _state.house.PRESETS]
    rows += [(label, None, ('two', variant)) for label, variant in _state.house.TWO_FLOOR_PRESETS]
    _state.core.choose(
        'Ready-made house layouts',
        'Preserves the original room dimensions and door geometry. Stories, basement and room names can be adjusted in the review.',
        rows,
        _preset,
        tag='build',
    )


def legacy_plan(kinds: Any) -> None:
    _preset(kinds if isinstance(kinds, tuple) else ('one', kinds))


def open_builder() -> None:
    if not _state.build.in_build_mode():
        _state.core.speak('Enter Build mode first.')
        return
    if _state.build.no_mouse():
        return
    rows: list[tuple[str, str, Callable[[], object]]] = [
        (
            'Start with a ready-made layout',
            'Original one- and two-story presets, with a common review and customization flow.',
            _ready_made,
        ),
        (
            'Design a square or rectangular house',
            'Choose footprint, front/back or four-room layout, stories and basement.',
            _custom,
        ),
        (
            'Choose the room list yourself',
            'Original one-floor room-list planner, using the same review.',
            lambda: _state.house.choose_custom_rooms([]),
        ),
    ]
    _state.core.choose(
        'Build my house',
        'One house builder: choose a starting layout, then review room positions, names, stories and basement before building.',
        rows,
        lambda fn: fn(),
        tag='build',
    )


def say(text: str) -> None:
    import simsight_basement as basement

    reason = basement.failure_reason()
    if reason and text.startswith('I stopped building at the '):
        text = (
            'Basement construction stopped: '
            + reason
            + '. No further rooms were attempted. Inspect the result before using Undo or trying again.'
        )
    _original_say(text)


def install() -> None:
    """Startup: record the simulation thread for room scans. Idempotent."""
    if _state.installed:
        return
    import simsight_plus_build as build
    import simsight_plus_core as core
    import simsight_plus_house as house
    import simsight_house_planner as planner

    _state.installed = True
    _state.build, _state.core, _state.house, _state.planner = build, core, house, planner
    core.post(_record_main)
    # The pump may discard old queued work during loading. Its ticker also
    # records the owner, and follows any later simulation-thread change.
    core.add_ticker(0.1, _record_main)
    _log('installed')


def _original_scan() -> Any:
    import simsight_plus_rooms as rooms

    return rooms.scan_rooms_base()


def _original_say(text: str) -> None:
    import simsight_plus_house as house

    house.say_base(text)

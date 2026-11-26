"""Conservative detached one-floor structure planner for the Build grid."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    Box = tuple[int, int, int, int]  # west, north, east, south lot tiles
    Tile = tuple[int, int]


def _clean(text: str | None, default: str) -> str:
    value = ' '.join((text or '').strip().split())[:60]
    return value or default


def _object_summary(objects: Iterable[Any]) -> str:
    try:
        import simsight_plus_rooms as rooms

        tags: list[str] = []
        for obj in objects:
            tags.extend(rooms.tag_names(obj))
        words = ' '.join(tags).lower()
        if any(v in words for v in ('plant', 'flower', 'shrub', 'tree', 'garden')):
            return 'landscaping'
    except (ImportError, AttributeError, TypeError):
        pass  # objects of an unexpected shape are simply "object"
    return 'object'


def _clearance(box: Box) -> list[str]:
    """What stands in the way of building on `box` and its one-tile margin."""
    import simsight_tiles as tiles

    frame = tiles.frame()
    if frame is None:  # no lot loaded: fails at the caller's boundary, as before
        raise RuntimeError('no lot frame')
    problems = []
    x1, n1, x2, n2 = box
    for i in range(x1 - 1, x2 + 1):
        for j in range(n1 - 1, n2 + 1):
            if not frame.inside(i, j):
                problems.append(f'lot edge at {i},{j}')
                continue
            facts = tiles.ground(frame, i, j, 0)
            objects = tiles.objects_on(frame, i, j, 0)
            rid = tiles.room(frame, i, j, 0)
            inside = x1 <= i < x2 and n1 <= j < n2
            if facts.get('pool'):
                problems.append(f'pool at {i},{j}')
            if rid is not None:
                problems.append(f'room at {i},{j}')
            if objects:
                problems.append(f'{_object_summary(objects)} at {i},{j}')
            if inside and facts.get('floor') and facts.get('outside'):
                problems.append(f'exterior flooring at {i},{j}')
    unique = list(dict.fromkeys(problems))
    simsight_log.line(f'detached builder: clearance box={box} problems={unique}')
    return unique


def _door(side: str, box: Box) -> tuple[Tile, Tile]:
    """(tile inside, tile outside) of the exterior door centred on `side`."""
    x1, n1, x2, n2 = box
    if side == 'north':
        x = (x1 + x2 - 1) // 2
        return (x, n1), (x, n1 - 1)
    if side == 'south':
        x = (x1 + x2 - 1) // 2
        return (x, n2 - 1), (x, n2)
    if side == 'west':
        n = (n1 + n2 - 1) // 2
        return (x1, n), (x1 - 1, n)
    n = (n1 + n2 - 1) // 2
    return (x2 - 1, n), (x2, n)


def _make_plan(
    box: Box, layout: str, names: Sequence[str], *, entrance: str, building_name: str, kind: str
) -> Any:
    """A one-floor house.Plan: one room, or two side by side ('side') or front and back."""
    import simsight_plus_house as house

    p: Any = house.Plan()
    x1, n1, x2, n2 = box
    p.width = x2 - x1
    p.depth = n2 - n1
    p.side = 'north'
    p.floors = 1
    if layout == 'one':
        p.rooms = [{'kind': 'room', 'name': names[0], 'box': box, 'level': 0}]
    elif layout == 'side':
        split = x1 + (x2 - x1) // 2
        p.rooms = [
            {'kind': 'room', 'name': names[0], 'box': (x1, n1, split, n2), 'level': 0},
            {'kind': 'room', 'name': names[1], 'box': (split, n1, x2, n2), 'level': 0},
        ]
        n = (n1 + n2 - 1) // 2
        p.doors.append(
            {
                'what': f'Door between {names[0]} and {names[1]}',
                'tile_a': (split - 1, n),
                'tile_b': (split, n),
                'front': False,
                'level': 0,
            }
        )
    else:
        split = n1 + (n2 - n1) // 2
        p.rooms = [
            {'kind': 'room', 'name': names[0], 'box': (x1, n1, x2, split), 'level': 0},
            {'kind': 'room', 'name': names[1], 'box': (x1, split, x2, n2), 'level': 0},
        ]
        x = (x1 + x2 - 1) // 2
        p.doors.append(
            {
                'what': f'Door between {names[0]} and {names[1]}',
                'tile_a': (x, split - 1),
                'tile_b': (x, split),
                'front': False,
                'level': 0,
            }
        )
    a, b = _door(entrance, box)
    p.doors.insert(
        0,
        {
            'what': f'Exterior door into {names[0]}',
            'tile_a': a,
            'tile_b': b,
            'front': True,
            'level': 0,
        },
    )
    p._a11y_detached = {
        'box': box,
        'levels': [0],
        'name': building_name,
        'kind': kind,
        'rooms': list(names),
    }
    return p


def _review(plan: Any) -> None:
    import simsight_plus_core as core
    import simsight_plus_house as house

    meta = plan._a11y_detached
    problems = _clearance(meta['box'])
    if problems:
        sample = '; '.join(problems[:5])
        more = len(problems) - 5
        core.speak(
            'That footprint is not clear: {}{}. Move the Build-grid cursor to another north-west corner; nothing was changed.'.format(
                sample, f'; and {more} more conflicts' if more > 0 else ''
            )
        )
        return
    estimate = house.wall_meters(plan) * house.WALL_PRICE + len(plan.doors) * 100
    x1, n1, x2, n2 = meta['box']
    text = '{}: {} by {} metres, {} room{}, north-west corner {} from west and {} from north, estimated structural cost {:,}. The surrounding one-tile clearance is clear in the current scan.'.format(
        meta['name'],
        x2 - x1,
        n2 - n1,
        len(plan.rooms),
        '' if len(plan.rooms) == 1 else 's',
        x1,
        n1,
        estimate,
    )
    rows = [
        ('Build it', 'The existing room and door workers verify every game action.', True),
        ('Cancel', None, False),
    ]

    def chosen(value: bool) -> None:
        if value:
            simsight_log.line(
                'detached builder: approved kind={} name={!r} box={} rooms={} doors={}'.format(
                    meta['kind'],
                    meta['name'],
                    meta['box'],
                    [(r['name'], r['box']) for r in plan.rooms],
                    [(d['what'], d['tile_a'], d['tile_b']) for d in plan.doors],
                )
            )
            house.start_build(plan)

    core.choose('Detached structure plan', text, rows, chosen, tag='build')


def _room_names(text: str | None, count: int, default_room: str) -> list[str]:
    """Exactly `count` cleaned room names from a comma list; defaults fill the gaps."""
    values = [
        _clean(v, default_room if i == 0 else 'Side room')
        for i, v in enumerate((text or '').split(','))
        if v.strip()
    ]
    while len(values) < count:
        values.append(default_room if not values else 'Side room')
    return values[:count]


_KINDS = [
    (
        'Storage shed',
        'Small enclosed one-room building.',
        ('shed', 4, 4, 'Storage shed', 'Storage'),
    ),
    (
        'Detached garage',
        'Cosmetic enclosed garage; functional cars or garage doors are not assumed.',
        ('garage', 6, 8, 'Detached garage', 'Garage'),
    ),
    (
        'Winter garden',
        'Enclosed garden room. Walls and roof are built; add large windows afterward with the validated wall placement tools.',
        ('winter garden', 6, 8, 'Winter garden', 'Winter garden'),
    ),
    (
        'Party house',
        'One or two enclosed rooms for entertaining.',
        ('party house', 8, 10, 'Party house', 'Party room'),
    ),
    (
        'Custom small building',
        'Choose a conservative footprint and room layout.',
        ('custom', None, None, 'Detached building', 'Main room'),
    ),
]


class _StructureChoices:
    """The builder's dialogs, one after the other: kind, size (custom only), layout,
    entrance, building name, room names, then the review."""

    def __init__(self, anchor: Tile) -> None:
        import simsight_plus_core as core

        self.core: Any = core
        self.anchor = anchor
        self.kind = ''
        self.default_building = ''
        self.default_room = ''
        self.size = (0, 0)
        self.layout = 'one'
        self.entrance = 'north'
        self.building_name = ''

    def start(self) -> None:
        anchor = self.anchor
        self.core.choose(
            'Detached structure',
            f'Current Build-grid tile {anchor[0]} from west and {anchor[1]} from north is the proposed north-west corner.',
            _KINDS,
            self.kind_chosen,
            tag='build',
        )

    def kind_chosen(self, value: tuple[str, int | None, int | None, str, str]) -> None:
        kind, w, d, self.default_building, self.default_room = value
        self.kind = kind
        if w is None or d is None:
            sizes = [('4 by 4', None, (4, 4)), ('6 by 8', None, (6, 8)), ('8 by 10', None, (8, 10))]
            self.core.choose(
                'Structure size',
                'The current grid tile remains the north-west corner.',
                sizes,
                self.size_chosen,
                tag='build',
            )
        else:
            self.size_chosen((w, d))

    def size_chosen(self, size: tuple[int, int]) -> None:
        self.size = size
        layouts = [
            ('One room', None, 'one'),
            ('Two rooms, west and east', None, 'side'),
            ('Two rooms, north and south', None, 'front'),
        ]
        self.core.choose(
            'Room layout',
            'Rooms divide the selected footprint evenly.',
            layouts,
            self.layout_chosen,
            tag='build',
        )

    def layout_chosen(self, layout: str) -> None:
        self.layout = layout
        entrances = [
            ('North entrance', None, 'north'),
            ('East entrance', None, 'east'),
            ('South entrance', None, 'south'),
            ('West entrance', None, 'west'),
        ]
        self.core.choose(
            'Entrance side',
            'The exterior door is centred on this side.',
            entrances,
            self.entrance_chosen,
            tag='build',
        )

    def entrance_chosen(self, entrance: str) -> None:
        self.entrance = entrance
        self.core.ask_text(
            'Building name',
            'Type the custom name used by the lot grid.',
            self.default_building,
            self.building_named,
        )

    def _room_count(self) -> int:
        return 1 if self.layout == 'one' else 2

    def building_named(self, text: str) -> None:
        self.building_name = _clean(text, self.default_building)
        prompt = (
            'Type the room name.'
            if self._room_count() == 1
            else 'Type two room names separated by a comma, in the selected positional order.'
        )
        self.core.ask_text('Room names', prompt, self.default_room, self.rooms_named)

    def rooms_named(self, text: str) -> None:
        names = _room_names(text, self._room_count(), self.default_room)
        anchor, (width, depth) = self.anchor, self.size
        box = (anchor[0], anchor[1], anchor[0] + width, anchor[1] + depth)
        _review(
            _make_plan(
                box,
                self.layout,
                names,
                entrance=self.entrance,
                building_name=self.building_name,
                kind=self.kind,
            )
        )


def open_builder() -> None:
    import simsight_grid as grid
    import simsight_plus_build as build
    import simsight_plus_core as core

    if not build.in_build_mode():
        core.speak('Enter Build mode first.')
        return
    if not grid.build_cursor():
        core.speak('Turn on the Build grid and move to the intended north-west corner first.')
        return
    anchor = tuple(grid.location.tile)
    _StructureChoices((anchor[0], anchor[1])).start()


def _built_completely(plan: Any) -> bool:
    """After a build: does every planned room exist, with its box and level?"""
    try:
        import simsight_plus_build as build
        import simsight_plus_rooms as rooms

        actual = {(build._room_box(r), int(r.level)) for r in rooms.scan_rooms()}
        complete = all((tuple(r['box']), int(r.get('level', 0))) in actual for r in plan.rooms)
        simsight_log.line(
            'detached builder: post-build complete={} actual={} wanted={}'.format(
                complete, actual, [(r['box'], r['level']) for r in plan.rooms]
            )
        )
        return complete
    except Exception as ex:
        simsight_log.line(f'detached builder: post-build verification failed: {ex!r}')
        simsight_log.error('detached builder: post-build verification')
        return False


def _follow_up(meta: dict[str, Any]) -> None:
    """A complete structure: label it on the lot grid, then offer its roof."""
    try:
        import simsight_lot_grid as lot_grid

        lot_grid.register_box_label(meta['box'], meta['levels'], meta['name'])
    except Exception as ex:
        simsight_log.line(f'detached builder: building label follow-up failed: {ex!r}')
        simsight_log.error('detached builder: building label')
    try:
        import simsight_plus_core as core
        import simsight_roof_tools as roofs

        core.post(
            core.speak,
            'The detached structure is complete. Choose its roof next. For a winter garden, place large windows afterward with the normal validated wall tools.',
        )
        core.post(roofs.roof_builder_for_box, meta['box'], 0, meta['rooms'])
    except Exception as ex:
        simsight_log.line(f'detached builder: roof follow-up failed: {ex!r}')
        simsight_log.error('detached builder: roof follow-up')


def _build_followup(plan: Any) -> Any:
    """Build the plan; a complete detached structure gets its label and roof offer."""
    result = _original_build(plan)
    meta = getattr(plan, '_a11y_detached', None)
    if not meta:
        return result
    if _built_completely(plan):
        _follow_up(meta)
    return result


def _original_build(plan: Any) -> Any:
    import simsight_house_planner as planner

    return planner._build_with_followup(plan)

"""Additive nonvisual roof builder and geometry-based build describer."""

from __future__ import annotations

import math
import threading
from typing import TYPE_CHECKING, Any, NamedTuple

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    Cell = tuple[int, int]
    Box = tuple[Any, ...]  # west, north, east, south (lot metres)
    # (shape, box, level, room names) or (shape, box, level, names, style)
    Plan = tuple[Any, ...]
    Row = tuple[str, str, Any]  # a chooser row: label, description, value

_plans: list[Plan] = []  # the roofs chosen this session
_PLANT_WORDS = ('plant', 'flower', 'shrub', 'tree', 'garden')
_FURNITURE_WORDS = (
    'fireplace',
    'bed',
    'wardrobe',
    'dresser',
    'sofa',
    'toilet',
    'shower',
    'sink',
    'table',
    'desk',
)


def _log(text: str) -> None:
    simsight_log.line('roof tools: ' + text)


def _rooms() -> list[Any]:
    import simsight_plus_rooms as rooms

    return rooms.scan_rooms()


def _box(room: Any) -> Box:
    import simsight_plus_build as build

    return build._room_box(room)


def _room_cells(room: Any) -> set[Cell]:
    import simsight_plus_build as build

    frame = build.frame()
    return {(math.floor(e), math.floor(n)) for e, n in (frame.local(x, z) for x, z in room.tiles)}


def _row_col_counts(
    cells: set[Cell], x1: int, z1: int, x2: int, z2: int
) -> tuple[set[int], set[int]]:
    """The distinct cell counts of the rows and of the columns of a box."""
    rows = {sum(1 for x in range(x1, x2) if (x, z) in cells) for z in range(z1, z2)}
    cols = {sum(1 for z in range(z1, z2) if (x, z) in cells) for x in range(x1, x2)}
    return rows, cols


def _shape(room: Any) -> str:
    cells = _room_cells(room)
    x1, z1, x2, z2 = _box(room)
    width, depth = x2 - x1, z2 - z1
    if len(cells) == width * depth:
        if min(width, depth) <= 2 and max(width, depth) >= 4:
            return 'straight'
        if abs(width - depth) <= 1:
            return 'roughly square'
        return 'rectangular'
    rows, cols = _row_col_counts(cells, x1, z1, x2, z2)
    if len(rows) <= 2 and len(cols) <= 2:
        return 'L-shaped'
    return 'irregular'


def _cells_shape(cells: Iterable[Cell]) -> str:
    cells = set(cells)
    if not cells:
        return 'empty'
    xs, zs = [p[0] for p in cells], [p[1] for p in cells]
    width, depth = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    if len(cells) == width * depth:
        return 'roughly square' if abs(width - depth) <= 1 else 'rectangular'
    rows, cols = _row_col_counts(cells, min(xs), min(zs), max(xs) + 1, max(zs) + 1)
    if len(rows) <= 2 and len(cols) <= 2:
        return 'L-shaped'
    return 'irregular'


def _position(box: Box, width: float, depth: float) -> str:
    cx, cz = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    ew = 'west' if cx < width / 3.0 else ('east' if cx > width * 2 / 3.0 else 'central')
    ns = 'north' if cz < depth / 3.0 else ('south' if cz > depth * 2 / 3.0 else 'central')
    if ew == 'central' and ns == 'central':
        return 'near the center'
    return 'in the {}{} part'.format(
        '' if ns == 'central' else ns + ' ', ew if ew != 'central' else 'central'
    )


def _components(cells: Iterable[Cell]) -> list[set[Cell]]:
    """Groups of edge-connected cells, largest first."""
    remaining, out = set(cells), []
    while remaining:
        todo, component = [remaining.pop()], set()
        while todo:
            p = todo.pop()
            component.add(p)
            for q in ((p[0] - 1, p[1]), (p[0] + 1, p[1]), (p[0], p[1] - 1), (p[0], p[1] + 1)):
                if q in remaining:
                    remaining.remove(q)
                    todo.append(q)
        out.append(component)
    return sorted(out, key=len, reverse=True)


def _bounds(points: Iterable[tuple[Any, Any]]) -> Box:
    """(west, north, east, south) around points, one metre past the last."""
    xs, zs = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(zs), max(xs) + 1, max(zs) + 1)


def _building_parts(rooms: list[Any], frame: Any) -> list[str]:
    all_cells = set().union(*(_room_cells(r) for r in rooms)) if rooms else set()
    buildings = _components(all_cells)
    parts = []
    for number, cells in enumerate(buildings, 1):
        box = _bounds(cells)
        label = 'Main building' if number == 1 else f'Building {number}'
        parts.append(
            f'{label} {_position(box, frame.width, frame.depth)}, {_cells_shape(cells)}, footprint {box[2] - box[0]} by {box[3] - box[1]} metres.'
        )
    return parts


def _ground_cells(frame: Any) -> tuple[set[Cell], set[Cell]]:
    """(pool cells, exterior floor cells) on the ground floor."""
    import simsight_tiles as tiles

    pool: set[Cell] = set()
    exterior_floor: set[Cell] = set()
    for i in range(frame.width):
        for j in range(frame.depth):
            facts = tiles.ground(frame, i, j, 0)
            if facts.get('pool'):
                pool.add((i, j))
            if facts.get('outside') and facts.get('floor'):
                exterior_floor.add((i, j))
    return pool, exterior_floor


def _ground_parts(frame: Any) -> list[str]:
    pool, exterior_floor = _ground_cells(frame)
    parts = []
    for cells in _components(pool):
        box = _bounds(cells)
        parts.append(
            f'Pool {_position(box, frame.width, frame.depth)}, approximately {box[2] - box[0]} by {box[3] - box[1]} metres.'
        )
    for cells in _components(exterior_floor)[:4]:
        box = _bounds(cells)
        parts.append(
            f'Possible patio, porch, deck or path {_position(box, frame.width, frame.depth)}, {len(cells)} square metres; exterior flooring alone cannot distinguish its purpose.'
        )
    return parts


def _plant_point(obj: Any, frame: Any) -> tuple[Any, Any] | None:
    """Lot coordinates of a plant, or None for anything else."""
    try:
        if obj.is_sim:
            return None
        import simsight_plus_rooms as rooms

        tags = ' '.join(rooms.tag_names(obj)).lower()
        if any(w in tags for w in _PLANT_WORDS):
            return frame.local(obj.position.x, obj.position.z)
    except Exception:  # an object that will not say is not a plant
        return None
    return None


def _plant_parts(frame: Any) -> list[str]:
    import services

    plants = [
        point
        for point in (_plant_point(obj, frame) for obj in services.object_manager().values())
        if point is not None
    ]
    if not plants:
        return []
    box = _bounds(plants)
    return [
        f'Possible garden or landscaped plant cluster {_position(box, frame.width, frame.depth)}, {len(plants)} detected plants.'
    ]


def describe_lot() -> None:
    import simsight_plus_build as build
    import simsight_plus_core as core

    rooms, frame = _rooms(), build.frame()
    buildings = _building_parts(rooms, frame)
    parts = [f'Lot {frame.width} by {frame.depth} metres.']
    parts += buildings
    parts += _ground_parts(frame)
    parts += _plant_parts(frame)
    if not buildings:
        parts.append('No enclosed building footprint detected.')
    core.speak(' '.join(parts))


def _side(room: Any, obj: Any) -> str:
    import simsight_plus_build as build

    x1, z1, x2, z2 = _box(room)
    try:
        x, z = build.frame().local(float(obj.position.x), float(obj.position.z))
    except Exception:  # no lot frame: world coordinates
        x, z = float(obj.position.x), float(obj.position.z)
    values = (
        (abs(z - z1), 'north'),
        (abs(x2 - x), 'east'),
        (abs(z2 - z), 'south'),
        (abs(x - x1), 'west'),
    )
    return min(values)[1]


def _kind(obj: Any) -> str | None:
    try:
        import simsight_plus_rooms as rooms

        tags = rooms.tag_names(obj)
        for name, wanted, _weight in rooms.KINDS:
            if tags.intersection(wanted):
                return name.lower()
        joined = ' '.join(tags).lower()
        for word in _FURNITURE_WORDS:
            if word in joined:
                return word
    except Exception:  # an object without tags is no major furniture
        return None
    return None


def _doors(room: Any, by_id: dict[tuple[Any, Any], str], frame: Any) -> list[tuple[str, str]]:
    import simsight_tiles as tiles

    found = set()
    for i, j in _room_cells(room):
        for side in _doors_or_none(frame, i, j, room.level):
            di, dj = tiles.STEPS[side]
            rid = tiles.room(frame, i + di, j + dj, room.level)
            destination = by_id.get((rid, room.level), 'outside')
            found.add((tiles.direction_name(side), destination))
    return sorted(found)


def _doors_or_none(frame: Any, i: int, j: int, level: Any) -> dict[int, Any]:
    import simsight_tiles as tiles

    try:
        return tiles.doors(frame, i, j, level)
    except Exception:  # doors the game will not list
        return {}


def _room_text(room: Any, all_rooms: list[Any], frame: Any) -> str:
    x1, z1, x2, z2 = _box(room)
    by_id = {(r.rid, r.level): r.name for r in all_rooms}
    text = f'{room.name}, {_shape(room)}, {x2 - x1} by {z2 - z1} metres'
    doors = _doors(room, by_id, frame)
    if doors:
        text += '. Doors: ' + '; '.join(f'{side} wall to {dest}' for side, dest in doors)
    major = []
    for obj in room.objects:
        kind = _kind(obj)
        if kind:
            major.append(f'{kind} on the {_side(room, obj)} side')
    if major:
        text += '. ' + '; '.join(major[:12])
    return text + '.'


def _outer_box(boxes: Sequence[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def describe_build() -> None:
    import simsight_plus_build as build
    import simsight_plus_core as core

    rooms = _rooms()
    if not rooms:
        core.speak('No enclosed rooms were found on this lot.')
        return
    frame = build.frame()
    levels = sorted({r.level for r in rooms})
    house = _outer_box([_box(r) for r in rooms])
    summary = f'{len(levels)} floors, {len(rooms)} rooms. Overall enclosed footprint approximately {house[2] - house[0]} by {house[3] - house[1]} metres.'
    rows = []
    for level in levels:
        floor_rooms = [r for r in rooms if r.level == level]
        rows.append(
            (
                f'Floor {level}',
                '{} rooms: {}.'.format(len(floor_rooms), ', '.join(r.name for r in floor_rooms)),
                floor_rooms,
            )
        )

    def floor_chosen(floor_rooms: list[Any]) -> None:
        rrows = [(r.name, _room_text(r, rooms, frame), r) for r in floor_rooms]
        core.choose(
            f'Rooms on floor {floor_rooms[0].level}',
            'Choose a room for its geometry, doors and major furniture.',
            rrows,
            lambda r: core.speak(_room_text(r, rooms, frame)),
            tag='build',
        )

    core.choose('Describe this build', summary, rows, floor_chosen, tag='build')


def _target_rows() -> tuple[list[Row], list[Any]]:
    rooms = _rooms()
    if not rooms:
        return [], []
    top = max(r.level for r in rooms)
    top_rooms = [r for r in rooms if r.level == top]
    whole = _outer_box([_box(r) for r in top_rooms])
    union = set().union(*(_room_cells(r) for r in top_rooms))
    safe_whole = _cells_shape(union) in ('rectangular', 'roughly square')
    rows: list[Row] = [
        (
            'Whole top floor',
            '{} rooms, {} by {} metres; {}.'.format(
                len(top_rooms),
                whole[2] - whole[0],
                whole[3] - whole[1],
                'safe rectangular footprint'
                if safe_whole
                else 'non-rectangular and will be refused',
            ),
            (whole, top, [r.name for r in top_rooms], safe_whole),
        )
    ]
    rows += [
        (
            r.name,
            f'{_shape(r)}, {_box(r)[2] - _box(r)[0]} by {_box(r)[3] - _box(r)[1]} metres.',
            (_box(r), r.level, [r.name], _shape(r) in ('rectangular', 'roughly square')),
        )
        for r in top_rooms
    ]
    return rows, rooms


class _RoofTexts(NamedTuple):
    """What a roof chooser says; the two choosers word it differently."""

    title: str
    flat_row: str
    hip_row: str
    flat_said: str  # formatted with the room names, width and depth
    overhang_prompt: str
    half_metre: str
    one_metre: str


_WHOLE_FLOOR = _RoofTexts(
    title='Roof shape',
    flat_row='Uses the existing top-floor ceiling as the roof deck; no pitched roof piece is added.',
    hip_row='Slopes on four sides; a square can meet at a pyramid-like point.',
    flat_said='Flat roof selected. The ceiling over {} is the roof deck, {} by {} metres. Add a floor pattern or roof trim from the catalog if wanted.',
    overhang_prompt='Choose a conservative preset. The roof worker verifies the final handles.',
    half_metre='Extends half a metre on every side, where the lot boundary permits.',
    one_metre='Extends one metre on every side, where the lot boundary permits.',
)
_DETACHED = _RoofTexts(
    title='Roof for detached structure',
    flat_row='Uses the completed room ceiling as the roof deck.',
    hip_row='Slopes on four sides; a square may meet at a pyramid-like point.',
    flat_said='Flat roof selected over {}, {} by {} metres.',
    overhang_prompt='Choose a conservative preset.',
    half_metre='Extends half a metre where the lot boundary permits.',
    one_metre='Extends one metre where the lot boundary permits.',
)


def _choose_roof_kind(texts: _RoofTexts, box: Box, level: Any, names: list[str]) -> None:
    """Flat, gabled or hipped over box; a pitched roof then asks the overhang."""
    import simsight_plus_core as core

    width, depth = box[2] - box[0], box[3] - box[1]
    kinds = [
        ('Flat roof', texts.flat_row, 'flat'),
        ('Gabled roof', 'Triangular-prism roof with a ridge along the longest side.', 'gable'),
        ('Hipped roof', texts.hip_row, 'hip'),
    ]

    def kind_chosen(kind: str) -> None:
        if kind == 'flat':
            _plans.append(('flat', box, level, tuple(names)))
            core.speak(texts.flat_said.format(', '.join(names), width, depth))
            return
        overhangs = [
            ('No overhang', 'Roof edges follow the detected walls.', 0),
            ('Half-metre overhang', texts.half_metre, 0.5),
            ('One metre overhang', texts.one_metre, 1),
        ]

        def overhang_chosen(amount: float) -> None:
            import simsight_plus_build as build

            frame = build.frame()
            expanded = (
                max(0, box[0] - amount),
                max(0, box[1] - amount),
                min(frame.width, box[2] + amount),
                min(frame.depth, box[3] + amount),
            )
            _choose_roof_style(kind, expanded, level, names, amount)

        core.choose('Roof overhang', texts.overhang_prompt, overhangs, overhang_chosen, tag='build')

    core.choose(
        texts.title,
        'Target {} by {} metres over {}.'.format(width, depth, ', '.join(names)),
        kinds,
        kind_chosen,
        tag='build',
    )


def roof_builder() -> None:
    import simsight_plus_core as core

    rows, _rooms_found = _target_rows()
    if not rows:
        core.speak('Build at least one enclosed room first.')
        return

    def target_chosen(target: tuple[Box, Any, list[str], bool]) -> None:
        box, level, names, safe = target
        if not safe:
            _log(f'roof refused: non-rectangular target level {level} rooms {names} box {box}')
            core.speak(
                'This footprint is not rectangular or square. Automatic L and T shaped roofing is disabled until it has been tested. Nothing was placed.'
            )
            return
        _choose_roof_kind(_WHOLE_FLOOR, box, level, names)

    core.choose(
        'Accessible roof builder', 'Choose what the roof covers.', rows, target_chosen, tag='build'
    )


def roof_builder_for_box(box: Sequence[Any], level: Any, names: Iterable[str]) -> None:
    """Open the normal conservative roof chooser for one verified footprint."""
    box_t = tuple(box)
    name_list = list(names)
    _log(f'targeted roof chooser level={level} box={box_t} rooms={name_list}')
    _choose_roof_kind(_DETACHED, box_t, level, name_list)


def _choose_roof_style(
    kind: str, box: Box, level: Any, names: list[str], overhang: float = 0
) -> None:
    """List the game's roof styles of this kind (catalog read off the
    simulation thread), then draw the chosen one over box."""
    import simsight_plus_build as build
    import simsight_plus_core as core
    import simsight_plus_draw as draw

    def chosen(name: str) -> None:
        if build._busy[0]:
            core.speak('Still working on the last build step.')
            return
        build._busy[0] = True
        draw_state: dict[str, Any] = draw._state  # its values vary by key
        draw_state['style'] = (4, '', name)
        _plans.append((kind, box, level, tuple(names), name))
        _log(
            f'roof request kind={kind} style={name!r} level={level} box={box} overhang={overhang} rooms={names}'
        )
        core.speak(
            'Placing {} over {}. Please wait and let go of Alt.'.format(name, ', '.join(names))
        )
        threading.Thread(
            target=draw._draw_roof_box, args=(box, level), name='a11y_roof_builder', daemon=True
        ).start()

    def work() -> None:
        try:
            items = build.fetch_catalog(4, '', 'none')
            words = ('gable', 'gabled') if kind == 'gable' else ('hip', 'hipped', 'pyramid')
            matched = [it for it in items if any(w in str(it[0]).lower() for w in words)]
            choices = matched or items
            _log(
                f'roof catalog kind={kind} entries={[(str(name), price, colors) for name, price, colors in choices]}'
            )
            rows = [
                (
                    name,
                    'Game roof style, {} color swatch{} reported; the update currently selects the default swatch. Placement will be verified after its handles are stretched.'.format(
                        colors, '' if colors == 1 else 'es'
                    ),
                    name,
                )
                for name, _price, colors in choices
            ]
            core.post(
                core.choose,
                f'{kind.capitalize()} styles',
                f'{len(rows)} matching styles.',
                rows,
                chosen,
                'build',
            )
        except Exception:
            simsight_log.error('roof tools: roof catalog')

    threading.Thread(target=work, name='a11y_roof_catalog', daemon=True).start()


def describe_roof_level() -> None:
    import simsight_plus_core as core

    rooms = _rooms()
    if not rooms:
        core.speak('No roof-supporting rooms were found.')
        return
    top = max(r.level for r in rooms)
    below = [r for r in rooms if r.level == top]
    fireplaces = [(r, o) for r in below for o in r.objects if _kind(o) == 'fireplace']
    text = 'Roof level above floor {}. It covers {}.'.format(top, ', '.join(r.name for r in below))
    if _plans:
        text += ' This session created {} roof plan{}.'.format(
            len(_plans), '' if len(_plans) == 1 else 's'
        )
    if fireplaces:
        text += ' {} fireplace{} below provide chimney alignment points.'.format(
            len(fireplaces), '' if len(fireplaces) == 1 else 's'
        )
    core.speak(text)


def _lot_point(obj: Any) -> tuple[float, float]:
    """(east, north) of an object in lot metres; world x, z without a frame."""
    try:
        import simsight_plus_build as build

        return build.frame().local(float(obj.position.x), float(obj.position.z))
    except Exception:  # no lot frame: world coordinates
        return float(obj.position.x), float(obj.position.z)


def _move_cursor_above(obj: Any, roof_level: int) -> bool:
    """Put the Build cursor above obj on the roof level."""
    try:
        import simsight_grid as grid

        if not grid.active():
            grid.enter()
        return bool(grid.focus_position(obj.position, roof_level))
    except Exception as ex:
        _log(f'chimney cursor move failed: {ex!r}')
        return False


def _chimney_chosen(value: tuple[Any, Any]) -> None:
    import simsight_plus_core as core

    room, obj = value
    roof_level = max(r.level for r in _rooms()) + 1
    east, north = _lot_point(obj)
    moved = _move_cursor_above(obj, roof_level)
    _log(
        'chimney target fireplace={} room={!r} roof_level={} tile=({}, {}) moved={}'.format(
            getattr(obj, 'id', '?'),
            room.name,
            roof_level,
            int(round(east)),
            int(round(north)),
            moved,
        )
    )
    core.speak(
        'Chimney alignment point: directly above the fireplace on the {} side of {}, approximately {} metres from the west edge and {} metres from the north edge. {} Choose a chimney in the catalog, return to the grid, and press Enter yourself. The normal exact placement validator decides whether it can be bought.'.format(
            _side(room, obj),
            room.name,
            int(round(east)),
            int(round(north)),
            f'The Build cursor is positioned there on roof level {roof_level}.'
            if moved
            else 'The cursor could not be moved, so nothing was changed.',
        )
    )


def chimney_assistant() -> None:
    import simsight_plus_core as core

    fireplaces = [(r, o) for r in _rooms() for o in r.objects if _kind(o) == 'fireplace']
    if not fireplaces:
        core.speak('No fireplace was identified. Nothing will be placed.')
        return
    rows = [
        (
            f'Fireplace in {room.name}',
            f'On the {_side(room, obj)} side; roof point directly above it.',
            (room, obj),
        )
        for room, obj in fireplaces
    ]
    core.choose(
        'Chimney assistant',
        'Choose a fireplace. This assistant will not force an unverified object onto the roof.',
        rows,
        _chimney_chosen,
        tag='build',
    )

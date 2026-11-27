"""Build a room, draw a wall, put a door or window on a wall, paint a room."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_plus_core as core

from .state import _product, busy, catalog_state
from .lot import _touch_text, frame, free_spots, in_build_mode, tile_map
from .pointer import click, drag, send_ui, set_floor, user32
from .jobs import (
    VIEW_FROM,
    _tool_is,
    cost_check,
    funds,
    no_mouse,
    on_main,
    point_and_act,
    run_job,
    tool_name,
    undo,
    whole_numbers,
)
from .rooms import (
    _cell_status,
    _new_room_box,
    room_ids,
    room_rows,
    room_walls,
    wall_cell_point,
    wall_point,
    wall_things,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .state import Box, Row


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
        _f, grid = tile_map()
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
                    lambda t: _do_room(w, d, *([*whole_numbers(t), None, None])[:2]),
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
            simsight_log.error(core.log_exc('room job'))
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


def room_worker(want: Sequence[int], overview: bool = False, tries: int = 4, level: int = 0) -> Any:
    """Basement rooms (level below 0) go through the game's Basement tool."""
    import simsight_basement as basement

    return basement.room_worker(want, overview=overview, tries=tries, level=level)

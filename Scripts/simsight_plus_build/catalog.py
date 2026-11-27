"""The accessible catalog: lists from the game's catalog through the Flash
bridge (a11yplus.flash), choosing an item, furnished rooms."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from simsight_commands import real_annotations
import simsight_plus_core as core
import simsight_steps as steps

from .state import _product, busy, catalog_state, last_error
from .lot import _touch_text, free_spots, in_build_mode, tile_map
from .pointer import click, send_ui
from .jobs import cost_check, no_mouse, run_job, tool_name, whole_numbers
from .rooms import _new_room_box, room_ids
from .features import reselect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from simsight_steps import Job
    from .state import Row


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

_bridge = {'pong': 0.0}


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


def _flash_reply(encoded: str = '') -> Any:
    import simsight_basement as basement

    return basement.reply(encoded)

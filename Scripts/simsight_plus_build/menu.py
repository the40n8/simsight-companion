"""The build helper menu (Alt+B in Build mode)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import simsight_plus_core as core

from .lot import in_build_mode
from .jobs import tool_name, undo, watch_product
from .features import build_room, build_wall, paint_room, place_on_wall
from .catalog import open_catalog, place_object_in_room, place_styled_room

if TYPE_CHECKING:
    from collections.abc import Callable


def _undo_spoken() -> None:
    undo()
    core.speak('Undone.')


@core.on_zone_started
def _start() -> None:
    watch_product()


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

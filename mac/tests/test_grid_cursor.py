"""The lot grid and the Build cursor (simsight_grid) on a fake 6 by 8 lot:
moving, what a tile says, Enter placing exactly or offering the nearest
tile that fits, and the keys other modules rely on (grid.location.tile, .level,
.facing)."""

import types
import unittest

import fakegame
from fakegame import V, module

fakegame.scripts_on_path()
SAID = []  # everything spoken, in order
PLACED = []  # simsight_place.place calls


def said():
    out = list(SAID)
    del SAID[:]
    return out


module('simsight_lang', _detect_lang=lambda: 'en')
module('simsight_speech', speak=lambda text, explicit=False: SAID.append(text))
sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands',
    Command=lambda *a, **k: lambda f: f,
    CommandType=types.SimpleNamespace(Live=1),
)
sims4.math = module(
    'sims4.math',
    Vector3=V,
    Location=lambda transform, surface: transform,
    Transform=lambda point, rotation: (point, rotation),
    angle_to_yaw_quaternion=lambda angle: angle,
    Quaternion=types.SimpleNamespace(IDENTITY=lambda: None),
)


class Lot:
    corners = [V(0, 0, 0), V(6, 0, 0), V(0, 0, 8), V(6, 0, 8)]
    center = V(3, 0, 4)

    def is_position_on_lot(self, p):
        return 0 <= p.x <= 6 and 0 <= p.z <= 8

    def get_default_position(self):
        return V(3, 0, 0.2)  # the street is south

    def get_front_door(self):
        return None


class Sim:
    id, is_sim, level, parent = 1, True, 0, None
    position = V(1.5, 0, 1.5)

    def is_in_inventory(self):
        return False

    def is_hidden(self):
        return False

    def raycast_context(self):
        return None  # no ray tests: no walls


SIM = Sim()
module(
    'services',
    current_zone_id=lambda: 42,
    active_lot=Lot,
    get_active_sim=lambda: SIM,
    object_manager=lambda: {1: SIM},
)
module('terrain', get_terrain_height=lambda x, z, routing_surface=None: 0.0)
module(
    'routing',
    SurfaceIdentifier=lambda zone, level, kind: (zone, level),
    SurfaceType=types.SimpleNamespace(SURFACETYPE_WORLD='world'),
    Location=lambda *a: a,
    test_point_placement_in_navmesh=lambda surface, p: True,
)


def test_location(obj, def_id, location, ignore):
    point, angle = location
    ok = point.x > 3.2  # the object fits from the fourth column east
    return (ok, None if ok else 'ObjectFootprint intersects')


module(
    'build_buy',
    has_floor_at_location=lambda p, level: True,
    is_location_outside=lambda p, level: False,
    is_location_pool=lambda p, level: False,
    is_location_natural_ground=lambda p, level: False,
    get_block_id=lambda zone, p, level: 1,
    test_location_for_object=test_location,
    get_lowest_level_allowed=lambda: 0,
    get_highest_level_allowed=lambda: 1,
)
dist = module('distributor')
dist.ops = module('distributor.ops', SendUIMessage=lambda message: message)
dist.system = module(
    'distributor.system',
    Distributor=types.SimpleNamespace(
        instance=lambda: types.SimpleNamespace(add_op_with_no_owner=lambda op: None)
    ),
)
module('simsight_settings', get=lambda key, default=None: default)
module(
    'simsight_scanner',
    _speak_parts=lambda parts: SAID.append(''.join(str(p) for p in parts)),
    _entry_for=lambda obj, *a: types.SimpleNamespace(name='Bella'),
    _name_part=lambda entry: entry.name,
)
module('simsight_keys', _scanner_effective=lambda: False)
module(
    'simsight_build',
    controls_enabled=lambda: True,
    active=lambda: False,
    aiming=lambda: False,
    refuse_disabled=lambda: False,
)
PRODUCT = [{'id': 7, 'name': 'Sofa'}]


def place(point, level, surface, **options):
    PLACED.append(((point.x, point.z), level, sorted(options.items())))
    return True


module(
    'simsight_place',
    in_build_mode=lambda: True,
    product=lambda: PRODUCT[0],
    requirement=lambda def_id: None,
    _NEEDS={},
    place=place,
    cancel_pending=lambda: None,
    undo=lambda: None,
    sell=lambda obj: None,
    rotate=lambda step: None,
)
module(
    'simsight_rooms',
    display_name=lambda room, level: 'Lounge',
    wall_context=lambda f: [],
    show_picker=lambda: None,
)
module('simsight_chair_snap', fit_parts=lambda item, f: None, try_place=lambda item, f: None)
module(
    'simsight_layers',
    focused_target=lambda: None,
    enabled=lambda: False,
    change_layer=lambda delta: None,
)


def alignment_fit(frame, with_facing=True, objects=None):
    return grid._fit_parts_base(frame, with_facing=with_facing, objects=objects)


def room_parts(frame):
    return grid._room_parts_base(frame)


module('simsight_chair_alignment', fit_parts=alignment_fit)
module('simsight_lot_grid', room_parts=room_parts)

import simsight_grid as grid  # noqa: E402


class BuildCursor(unittest.TestCase):
    def setUp(self):
        del SAID[:], PLACED[:]
        grid.retire()
        self.assertTrue(grid.placement_toggle())

    def test_enter_reads_the_start_tile(self):
        self.assertEqual(
            said(),
            [
                (
                    'Build placement on. Arrow keys move one tile. R turns the object, Enter places '
                    'it, number keys 1 to 6 read details, Space repeats, Escape leaves. Selected: '
                    'Sofa. The street is to the south. '
                ),
                'Bella. Does not fit here facing north: something is in the way. ',
            ],
        )
        self.assertEqual(
            (grid.location.tile, grid.location.level, grid.location.facing), ((1, 1), 0, 0)
        )
        self.assertTrue(grid.build_cursor())

    def test_arrows_move_one_tile_and_stop_at_the_edge(self):
        said()
        self.assertTrue(grid.on_key('right'))
        self.assertEqual(grid.location.tile, (2, 1))
        self.assertEqual(
            said(), ['Empty floor. Does not fit here facing north: something is in the way. ']
        )
        grid.on_key('left')
        grid.on_key('left')
        said()
        self.assertFalse(grid.on_key('left'))
        self.assertEqual(said(), ['Lot edge.'])
        self.assertEqual(grid.location.tile, (0, 1))

    def test_turn_and_floor(self):
        said()
        grid.on_key('turn')
        self.assertEqual(grid.location.facing, 1)
        self.assertEqual(said(), ['Facing east. Does not fit here: something is in the way. '])
        grid.on_key('floorup')
        self.assertEqual(grid.location.level, 1)
        said()
        self.assertFalse(grid.on_key('floorup'))
        self.assertEqual(said(), ['Floor limit.'])

    def test_enter_offers_the_nearest_tile_then_places_there(self):
        said()
        self.assertFalse(grid.on_key('open'))
        self.assertEqual(
            said(),
            [
                (
                    'Sofa does not fit here: something is in the way. It fits 2 east, facing north. '
                    'Press Enter again to place it there, or move to cancel.'
                )
            ],
        )
        self.assertEqual(PLACED, [])
        self.assertTrue(grid.on_key('open'))
        self.assertEqual(PLACED, [((3.5, 1.5), 0, [('angle', 0.0), ('exact', True)])])

    def test_moving_cancels_the_offer(self):
        grid.on_key('open')
        grid.on_key('up')
        grid.on_key('down')
        del SAID[:]
        grid.on_key('open')
        self.assertEqual(PLACED, [])
        self.assertTrue(SAID[0].startswith('Sofa does not fit here'))

    def test_enter_places_exactly_where_it_fits(self):
        grid.focus_position(V(4.2, 0, 6.7), 0)
        self.assertEqual(grid.location.tile, (4, 6))
        self.assertTrue(grid.on_key('open'))
        self.assertEqual(PLACED, [((4.5, 6.5), 0, [('angle', 0.0), ('exact', True)])])

    def test_position_detail(self):
        said()
        grid.on_key('info6')
        self.assertEqual(
            said(), ['Column 2 of 6, row 2 of 8, floor 0. The street is to the south. ']
        )

    def test_escape_leaves(self):
        said()
        self.assertTrue(grid.on_key('back'))
        self.assertEqual(said(), ['Lot grid off.'])
        self.assertFalse(grid.build_cursor())


if __name__ == '__main__':
    unittest.main(verbosity=0)

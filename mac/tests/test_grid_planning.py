"""Lot planning (simsight_lot_grid) and roof tools (simsight_roof_tools):
footprint shapes, rooms joined into buildings, what is said about them,
and the saved building labels (SimSight_BuildingLabels.json must keep its
name and format: players' labels have to load again)."""

import json
import os
import shutil
import tempfile
import types
import unittest

import fakegame
from fakegame import V, module

fakegame.scripts_on_path()
SAID = []
module('simsight_lang', detect_lang=lambda: 'en', text=lambda en, pl: en)
module('simsight_plus_core', speak=SAID.append)


class Frame:
    """simsight_plus_build.Frame of a lot from x 0..10 and z 0..8."""

    width, depth = 10, 8

    def world(self, e, n):
        return (0.0 + e, 8.0 - n)

    def local(self, x, z):
        return (x - 0.0, 8.0 - z)


class Room:
    def __init__(self, rid, name, level, cells, objects=()):
        self.rid, self.name, self.level, self.objects = rid, name, level, list(objects)
        # room tiles are world tile centres; Frame.local maps them back
        self.tiles = [(e + 0.5, 8.0 - n - 0.5) for e, n in cells]


def block(e1, n1, e2, n2):
    return [(e, n) for e in range(e1, e2) for n in range(n1, n2)]


class Obj:
    is_sim = False

    def __init__(self, oid, x, z, tags, level=0):
        self.id, self.level, self.tags = oid, level, set(tags)
        self.position = V(x, 0, z)

    def is_in_inventory(self):
        return False

    def is_hidden(self):
        return False


class Door(Obj):
    pass


ROOMS = [
    Room(1, 'Kitchen', 0, block(0, 0, 4, 3)),
    Room(2, 'Lounge', 0, block(4, 0, 7, 3)),
    Room(3, 'Attic', 1, block(0, 0, 4, 3)),
    Room(4, 'Shed', 0, block(8, 6, 10, 8)),
]
OBJECTS = {
    1: Door(1, 4.0, 6.5, ('door',)),  # between the kitchen and the lounge
    2: Obj(2, 1.5, 8.0, ('window',)),
    3: Obj(3, 9.0, 0.5, ('window',)),
    4: Obj(4, 5.0, 1.0, ('plant',)),
}


def room_box(room):
    cells = [(int(e), int(n)) for e, n in (Frame().local(x, z) for x, z in room.tiles)]
    return (
        min(c[0] for c in cells),
        min(c[1] for c in cells),
        max(c[0] for c in cells) + 1,
        max(c[1] for c in cells) + 1,
    )


module('simsight_plus_build', frame=Frame, _room_box=room_box)
module(
    'simsight_plus_rooms',
    scan_rooms=lambda: list(ROOMS),
    tag_names=lambda obj: set(obj.tags),
    KINDS=[],
)


class Lot:
    corners = [V(0, 0, 0), V(10, 0, 0), V(0, 0, 8), V(10, 0, 8)]

    def is_position_on_lot(self, p):
        return 0 <= p.x <= 10 and 0 <= p.z <= 8


module(
    'services',
    active_lot=Lot,
    object_manager=lambda: dict(OBJECTS),
    current_zone_id=lambda: 42,
    get_persistence_service=lambda: types.SimpleNamespace(
        get_save_slot_proto_buff=lambda: types.SimpleNamespace(slot_id=3)
    ),
)

module('terrain', get_terrain_height=lambda x, z, routing_surface=None: 0.0)
module(
    'routing',
    SurfaceIdentifier=lambda zone, level, kind: (zone, level),
    SurfaceType=types.SimpleNamespace(SURFACETYPE_WORLD='world'),
)
module('sims4').math = module('sims4.math', Vector3=V)
# A pool in the south west corner (world x and z below 2), a patio north
# of it (x 4 to 6, z below 1), open ground east of x 7.
module(
    'build_buy',
    is_location_pool=lambda p, level: p.x < 2 and p.z < 2,
    has_floor_at_location=lambda p, level: 4 <= p.x < 6 and p.z < 1,
    is_location_outside=lambda p, level: p.z < 2 or p.x >= 7,
)

import simsight_log  # noqa: E402
import simsight_lot_grid as lot_grid  # noqa: E402
import simsight_roof_tools as roof  # noqa: E402


class Shapes(unittest.TestCase):
    def test_cells_shape(self):
        self.assertEqual(roof._cells_shape([]), 'empty')
        self.assertEqual(roof._cells_shape(block(0, 0, 3, 3)), 'roughly square')
        self.assertEqual(roof._cells_shape(block(0, 0, 5, 2)), 'rectangular')
        self.assertEqual(roof._cells_shape(block(0, 0, 3, 1) + [(0, 1), (0, 2)]), 'L-shaped')
        stairs = [(0, 0), (0, 1), (1, 1), (0, 2), (1, 2), (2, 2)]
        self.assertEqual(roof._cells_shape(stairs), 'irregular')

    def test_components_largest_first(self):
        groups = roof._components(block(0, 0, 2, 2) + [(5, 5)] + block(8, 0, 9, 3))
        self.assertEqual([len(g) for g in groups], [4, 3, 1])

    def test_position(self):
        self.assertEqual(roof._position((4, 3, 6, 5), 10, 8), 'near the center')
        self.assertEqual(roof._position((0, 0, 2, 2), 10, 8), 'in the north west part')
        self.assertEqual(roof._position((4, 6, 6, 8), 10, 8), 'in the south central part')
        self.assertEqual(roof._position((8, 3, 10, 5), 10, 8), 'in the east part')

    def test_room_shape(self):
        self.assertEqual(roof._shape(ROOMS[0]), 'roughly square')
        self.assertEqual(roof._shape(Room(8, 'Den', 0, block(0, 0, 5, 3))), 'rectangular')
        self.assertEqual(roof._shape(Room(9, 'Hall', 0, block(0, 0, 1, 5))), 'straight')


class Buildings(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        simsight_log.PATH = os.path.join(self.folder, 'SimSight.log')
        lot_grid._state.labels = None
        lot_grid._state.cache = None
        del SAID[:]

    def tearDown(self):
        shutil.rmtree(self.folder)

    def labels_file(self):
        return os.path.join(self.folder, 'SimSight_BuildingLabels.json')

    def test_rooms_join_side_by_side_and_above(self):
        snap = lot_grid._snapshot(refresh=True)
        house, shed = snap.buildings
        self.assertEqual(sorted(r.room.name for r in house.rooms), ['Attic', 'Kitchen', 'Lounge'])
        self.assertEqual((house.box, house.levels), ((0, 0, 7, 3), [0, 1]))
        self.assertEqual(house.signature, '3:42:0:0:7:3:0:1')
        self.assertEqual((shed.box, shed.signature), ((8, 6, 10, 8), '3:42:8:6:10:8:0:0'))
        self.assertEqual((house.doors, house.windows), ({1}, {2}))
        self.assertEqual((shed.doors, shed.windows), (set(), {3}))

    def test_cursor_tiles_find_their_building_and_room(self):
        # Build cursor tile (i, j) has its centre at world x = j + 0.5,
        # z = i + 0.5 on this lot; the kitchen's cell (1, 1) is at x 1.5, z 6.5.
        self.assertEqual(lot_grid._describe_tile(6, 1, 0), 'Main building, Kitchen.')
        self.assertEqual(lot_grid._describe_tile(6, 1, 1), 'Main building, Attic.')
        self.assertEqual(lot_grid._describe_tile(1, 8, 0), 'Unlabelled building 1, Shed.')
        self.assertEqual(lot_grid._describe_tile(0, 0, 0), 'Pool.')
        self.assertEqual(
            lot_grid._describe_tile(4, 8, 0),
            'Open outdoor ground; apparently clear, but construction still requires game '
            'validation.',
        )
        self.assertIsNone(lot_grid._building_at(4, 8, 0))
        self.assertEqual(lot_grid._building_at(6, 5, 0).name, 'Main building')

    def test_describe_buildings(self):
        lot_grid.describe_buildings()
        self.assertEqual(
            SAID,
            [
                (
                    'Main building: footprint 7 by 3 metres, 3 rooms, level0,1, 1 door, 1 window. '
                    'Unlabelled building 1: footprint 2 by 2 metres, 1 room, level0, 0 doors, 1 window.'
                )
            ],
        )

    def test_saved_labels_load_and_save_in_the_same_format(self):
        with open(self.labels_file(), 'w', encoding='utf-8') as stream:
            json.dump({'3:42:8:6:10:8:0:0': 'Garden shed', 'old': '  '}, stream)
        self.assertTrue(lot_grid.register_box_label((0, 0, 7, 3), [1, 0], '  Big   house  '))
        with open(self.labels_file(), encoding='utf-8') as stream:
            text = stream.read()
        self.assertEqual(
            text,
            '{\n  "3:42:0:0:7:3:0:1": "Big house",\n  "3:42:8:6:10:8:0:0": "Garden shed"\n}',
        )
        lot_grid.describe_buildings()
        self.assertTrue(SAID[-1].startswith('Big house: footprint 7 by 3 metres'))
        self.assertIn('Garden shed: footprint 2 by 2 metres', SAID[-1])

    def test_label_names_are_cut_to_sixty(self):
        self.assertTrue(lot_grid.register_box_label((8, 6, 10, 8), [0], 'ż' * 70))
        with open(self.labels_file(), encoding='utf-8') as stream:
            self.assertEqual(json.load(stream), {'3:42:8:6:10:8:0:0': 'ż' * 60})

    def test_unknown_box_is_not_labelled(self):
        self.assertFalse(lot_grid.register_box_label((0, 0, 1, 1), [0], 'Nowhere'))
        self.assertFalse(os.path.exists(self.labels_file()))

    def test_broken_labels_file_is_ignored(self):
        with open(self.labels_file(), 'w', encoding='utf-8') as stream:
            stream.write('{not json')
        self.assertEqual(lot_grid._labels(), {})


class LotDescription(unittest.TestCase):
    def test_describe_lot(self):
        del SAID[:]
        roof.describe_lot()
        self.assertEqual(
            SAID,
            [
                (
                    'Lot 10 by 8 metres. Main building in the north central part, rectangular, '
                    'footprint 7 by 3 metres. Building 2 in the south east part, roughly square, '
                    'footprint 2 by 2 metres. Pool in the south west part, approximately 2 by 2 '
                    'metres. Possible patio, porch, deck or path in the south central part, 2 '
                    'square metres; exterior flooring alone cannot distinguish its purpose. '
                    'Possible garden or landscaped plant cluster in the south central part, 1 '
                    'detected plants.'
                )
            ],
        )

    def test_room_doors(self):
        kitchen, lounge = ROOMS[0], ROOMS[1]
        self.assertEqual(
            roof._room_text(kitchen, ROOMS, Frame()),
            'Kitchen, roughly square, 4 by 3 metres. Doors: east wall to Lounge.',
        )
        self.assertEqual(
            roof._room_text(lounge, ROOMS, Frame()),
            'Lounge, roughly square, 3 by 3 metres. Doors: west wall to Kitchen.',
        )


if __name__ == '__main__':
    unittest.main(verbosity=0)

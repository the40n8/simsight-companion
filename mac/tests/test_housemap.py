"""House map: rooms, doors, garden, pool and stairs on a fake 14x10 lot.
Ground floor: kitchen (x 2-6) and living room (x 6-12), z 2-8; yard around;
a pool at x<2, z<3; upstairs a room above the living room; stairs in it."""

import tempfile
import types
import unittest

import fakegame
from fakegame import V, module

fakegame.scripts_on_path()


def room_id(x, z, level):
    if level == 1:
        return 3 if (6 <= x < 12 and 2 <= z < 8) else 0
    if level != 0:
        return 0
    if not (2 <= x < 12 and 2 <= z < 8):
        return 9  # the yard
    return 1 if x < 6 else 2


class Obj:
    is_sim = False

    def __init__(self, i, x, z, tags=(), fwd=(1, 0)):
        self.id, self.position, self.level = i, V(x, 0, z), 0
        self.tags, self.forward = set(tags), V(fwd[0], 0, fwd[1])
        self.definition = types.SimpleNamespace(name='thing')

    def is_hidden(self):
        return False

    def super_affordances(self):
        return [1]


class Sim:
    position = V(9, 0, 4)
    level = 0
    sim_info = types.SimpleNamespace(first_name='Bella')


SIM = Sim()
OBJECTS = [
    Obj(1, 3, 7, {'Func_Fridge'}),
    Obj(2, 6, 5, {'BuyCatSS_Door'}),
    Obj(3, 12, 5, {'BuyCatSS_Door'}),
    Obj(4, 9, 5),
    Obj(5, 1, 1),
    Obj(6, 13, 9),
    Obj(7, 10, 5, {'Build_Stair'}, (0, 1)),
]
LOT = types.SimpleNamespace(
    corners=[V(0, 0, 0), V(14, 0, 0), V(14, 0, 10), V(0, 0, 10)], min_level=0, max_level=1
)

module(
    'simsight_plus_core',
    hotkey=lambda vk, description: lambda fn: fn,
    MODS_FOLDER=tempfile.mkdtemp(),
    IN_GAME=False,
)
module(
    'services',
    active_lot=lambda: LOT,
    get_active_sim=lambda: SIM,
    object_manager=lambda: types.SimpleNamespace(get_valid_objects_gen=lambda: iter(OBJECTS)),
    sim_info_manager=lambda: types.SimpleNamespace(instanced_sims_gen=lambda: iter([SIM])),
)
module('build_buy', is_location_pool=lambda p, level: p.x < 2 and p.z < 3)
module('terrain', get_terrain_height=lambda x, z: 0.0)
math_mod = module('sims4.math', Vector3=V)
module('sims4', math=math_mod)
module('simsight_bridge', classify=lambda o: ('MISC', 'k', ''), object_name=lambda o: f'obj{o.id}')

import simsight_housemap as housemap  # noqa: E402
import simsight_plus_rooms as rooms  # noqa: E402

# The real lot grid and room kind scoring, on this lot's rooms and tags.
rooms.room_id_at = room_id
rooms.tag_names = lambda o: o.tags
rooms.given_name = lambda r: ''
rooms.KINDS = (('Kitchen', ('Func_Fridge',), 5),)


class HouseMapTest(unittest.TestCase):
    def setUp(self):
        self.house = housemap.build()
        self.rooms = {r['id']: r for r in self.house['rooms']}

    def links(self, room_id):
        return {link['roomId']: link for link in self.rooms[room_id]['links']}

    def test_rooms_and_kinds(self):
        self.assertEqual(self.rooms['1:0']['kind'], 'KITCHEN')
        self.assertEqual((self.rooms['1:0']['width'], self.rooms['1:0']['depth']), (4, 6))
        self.assertIn('outside:0', self.rooms)
        self.assertIn('pool:0', self.rooms)
        self.assertEqual(self.house['floors'], [0, 1])

    def test_doors(self):
        self.assertTrue(self.links('1:0')['2:0']['door'])
        self.assertTrue(self.links('2:0')['outside:0']['door'])
        self.assertTrue(self.links('outside:0')['2:0']['door'])

    def test_where_things_are(self):
        living = self.rooms['2:0']
        self.assertTrue(living['here'])
        self.assertEqual(living['sims'], ['Bella'])
        self.assertEqual([i['target']['name'] for i in self.rooms['pool:0']['items']], ['obj5'])
        self.assertEqual([i['target']['name'] for i in self.rooms['outside:0']['items']], ['obj6'])

    def test_stairs(self):
        self.assertEqual(self.links('2:0')['3:1']['stairs'], 'up')
        self.assertEqual(self.links('3:1')['2:0']['stairs'], 'down')
        self.assertEqual(
            self.house['stairs'],
            [{'floor': 0, 'part': 'E', 'bottomRoomId': '2:0', 'topRoomId': '3:1'}],
        )


if __name__ == '__main__':
    unittest.main()

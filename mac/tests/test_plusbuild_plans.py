"""House plans: room names, stacked levels and doors, detached structures."""

import types
import unittest

import fakegame

fakegame.scripts_on_path()

import simsight_detached as detached  # noqa: E402
import simsight_house_planner as planner  # noqa: E402
import simsight_house_workflow as workflow  # noqa: E402


class CleanNames(unittest.TestCase):
    def test_defaults_fill_the_gaps(self):
        self.assertEqual(
            planner._clean_names(' big  hall , ', 4, 1),
            ['big hall', 'Bedroom', 'Study', 'Bathroom'],
        )

    def test_ground_and_attic_defaults(self):
        self.assertEqual(planner._clean_names('', 2, 0), ['Living room', 'Kitchen'])
        self.assertEqual(planner._clean_names(None, 2, 2, attic=True), ['Attic room', 'Storage'])

    def test_extra_names_are_dropped(self):
        self.assertEqual(planner._clean_names('a,b,c', 2, 0), ['a', 'b'])


class StackedLevel(unittest.TestCase):
    def test_four_rooms_and_hall_doors(self):
        rooms = planner._level_rooms(['A', 'B', 'C', 'D'], 1, 10, 12, 'four')
        self.assertEqual(
            [(r['kind'], r['name'], r['uv']) for r in rooms],
            [
                ('room', 'A', (0, 0, 5, 4)),
                ('room', 'B', (5, 0, 10, 4)),
                ('hall', 'Floor 2 hallway', (0, 4, 10, 6)),
                ('room', 'C', (0, 6, 5, 12)),
                ('room', 'D', (5, 6, 10, 12)),
            ],
        )
        doors = planner._hall_doors(rooms, 1)
        self.assertEqual(
            [(d['what'], d['a'], d['b']) for d in doors],
            [
                ('Door from A to Floor 2 hallway', (2, 3), (2, 4)),
                ('Door from B to Floor 2 hallway', (7, 3), (7, 4)),
                ('Door from Floor 2 hallway to C', (2, 5), (2, 6)),
                ('Door from Floor 2 hallway to D', (7, 5), (7, 6)),
            ],
        )

    def test_two_rooms_basement(self):
        rooms = planner._level_rooms(['A', 'B'], -1, 10, 11, 'two')
        self.assertEqual([r['name'] for r in rooms], ['A', 'Basement hallway', 'B'])
        self.assertEqual(rooms[2]['uv'], (0, 6, 10, 11))


class Detached(unittest.TestCase):
    def test_exterior_door_centred_on_each_side(self):
        box = (2, 3, 6, 9)
        self.assertEqual(detached._door('north', box), ((3, 3), (3, 2)))
        self.assertEqual(detached._door('south', box), ((3, 8), (3, 9)))
        self.assertEqual(detached._door('west', box), ((2, 5), (1, 5)))
        self.assertEqual(detached._door('east', box), ((5, 5), (6, 5)))

    def test_room_names(self):
        self.assertEqual(detached._room_names('', 2, 'Garage'), ['Garage', 'Side room'])
        self.assertEqual(detached._room_names(' a  b ,, c', 2, 'G'), ['a b', 'c'])
        self.assertEqual(detached._room_names('x', 1, 'G'), ['x'])


def _plan(rooms, doors=()):
    return types.SimpleNamespace(rooms=list(rooms), doors=list(doors))


class Workflow(unittest.TestCase):
    def test_floor_names(self):
        self.assertEqual(
            [workflow._floor(v) for v in (-1, 0, 2)], ['Basement', 'Ground floor', 'Floor 3']
        )

    def test_position(self):
        plan = _plan(
            [
                {'name': 'A', 'kind': 'room', 'uv': (0, 0), 'box': (0, 0, 5, 4)},
                {'name': 'H', 'kind': 'hall', 'uv': (0, 4), 'box': (0, 4, 10, 6)},
                {'name': 'B', 'kind': 'room', 'uv': (0, 6), 'box': (5, 6, 10, 12)},
            ]
        )
        self.assertEqual(
            workflow._position(plan, plan.rooms[0]),
            'Ground floor; street-facing room; north west side; 5 by 4 metres',
        )
        self.assertEqual(
            workflow._position(plan, plan.rooms[2]),
            'Ground floor; rear room; south east side; 5 by 6 metres',
        )

    def test_door_names(self):
        rooms = [
            {'name': 'Kitchen', 'box': (0, 0, 5, 4)},
            {'name': 'Hall', 'box': (0, 4, 10, 6)},
        ]
        doors = [
            {'what': 'x', 'tile_a': (2, 3), 'tile_b': (2, 4), 'front': False},
            {'what': 'y', 'tile_a': (2, 0), 'tile_b': (2, -1), 'front': True},
            {'what': 'z', 'tile_a': (20, 0), 'tile_b': (21, 0), 'front': False},
        ]
        plan = _plan(rooms, doors)
        workflow._door_names(plan)
        self.assertEqual(
            [d['what'] for d in plan.doors],
            ['Door between Kitchen and Hall', 'Front door into Kitchen', 'z'],
        )


if __name__ == '__main__':
    unittest.main()

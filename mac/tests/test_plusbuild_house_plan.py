"""Build my house: room layout, doors and wall length on a fake 30 by 20 meter lot
(simsight_plus_house). Only the plan; building it needs the game."""

import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()


class Frame:
    width, depth, x0, z1 = 30, 20, 0.0, 20.0

    def local(self, x, z):
        return (x - self.x0, self.z1 - z)


module('simsight_plus_core')
module('simsight_plus_build', frame=Frame)

import simsight_plus_house as house  # noqa: E402 - after the stand-ins


def plan_from(side, kinds):
    house.street_side = lambda: side
    return house.make_plan(kinds)


class OneFloor(unittest.TestCase):
    def setUp(self):
        self.p = plan_from('north', ['kitchen', 'bedroom', 'bathroom', 'bedroom'])

    def test_rows(self):
        names = [r['name'] for r in self.p.rooms]
        self.assertEqual(
            names, ['Living room', 'Kitchen', 'Hallway', 'Bedroom 1', 'Bathroom', 'Bedroom 2']
        )
        self.assertEqual((self.p.width, self.p.depth), (11, 11))

    def test_boxes_centered_behind_the_front_yard(self):
        boxes = {r['name']: r['box'] for r in self.p.rooms}
        self.assertEqual(boxes['Living room'], (9, 2, 16, 7))  # takes the spare width
        self.assertEqual(boxes['Hallway'], (9, 7, 20, 9))
        self.assertEqual(boxes['Bedroom 2'], (16, 9, 20, 13))

    def test_doors(self):
        doors = {d['what']: (d['tile_a'], d['tile_b']) for d in self.p.doors}
        self.assertEqual(doors['Front door into the living room'], ((12, 2), (12, 1)))
        self.assertEqual(doors['Door from the hallway to bedroom 1'], ((11, 9), (11, 8)))
        self.assertEqual(len(doors), 6)

    def test_wall_meters(self):
        self.assertEqual(house.wall_meters(self.p), 79)

    def test_too_big(self):
        p = plan_from('north', ['living'] + ['bedroom'] * 8)
        self.assertTrue(p.problem.startswith('A house of 32 by 11 meters does not fit'))
        self.assertEqual(p.rooms, [])


class TwoFloors(unittest.TestCase):
    def test_study_variant_on_the_east_side(self):
        house.street_side = lambda: 'east'
        p = house.make_two_floor_plan('2bed')
        self.assertEqual(p.stairs['top_lot'], (22, 13))
        self.assertEqual(p.stairs['climb_lot'], (0, 1))
        upstairs = [r['name'] for r in p.rooms if r['level'] == 1]
        self.assertIn('Study', upstairs)
        self.assertEqual(house.wall_meters(p), 142)


class Walls(unittest.TestCase):
    def test_side_between(self):
        self.assertEqual(house.side_between((3, 3), (3, 2)), 'north')
        self.assertEqual(house.side_between((3, 3), (4, 3)), 'east')

    def test_wall_tiles(self):
        self.assertEqual(house._wall_tiles(5.0, 3.4), ((4, 3), (5, 3)))
        self.assertEqual(house._wall_tiles(5.5, 3.02), ((5, 2), (5, 3)))

    def test_names_numbered_only_when_repeated(self):
        self.assertEqual(
            house._names_for(['bedroom', 'kitchen', 'bedroom']),
            ['Bedroom 1', 'Kitchen', 'Bedroom 2'],
        )


if __name__ == '__main__':
    unittest.main()

"""Build cursor tiles (simsight_tiles): the lot frame's geometry, spoken
directions and reasons, and the search for the nearest tile that fits, on
a fake 6 by 8 lot."""

import math
import types
import unittest

import fakegame
from fakegame import V, module

fakegame.scripts_on_path()
LANG = ['en']
module('simsight_lang', _detect_lang=lambda: LANG[0])
module('services', current_zone_id=lambda: 1)
module('terrain', get_terrain_height=lambda x, z, routing_surface=None: 0.0)
module(
    'routing',
    SurfaceIdentifier=lambda zone, level, kind: (zone, level, kind),
    SurfaceType=types.SimpleNamespace(SURFACETYPE_WORLD='world'),
)
module('sims4').math = module(
    'sims4.math',
    Vector3=V,
    Location=lambda transform, surface: transform,
    Transform=lambda point, rotation: point,
    angle_to_yaw_quaternion=lambda angle: angle,
)
FITS = set()  # world (x, z) points where test_location_for_object passes
ASKED = []


def test_location(obj, def_id, point, ignore):
    ASKED.append((point.x, point.z))
    if (point.x, point.z) in FITS:
        return (True, None)
    return (False, 'ObjectFootprint intersects')


def broken(point, level):
    raise RuntimeError('no answer')


module(
    'build_buy',
    test_location_for_object=test_location,
    has_floor_at_location=lambda point, level: 1,
    is_location_outside=lambda point, level: None,
    is_location_pool=broken,
)

import simsight_tiles as tiles  # noqa: E402

# x runs 0..6 and z 0..8: the nearest corner to the origin is (6, 0), so
# east (i) is world +x and north (j) is world +z.
LOT = [V(0, 0, 0), V(6, 0, 0), V(0, 0, 8), V(6, 0, 8)]


class FrameGeometry(unittest.TestCase):
    def setUp(self):
        self.f = tiles.Frame(LOT)

    def test_axes_and_size(self):
        self.assertEqual((self.f.width, self.f.depth), (6, 8))
        self.assertEqual(self.f.east, (1.0, 0.0))
        self.assertEqual(self.f.north, (0.0, 1.0))

    def test_world_and_tile_of(self):
        self.assertEqual(self.f.world(2, 3), (2.5, 3.5))
        self.assertEqual(self.f.world(2, 3, 0.0, 1.0), (2.0, 4.0))
        self.assertEqual(self.f.tile_of(2.99, 3.01), (2, 3))
        self.assertEqual(self.f.tile_of(-0.1, 0), (-1, 0))

    def test_inside_and_clamp(self):
        self.assertTrue(self.f.inside(5, 7))
        self.assertFalse(self.f.inside(6, 0))
        self.assertFalse(self.f.inside(0, -1))
        self.assertEqual(self.f.clamp(-3, 20), (0, 7))

    def test_yaw_and_facing(self):
        self.assertAlmostEqual(self.f.yaw(0), 0.0)  # north: +z
        self.assertAlmostEqual(self.f.yaw(1), math.pi / 2)  # east: +x
        for facing in range(4):
            self.assertEqual(self.f.facing_of(self.f.yaw(facing) + 0.3), facing)

    def test_rotated_lot_round_trip(self):
        c, s = math.cos(0.5), math.sin(0.5)
        corners = [
            V(10 + 5 * c * a - 7 * s * b, 0, 20 + 5 * s * a + 7 * c * b)
            for a, b in ((0, 0), (1, 0), (0, 1), (1, 1))
        ]
        f = tiles.Frame(corners)
        self.assertEqual((f.width, f.depth), (5, 7))
        for i in range(f.width):
            for j in range(f.depth):
                self.assertEqual(f.tile_of(*f.world(i, j)), (i, j))

    def test_degenerate_lot(self):
        with self.assertRaises(ValueError):
            tiles.Frame([V(0, 0, 0), V(0.1, 0, 0), V(0, 0, 0.1)])
        with self.assertRaises(ValueError):
            tiles.Frame([V(0, 0, 0), V(4, 0, 0)])


class Words(unittest.TestCase):
    def tearDown(self):
        LANG[0] = 'en'

    def test_relative(self):
        self.assertEqual(tiles.relative(0, 0), 'here')
        self.assertEqual(tiles.relative(1, -2), '2 south, 1 east')
        self.assertEqual(tiles.relative(-3, 0), '3 west')
        LANG[0] = 'pl'
        self.assertEqual(tiles.relative(0, 1), '1 północ')
        self.assertEqual(tiles.relative(0, 0), 'tutaj')

    def test_reason(self):
        self.assertEqual(tiles.reason('Wall intersects'), 'a wall is in the way')
        self.assertEqual(tiles.reason('ObjectFootprint'), 'something is in the way')
        self.assertEqual(tiles.reason('NeedsFloor'), 'it needs a floor')
        self.assertEqual(tiles.reason('OutOfLotBounds'), 'it would leave the lot')
        self.assertEqual(tiles.reason(None), 'the game will not allow it here')
        LANG[0] = 'pl'
        self.assertEqual(tiles.reason('water'), 'przeszkadza woda')

    def test_location_facts_leaves_unknown_out(self):
        # floor answers 1, outside None, pool raises
        self.assertEqual(tiles.location_facts(V(0, 0, 0), 0), {'floor': True})


class Fitting(unittest.TestCase):
    def setUp(self):
        self.f = tiles.Frame(LOT)
        FITS.clear()
        del ASKED[:]

    def test_fit_at_tries_the_centre_first(self):
        FITS.add((1.5, 1.5))
        point, why = tiles.fit_at(self.f, 7, 1, 1, 0, 0)
        self.assertEqual((point.x, point.z, why), (1.5, 1.5, None))
        self.assertEqual(ASKED, [(1.5, 1.5)])

    def test_fit_at_gives_the_first_reason(self):
        point, why = tiles.fit_at(self.f, 7, 1, 1, 0, 0)
        self.assertIsNone(point)
        self.assertEqual(why, 'something is in the way')
        self.assertEqual(len(ASKED), len(tiles.OFFSETS))

    def test_nearest_fit_nearest_ring_and_facing_first(self):
        FITS.add((3.5, 3.5))  # tile (3, 3): two rings away from (1, 1)
        FITS.add((1.5, 2.5))  # tile (1, 2): one ring away
        ni, nj, point, facing = tiles.nearest_fit(self.f, 7, 1, 1, 0, 2)
        self.assertEqual((ni, nj, facing), (1, 2, 2))
        self.assertEqual((point.x, point.z), (1.5, 2.5))

    def test_nearest_fit_none(self):
        self.assertIsNone(tiles.nearest_fit(self.f, 7, 0, 0, 0, 0))

    def test_ring_is_sorted_and_on_the_lot(self):
        ring = tiles._ring(self.f, 0, 0, 1)
        self.assertEqual(ring, [(1, 0, 1), (1, 1, 0), (2, 1, 1)])


if __name__ == '__main__':
    unittest.main(verbosity=0)

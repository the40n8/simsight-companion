"""Build helper layout logic: the lot frame, free spots for a new room, how a
room joins the house, a room's walls, and the catalog replies from the
interface file. A fake 8x6 lot; nothing of the game runs."""

import types
import unittest

import fakegame
from fakegame import V, module

fakegame.scripts_on_path()
module('simsight_speech', speak=lambda *a, **k: None)
module('sims4').commands = module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)

import simsight_plus_build as build  # noqa: E402
from simsight_plus_build import catalog, lot, rooms  # noqa: E402
import simsight_plus_core as core  # noqa: E402

# x 10..18 west to east, z 20..26 south to north: 8 by 6 meters
LOT = types.SimpleNamespace(corners=[V(10, 0, 20), V(18, 0, 20), V(18, 0, 26), V(10, 0, 26)])


def grid_with_house():
    """6 rows (north to south) of 8 tiles; a 2x2 room (id 1) at e 1-2, n 1-2."""
    grid = [['free'] * 8 for _ in range(6)]
    for n in (1, 2):
        for e in (1, 2):
            grid[n][e] = 1
    return grid


class FrameTest(unittest.TestCase):
    def test_size_and_round_trip(self):
        f = build.Frame(LOT)
        self.assertEqual((f.width, f.depth), (8, 6))
        self.assertEqual(f.world(0, 0), (10, 26))  # north west corner
        self.assertEqual(f.world(8, 6), (18, 20))
        self.assertEqual(f.local(*f.world(2.5, 3.5)), (2.5, 3.5))


class FreeSpotsTest(unittest.TestCase):
    def test_spot_touching_the_house_comes_first(self):
        spots = build.free_spots(grid_with_house(), 2, 2)
        self.assertTrue(spots)
        e, n = spots[0]
        text = lot._touch_text(grid_with_house(), e, n, 2, 2)
        self.assertTrue(text.startswith('joins the house on its'), text)

    def test_spots_stay_inside_the_edge_and_free(self):
        grid = grid_with_house()
        for e, n in build.free_spots(grid, 2, 2, limit=20):
            self.assertGreaterEqual(e, 1)
            self.assertGreaterEqual(n, 1)
            self.assertLess(e + 2, 8)
            self.assertLess(n + 2, 6)
            cells = [grid[n + j][e + i] for j in range(2) for i in range(2)]
            self.assertEqual(set(cells), {'free'})

    def test_nothing_fits(self):
        self.assertEqual(build.free_spots(grid_with_house(), 7, 7), [])

    def test_touch_text(self):
        grid = grid_with_house()
        self.assertEqual(lot._touch_text(grid, 3, 1, 2, 2), 'joins the house on its west side')
        self.assertEqual(lot._touch_text(grid, 5, 3, 2, 2), 'stands on its own')


class RoomWallsTest(unittest.TestCase):
    def setUp(self):
        # Patched where room_box, room_walls and wall_cell_point look it up.
        self._frame = rooms.frame
        rooms.frame = lambda: build.Frame(LOT)

    def tearDown(self):
        rooms.frame = self._frame

    def test_walls_of_a_two_by_one_room(self):
        f = build.Frame(LOT)
        tiles = [f.world(1.5, 1.5), f.world(2.5, 1.5)]
        room = types.SimpleNamespace(tiles=tiles)
        self.assertEqual(build.room_box(room), (1, 1, 3, 2))
        walls = {w['side']: w['cells'] for w in build.room_walls(room)}
        self.assertEqual(walls['north'], [(1, 1), (2, 1)])  # left to right from inside
        self.assertEqual(walls['south'], [(2, 1), (1, 1)])
        self.assertEqual(walls['west'], [(1, 1)])
        self.assertEqual(walls['east'], [(2, 1)])
        self.assertEqual(build.wall_cell_point('north', (1, 1)), f.world(1.5, 1))
        self.assertEqual(build.wall_cell_point('east', (2, 1)), f.world(3, 1.5))


class FlashReplyTest(unittest.TestCase):
    def setUp(self):
        self.posted = []
        self._post = core.post
        core.post = lambda fn, *args: self.posted.append(fn)
        build.catalog_state.update(serial=5, items=[], quiet=None)

    def tearDown(self):
        core.post = self._post

    def test_catalog_items_then_end(self):
        build.flash_reply_base('cat|5|0|2|Oak%20door~350~3^Plain~12.0~')
        self.assertEqual(build.catalog_state['total'], 2)
        self.assertEqual(build.catalog_state['items'], [('Oak door', 350, 3), ('Plain', 12, 1)])
        build.flash_reply_base('catend|5')
        self.assertEqual(self.posted, [catalog._show_catalog])

    def test_old_serial_is_ignored(self):
        build.flash_reply_base('cat|4|0|1|Old~1~1')
        self.assertEqual(build.catalog_state['items'], [])

    def test_build_error_is_kept(self):
        build.flash_reply_base('builderror|Not%20enough%20room')
        self.assertEqual(build.last_error['text'], 'Not enough room')


if __name__ == '__main__':
    unittest.main()

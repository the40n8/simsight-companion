"""SimSight Plus drawing cursor and furnishing: what the cursor says, shape checks,
roof handle math and the screen blob finder; furnishing spots by walls and doors."""

import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

SAID = []
module(
    'simsight_plus_core',
    speak=lambda text, interrupt=True: SAID.append(text),
    post=lambda fn, *a: fn(*a),
    hotkey=lambda vk, desc: lambda fn: fn,
    log=lambda m: None,
    log_exc=lambda where: None,
)
module('simsight_plus_build', busy=[False], last_error={'text': '', 'at': 0.0})

import simsight_plus_draw as draw  # noqa: E402
import simsight_plus_furnish as furnish  # noqa: E402

# 6 x 4 lot: room 1 at e 1-3, room 2 at e 3-5, both n 1-3; the rest is outside (0).
GRID = [[1 if 1 <= e < 3 else 2 if 3 <= e < 5 else 0 for e in range(6)] for n in range(4)]
for _e in range(6):
    GRID[0][_e] = 0


class DrawCursorTest(unittest.TestCase):
    def setUp(self):
        draw.draw_state.update(
            grid=[row[:] for row in GRID],
            names={1: 'kitchen', 2: 'hall'},
            start=None,
            tool='wall',
            style=None,
            e=1,
            n=1,
        )

    def test_wall_dirs_at_room_corner(self):
        self.assertEqual(draw._wall_dirs(1, 1), ['east', 'south'])
        self.assertEqual(draw._wall_dirs(3, 2), ['north', 'south'])

    def test_area_text(self):
        self.assertEqual(draw._area_text(2, 2), 'inside the kitchen')
        self.assertEqual(draw._area_text(3, 2), 'between kitchen and hall')
        self.assertEqual(draw._area_text(0, 0), 'outside')

    def test_outside_the_grid_is_outside(self):
        self.assertEqual(draw._tile(-1, 0), 0)
        self.assertEqual(draw._tile(9, 9), 0)

    def test_from_start(self):
        self.assertEqual(draw._from_start(2, 2), '')
        draw.draw_state['start'] = (1, 1)
        self.assertEqual(draw._from_start(1, 1), 'At the start point.')
        self.assertEqual(draw._from_start(3, 0), 'From the start: 2 east and 1 north.')

    def test_where(self):
        draw.draw_state.update(e=2, n=2)
        self.assertEqual(draw._where(), '2 from west, 2 from north. Inside the kitchen.')
        draw.draw_state.update(e=1, n=1, tool='roof', style=(4, '', 'Gable'))
        self.assertEqual(
            draw._where(full=True),
            '1 from west, 1 from north. Between outside and kitchen.'
            ' On a wall going east, south. Tool: Roof, Gable.',
        )
        self.assertIn(' On a wall.', draw._where())

    def test_shape_problem(self):
        self.assertIn('on the start point', draw._shape_problem('wall', (1, 1), 1, 1))
        self.assertIn('goes straight', draw._shape_problem('wall', (1, 1), 2, 3))
        self.assertIsNone(draw._shape_problem('wall', (1, 1), 1, 3))
        self.assertIn('opposite corners', draw._shape_problem('room', (1, 1), 1, 3))
        self.assertIsNone(draw._shape_problem('roof', (1, 1), 2, 3))

    def test_wants_shift_marker(self):
        self.assertTrue(getattr(draw._west, '_wants_shift', False))
        self.assertFalse(getattr(draw._say_where, '_wants_shift', False))


class RoofTest(unittest.TestCase):
    def test_handle_points(self):
        r = [0, 0, 4, 2]
        self.assertEqual(draw._handle_point('south', r), (2.0, 2 + draw.HANDLE_OUT))
        self.assertEqual(draw._handle_point('west', r), (-draw.HANDLE_OUT, 1.0))

    def test_results(self):
        box = (0, 0, 5, 3)
        self.assertEqual(draw._roof_result(True, box), 'The roof is on, 5 by 3 meters.')
        self.assertIn('could not grab', draw._roof_result('nohandle', box))
        self.assertEqual(draw._roof_result('noscreen', box), 'I could not get that spot on screen.')

    def test_handle_center_finds_white_blob(self):
        radius = 10
        w = 2 * radius
        data = bytearray(w * w * 4)
        for y in range(12, 15):
            for x in range(4, 7):
                i = (y * w + x) * 4
                data[i], data[i + 1] = 255, 255
        self.assertEqual(draw._handle_center(bytes(data), 100, 200, radius), (105.0, 213.0))

    def test_handle_center_needs_enough_pixels(self):
        data = bytearray(20 * 20 * 4)
        data[0], data[1] = 255, 255
        self.assertIsNone(draw._handle_center(bytes(data), 0, 0, 10))


class FurnishTest(unittest.TestCase):
    def test_guess_set(self):
        self.assertEqual(furnish._guess_set('Main Bathroom'), 4)
        self.assertEqual(furnish._guess_set('Kitchen'), 5)
        self.assertIsNone(furnish._guess_set('Studio'))

    def test_door_tiles(self):
        tiles = furnish._door_tiles([(2.0, 1.0, 'door'), (5.0, 5.0, 'window')])
        self.assertEqual(tiles, {(1, 0), (1, 1), (2, 0), (2, 1)})

    def test_wall_faces(self):
        box = (0, 0, 3, 3)
        self.assertEqual(furnish._wall_faces((0, 0), box), [(0, 1), (1, 0)])
        self.assertEqual(furnish._wall_faces((1, 1), box), [])
        self.assertEqual(furnish._wall_faces((2, 2), box), [(0, -1), (-1, 0)])

    def test_spot_cursor_goes_round(self):
        cursor = furnish._SpotCursor([(1, 1, 0), (2, 2, 0)], (9, 9, 0))
        self.assertEqual([cursor.next_spot() for _ in range(3)], [(1, 1, 0), (2, 2, 0), (1, 1, 0)])
        empty = furnish._SpotCursor([], (9, 9, 0))
        self.assertEqual(empty.next_spot(), (9, 9, 0))

    def test_furnish_text(self):
        room = types.SimpleNamespace(name='hall')
        self.assertEqual(
            furnish._furnish_text(room, [], ['floor lamp'], 0),
            'Done furnishing the hall. Bought: nothing. It cost 0 simoleons.'
            ' Could not place: floor lamp. The room may be too full.',
        )


if __name__ == '__main__':
    unittest.main()

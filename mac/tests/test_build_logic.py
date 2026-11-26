"""Build/Buy logic that needs no game: the Build cursor's request and reply
protocol, basement footprints, chair facing, native floor replies and
placement requirements."""

import os
import tempfile
import threading
import types
import unittest

import fakegame
from fakegame import V, module

fakegame.scripts_on_path()

SPOKEN, UI = [], []
SETTINGS = {'build_controls': True}
ZONE = [types.SimpleNamespace(id=7, is_in_build_buy=True)]
FLAGS = {1: 0, 2: 1024, 3: 2, 4: 1024 | 2, 5: 65536}

sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands',
    Command=lambda *names, **kw: lambda func: func,
    CommandType=types.SimpleNamespace(Live='live'),
)
module(
    'build_buy',
    register_build_buy_exit_callback=id,
    register_build_buy_enter_callback=id,
    get_object_placement_flags=lambda def_id: FLAGS[def_id],
)
module('simsight_speech', speak=lambda text, **kw: SPOKEN.append(text))
module('simsight_lang', _detect_lang=lambda: 'en', t=lambda text: text)
module('simsight_settings', get=lambda key, default=None: SETTINGS.get(key, default))
module('services', current_zone=lambda: ZONE[0], current_zone_id=lambda: 7)
distributor = module('distributor')
distributor.ops = module('distributor.ops', SendUIMessage=lambda message: message)
distributor.system = module(
    'distributor.system',
    Distributor=types.SimpleNamespace(
        instance=lambda: types.SimpleNamespace(add_op_with_no_owner=UI.append)
    ),
)
module(
    'simsight_grid',
    active=lambda: False,
    leave=lambda: None,
    retire=lambda: None,
    cursor=lambda: (V(1.5, 0.0, 2.5), 0, 7),
)


def tag(value):
    if value != 1:
        raise ValueError(value)  # as EA's enum does for a number it does not name
    return types.SimpleNamespace(name='BuyCatSS_DiningChair')


module('tag', Tag=tag)

import simsight_log  # noqa: E402

simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

import simsight_basement as basement  # noqa: E402
import simsight_build as build  # noqa: E402
import simsight_build_helpers as helpers  # noqa: E402
import simsight_chair_alignment as alignment  # noqa: E402
import simsight_chair_snap as chair_snap  # noqa: E402
import simsight_place as place  # noqa: E402


class BuildCursor(unittest.TestCase):
    def setUp(self):
        SPOKEN.clear()
        UI.clear()

    def test_on_reply_and_stale_reply(self):
        self.assertTrue(build.request('on'))
        serial = build._serial
        self.assertEqual(UI[-1], f'A11y|build|on|{serial}')
        self.assertEqual(build._pending[serial], 7)
        self.assertTrue(build.state(f'{serial}|1|on|100|200|16'))
        self.assertTrue(build.active())
        self.assertEqual(
            SPOKEN[-1],
            'Build cursor on. Screen pixels, not lot tiles. Screen x 100, y 200. Step 16 pixels.',
        )
        self.assertFalse(build.state(f'{serial}|1|on|100|200|16'))  # answered already
        for bad in ('x|1', '1|2|on|0|0|16', '0|1|off|0|0|4', f'{serial}|1|on|0|0|5'):
            self.assertFalse(build.state(bad))

    def test_feedback_follows_status(self):
        build.request('on')
        build.state(f'{build._serial}|1|on|1|1|4')
        self.assertTrue(build.info('name|Dining%20chair'))
        self.assertEqual(SPOKEN[-1], 'Dining chair')
        self.assertTrue(build.request('status'))
        build.state(f'{build._serial}|1|status|3|4|64')
        self.assertEqual(SPOKEN[-1], 'Screen x 3, y 4. Step 64 pixels. Dining chair')

    def test_retirement_and_disabled(self):
        build.request('on')
        self.assertTrue(build.state('0|0|off|0|0|4'))
        self.assertFalse(build.active())
        self.assertEqual(SPOKEN[-1], 'Build cursor off.')
        SETTINGS['build_controls'] = False
        try:
            self.assertFalse(build.request('left'))
            self.assertEqual(
                SPOKEN[-1], 'Build controls are off. Turn them on in the settings menu.'
            )
            self.assertTrue(build.request('off'))  # a release is never blocked
        finally:
            SETTINGS['build_controls'] = True

    def test_cursor_off_refuses_moves(self):
        build.state('0|0|off|0|0|4')
        self.assertFalse(build.request('left'))
        self.assertEqual(SPOKEN[-1], 'Build cursor is off.')
        self.assertFalse(build.request('fly'))
        self.assertTrue(build.request_menu('rotateleft'))
        self.assertEqual(UI[-1], f'A11y|build|menu:rotateleft|{build._serial}')


class Basement(unittest.TestCase):
    frame = types.SimpleNamespace(width=20, depth=15)

    def test_bounds(self):
        self.assertEqual(basement._bounds((1, 1, 19, 14), self.frame), (1, 1, 19, 14))
        self.assertEqual(basement._bounds([2.0, 3.0, 6.0, 8.0], self.frame), (2, 3, 6, 8))
        for bad in (
            (0, 1, 5, 5),
            (1, 1, 20, 5),
            (1, 1, 5),
            (1.5, 1, 5, 5),
            (True, 1, 5, 5),
            (5, 1, 2, 5),
        ):
            with self.subTest(want=bad), self.assertRaises(ValueError):
                basement._bounds(bad, self.frame)

    def test_overlap_and_result(self):
        self.assertTrue(basement._overlap((1, 1, 5, 5), (4, 4, 8, 8)))
        self.assertFalse(basement._overlap((1, 1, 5, 5), (5, 1, 8, 5)))  # touching edges
        before = {((1, 1, 3, 3), 0)}
        wanted = (2, 2, 6, 6)
        self.assertTrue(basement._check_result(before, before | {(wanted, -1)}, wanted))
        self.assertFalse(basement._check_result(before, before | {(wanted, 0)}, wanted))
        self.assertFalse(basement._check_result(before, {(wanted, -1)}, wanted))  # a room lost
        extra = before | {(wanted, -1), ((7, 7, 8, 8), -1)}
        self.assertFalse(basement._check_result(before, extra, wanted))


class Chairs(unittest.TestCase):
    def test_side_of(self):
        self.assertEqual(
            [
                chair_snap.side_of(*d)
                for d in ((0, 1), (1, 0), (0, -1), (-1, 0), (2, 1), (1, 1), (-1, -1))
            ],
            [0, 1, 2, 3, 1, 0, 2],
        )
        self.assertIsNone(alignment._direction(3, 3, (3, 3)))
        self.assertEqual(alignment._direction(3, 3, (5, 4)), 1)

    def test_relation(self):
        self.assertEqual(alignment._relation(1, 1)[0], 'toward')
        self.assertEqual(alignment._relation(3, 1)[0], 'away')
        self.assertEqual(alignment._relation(0, 1)[0], 'side-on')
        self.assertEqual(alignment._relation(2, 1)[0], 'side-on')
        self.assertEqual(alignment._relation(0, None)[0], 'same tile')

    def test_tag_names(self):
        definition = types.SimpleNamespace(get_tags=lambda: (1, 99))
        self.assertEqual(chair_snap.tag_names(definition), {'buycatssdiningchair'})
        self.assertEqual(chair_snap.tag_names(None), set())


class NativeFloor(unittest.TestCase):
    def setUp(self):
        helpers._runtime.floor_sequence = -1
        self.job = {'zone': 7, 'done': threading.Event()}
        helpers._runtime.floor_job = self.job

    def tearDown(self):
        helpers._runtime.floor_job = None

    def test_reply(self):
        helpers._receive_floor('7', 3, 'ok', 1, -1, 2)
        self.assertEqual(self.job['result'], {'floor': 1, 'minimum': -1, 'maximum': 2})
        self.assertTrue(self.job['done'].is_set())

    def test_refused_replies(self):
        helpers._receive_floor('8', 3, 'ok', 1, -1, 2)  # another zone
        helpers._receive_floor('7', True, 'ok', 1, -1, 2)  # not a sequence number
        self.assertFalse(self.job['done'].is_set())
        helpers._receive_floor('7', 4, 'ok', 3, -1, 2)  # outside its own bounds
        self.assertEqual(self.job['error'], 'Invalid native floor bounds')
        helpers._runtime.floor_job = job = {'zone': 7, 'done': threading.Event()}
        helpers._receive_floor('7', 4, 'ok', 1, -1, 2)  # a late repeat of sequence 4
        self.assertFalse(job['done'].is_set())
        helpers._receive_floor('7', 5, 'busy', 0, 0, 0)
        self.assertEqual(job['error'], 'Native floor query: busy')

    def test_whole_level(self):
        self.assertEqual([helpers._whole_level(v) for v in (2, -1.0, 0)], [2, -1, 0])
        for bad in (1.5, True, 'x', float('inf'), float('nan'), None):
            self.assertIsNone(helpers._whole_level(bad))


class Requirements(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(
            [place.requirement(i) for i in (1, 2, 3, 4, 5)],
            [None, 'slot', 'wall', 'slot', 'terrain'],
        )

    def test_tile_key(self):
        self.assertEqual(place.tile_key(V(2.9, 0, -0.1), 1.0), (2, -1, 1))

    def test_lot_area(self):
        centers = [V(0, 0, 0), V(10, 0, 0)]
        rooms = {(1, 1): 5}
        area = place.LotArea(1, centers, [], lambda p: rooms.get((p.x, p.z)), None)
        self.assertTrue(area.contains(V(8, 0, 0)))  # nearest the second centre
        self.assertFalse(area.contains(V(2, 0, 0)))
        self.assertFalse(area.contains(V(1, 0, 1)))  # inside a room
        room = place.LotArea(None, centers, [], lambda p: rooms.get((p.x, p.z)), 5)
        self.assertTrue(room.contains(V(1, 0, 1)))
        self.assertFalse(room.contains(V(8, 0, 0)))


if __name__ == '__main__':
    unittest.main()

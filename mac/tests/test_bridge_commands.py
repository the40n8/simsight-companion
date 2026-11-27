"""The companion's own logic, offline: game text placeholders, readable
tuning names, which command runs when, and the result sent back."""

import json
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()
import simsight_log  # noqa: E402

simsight_log.PATH = '/dev/null'  # failures under test are not written next to the sources

SPOKEN = []
module(
    'simsight_speech',
    speak=lambda text, **kw: SPOKEN.append((text, kw)),
    log_line=lambda *a: None,
    MODS_FOLDER='/nonexistent',
    mods_root=lambda: '/nonexistent',
)
module('sims4')
module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('sims4.core_services', on_tick=lambda *a, **k: None)
module('simsight_scanner', loc_cache={5: 'Fridge'})
GAME = {'zone': None}
module(
    'services',
    current_zone=lambda: GAME['zone'],
    active_household=object,
)

import simsight_bridge as bridge  # noqa: E402


def token(**kw):
    return types.SimpleNamespace(**kw)


class Tokens(unittest.TestCase):
    def test_names_and_gender(self):
        bella = token(first_name='Bella', last_name='Goth', is_female=True)
        self.assertEqual(
            bridge.text._fill_tokens('{0.SimFirstName} wants {M0.his}{F0.her} tea', [bella]),
            'Bella wants her tea',
        )
        self.assertEqual(bridge.text._fill_tokens('{0.SimFullName}', [bella]), 'Bella Goth')

    def test_numbers_money_and_plural(self):
        self.assertEqual(bridge.text._fill_tokens('{0.Money}', [token(number=12.0)]), '§12')
        self.assertEqual(bridge.text._fill_tokens('{0.Number}', [token(number=1.5)]), '1.5')
        self.assertEqual(bridge.text._fill_tokens('One {S.Cereal}{P.Bowls}', []), 'One Cereal')

    def test_missing_token_and_unknown_placeholder(self):
        self.assertEqual(bridge.text._fill_tokens('Hi {0.SimFirstName} {X}', []), 'Hi')

    def test_object_name_from_the_game_text(self):
        obj = token(
            has_custom_name=lambda: False,
            definition=token(name='fridge_Cheap'),
            catalog_name=5,
        )
        self.assertEqual(bridge.object_name(obj), 'Fridge')
        obj.catalog_name = 6  # not known yet: the tuning name, and asked of the UI
        self.assertEqual(bridge.object_name(obj), 'Fridge Cheap')
        self.assertIn(6, bridge._state.loc_wanted)


class Pretty(unittest.TestCase):
    def test_tuning_names(self):
        self.assertEqual(
            bridge.text._pretty('Buff_Energized_Coffee', ('buff_',)), 'Energized Coffee'
        )
        self.assertEqual(bridge.text._pretty('si_GoToBed', ('si_',)), 'Go To Bed')

    def test_need_levels(self):
        levels = [bridge.sim_status._need_level(p) for p in (10, 20, 50, 90)]
        self.assertEqual(
            levels,
            ['NEED_LEVEL_CRITICAL', 'NEED_LEVEL_LOW', 'NEED_LEVEL_OKAY', 'NEED_LEVEL_GOOD'],
        )


class Commands(unittest.TestCase):
    def setUp(self):
        SPOKEN.clear()
        GAME['zone'] = None

    def test_say_works_outside_a_game(self):
        self.assertEqual(
            bridge.commands._run({'say': {'text': 'Paired', 'interrupt': True}}), (True, '')
        )
        self.assertEqual(SPOKEN[0][0], 'Paired')
        self.assertTrue(SPOKEN[0][1]['interrupt'])

    def test_game_commands_need_a_game(self):
        self.assertEqual(bridge.commands._run({'listTargets': {}}), (False, 'not_in_game'))
        self.assertEqual(bridge.commands._run({'nothing': {}}), (False, 'not_in_game'))

    def test_in_game(self):
        GAME['zone'] = token(is_zone_running=True)
        self.assertEqual(bridge.commands._run({'nothing': {}}), (False, 'unknown_command'))
        self.assertEqual(bridge.commands._run({'setSpeed': {'speed': 'SPEED_X'}}), (False, 'error'))
        self.assertEqual(
            bridge.commands._run({'openMenu': {'menu': 'x'}}), (False, 'unknown_command')
        )

    def test_result_message(self):
        sent = []
        old = bridge.commands.send
        try:
            bridge.commands.send = sent.append
            bridge.commands._handle(json.dumps({'id': '4', 'say': {'text': ''}}).encode())
            bridge.commands._handle(json.dumps({'id': '5', 'listTargets': {}}).encode())
            bridge.commands._handle(b'not json')
        finally:
            bridge.commands.send = old
        self.assertEqual(
            sent,
            [
                {'result': {'id': '4', 'outcome': {'ok': True}}},
                {'result': {'id': '5', 'outcome': {'reason': 'not_in_game'}}},
            ],
        )
        self.assertEqual(bridge._state.last_state_time, 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=0)

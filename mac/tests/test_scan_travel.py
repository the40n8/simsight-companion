"""Travel list logic that needs no game: command argument types, prices,
buildings and worlds, client price replies, keyboard entry requests."""

import os
import tempfile
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

sent = []
sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('simsight_lang', t=lambda term: term)
module('simsight_menu', _raw=lambda s: s, _join=lambda *s: s, _cancel_dialog=lambda i: None)
module('simsight_pump', post=lambda fn, *a: True)
module('simsight_settings', get=lambda name, fallback=None: fallback)
module('simsight_speech', speak=lambda *a, **k: None)


class _Picker:
    def distribute_dialog(self, msg_type, dialog_msg, **kwargs):
        return 'native'


module('ui')


class _SimPicker(_Picker):
    pass


module('ui.ui_dialog_picker', UiMapViewPicker=_Picker, UiSimPicker=_SimPicker)

import simsight_travel as travel  # noqa: E402

travel._ui = lambda message: sent.append(message) or True


def place(zone, master=None, world=1, name='Lot'):
    p = travel._Place(zone, name)
    p.master = zone if master is None else master
    p.world_key = world
    p.world_name = f'World {world}'
    return p


class CommandTypes(unittest.TestCase):
    def test_parser_gets_real_types(self):
        # The game converts each argument with its annotation: it must be str.
        self.assertEqual(travel.a11y_travel.__annotations__, {'lease': str, 'request': str})
        self.assertEqual(travel.a11y_lotprice_enc.__annotations__, {'payload': str})
        self.assertEqual(travel.a11y_travel_probe.__annotations__, {})


class Prices(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(travel._positive('12.5'), 12.5)
        for bad in (0, -3, 'x', None, float('nan'), float('inf'), float('-inf')):
            self.assertIsNone(travel._positive(bad), bad)

    def test_client_price_only_for_its_request(self):
        pending = travel._Pending(None, None, None, {}, travel.VACATION)
        pending.generation = 4
        travel._state.pending = pending
        try:
            self.assertFalse(travel.a11y_lotprice_enc('7|100|3'))
            self.assertTrue(travel.a11y_lotprice_enc('7|100.9|4'))
            self.assertEqual(pending.prices, {7: 100})
            self.assertFalse(travel.a11y_lotprice_enc('7|0|4'))
            self.assertEqual(pending.prices, {})
            self.assertFalse(travel.a11y_lotprice_enc('bad'))
        finally:
            travel._state.pending = None


class Buildings(unittest.TestCase):
    def test_units_grouped_under_offered_master(self):
        places = [place(1, name='Tower'), place(2, master=1), place(3, master=1), place(9)]
        top = travel._buildings(places)
        self.assertEqual([p.zone_id for p in top], [1, 9])
        self.assertEqual([p.zone_id for p in top[0].units], [1, 2, 3])
        self.assertIsNot(top[0].units[0], top[0])
        self.assertEqual(top[1].units, [])
        # The collected places are never changed.
        self.assertEqual(places[0].units, [])

    def test_building_without_offered_master(self):
        travel._zone_name = lambda zone: ''
        top = travel._buildings([place(2, master=5), place(3, master=5)])
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0].zone_id, 5)
        self.assertEqual(top[0].name, 'World 1')
        self.assertEqual([p.zone_id for p in top[0].units], [2, 3])

    def test_worlds_sorted_and_counted(self):
        places = [place(1, world=2), place(2, world=1), place(3, world=2)]
        places[1].world_name = 'alpha'
        self.assertEqual(travel._worlds(places), [(1, 'alpha', 1, None), (2, 'World 2', 2, None)])

    def test_label_family_first_for_occupied_homes(self):
        p = place(1, name='Lot')
        p.household = 'Goth'
        self.assertEqual(p.label(), 'Lot')
        p.residential = True
        self.assertEqual(p.label(), 'Goth, Lot')


class Requests(unittest.TestCase):
    def setUp(self):
        del sent[:]
        del travel._state.requests[:]
        travel._state.epoch = 3
        travel._state.lease_running = True
        travel._zone_loaded = lambda: True

    def test_cancel_before_request_refuses_it(self):
        self.assertTrue(travel.a11y_travel_cancel('5', '3'))
        self.assertFalse(travel.a11y_travel('3', '5'))
        self.assertEqual(
            sent,
            ['A11y|travelresult|3|5|refused|cancelled', 'A11y|travelresult|3|5|refused|cancelled'],
        )

    def test_stale_epoch_refused_and_answered_again(self):
        self.assertFalse(travel.a11y_travel('2', '8'))
        self.assertFalse(travel.a11y_travel('2', '8'))
        self.assertEqual(sent, ['A11y|travelresult|2|8|refused|stale'] * 2)

    def test_queued_request_waits_then_lease_invalidates(self):
        self.assertTrue(travel.a11y_travel('3', '9'))
        self.assertFalse(travel.a11y_travel('3', '9'))  # still pending: no answer
        self.assertEqual(sent, [])
        travel.stop_lease()
        self.assertEqual(sent, ['A11y|travelresult|3|9|refused|stale', 'A11y|travelready|0'])

    def test_bad_numbers(self):
        self.assertFalse(travel.a11y_travel('x', '1'))
        self.assertEqual(sent, ['A11y|travelresult|0|0|refused|error'])


class Hooks(unittest.TestCase):
    def test_map_hook_failure_goes_native(self):
        original = travel._distribute

        def broken(*a, **k):
            raise RuntimeError('boom')

        travel._distribute = broken
        try:
            self.assertEqual(_Picker().distribute_dialog(1, 2), 'native')
        finally:
            travel._distribute = original


if __name__ == '__main__':
    unittest.main(verbosity=0)

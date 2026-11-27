"""The social group's wrappers on EA's functions: each passes the game's
arguments on unchanged (positional or keyword) and reads its own from them."""

import os
import tempfile
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('simsight_lang', t=lambda term: term)
module(
    'simsight_menu', raw_text=lambda s: s, join_localized=lambda *s: s, cancel_dialog=lambda i: None
)
module('simsight_pump', post=lambda fn, *a: True)
module('simsight_settings', get=lambda name, fallback=None: fallback)
module('simsight_speech', speak=lambda *a, **k: None)

CALLS = []


class _Picker:
    def distribute_dialog(self, dialog_type, dialog_msg, immediate=False):
        CALLS.append(('native', dialog_type, dialog_msg, immediate))
        return 'native'


class _SimPicker(_Picker):
    pass


class _Notification:
    def distribute_dialog(self, dialog_type, dialog_msg, **kwargs):
        CALLS.append(('note', dialog_type, dialog_msg, kwargs))
        return 'note'


class _Dialog:
    def build_msg(self, *args, **kwargs):
        return types.SimpleNamespace(dialog_id=5)


class _Tracker:
    def activate_holiday(self, holiday_id, from_load=False):
        CALLS.append(('activate', holiday_id, from_load))
        return 'activated'


class _Zone:
    def on_loading_screen_animation_finished(self):
        CALLS.append(('loaded',))


module('ui')
module('ui.ui_dialog_picker', UiMapViewPicker=_Picker, UiSimPicker=_SimPicker)
module('ui.ui_dialog', UiDialog=_Dialog)
module('ui.ui_dialog_notification', UiDialogNotification=_Notification)
module('holidays')
module('holidays.holiday_tracker', HolidayTracker=_Tracker)
module('zone', Zone=_Zone)

import simsight_dialogs as dialogs  # noqa: E402
import simsight_holidays as holidays  # noqa: E402
from simsight_travel import hooks  # noqa: E402


class TravelHooks(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        hooks.install()

    def test_positional_extra_goes_to_ea_unchanged(self):
        # EA passes immediate as a keyword today; a positional one is handed on as is.
        self.assertEqual(_SimPicker().distribute_dialog('t', 'm', True), 'native')
        self.assertEqual(CALLS, [('native', 't', 'm', True)])
        self.assertEqual(_Picker().distribute_dialog('t', 'm', True), 'native')
        self.assertEqual(CALLS[-1], ('native', 't', 'm', True))


class DialogHooks(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        self.notes = []
        dialogs._note = self.notes.append
        dialogs.install()

    def test_notification_message_read_by_name_or_position(self):
        _Notification().distribute_dialog('t', 'first')
        _Notification().distribute_dialog(dialog_type='t', dialog_msg='second', extra=1)
        self.assertEqual(self.notes, ['first', 'second'])
        self.assertEqual(CALLS[-1], ('note', 't', 'second', {'extra': 1}))

    def test_built_message_kept(self):
        dialogs._msgs.clear()
        _Dialog().build_msg(1, text=2)
        self.assertEqual(list(dialogs._msgs), [5])


class HolidayHooks(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        self.seen = []
        holidays._on_activate = lambda tracker, holiday_id, args, kwargs: self.seen.append(
            (holiday_id, args, kwargs)
        )

    def test_activate_arguments_passed_on(self):
        tracker = _Tracker()
        self.assertEqual(tracker.activate_holiday(7, True), 'activated')
        self.assertEqual(tracker.activate_holiday(8, from_load=True), 'activated')
        self.assertEqual(CALLS, [('activate', 7, True), ('activate', 8, True)])
        self.assertEqual(self.seen, [(7, (True,), {}), (8, (), {'from_load': True})])


if __name__ == '__main__':
    unittest.main(verbosity=0)

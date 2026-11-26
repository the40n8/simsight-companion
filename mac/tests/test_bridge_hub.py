"""The accessibility menu's own logic, checked offline: the action queue
descriptions, who counts as an event guest, ending an event, and the
phone list grouped like EA's phone menu."""

import os
import sys
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()
import simsight_log  # noqa: E402

simsight_log.PATH = os.devnull  # nothing written next to the sources

SPOKEN = []
module(
    'sims4',
    commands=module(
        'sims4.commands',
        Command=lambda *a, **k: lambda f: f,
        CommandType=types.SimpleNamespace(Live=1),
    ),
)
module('simsight_lang', t=lambda term: term)
module('simsight_menu', _raw=lambda text: text)
module('simsight_pump', post=lambda fn, *args: True)
module('simsight_settings', get=lambda key, default=None: default)
module('simsight_speech', speak=lambda text, **kw: SPOKEN.append(text))
HOUSEHOLD = types.SimpleNamespace(id=1)
module('services', active_household=lambda: HOUSEHOLD)


class UiDialogService:
    def dialog_show(self, dialog, phone_ring_type, *args, **kwargs):
        return 'shown'


module('ui', ui_dialog_service=module('ui.ui_dialog_service', UiDialogService=UiDialogService))

import simsight_hub as hub  # noqa: E402
import simsight_phone as phone  # noqa: E402


def info(**kw):
    return types.SimpleNamespace(**kw)


class QueueDetail(unittest.TestCase):
    def test_waiting_autonomous(self):
        entry = info(is_visual_type_posture=lambda: False, user_cancelable=True)
        action = info(is_autonomous=True, is_user_directed=False)
        self.assertEqual(
            hub._queue_detail(entry, action, 'queued', 2, 3),
            'queue waiting. queue autonomous. queue enter cancels',
        )

    def test_running_posture_cannot_cancel(self):
        entry = info(is_visual_type_posture=lambda: True, user_cancelable=False)
        self.assertEqual(
            hub._queue_detail(entry, None, 'running', 0, 0), 'queue posture. queue cannot cancel'
        )


class Guests(unittest.TestCase):
    def test_household_and_selectable_sims_are_not_guests(self):
        home = info(sim_info=info(household_id=1, is_selectable=False))
        played = info(sim_info=info(household_id=2, is_selectable=True))
        guest = info(sim_info=info(household_id=2, is_selectable=False))
        gone = info()  # no sim_info any more
        situation = info(all_sims_in_situation_gen=lambda: iter([home, played, guest, gone]))
        self.assertEqual(hub._guests(situation), [guest])

    def test_end_event_counts_and_says(self):
        guest = info(sim_info=info(household_id=2, is_selectable=False))
        situation = info(
            is_user_facing=True, all_sims_in_situation_gen=lambda: iter([guest, guest])
        )
        calls = []

        class Manager:
            def get(self, situation_id):
                return situation

            def make_sim_leave(self, sim):
                if calls:
                    raise RuntimeError('refused')  # the second guest refuses
                calls.append('leave')

            def pre_destroy_situation_by_id(self, situation_id):
                calls.append('pre')

            def destroy_situation_by_id(self, situation_id):
                calls.append('destroy')

        module('services', active_household=lambda: HOUSEHOLD, get_zone_situation_manager=Manager)
        del SPOKEN[:]
        self.assertTrue(hub.end_event(5, send_home=True, end=True))
        self.assertEqual(calls, ['leave', 'pre', 'destroy'])
        self.assertEqual(SPOKEN, ['guests sent home event ended'])


class Category:
    def __init__(self, name, parent=None, priority=0):
        self._name, self._parent, self._display_priority = name, parent, priority

    def _display_name(self, sim):
        return self._name


class PhoneGroups(unittest.TestCase):
    def test_root_and_priority(self):
        top = Category('top', priority='x')
        self.assertIs(phone._root(Category('leaf', Category('mid', top))), top)
        self.assertIsNone(phone._root(None))
        self.assertEqual(phone._priority(top), 0)

    def test_collect_groups_in_phone_order(self):
        social = Category('Social', priority=2)
        work = Category('Work', priority=1)
        jobs = Category('Jobs', work)
        cats = {'s': social, 'j': jobs}

        def item(name, key, ok=True):
            affordance = info(
                guid64=name,
                get_name=lambda *a, **k: name,
                get_display_tooltip=lambda **k: 'tip ' + name,
                create_localized_string=lambda *a, **k: 'why',
            )
            aop = info(affordance=affordance, target=None, interaction_parameters={})
            return 0, info(aop=aop, context=None, category_key=key, result=info(tooltip=None))

        items = [item('Call', 's'), item('Find job', 'j'), item('Hidden', 's'), item('Move', None)]
        sim = info(
            sim_id=9,
            test_pie_menu_modifiers=lambda a: (a.guid64 != 'Hidden', None),
            apply_forbid_by_object_tags_pie_menu_modifiers=lambda aop: None,
            potential_phone_interactions=lambda context: (),
        )
        client = info(create_interaction_context=lambda sim: None)

        class ChoiceMenu:
            def __init__(self, sim):
                pass

            def add_potential_aops(self, *args):
                pass

            def __iter__(self):
                return iter(items)

        module('interactions', choices=module('interactions.choices', ChoiceMenu=ChoiceMenu))
        sys.modules['sims4'].resources = module('sims4.resources', Types=info(PIE_MENU_CATEGORY=1))
        module(
            'services',
            get_instance_manager=lambda kind: cats,
            get_tutorial_service=lambda: None,
        )
        found = phone._collect(sim, client)
        self.assertEqual([label for label, _ in found.groups], ['Work', 'Social'])
        self.assertEqual([e.name for e in found.groups[0][1]], ['Find job'])
        self.assertEqual(found.groups[0][1][0].detail, 'Jobs')  # a subcategory names itself
        self.assertEqual([e.detail for e in found.groups[1][1]], ['tip Call'])
        self.assertEqual([e.name for e in found.direct], ['Move'])

    def test_hook_installed_once(self):
        self.assertTrue(getattr(UiDialogService.dialog_show, '_a11y_phone_hook', False))
        before = UiDialogService.dialog_show
        phone._install()
        self.assertIs(UiDialogService.dialog_show, before)


if __name__ == '__main__':
    unittest.main(verbosity=0)

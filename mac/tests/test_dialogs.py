"""Game windows: reading a picker and answering it like its own buttons."""

import unittest

from fakegame import module
import fakegame

fakegame.scripts_on_path()


class Loc:
    def __init__(self, h):
        self.hash, self.tokens = h, []


class Msg:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def HasField(self, f):
        return getattr(self, f, None) is not None


WORDS = {1: 'Cook', 2: 'What to cook?', 3: 'Pancakes', 4: 'Lobster', 5: 'Needs skill 9'}
SENT, CALLS = [], []
module(
    'simsight_bridge',
    text_of=lambda v, fb='': WORDS.get(getattr(v, 'hash', 0), fb),
    send=SENT.append,
    log=lambda m: None,
)


class Service:
    def __init__(self, dialogs):
        self._active_dialogs = dialogs

    def get_dialog(self, key):
        return self._active_dialogs.get(key)

    def dialog_pick_result(self, key, picked_results, **kw):
        CALLS.append(('pick', key, picked_results))

    def dialog_text_input(self, key, name, value):
        CALLS.append(('text', key, name, value))

    def dialog_respond(self, key, choice):
        CALLS.append(('respond', key, choice))


def row(i, name, enabled, tip=None):
    return Msg(
        option_id=i,
        name=Loc(name),
        row_description=None,
        is_enable=enabled,
        is_selected=False,
        row_tooltip=Loc(tip) if tip else None,
    )


DIALOG = Msg(picker_rows=[row(11, 3, True), row(12, 4, False, 5)])
module('services', ui_dialog_service=lambda: SERVICE)
SERVICE = Service({7: DIALOG})

import simsight_dialogs as dialogs  # noqa: E402


class DialogsTest(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        dialogs._msgs.clear()
        dialogs._msgs[7] = Msg(
            title=Loc(1),
            text=Loc(2),
            choices=[],
            text_input=None,
            picker_data=Msg(min_selectable=1, max_selectable=1, title=None),
        )
        dialogs._msgs[99] = Msg(
            title=Loc(1), text=None, choices=[], picker_data=None, text_input=None
        )

    def test_reads_picker(self):
        [d] = dialogs.active()
        self.assertEqual((d['title'], d['text']), ('Cook', 'What to cook?'))
        self.assertEqual([r['name'] for r in d['rows']], ['Pancakes', 'Lobster'])
        self.assertEqual(d['rows'][1]['reason'], 'Needs skill 9')
        self.assertEqual([c['id'] for c in d['choices']], [10001, 10002])
        self.assertNotIn(99, dialogs._msgs)  # closed windows are forgotten

    def test_ok_picks_then_responds(self):
        self.assertEqual(dialogs.respond('7', 10001, ['11'], ''), (True, ''))
        self.assertEqual(CALLS, [('pick', 7, [11]), ('respond', 7, 10001)])

    def test_cancel_clears_the_pick(self):
        self.assertEqual(dialogs.respond('7', 10002, [], ''), (True, ''))
        self.assertEqual(CALLS, [('respond', 7, 10002)])
        self.assertEqual(DIALOG.picked_results, [])

    def test_rows_from_the_game_message(self):
        def base(i, name, enabled=True):
            return Msg(
                option_id=i,
                name=Loc(name) if name else None,
                description=None,
                tooltip=None,
                is_enable=enabled,
                is_selected=False,
            )

        picker = Msg(
            recipe_picker_data=Msg(
                row_data=[Msg(base_data=base(21, 3), price=12, sim_id=0, object_id=0)]
            ),
            sim_picker_data=None,
            object_picker_data=None,
            outfit_picker_data=None,
            shop_picker_data=None,
            lot_picker_data=None,
            odd_job_picker_data=None,
            row_picker_data=[],
        )
        rows = dialogs._msg_rows(Msg(picker_data=picker))
        self.assertEqual(
            [(r['id'], r['name'], r['description']) for r in rows], [('21', 'Pancakes', '§12')]
        )

    def test_sim_picker_gets_names(self):
        module(
            'services',
            ui_dialog_service=lambda: SERVICE,
            sim_info_manager=lambda: Msg(
                get=lambda i: Msg(first_name='Nancy', last_name='Landgraab')
            ),
        )
        base = Msg(
            option_id=9,
            name=None,
            description=None,
            tooltip=None,
            is_enable=True,
            is_selected=False,
        )
        picker = Msg(
            recipe_picker_data=None,
            sim_picker_data=Msg(row_data=[Msg(base_data=base, sim_id=9)]),
            object_picker_data=None,
            outfit_picker_data=None,
            shop_picker_data=None,
            lot_picker_data=None,
            odd_job_picker_data=None,
            row_picker_data=[],
        )
        rows = dialogs._msg_rows(Msg(picker_data=picker))
        self.assertEqual(rows[0]['name'], 'Nancy Landgraab')

    def test_closed_window(self):
        self.assertEqual(dialogs.respond('8', 10001, [], ''), (False, 'dialog_closed'))

    def test_notification_waits_for_words(self):
        del SENT[:]
        dialogs._notes.append([0, Loc(1), Loc(2)])
        dialogs.flush_notes()
        self.assertEqual(
            SENT,
            [
                {
                    'event': {
                        'kind': 'EVENT_KIND_NOTIFICATION',
                        'subject': 'Cook',
                        'detail': 'What to cook?',
                    }
                }
            ],
        )


if __name__ == '__main__':
    unittest.main()

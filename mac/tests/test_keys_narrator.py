"""Narrator and settings menu logic: command argument types, payload
decoding, reading order, mood order, menu value steps."""

import inspect
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

SPOKEN = []
SPECS = {}


def command(*names, **kwargs):
    def register(func):
        # What EA's sims4.commands reads, once, when a command is registered.
        SPECS[names[0]] = inspect.getfullargspec(func)
        return func

    return register


SIMS4 = module('sims4')
SIMS4.commands = module(
    'sims4.commands', Command=command, CommandType=types.SimpleNamespace(Live=1)
)
module(
    'simsight_speech',
    speak=lambda text, **k: SPOKEN.append(text),
    log_line=lambda *a: None,
    speech_mode='hover',
    braille_mode='concise',
)
module('simsight_settings', get=lambda name, fallback=None: fallback, brief=lambda: False)
module('simsight_pump', status=lambda: 'ok')
module('zone', Zone=type('Zone', (), {'on_loading_screen_animation_finished': lambda self: None}))

import simsight_menu as menu  # noqa: E402
import simsight_narrator as narrator  # noqa: E402


class CommandTypesTest(unittest.TestCase):
    def test_parser_sees_real_types_only(self):
        spec = SPECS['simsight.inspect_ui']
        self.assertEqual(spec.annotations, {'delay': float})
        self.assertEqual(SPECS['simsight.speak_enc'].annotations, {'text': str})
        self.assertEqual(SPECS['simsight.menu'].annotations, {'action': str})
        # Untyped before: *args and _connection stay without a type.
        self.assertEqual(SPECS['simsight.voice'].annotations, {})
        self.assertEqual(SPECS['simsight.voice'].varargs, 'args')


class DecodeTest(unittest.TestCase):
    def test_utf8_and_stray_latin1(self):
        # Scaleform: e-acute as a bare Latin-1 byte, the euro sign as UTF-8.
        self.assertEqual(narrator._decode_percent('caf%E9 %E2%82%AC5'), 'café €5')

    def test_plain_ascii(self):
        self.assertEqual(narrator._decode_percent('a%20b'), 'a b')


class SegmentsTest(unittest.TestCase):
    def test_rows_labels_and_duplicate_headers(self):
        payload = '\x01'.join(
            ['10|0|Eco Footprint', '30|0|Eco Footprint:', '50|0|Neutral', '70|5|b', '70|0|a']
        )
        self.assertEqual(
            narrator._fields_to_segments(payload), ['Eco Footprint: Neutral', 'a', 'b']
        )

    def test_equal_x_keeps_posted_order(self):
        payload = '\x01'.join(['Increase', 'Chair', '3'])
        self.assertEqual(narrator._fields_to_segments(payload), ['Increase', 'Chair', '3'])

    def test_placeholders_dropped(self):
        payload = '\x01'.join(['0|0|1234567890', '0|5|txtSimName', '0|9|%', '0|12|Name'])
        self.assertEqual(narrator._fields_to_segments(payload), ['Name'])

    def test_moodlet_order(self):
        segments = ['Happy', 'Feeling good', '(From a nap)', 'Happy +1', '4 hours']
        self.assertEqual(
            narrator._order_segments(segments),
            ['Happy', 'Happy +1', '4 hours', '(From a nap)', 'Feeling good'],
        )

    def test_source_last(self):
        self.assertEqual(narrator._order_segments(['(From x)', 'a', 'b']), ['a', 'b', '(From x)'])


class MoodTest(unittest.TestCase):
    def setUp(self):
        del SPOKEN[:]
        narrator._state.mood_reading = None

    def record(self, *fields):
        narrator.a11y_mood_enc('\x01'.join(fields))

    def test_reading(self):
        self.record('M', '1', '2', 'happy', 'Happy', 'Content')
        self.record('B', '1', '0', 'sad', '0', '3', 'Lonely', '', '', '', '')
        self.record('B', '1', '1', 'happy', '1', '1', 'Boosted', '', '', '', '')
        self.record('B', '1', '2', 'happy', '0', '1', 'Rested', '', '', '', '', 'Slept')
        self.record('B', '9', '3', 'happy', '0', '1', 'Stale', '', '', '', '')
        self.record('E', '1')
        self.assertEqual(SPOKEN, ['Happy. Content. Rested. Slept. Boosted. Lonely'])

    def test_empty_means_unavailable(self):
        narrator.a11y_mood_enc('')
        self.assertEqual(len(SPOKEN), 1)


class MenuTest(unittest.TestCase):
    def row(self, key):
        return next(row for row in menu.ROWS if row.key == key)

    def test_number_wraps(self):
        delay = self.row('tooltip_delay')
        self.assertEqual(menu._next_number(delay, 10.0, 1), 0.0)
        self.assertEqual(menu._next_number(delay, 0.0, -1), 10.0)
        self.assertEqual(menu._next_number(delay, 1.0, 1), 1.5)
        volume = self.row('sapi_volume')
        self.assertEqual(menu._next_number(volume, 95.0, 1), 100)
        self.assertIsInstance(menu._next_number(volume, 95.0, 1), int)

    def test_voice_match(self):
        names = ['Microsoft David', 'Microsoft Zira']
        self.assertEqual(menu._voice_index(names, 'zira'), 1)
        self.assertEqual(menu._voice_index(names, ''), -1)
        self.assertEqual(menu._voice_index(names, 'nobody'), -1)

    def test_rows_order(self):
        keys = [row.key for row in menu.ROWS]
        self.assertEqual(keys[:3], ['speech_mode', 'braille_mode', 'verbosity'])
        self.assertEqual(keys[-2:], ['reload', 'close'])
        self.assertEqual(len(keys), 30)


if __name__ == '__main__':
    unittest.main()

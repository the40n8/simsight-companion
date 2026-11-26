"""SimSight.cfg: reading a hand-edited file (forgiving, with load errors)
and writing it back in the same format."""

import os
import tempfile
import unittest

import fakegame

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

WRITTEN = []
simsight_log._write = WRITTEN.append

import simsight_settings as settings  # noqa: E402


class SettingsFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        settings.PATH = os.path.join(self.folder.name, 'SimSight.cfg')

    def tearDown(self) -> None:
        settings.PATH = os.path.join(self.folder.name, 'missing.cfg')
        settings.reload()
        self.folder.cleanup()

    def load(self, text: str) -> None:
        with open(settings.PATH, 'w', encoding='utf-8') as handle:
            handle.write(text)
        settings.reload()

    def test_values_are_parsed_and_clamped(self) -> None:
        self.load(
            '[Features]\n  Sim   Actions = OFF\nscanner range = 12 Metres\n'
            '[speech]\nmode = talk\nbraille = FULL\ntooltip delay = 99\n'
            '[voice]\nrate = 50\nvolume = -3\nname = David\n'
        )
        self.assertIs(settings.get('sim_actions'), False)
        self.assertEqual(settings.get('scanner_range'), '12 meters')
        self.assertEqual(settings.get('speech_mode'), 'hover')  # legacy name
        self.assertEqual(settings.get('braille_mode'), 'full')
        self.assertEqual(settings.get('tooltip_delay'), 10.0)
        self.assertEqual(settings.get('sapi_rate'), 10)
        self.assertEqual(settings.get('sapi_volume'), 0)
        self.assertEqual(settings.get('sapi_voice'), 'David')
        self.assertEqual(settings.load_errors, [])

    def test_bad_values_keep_defaults_and_say_why(self) -> None:
        self.load(
            '[features]\nnot an option\nkeyboard = maybe\n'
            '[speech]\nmode = loud\nverbosity = chatty\ntooltip delay = soon\n'
            '[voice]\nrate = fast\n'
        )
        for key in ('keyboard', 'speech_mode', 'verbosity', 'tooltip_delay', 'sapi_rate'):
            self.assertEqual(settings.get(key), settings.DEFAULTS[key], key)
        self.assertEqual(
            settings.load_errors,
            [
                'line 2 is not "option = value" and was skipped: \'not an option\\n\'',
                '"maybe" is not on/off for keyboard; keeping default',
                'unknown speech mode "loud"; keeping default',
                'unknown verbosity "chatty"; keeping default',
                'tooltip delay must be a number of seconds, not "soon"',
                'sapi_rate must be a number, not "fast"',
            ],
        )

    def test_glued_indented_line(self) -> None:
        self.load('[voice]\nrate = 2\n  oops = 3\n')
        self.assertEqual(settings.get('sapi_rate'), 2)
        self.assertEqual(
            settings.load_errors, ['sapi_rate had an extra indented line glued to it; using "2"']
        )

    def test_legacy_section_is_read(self) -> None:
        self.load('[contribution]\nscanner scope = whole lot\nexperimental water = off\n')
        self.assertEqual(settings.get('scanner_range'), 'whole lot')
        self.assertIs(settings.get('experimental_water'), False)

    def test_set_writes_a_file_that_reads_back(self) -> None:
        self.load('[speech]\nmode = beeps\n')
        self.assertTrue(settings.set('tooltip_delay', 2.0))
        self.assertTrue(settings.set('scanner', True))
        with open(settings.PATH, encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn('tooltip delay = 2\n', text)
        self.assertIn('mode = beeps\n', text)
        self.assertIn('scanner = on\n', text)
        settings.reload()
        self.assertEqual(settings.get('speech_mode'), 'beeps')
        self.assertEqual(settings.get('tooltip_delay'), 2.0)
        self.assertIs(settings.get('scanner'), True)

    def test_captions_survive_a_save(self) -> None:
        self.load('[features]\ncaptions = on\n')
        self.assertIs(settings.get('captions'), True)
        settings.set('keyboard', False)  # any save rewrites the whole file
        settings.reload()
        self.assertIs(settings.get('captions'), True)

    def test_a_line_before_the_first_section_does_not_lose_the_file(self) -> None:
        self.load('oops\n# a comment\n[features]\nsim actions = off\nnot an option\n')
        self.assertIs(settings.get('sim_actions'), False)
        self.assertEqual(
            settings.load_errors,
            [
                'line 1 is before any [section] and was skipped: oops',
                'line 5 is not "option = value" and was skipped: \'not an option\\n\'',
            ],
        )

    def test_get_fallback_only_when_unset(self) -> None:
        self.load('[speech]\nlogging = on\n')
        self.assertIs(settings.get('logging', False), True)
        self.assertEqual(settings.get('no such key', 'x'), 'x')
        self.assertIsNone(settings.get('no such key'))


if __name__ == '__main__':
    unittest.main()

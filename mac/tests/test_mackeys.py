"""Mac keyboard feeder: families, repeats, key-up, focus loss, owned keys."""

import time
import types
import unittest

from fakegame import module
import fakegame

fakegame.scripts_on_path()

LOG = []
module('simsight_speech', log_line=lambda m: None)
module('simsight_pump', alive=lambda: True, post=lambda fn, *a: LOG.append(('post',)))
KEYS = module(
    'simsight_keys',
    status=types.SimpleNamespace(enabled=True),
    _REPEAT_DELAY=0.03,
    _REPEAT_INTERVAL=0.0,
    CHORDS={0x28: ('next', True), 0x48: ('help', False)},
    _REPEATABLE={'A11yArrowDown'},
    _announce_ui_settings=lambda: None,
)
KEYS.modal = True


def plain(vk, ctrl, alt, shift):
    if not KEYS.modal:
        return None
    if vk == 0x09 and shift:
        return 'A11yTabPrev'
    return None if shift else {0x28: 'A11yArrowDown', 0x0D: 'A11yActivate'}.get(vk)


KEYS._plain_message = plain
KEYS._send = lambda m: LOG.append(('send', m))
KEYS._chord = lambda i: LOG.append(('chord', i, False))
KEYS.repeat_chord = lambda i: LOG.append(('chord', i, True))


def read_time():
    LOG.append(('plus', 'time'))


module('simsight_plus_core', HOTKEYS={0x54: (read_time, 'time')}, _wants_shift=lambda fn: False)

import simsight_mackeys as mackeys  # noqa: E402


class MacKeysTest(unittest.TestCase):
    def setUp(self):
        del LOG[:]
        mackeys.reset()
        KEYS.modal = True

    def test_families(self):
        mackeys.handle('chord', 0x48, True, False)
        mackeys.handle('needs', 0x4E, True, False)
        mackeys.handle('plus', 0x54, True, False)
        self.assertEqual(LOG, [('chord', 'help', False), ('send', 'A11yNeeds'), ('plus', 'time')])

    def test_plain_repeat_and_keyup(self):
        mackeys.handle('plain', 0x28, True, False)
        time.sleep(0.05)
        mackeys._repeat(time.monotonic())
        mackeys.handle('plain', 0x28, False, False)
        self.assertEqual(
            LOG, [('send', 'A11yArrowDown'), ('send', 'A11yArrowDown'), ('send', 'A11y|keyup|40')]
        )

    def test_chord_repeat_is_flagged(self):
        mackeys.handle('chord', 0x28, True, False)
        time.sleep(0.05)
        mackeys._repeat(time.monotonic())
        self.assertEqual(LOG, [('chord', 'next', False), ('chord', 'next', True)])

    def test_focus_lost_releases(self):
        mackeys.handle('plain', 0x28, True, False)
        mackeys.focus_lost()
        self.assertEqual(LOG[-1], ('send', 'A11y|keyup|40'))
        self.assertEqual(mackeys._held, {})

    def test_owned_keys_follow_the_mod(self):
        sent = []
        mackeys.tick(sent.append)
        self.assertEqual(sent, [{'grab': {'keys': ['13', '40', '9+shift']}}])
        KEYS.modal = False
        mackeys.reset()
        mackeys.tick(sent.append)
        self.assertEqual(sent[-1], {'grab': {'keys': []}})


if __name__ == '__main__':
    unittest.main()

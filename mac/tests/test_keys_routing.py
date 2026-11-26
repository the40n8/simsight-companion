"""Keyboard routing (simsight_keys): command argument types, Flash's owner
reports, which message a bare key sends, where a chord goes, and the
Windows poller's press / repeat / release with a fake user32."""

import inspect
import time
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

SPECS = {}
SPOKEN = []
POSTED = []
SETTINGS = {}


def command(*names, **kwargs):
    def register(func):
        # What EA's sims4.commands reads, once, when a command is registered.
        SPECS[names[0]] = inspect.getfullargspec(func)
        return func

    return register


SIMS4 = module('sims4')
SIMS4.commands = module(
    'sims4.commands',
    Command=command,
    CommandType=types.SimpleNamespace(Live=1),
    CheatOutput=lambda connection: lambda text: None,
)
module('simsight_lang', t=lambda text: text)
module(
    'simsight_settings',
    get=lambda name, fallback=None: SETTINGS.get(name, fallback),
    set=SETTINGS.__setitem__,
    KEY_NAMES={'Tab': 0x09},
)
module(
    'simsight_speech',
    speak=lambda text, **k: SPOKEN.append(text),
    repeat_last=lambda: SPOKEN.append('repeat'),
    log_line=lambda *a: None,
)
for name in (
    'simsight_phone',
    'simsight_hub',
    'simsight_careerlog',
    'simsight_modui',
    'simsight_rooms',
    'simsight_holidays',
    'simsight_plus',
):
    module(name)
module('simsight_pump', post=lambda fn, *args: POSTED.append((fn.__name__,) + args) or True)
module('simsight_startup', send_navigation=lambda message: False)
CLOSED = []
module('simsight_scanner', a11y_menuclosed=lambda: CLOSED.append(True))

import simsight_keys as keys  # noqa: E402


def reset():
    del SPOKEN[:], POSTED[:], CLOSED[:]
    SETTINGS.clear()
    keys._enabled = True
    keys._modal_active = False
    keys._editing_active = False
    keys._build_grid_owner = False
    keys._state.blocking_explicit = None
    keys._state.scanner_owner = False
    keys._opening_keys.clear()
    keys._dlls = lambda: (FakeUser32(), None)  # no Windows here


class CommandTypesTest(unittest.TestCase):
    def test_parser_sees_real_types_only(self):
        self.assertEqual(SPECS['simsight.modalstate'].annotations, {'state': str})
        self.assertEqual(SPECS['simsight.keys'].annotations, {'mode': str})
        self.assertEqual(SPECS['simsight.inputfence'].args, ['payload', '_connection'])


class ModalStateTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_dropped_leading_zero_and_numbers(self):
        self.assertTrue(keys.apply_modal_state('1', 'modalstate'))
        self.assertEqual((keys._modal_active, keys._editing_active), (False, True))
        self.assertTrue(keys.apply_modal_state(10, 'modalstate'))
        self.assertEqual((keys._modal_active, keys._editing_active), (True, False))

    def test_context_suffix_carries_blocking(self):
        self.assertTrue(keys.apply_modal_state('100', 'context'))
        self.assertTrue(keys._modal_active)
        self.assertFalse(keys.blocking_active())
        # Three digits are only accepted from the context suffix.
        self.assertFalse(keys.apply_modal_state('101', 'modalstate'))

    def test_owner_without_bit_counts_as_blocking(self):
        keys.apply_modal_state('100', 'context')
        keys.apply_modal_state('00', 'modalstate')
        self.assertTrue(CLOSED)  # no owner: the scanner hears the menu closed
        keys.apply_modal_state('10', 'modalstate')
        self.assertTrue(keys.blocking_active())

    def test_closed_owner_bumps_the_epoch(self):
        keys.apply_modal_state('10', 'modalstate')
        epoch = keys._state.input_epoch
        keys.apply_modal_state('00', 'modalstate')
        self.assertGreater(keys._state.input_epoch, epoch)

    def test_rejects_garbage(self):
        self.assertFalse(keys.apply_modal_state('2', 'modalstate'))
        self.assertFalse(keys.apply_modal_state(None, 'modalstate'))
        self.assertFalse(keys.a11y_blocking('x'))
        self.assertTrue(keys.a11y_blocking(0))
        self.assertIs(keys._state.blocking_explicit, False)


class PlainMessageTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_world_keys_stay_with_the_game(self):
        self.assertIsNone(keys._plain_message(0x28))
        self.assertIsNone(keys._plain_message(0x09, shift=True))  # first-person camera
        self.assertEqual(keys._plain_message(0x09), 'A11yTabNext')
        self.assertEqual(keys._plain_message(0x4E, ctrl=True, shift=True), 'A11yNeeds')

    def test_modal_owner(self):
        keys._modal_active = True
        self.assertEqual(keys._plain_message(0x28), 'A11yArrowDown')
        self.assertEqual(keys._plain_message(0x09, shift=True), 'A11yTabPrev')
        self.assertEqual(keys._plain_message(0x1B), 'A11yEscape')
        self.assertIsNone(keys._plain_message(0x28, shift=True))
        self.assertIsNone(keys._plain_message(0x28, ctrl=True))
        self.assertIsNone(keys._plain_message(0x64))
        SETTINGS['numpad_navigation'] = True
        self.assertEqual(keys._plain_message(0x64), 'A11yArrowUp')

    def test_editing_keeps_arrows(self):
        keys._modal_active = True
        keys._editing_active = True
        self.assertIsNone(keys._plain_message(0x28))
        self.assertEqual(keys._plain_message(0x1B), 'A11yEscape')

    def test_build_cursor_and_scanner(self):
        keys._build_grid_owner = True
        self.assertEqual(keys._plain_message(0x52), 'A11yBuildGrid:turn')
        self.assertEqual(keys._plain_message(0x52, shift=True), 'A11yBuildGrid:turnback')
        keys._build_grid_owner = False
        keys._state.scanner_owner = True
        SETTINGS['scanner_mode'] = True
        self.assertEqual(keys._plain_message(0x28), 'A11yScanner:next')
        keys._enabled = False
        self.assertIsNone(keys._plain_message(0x28))


class ChordTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_world_chord_is_queued(self):
        keys._chord('next')
        self.assertEqual(POSTED, [('_world_chord', 'next', keys._state.input_epoch)])
        del POSTED[:]
        keys._repeat_chord('next')
        self.assertEqual(POSTED, [('_world_chord', 'next', keys._state.input_epoch, True)])
        self.assertFalse(keys._chord_repeating)

    def test_modal_owner_gets_navigation(self):
        keys._modal_active = True
        keys._chord('next')
        self.assertEqual(POSTED, [('_deliver_activation', 'A11yNavNext', keys._state.input_epoch)])
        keys._chord('rescan')
        self.assertEqual(len(POSTED), 1)  # nothing reaches the world behind a dialog
        keys._chord('help')
        self.assertTrue(SPOKEN[-1].startswith('Dialog.'))

    def test_pickers_and_phone_wait_for_the_dialog(self):
        keys._modal_active = True
        keys._chord('queue')
        keys._chord('phone')
        self.assertEqual(
            SPOKEN,
            [
                'close the dialog first',
                'Close the current panel or dialog before opening a waiting call.',
            ],
        )
        self.assertEqual(POSTED, [])
        keys._modal_active = False
        keys._chord('catalog')
        keys._chord('sim2')
        self.assertEqual(
            [p[:2] for p in POSTED], [('_open_catalog', 'catalog'), ('_switch_household_sim', 2)]
        )

    def test_text_field_owns_most_chords(self):
        keys._editing_active = True
        keys._chord('next')
        keys._chord('scannermode')
        keys._chord('clock')
        self.assertEqual(POSTED, [])
        keys._chord('repeat')
        self.assertEqual(SPOKEN, ['repeat'])

    def test_stale_pump_turn_is_dropped(self):
        epoch = keys._state.input_epoch
        keys.fence_opening_input()
        self.assertFalse(keys._open_queue('queue', epoch))


class FakeUser32:
    def __init__(self):
        self.down = set()
        self.taps = set()

    def GetAsyncKeyState(self, vk):
        value = (0x8000 if vk in self.down else 0) | (1 if vk in self.taps else 0)
        self.taps.discard(vk)
        return value


class PollerTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.sent = []
        self.chords = []
        self._send, self._chord, self._fg = keys._send, keys._chord, keys._game_is_foreground
        keys._send = self.sent.append
        keys._chord = self.chords.append
        keys._game_is_foreground = lambda user32, kernel32: True
        self.user32 = FakeUser32()
        self.poller = keys._Poller(self.user32, None)
        self.poller.last_lease = time.monotonic() + 3600  # no lease in these tests

    def tearDown(self):
        keys._send, keys._chord, keys._game_is_foreground = self._send, self._chord, self._fg

    def test_press_repeat_release(self):
        keys._modal_active = True
        self.user32.down.add(0x28)
        self.poller.poll()
        self.assertEqual(self.sent, ['A11yArrowDown'])
        key = ('plain', 0x28)
        self.poller.pressed_at[key] -= 1.0
        self.poller.last_fire[key] -= 1.0
        self.poller.poll()
        self.assertEqual(self.sent, ['A11yArrowDown', 'A11yArrowDown'])
        self.user32.down.clear()
        self.poller.poll()
        self.assertEqual(self.sent[-1], 'A11y|keyup|40')

    def test_quick_tap_between_polls(self):
        keys._modal_active = True
        self.user32.taps.add(0x0D)
        self.poller.poll()
        self.assertEqual(self.sent, ['A11yActivate', 'A11y|keyup|13'])

    def test_chord_fires_once_while_held(self):
        self.user32.down.update((0x11, 0x12, 0x48))  # Ctrl+Alt+H
        self.poller.poll()
        self.poller.poll()
        self.assertEqual(self.chords, ['help'])

    def test_key_held_across_focus_return_is_ignored(self):
        keys._modal_active = True
        self.poller._focus_left()
        self.user32.down.add(0x28)
        self.poller.poll()
        self.assertEqual(self.sent, [])
        self.user32.down.clear()
        self.poller.poll()
        self.user32.down.add(0x28)
        self.poller.poll()
        # The release frees the native guard; only the new press acts.
        self.assertEqual(self.sent, ['A11y|keyup|40', 'A11yArrowDown'])


if __name__ == '__main__':
    unittest.main()

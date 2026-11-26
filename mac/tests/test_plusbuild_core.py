"""SimSight Plus core: typed text capture, Alt hotkey polling, main-thread pump."""

import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()


class _Command:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, fn):
        return fn


class _Zone:
    def on_loading_screen_animation_finished(self, *args, **kwargs):
        return 'loaded'


SIMS4 = module('sims4')
SIMS4.commands = module(
    'sims4.commands',
    Command=_Command,
    CommandType=type('CommandType', (), {'Live': 1}),
    CheatOutput=lambda c: print,
)
module('zone', Zone=_Zone)
SAID = []
module('simsight_speech', speak=lambda text, **k: SAID.append(text))

import simsight_log  # noqa: E402
import simsight_plus_core as core  # noqa: E402

ERRORS = []
simsight_log._write = ERRORS.append  # nothing written next to the sources


class Keys:
    """GetAsyncKeyState stand-in: vk -> state for the next reads."""

    def __init__(self):
        self.state = {}

    def __call__(self, vk):
        return self.state.get(vk, 0)


def run_posted():
    batch = core._queue[:]
    del core._queue[:]
    for fn, args in batch:
        fn(*args)


class TypingTest(unittest.TestCase):
    def setUp(self):
        del core._queue[:]
        del SAID[:]
        self.typed = []
        core._state.typing = core._TypingSession(self.typed.append, 7)
        self.keys = Keys()
        self.held = set()

    def tap(self, vk, shift=False):
        self.keys.state = {vk: 0x8000}
        core._typing_poll(self.keys, shift, self.held)
        self.keys.state = {}
        core._typing_poll(self.keys, shift, self.held)

    def test_keys_held_when_the_box_opens_do_not_count(self):
        self.keys.state = {0x0D: 0x8000}
        core._typing_poll(self.keys, False, self.held)  # arms
        core._typing_poll(self.keys, False, self.held)  # Enter still held
        self.assertEqual(core._queue, [])
        self.assertTrue(core._state.typing.armed)

    def test_letters_digits_symbols_backspace_enter(self):
        core._typing_poll(self.keys, False, self.held)  # arms
        for vk, shift in ((0x41, True), (0x42, False), (0x33, False), (0xBD, True), (0x20, False)):
            self.tap(vk, shift)
        self.tap(0xDE, True)
        self.tap(0x08)
        self.tap(0xBE)
        self.assertEqual(core._state.typing.chars, list('Ab3_ .'))
        self.tap(0x0D)
        run_posted()
        self.assertIsNone(core._state.typing)
        self.assertEqual(self.typed, ['Ab3_ .'])

    def test_escape_cancels(self):
        core._typing_poll(self.keys, False, self.held)
        self.tap(0x41)
        self.tap(0x1B)
        run_posted()
        self.assertEqual(self.typed, [])
        self.assertEqual(SAID, ['Cancelled.'])

    def test_typed_char_table(self):
        self.assertEqual(core._typed_char(0x5A, False), 'z')
        self.assertEqual(core._typed_char(0x5A, True), 'Z')
        self.assertEqual(core._typed_char(0x30, True), '0')
        self.assertEqual(core._typed_char(0xDE, False), "'")
        self.assertIsNone(core._typed_char(0x0D, False))


class HotkeyTest(unittest.TestCase):
    def setUp(self):
        del core._queue[:]
        core._state.typing = None
        self.calls = []
        core.HOTKEYS.clear()
        core.HOTKEYS[0x54] = (lambda: self.calls.append('t'), 'time')
        core._game_foreground = lambda user32: True
        self.keys = Keys()
        self.poller = core._KeyPoller(self.keys, None)
        # time.monotonic() can start near 0 in a fresh process: no key "just seen"
        self.poller.alt_seen = self.poller.other_seen = -10.0

    def test_alt_key_posts_once_until_released(self):
        self.keys.state = {0x12: 0x8000, 0x54: 0x8000}
        self.poller.poll()
        self.poller.poll()
        run_posted()
        self.assertEqual(self.calls, ['t'])
        self.keys.state = {}
        self.poller.poll()
        self.assertEqual(self.poller.down, set())

    def test_control_blocks_the_hotkey(self):
        self.keys.state = {0x12: 0x8000, 0x11: 0x8000, 0x54: 0x8000}
        self.poller.poll()
        run_posted()
        self.assertEqual(self.calls, [])

    def test_shift_is_passed_when_wanted(self):
        got = []

        def fn(shift):
            got.append(shift)

        fn._wants_shift = True
        core.HOTKEYS[0x54] = (fn, 'shifted')
        self.keys.state = {0x12: 0x8000, 0x10: 0x8000, 0x54: 0x8000}
        self.poller.poll()
        run_posted()
        self.assertEqual(got, [True])


class PumpTest(unittest.TestCase):
    def test_failures_are_contained_and_tickers_run(self):
        ran = []
        core.post(lambda: 1 / 0)
        core.post(ran.append, 'posted')
        core.add_ticker(0.0, lambda: ran.append('tick'))
        core._drain()
        self.assertEqual(ran, ['posted', 'tick'])
        self.assertTrue(ERRORS[-1].startswith('ERROR plus: posted <lambda>'))

    def test_queue_keeps_the_newest(self):
        del core._queue[:]
        for i in range(40):
            core.post(print, i)
        self.assertEqual(len(core._queue), 33)
        self.assertEqual(core._queue[-1][1], (39,))
        del core._queue[:]


if __name__ == '__main__':
    unittest.main()

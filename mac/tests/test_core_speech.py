"""Speech pipeline without a backend: what the read-current key repeats,
burst thinning, repeat dedupe and the braille/caption text (fake clock and
timers; nothing is spoken)."""

import types
import unittest
from typing import Any, Callable, List

import fakegame

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

simsight_log._write = lambda text: None
LINES = []
simsight_log.line = LINES.append

import simsight_speech as speech  # noqa: E402


class Clock:
    now = 1000.0

    @classmethod
    def monotonic(cls) -> float:
        return cls.now


TIMERS = []


class Timer:
    def __init__(self, delay: float, fn: Callable[..., Any], args: tuple = ()) -> None:
        self.at, self.fn, self.args = Clock.now + delay, fn, args
        self.daemon = False
        self.state = 'new'

    def start(self) -> None:
        self.state = 'waiting'
        TIMERS.append(self)

    def cancel(self) -> None:
        self.state = 'cancelled'

    def is_alive(self) -> bool:
        return self.state == 'waiting'


def advance(seconds: float) -> None:
    Clock.now += seconds
    for timer in [t for t in TIMERS if t.state == 'waiting' and t.at <= Clock.now]:
        timer.state = 'done'
        timer.fn(*timer.args)


speech.time = types.SimpleNamespace(monotonic=Clock.monotonic)
speech.threading = types.SimpleNamespace(
    Timer=Timer, Thread=speech.threading.Thread, Lock=speech.threading.Lock
)


def spoken() -> List[str]:
    return [line for line in LINES if line.startswith('SPEAK')]


class SpeechTest(unittest.TestCase):
    def setUp(self) -> None:
        speech.modes.speech = 'hover'
        speech.modes.braille = 'concise'
        speech._state.sapi_proc = None
        speech._state.backend_name = 'log-only'
        speech.stop()
        advance(10)
        speech._state.captured_text = None
        speech._state.last_repeated = None
        del LINES[:]

    def test_pure_helpers(self) -> None:
        self.assertTrue(speech._adds_nothing('Hunger', 'Hunger. 80%'))
        self.assertFalse(speech._adds_nothing('Hunger. 80%', 'Hunger. 80%'))
        self.assertFalse(speech._adds_nothing('Thirst', 'Hunger. 80%'))
        self.assertEqual(speech._strip_heard('Cheerful. Happy sim', 'Trait. Cheerful'), 'Happy sim')
        self.assertIsNone(speech._strip_heard('Cheerful', 'Cheerful'))
        self.assertEqual(speech._braille_text('Fishing. 4. Catch fish'), 'Fishing')
        self.assertEqual(speech._caption_encode('a b|é~'), 'a%20b%7C%C3%A9~')

    def test_burst_keeps_the_newest(self) -> None:
        speech.speak('One', source='widget')
        speech.speak('Two', source='widget')
        speech.speak('Three', source='widget')
        self.assertEqual(spoken(), ['SPEAK[wi]: One'])
        advance(0.2)
        self.assertEqual(spoken(), ['SPEAK[wi]: One', 'SPEAK[wi]: Three'])

    def test_tooltip_queues_after_label_and_drops_what_was_heard(self) -> None:
        speech.speak('Cheerful', source='widget')
        advance(0.5)
        speech.speak('Cheerful. Happy sim', source='tooltip')
        self.assertEqual(spoken(), ['SPEAK[wi]: Cheerful', 'SPEAK[tq]: Happy sim'])

    def test_read_key_reads_then_stops(self) -> None:
        speech.modes.speech = 'manual'
        speech.speak('Fishing. 4', source='widget')
        self.assertEqual(spoken(), [])
        self.assertIn('CAPTURE[manual/w]: Fishing. 4', LINES)
        speech.repeat_last()
        self.assertEqual(spoken(), ['SPEAK[wi]: Fishing. 4'])
        advance(0.1)
        speech.repeat_last()  # still speaking: this press stops
        self.assertEqual(len(spoken()), 1)
        self.assertIsNone(speech._state.last_repeated)

    def test_explicit_speech_is_never_the_capture(self) -> None:
        speech.speak('Hunger. 80%', source='widget')
        speech.speak('Saved.', explicit=True)
        self.assertEqual(speech._state.captured_text, 'Hunger. 80%')
        self.assertEqual(spoken()[-1], 'SPEAK[tq]: Saved.')  # queued after the label

    def test_progressive_body_follows_unless_interrupted(self) -> None:
        speech.speak_progressive('12:00', 'Sunny, 20 degrees', delay=1.0)
        advance(1.1)
        self.assertEqual(spoken(), ['SPEAK[ti]: 12:00', 'SPEAK[ti]: Sunny, 20 degrees'])
        speech.speak_progressive('13:00', 'Rain')
        speech.speak('Other', explicit=True)
        advance(2)
        self.assertNotIn('SPEAK[ti]: Rain', spoken())

    def test_set_mode(self) -> None:
        import simsight_settings as settings

        saved = settings.set
        settings.set = lambda name, value: True
        try:
            self.assertFalse(speech.set_mode('loud'))
            self.assertTrue(speech.set_mode('beeps'))
            self.assertEqual(speech.modes.speech, 'beeps')
            self.assertEqual(spoken()[-1], 'SPEAK[wi]: Speech beeps.')
        finally:
            settings.set = saved


if __name__ == '__main__':
    unittest.main()

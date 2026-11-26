"""Step-by-step build jobs (simsight_steps): the Mac runs them on the Plus pump
with no thread, Windows on a worker thread; both must do the same game calls
and say the same things in the same order, with the same waits. A fake clock
and a fake catalog; nothing of the game runs."""

import threading
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()
SAID = []
module('simsight_speech', speak=lambda text, **k: SAID.append(text))
module('sims4').commands = module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('zone', Zone=type('Zone', (), {'on_loading_screen_animation_finished': lambda self: None}))

import simsight_log  # noqa: E402
import simsight_plus_build as build  # noqa: E402
import simsight_plus_core as core  # noqa: E402
import simsight_plus_furnish as furnish  # noqa: E402
import simsight_steps as steps  # noqa: E402

ERRORS = []
simsight_log._write = ERRORS.append


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


CLOCK = Clock()


def later_start():
    """A round time after every earlier test's (the pump's tickers never go back)."""
    CLOCK.now = float(int(CLOCK.now) + 100)
    return CLOCK.now


steps.time = types.SimpleNamespace(monotonic=CLOCK, sleep=None)
core.time = types.SimpleNamespace(monotonic=CLOCK, strftime=lambda f: '00:00:00')


def pump_until_idle(limit=2000):
    """The Plus pump on the Mac: drain every 0.1 s until no job is left."""
    for _ in range(limit):
        core._drain()
        if not steps._pump.tasks and not core._queue:
            return
        CLOCK.now += 0.1
    raise AssertionError('jobs still running')


class World:
    """The household's money, a catalog that answers, and a log of what happened."""

    def __init__(self, catalog, prices_paid):
        self.events = []
        self.money = 10000
        self.catalog = catalog  # tags -> [(name, price, colors)]
        self.pay = list(prices_paid)  # what each place() costs; 0: the game refused it
        self.selected = None

    def send_ui(self, msg):
        parts = msg.split('|')
        # The catalog serial counts every request of the run; leave it out.
        self.events.append(('ui', '|'.join(parts[:5]) if parts[2] == 'catalog' else msg))
        if parts[2] == 'catalog':
            items = self.catalog.get(parts[4], [])
            encoded = '^'.join(f'{n}~{p}~{c}' for (n, p, c) in items)
            build.flash_reply_base(f'cat|{parts[5]}|0|{len(items)}|{encoded}')
            build.flash_reply_base(f'catend|{parts[5]}')
        elif parts[2] == 'select':
            self.selected = int(parts[3])

    def funds(self):
        self.events.append(('funds', self.money))
        return self.money

    def place(self, spot):
        cost = self.pay.pop(0) if self.pay else 0
        self.events.append(('place', spot, self.selected, cost))
        self.money -= cost


BEDS = [('Cheap Bed', 300, 1), ('Killer Queen Double Bed', 900, 2)]
LAMPS = [('Bunk Lamp', 50, 1), ('Wall Lamp', 80, 1)]


class FurnishTest(unittest.TestCase):
    def setUp(self):
        self.saved = {
            n: getattr(furnish, n) for n in ('ITEMS', '_spots', '_placer', '_face_into_room')
        }
        self.saved_build = {n: getattr(build, n) for n in ('send_ui', 'funds')}
        furnish.ITEMS = {
            'double bed': ('Double bed', 'BEDS', ('Killer Queen Double Bed',), ('single',)),
            'lamp': ('Lamp', 'LAMPS', ('None of these',), ('bunk',)),
            'rug': ('Rug', 'RUGS', (), ()),  # the catalog lists none
        }
        furnish._spots = lambda room: ([(1.0, 2.0, 0.0), (3.0, 4.0, 1.5)], (2.0, 3.0, 0.0))
        del SAID[:]
        del core._queue[:]
        build.busy[0] = True

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(furnish, n, v)
        for n, v in self.saved_build.items():
            setattr(build, n, v)
        build.busy[0] = False

    def world(self):
        w = World({'BEDS': BEDS, 'LAMPS': LAMPS}, [900, 0, 0, 80])
        build.send_ui = w.send_ui
        build.funds = w.funds
        furnish._placer = lambda place, room, spot: lambda: w.place(spot)
        furnish._face_into_room = lambda place, room: w.events.append(('face',))
        return w

    def job(self):
        room = types.SimpleNamespace(name='bedroom', level=0)
        return furnish._furnish_steps(None, room, ['double bed', 'lamp', 'rug'])

    def on_windows(self):
        """The worker-thread driver, its sleeps and main-thread calls made at once."""
        w = self.world()
        steps.time.sleep = lambda s: w.events.append(('sleep', s))
        steps.on_main, saved = (lambda fn, *a: fn(*a)), steps.on_main
        core.post, saved_post = (lambda fn, *a: fn(*a)), core.post
        try:
            steps.run(self.job())
        finally:
            steps.on_main, core.post = saved, saved_post
        return w.events, list(SAID)

    def on_mac(self):
        w = self.world()
        start = CLOCK.now
        steps.IS_MAC, saved = True, steps.IS_MAC
        try:
            steps.start('furnish test', self.job())
            pump_until_idle()
        finally:
            steps.IS_MAC = saved
        return w.events, list(SAID), CLOCK.now - start

    def test_same_calls_and_words_on_both_drivers(self):
        mac_events, mac_said, took = self.on_mac()
        del SAID[:]
        build.busy[0] = True
        win_events, win_said = self.on_windows()
        waits = [e for e in win_events if e[0] == 'sleep']
        self.assertEqual([e for e in win_events if e[0] != 'sleep'], mac_events)
        self.assertEqual(win_said, mac_said)
        # The Mac waited at least as long as the worker thread slept.
        self.assertGreaterEqual(took + 1e-6, sum(s for (_k, s) in waits))

    def test_what_the_job_does(self):
        events, said, _took = self.on_mac()
        places = [e for e in events if e[0] == 'place']
        # The bed: preferred name (row 1), first try in the middle, bought.
        self.assertEqual(places[0], ('place', (2.0, 3.0, 0.0), 1, 900))
        # The lamp: the bunk lamp is ruled out, so the wall lamp (row 1);
        # two refused tries along the walls, then bought.
        self.assertEqual(
            [p[1:] for p in places[1:]],
            [((1.0, 2.0, 0.0), 1, 0), ((3.0, 4.0, 1.5), 1, 0), ((1.0, 2.0, 0.0), 1, 80)],
        )
        self.assertEqual(events.count(('face',)), 2)
        self.assertEqual(
            said,
            [
                (
                    'Done furnishing the bedroom. Bought: double bed, lamp. It cost 980 simoleons.'
                    ' Could not place: rug. The room may be too full.'
                )
            ],
        )
        self.assertFalse(build.busy[0])
        self.assertEqual(
            [e[1] for e in events if e[0] == 'ui' and '|select|' in e[1]],
            ['A11y|plus|select|1', 'A11y|plus|select|1'],
        )


class DriverTest(unittest.TestCase):
    def setUp(self):
        del core._queue[:]
        steps._pump.tasks[:] = []
        self.saved = steps.IS_MAC
        steps.IS_MAC = True

    def tearDown(self):
        steps.IS_MAC = self.saved

    def test_sleep_waits_for_the_clock(self):
        seen = []

        def job():
            seen.append(('start', CLOCK.now))
            yield steps.Sleep(0.25)
            seen.append(('woke', CLOCK.now))

        t0 = later_start()
        steps.start('sleep', job())
        self.assertEqual(seen, [])  # nothing runs before the next pump tick
        core._drain()
        self.assertEqual(seen, [('start', t0)])
        CLOCK.now = t0 + 0.2
        core._drain()
        self.assertEqual(len(seen), 1)
        CLOCK.now = t0 + 0.25
        core._drain()
        self.assertEqual(seen[-1], ('woke', t0 + 0.25))
        self.assertEqual(steps._pump.tasks, [])

    def test_on_main_runs_at_once_and_a_raise_gives_none(self):
        got = []

        def boom():
            raise ValueError('x')

        def job():
            got.append((yield steps.OnMain(lambda a, b: a + b, 2, 3)))
            got.append((yield steps.OnMain(boom)))

        steps.start('main', job())
        core._drain()
        self.assertEqual(got, [5, None])

    def test_wait_event_set_or_timed_out(self):
        got = []
        ev = threading.Event()

        def job():
            got.append((yield steps.WaitEvent(ev, 1.0)))
            got.append((yield steps.WaitEvent(threading.Event(), 0.5)))

        t0 = later_start()
        steps.start('wait', job())
        core._drain()
        self.assertEqual(got, [])
        ev.set()
        CLOCK.now = t0 + 0.1
        core._drain()
        self.assertEqual(got, [True])
        CLOCK.now = t0 + 0.5
        core._drain()
        self.assertEqual(got, [True])
        CLOCK.now = t0 + 0.75
        core._drain()
        self.assertEqual(got, [True, False])

    def test_a_job_that_raises_is_logged_and_dropped(self):
        def job():
            yield steps.Sleep(0)
            raise KeyError('k')

        del ERRORS[:]
        steps.start('bad', job())
        core._drain()
        self.assertEqual(steps._pump.tasks, [])
        self.assertTrue(any('steps: bad' in line for line in ERRORS))

    def test_after_posts_once_the_time_is_up(self):
        posted = []
        t0 = later_start()
        steps.after(4.0, lambda fn, *a: posted.append((fn, a)), len, 'abc')
        core._drain()
        CLOCK.now = t0 + 3.9
        core._drain()
        self.assertEqual(posted, [])
        CLOCK.now = t0 + 4.0
        core._drain()
        self.assertEqual(posted, [(len, ('abc',))])

    def test_windows_run_returns_the_job_value(self):
        slept = []
        steps.time.sleep = slept.append

        def job():
            yield steps.Sleep(0.5)
            value = yield steps.WaitEvent(threading.Event(), 0)
            return ('done', value)

        self.assertEqual(steps.run(job()), ('done', False))
        self.assertEqual(slept, [0.5])


class NoMouseTest(unittest.TestCase):
    def test_mac_refuses_pointing_jobs_without_a_thread(self):
        saved = steps.IS_MAC
        del SAID[:]
        threads = threading.active_count()
        try:
            steps.IS_MAC = True
            build.busy[0] = False
            build.run_job([(1.0, 2.0)], 0, lambda s: None, 'test')
            self.assertEqual(SAID, [build.NO_MOUSE_TEXT])
            self.assertFalse(build.busy[0])
            self.assertEqual(threading.active_count(), threads)
            steps.IS_MAC = False
            self.assertFalse(build.no_mouse())
        finally:
            steps.IS_MAC = saved


if __name__ == '__main__':
    unittest.main()

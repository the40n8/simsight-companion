"""The simulation-thread pump: refuses work when not running, keeps the
queue bounded, and one failing job never stops the others."""

import unittest
from typing import NoReturn

import fakegame

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

ERRORS = []
simsight_log._write = lambda text: None
simsight_log.error = lambda where, exc=None: ERRORS.append(where)

import simsight_pump as pump  # noqa: E402


class PumpTest(unittest.TestCase):
    def setUp(self) -> None:
        del ERRORS[:]
        del pump._queue[:]
        pump._state.handle = None

    def run_alive(self) -> None:
        pump._state.handle = object()
        pump._state.last_drain = pump.time.monotonic()

    def test_refused_when_not_started(self) -> None:
        self.assertEqual(pump.status(), 'not started')
        self.assertFalse(pump.post(print, 'x'))
        self.assertEqual(pump._queue, [])

    def test_drain_runs_in_order_and_logs_failures(self) -> None:
        self.run_alive()
        self.assertEqual(pump.status(), 'alive')
        done = []

        def broken() -> NoReturn:
            raise RuntimeError('boom')

        self.assertTrue(pump.post(done.append, 1))
        self.assertTrue(pump.post(broken))
        self.assertTrue(pump.post(done.append, 2))
        pump._drain()
        self.assertEqual(done, [1, 2])
        self.assertEqual(ERRORS, ['pump: broken'])
        self.assertEqual(pump._queue, [])

    def test_queue_is_bounded(self) -> None:
        self.run_alive()
        for index in range(pump._MAX_QUEUED + 3):
            pump.post(print, index)
        self.assertEqual(len(pump._queue), pump._MAX_QUEUED)
        self.assertEqual(pump._queue[0][1], (3,))

    def test_stale_without_drains(self) -> None:
        self.run_alive()
        pump._state.last_drain -= pump._STALE_SECONDS + 1
        self.assertEqual(pump.status(), 'stale')
        self.assertFalse(pump.post(print))


if __name__ == '__main__':
    unittest.main()

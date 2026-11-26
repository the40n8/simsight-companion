"""Small pure helpers of the game-window and event-goal readers."""

import unittest

import fakegame

fakegame.scripts_on_path()

import simsight_dialogs as dialogs  # noqa: E402
import simsight_event_goals as goals  # noqa: E402


class Price(unittest.TestCase):
    def test_price_before_the_description(self):
        self.assertEqual(dialogs._with_price('Tasty', 12), '§12 · Tasty')
        self.assertEqual(dialogs._with_price('', 12), '§12')

    def test_no_price_or_already_priced(self):
        self.assertEqual(dialogs._with_price('Tasty', 0), 'Tasty')
        self.assertEqual(dialogs._with_price('§5 each', 12), '§5 each')

    def test_row_ids(self):
        self.assertEqual(dialogs._row_ids(['11', 'x', 3]), [11, 'x', 3])


class EventGoalsReply(unittest.TestCase):
    JOB = goals._Job(serial=3, zone=77, event=9, until=0.0)

    def test_status_of_this_job(self):
        self.assertEqual(goals.parse_reply('3|77|opened', self.JOB), 'opened')

    def test_other_job_or_bad_reply(self):
        self.assertIsNone(goals.parse_reply('2|77|opened', self.JOB))
        self.assertIsNone(goals.parse_reply('3|78|opened', self.JOB))
        self.assertIsNone(goals.parse_reply('3|77', self.JOB))
        self.assertIsNone(goals.parse_reply('3|77|opened', None))


if __name__ == '__main__':
    unittest.main()

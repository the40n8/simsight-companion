"""Relationship labels in the right gender: the game's text for a label
describes the other Sim as token 0, {M0.Father}{F0.Mother}."""

import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()
module(
    'simsight_speech',
    speak=lambda *a, **k: None,
    log_line=lambda *a: None,
    MODS_FOLDER='/nonexistent',
    mods_root=lambda: '/nonexistent',
)
module('sims4')
module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('sims4.core_services', on_tick=lambda *a, **k: None)
module('simsight_scanner', loc_cache={7: '{M0.Father}{F0.Mother}'})

import simsight_bridge as bridge  # noqa: E402


class Token:
    def __init__(self, sim):
        self.is_female = sim.is_female


class Loc:
    hash = 7

    def __init__(self, tokens):
        self.tokens = [Token(t) for t in tokens]


class Bit:
    # A tuned string factory: called with the Sims it is about.
    @staticmethod
    def display_name(*sims):
        return Loc(sims)


def sim(female):
    return types.SimpleNamespace(is_female=female, first_name='A', last_name='B')


class Labels(unittest.TestCase):
    def test_mother(self):
        self.assertEqual(bridge.sim_life._bit_label(Bit, sim(True), sim(False)), 'Mother')

    def test_father(self):
        self.assertEqual(bridge.sim_life._bit_label(Bit, sim(False), sim(True)), 'Father')


class Minutes:
    def __init__(self, minutes):
        self.minutes = minutes

    def in_minutes(self):
        return self.minutes


class DateAndTime(int):
    # The game's: ticks; subtracting gives a time span (here 1 tick = 1 minute).
    def __sub__(self, other):
        return Minutes(int(self) - int(other))


module('date_and_time', DateAndTime=DateAndTime)


class PregnantBuff:
    """A moodlet whose name says mother or father for the Sim who has it."""

    visible = True
    mood_type = None
    mood_weight = 1
    buff_description = None

    def __init__(self, ends):
        self.ends = ends

    buff_type = None

    @staticmethod
    def buff_name(*sims):
        return Loc(sims)

    def get_timeout_time(self):
        return (self.ends, 1.0)


class Moodlets(unittest.TestCase):
    def test_name_is_for_the_sim_who_has_it(self):
        mom = bridge.sim_status._moodlet(PregnantBuff(0), DateAndTime(0), sim(True))
        dad = bridge.sim_status._moodlet(PregnantBuff(0), DateAndTime(0), sim(False))
        self.assertEqual((mom['name'], dad['name']), ('Mother', 'Father'))

    def test_minutes_left(self):
        self.assertEqual(
            bridge.sim_status._moodlet(PregnantBuff(0), DateAndTime(5), sim(True))['minutesLeft'],
            -1,
        )
        self.assertEqual(
            bridge.sim_status._moodlet(PregnantBuff(95), DateAndTime(5), sim(True))['minutesLeft'],
            90,
        )


if __name__ == '__main__':
    unittest.main(verbosity=0)

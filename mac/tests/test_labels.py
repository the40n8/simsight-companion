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
    _mods_root=lambda: '/nonexistent',
)
module('sims4')
module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('sims4.core_services', on_tick=lambda *a, **k: None)
module('simsight_scanner', _loc_cache={7: '{M0.Father}{F0.Mother}'})

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
        self.assertEqual(bridge._bit_label(Bit, sim(True), sim(False)), 'Mother')

    def test_father(self):
        self.assertEqual(bridge._bit_label(Bit, sim(False), sim(True)), 'Father')


if __name__ == '__main__':
    unittest.main(verbosity=0)

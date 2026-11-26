"""Build/Buy console commands: names and argument types as the game reads them.

The game converts each console argument with the function's annotation
(sims4.commands.parse_args). The modules use `from __future__ import
annotations`, which turns annotations into strings: these checks make sure
every command still hands the game real types, for exactly the arguments
it typed before (never `_connection`)."""

import inspect
import os
import tempfile
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

REGISTERED = {}


def command(*names, command_type=None):
    def register(func):
        spec = inspect.getfullargspec(func)
        REGISTERED[names[0]] = (names, spec.args, dict(spec.annotations))
        return func

    return register


sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands',
    Command=command,
    CommandType=types.SimpleNamespace(Live='live'),
    CheatOutput=lambda connection: print,
)
module('build_buy', register_build_buy_exit_callback=id, register_build_buy_enter_callback=id)
module('simsight_speech', speak=lambda *a, **k: None, log_line=lambda *a: None)
module('simsight_lang', _detect_lang=lambda: 'en', t=lambda text: text)
module('simsight_plus_build')
module('simsight_house_planner')

import simsight_log  # noqa: E402

simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

import simsight_build  # noqa: E402, F401
import simsight_place  # noqa: E402, F401

WINDOWS = {
    'simsight.coords_enc': (
        ('simsight.coords_enc', 'a11y.coords_enc'),
        ['text', '_connection'],
        {'text': str},
    ),
    'simsight.buildcoords': (
        ('simsight.buildcoords', 'a11y.buildcoords'),
        ['delay', '_connection'],
        {'delay': float},
    ),
    'simsight.inspect_display': (
        ('simsight.inspect_display', 'a11y.inspect_display'),
        ['delay', '_connection'],
        {'delay': float},
    ),
    'simsight.floor_native': (
        ('simsight.floor_native', 'a11y.floor_native'),
        ['zone', 'sequence', 'status', 'current', 'minimum', 'maximum', '_connection'],
        {
            'zone': str,
            'sequence': int,
            'status': str,
            'current': int,
            'minimum': int,
            'maximum': int,
        },
    ),
}
try:  # Windows-only modules: ctypes, which this Python has and the Mac game lacks
    import simsight_build_coordinates  # noqa: F401
    import simsight_build_helpers

    simsight_build_helpers.install()
    HAVE_WINDOWS = True
except ImportError:
    HAVE_WINDOWS = False

EXPECTED = {
    'simsight.build': (
        ('simsight.build', 'a11y.build'),
        ['action', '_connection'],
        {'action': str},
    ),
    'simsight.buildinfo': (
        ('simsight.buildinfo', 'a11y.buildinfo'),
        ['encoded', '_connection'],
        {'encoded': str},
    ),
    'simsight.buildlog': (
        ('simsight.buildlog', 'a11y.buildlog'),
        ['encoded', '_connection'],
        {'encoded': str},
    ),
    'simsight.buildstate': (
        ('simsight.buildstate', 'a11y.buildstate'),
        ['encoded', '_connection'],
        {'encoded': str},
    ),
    'simsight.buildproduct': (
        ('simsight.buildproduct', 'a11y.buildproduct'),
        ['encoded', '_connection'],
        {'encoded': str},
    ),
    'simsight.catalogleft': (
        ('simsight.catalogleft', 'a11y.catalogleft'),
        ['_state', '_connection'],
        {'_state': str},
    ),
    'simsight.buildhand': (
        ('simsight.buildhand', 'a11y.buildhand'),
        ['encoded', '_connection'],
        {'encoded': str},
    ),
}


class Commands(unittest.TestCase):
    def check(self, expected):
        for name, (names, args, annotations) in expected.items():
            with self.subTest(command=name):
                self.assertIn(name, REGISTERED)
                self.assertEqual(REGISTERED[name], (names, args, annotations))
                for kind in annotations.values():
                    self.assertIsInstance(kind, type)
                    kind('1')  # the game calls it on the typed text

    def test_mac_and_windows(self):
        self.check(EXPECTED)

    @unittest.skipUnless(HAVE_WINDOWS, 'needs ctypes')
    def test_windows_only(self):
        self.check(WINDOWS)


if __name__ == '__main__':
    unittest.main()

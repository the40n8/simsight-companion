"""Stand-ins for the game's modules, so Mac-only mod code can be checked
offline with the game's Python 3.7 (mac/test.sh). Not the real game: these
checks cover our own logic, not EA's APIs."""

import sys
import types


class V:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def scripts_on_path():
    import os
    import tempfile

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, '..', '..', 'Scripts'))
    # The mod logs next to itself: in tests, into a temporary folder instead.
    import simsight_log

    simsight_log.PATH = os.path.join(tempfile.mkdtemp(prefix='simsight-test-'), 'SimSight.log')

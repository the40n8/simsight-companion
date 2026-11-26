"""The companion on Windows, checked offline: the mod module loaded as if on
Windows, with stand-ins for the pipe functions (msvcrt, PeekNamedPipe).
Covers our own logic only: starting the bridge without its own voice or a
console window, reading commands without blocking, copying spoken lines to
the phone and speaking the bridge's announcements."""

import json
import os
import sys
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()
sys.platform = 'win32'  # this test process only

SPOKEN, LOGGED = [], []
module(
    'simsight_speech',
    speak=lambda text, **kw: SPOKEN.append((text, kw)),
    log_line=LOGGED.append,
    MODS_FOLDER='/nonexistent',
    _mods_root=lambda: '/nonexistent',
)
module('sims4')
module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('sims4.core_services', on_tick=lambda *a, **k: None)

WAITING = {'bytes': 0}


class Kernel32:
    def PeekNamedPipe(self, handle, buf, size, read, avail, left):
        avail.value = WAITING['bytes']
        return 1


class CULong:
    def __init__(self, value=0):
        self.value = value


module('msvcrt', get_osfhandle=lambda fd: 1000 + fd)
module(
    'ctypes',
    windll=types.SimpleNamespace(kernel32=Kernel32()),
    c_ulong=CULong,
    c_void_p=lambda v: v,
    byref=lambda x: x,
)

import simsight_bridge as bridge  # noqa: E402


class FakeProc:
    def __init__(self, read_fd):
        self.stdout = types.SimpleNamespace(fileno=lambda: read_fd)
        self.pid = 1

    def poll(self):
        return None


class WindowsCompanion(unittest.TestCase):
    def setUp(self):
        SPOKEN.clear()

    def test_names(self):
        self.assertTrue(bridge.IS_WINDOWS)
        self.assertEqual(bridge.BRIDGE, 'simsight-bridge.exe')

    def test_start_without_voice_or_window(self):
        calls = []

        def popen(args, **kw):
            calls.append((args, kw))
            return FakeProc(0)

        sent = []
        # The Mac's own subprocess cannot load as Windows: a stand-in.
        fake = types.SimpleNamespace(Popen=popen, PIPE=-1, DEVNULL=-3)
        old = (bridge._find, bridge.subprocess, bridge.send, bridge._state.proc)
        try:
            bridge._find = lambda name: (
                'C:\\Mods\\SimSight\\' + name if name == bridge.BRIDGE else None
            )
            bridge.subprocess = fake
            bridge.send = sent.append
            bridge._state.proc = None
            self.assertTrue(bridge.start())
        finally:
            bridge._find, bridge.subprocess, bridge.send, bridge._state.proc = old
        args, kw = calls[0]
        self.assertEqual(args[1:], ['--speaker', 'none'])
        self.assertEqual(kw.get('creationflags'), bridge.CREATE_NO_WINDOW)
        self.assertEqual(sent[0]['hello']['modVersion'], 'windows')

    def test_reads_commands_without_blocking(self):
        r, w = os.pipe()
        sent = []
        old = (bridge._state.proc, bridge.send, bridge._state.read_buf)
        try:
            bridge._state.proc = FakeProc(r)
            bridge.send = sent.append
            bridge._state.read_buf = b''
            WAITING['bytes'] = 0
            bridge._read_commands()  # nothing waiting: returns at once
            self.assertEqual(sent, [])
            line = json.dumps({'id': '7', 'say': {'text': 'Pairing code: 1 2 3'}}).encode() + b'\n'
            os.write(w, line)
            WAITING['bytes'] = len(line)
            bridge._read_commands()
        finally:
            bridge._state.proc, bridge.send, bridge._state.read_buf = old
            os.close(r)
            os.close(w)
        self.assertEqual(SPOKEN[0][0], 'Pairing code: 1 2 3')
        self.assertTrue(SPOKEN[0][1].get('explicit'))
        self.assertEqual(sent, [{'result': {'id': '7', 'outcome': {'ok': True}}}])

    def test_mirror_for_the_phone(self):
        lines = []
        old = (bridge.alive, bridge.write_line)
        try:
            bridge.alive = lambda: True
            bridge.write_line = lambda data: lines.append(data) or True
            bridge.mirror('Hunger low. 20%', False)
        finally:
            bridge.alive, bridge.write_line = old
        self.assertEqual(lines, [b'QHunger%20low.%2020%25'])


if __name__ == '__main__':
    unittest.main(verbosity=0)

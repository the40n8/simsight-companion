"""The simsight-bridge process: starting it, the lines written to it
(NDJSON messages, speech) and reading what it writes back without
blocking, and the bridge's own pages opened in the browser."""

from __future__ import annotations


import json
import os
import sys
import threading
import time
from typing import TYPE_CHECKING, Any


try:
    import subprocess
except ImportError:  # not every Python the game ships has it: no companion then
    subprocess = None  # type: ignore[assignment]


from simsight_commands import console_command
import simsight_log
import simsight_speech as speech

from .state import _state, log


if TYPE_CHECKING:
    from .state import Json


IS_MAC = sys.platform == 'darwin'
IS_WINDOWS = sys.platform == 'win32'
BRIDGE = 'simsight-bridge.exe' if IS_WINDOWS else 'simsight-bridge'
CREATE_NO_WINDOW = 0x08000000  # Windows: no console window for the bridge
SPEAKER = 'simsight-say'


RESPAWN_DELAY = 2.0  # seconds before restarting a bridge that exited
READ_SIZE = 65536


_write_lock = threading.Lock()


# ---------------------------------------------------------------- process


def _find(name: str) -> str | None:
    for folder in (speech.MODS_FOLDER, os.path.join(speech.mods_root(), 'SimSight')):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def _bridge_args(path: str) -> tuple[list[str], dict[str, Any]]:
    """Command line and extra Popen options for the bridge."""
    if IS_WINDOWS:
        # The screen reader speaks on Windows: the bridge only listens.
        return [path, '--speaker', 'none'], {'creationflags': CREATE_NO_WINDOW}
    speaker = _find(SPEAKER)
    return ([path, '--speaker', speaker] if speaker else [path]), {}


def start() -> bool:
    """Start the bridge if needed. Returns True when it is running."""
    if _state.proc is not None:
        if _state.proc.poll() is None:
            return True
        # It exited (crash, or replaced during development): start it again,
        # at most every RESPAWN_DELAY seconds.
        if time.monotonic() - _state.last_spawn < RESPAWN_DELAY:
            return False
        log(f'restarting (previous one exited with {_state.proc.returncode})')
        _state.proc = None
        _state.read_buf = b''
        _state.last_state_json = None  # the new bridge needs a full state
    _state.last_spawn = time.monotonic()
    path = _find(BRIDGE)
    if not (IS_MAC or IS_WINDOWS) or subprocess is None or path is None:
        return False
    args, extra = _bridge_args(path)
    try:
        _state.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **extra,
        )
        if not IS_WINDOWS:
            os.set_blocking(_state.proc.stdout.fileno(), False)
    except Exception:
        simsight_log.error('bridge: start')
        _state.proc = None
        return False
    log(f'started (pid {_state.proc.pid})')
    send(
        {
            'hello': {
                'modVersion': 'windows' if IS_WINDOWS else 'mac',
                'gameVersion': _game_version(),
            }
        }
    )
    if _state.voice is not None:
        send({'voice': _state.voice})
    return True


def alive() -> bool:
    return _state.proc is not None and _state.proc.poll() is None


def write_line(data: bytes) -> bool:
    """One complete line to the bridge. Safe from any thread (speech timers)."""
    if not alive():
        return False
    with _write_lock:
        try:
            _state.proc.stdin.write(data + b'\n')
            _state.proc.stdin.flush()
        except (OSError, ValueError) as ex:  # the bridge exited: alive() notices
            log(f'write failed: {ex!r}')
            return False
        return True


def send(message: Json) -> bool:
    return write_line(
        json.dumps(message, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    )


def mirror(text: str, interrupt: bool) -> bool:
    """Windows: a line the screen reader just said, for the phone's history."""
    if not alive():
        return False
    from urllib.parse import quote

    return write_line((('I' if interrupt else 'Q') + quote(text)).encode('ascii'))


def set_voice(rate: Any, volume: Any, name: str | None, lang: str | None) -> None:
    _state.voice = {
        'rate': int(rate),
        'volume': int(volume),
        'name': name or '',
        'lang': lang or '',
    }
    send({'voice': _state.voice})


class SpeechPipe:
    """Stands in for the fallback-voice process the speech module expects:
    `.stdin.write()/.flush()` send lines to the bridge, `.kill()` (used when
    voice settings change) leaves the bridge running; the speech module then
    calls set_voice again with the new settings."""

    def __init__(self) -> None:
        self.stdin = self
        self._buf = b''

    def write(self, data: bytes) -> None:
        self._buf += data
        while b'\n' in self._buf:
            line, self._buf = self._buf.split(b'\n', 1)
            write_line(line)

    def flush(self) -> None:
        """Lines are sent as soon as they are complete."""

    def poll(self) -> int | None:
        return None if alive() else 1

    def kill(self) -> None:
        """The bridge keeps running: it serves the phone too."""

    def wait(self, timeout: float | None = None) -> int | None:  # noqa: ARG002 - Popen.wait's signature
        """Nothing to wait for (kill() leaves the bridge running)."""
        return self.poll()


def _game_version() -> str:
    try:
        import sims4.common
    except ImportError:
        return ''  # outside the game (offline checks)
    get_version = getattr(sims4.common, 'get_version', None)
    try:
        return str(get_version() if get_version is not None else '')
    except Exception:
        simsight_log.error('bridge: game version')
        return ''


def _waiting_bytes() -> int:
    """Windows pipes cannot be made non-blocking: ask how much is waiting
    (PeekNamedPipe), so reading never blocks the game's tick."""
    handle = _state.msvcrt.get_osfhandle(_state.proc.stdout.fileno())
    avail = _state.ctypes.c_ulong(0)
    if not _state.ctypes.windll.kernel32.PeekNamedPipe(
        _state.ctypes.c_void_p(handle), None, 0, None, _state.ctypes.byref(avail), None
    ):
        return 0  # closed: the bridge exited (alive() notices)
    return int(avail.value)


def _read_chunk() -> bytes:
    """What the bridge wrote since the last poll, without blocking."""
    fd = _state.proc.stdout.fileno()
    if not IS_WINDOWS:
        return os.read(fd, READ_SIZE)
    waiting = _waiting_bytes()
    return os.read(fd, min(waiting, READ_SIZE)) if waiting else b''


# ---------------------------------------------------------------- pairing page

PAIR_URL = 'http://127.0.0.1:47800/pair'
SETUP_URL = 'http://127.0.0.1:47800/setup'


def _open_on_mac(url: str, said: str) -> bool:
    """Open one of the bridge's pages in the computer's browser (in front of the game)."""
    try:
        if IS_WINDOWS:
            os.startfile(url)  # type: ignore[attr-defined]  # Windows only
        elif IS_MAC and subprocess is not None:
            subprocess.Popen(
                ['/usr/bin/open', url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            return False
        speech.speak(said, explicit=True)
    except Exception:
        simsight_log.error(f'bridge: open {url}')
        return False
    return True


def open_pairing_page() -> bool:
    """Show the phone link's QR code and pairing code."""
    return _open_on_mac(PAIR_URL, 'Phone link opened in the browser.')


def open_setup_page() -> bool:
    """SimSight setup: phone address, meeting point, disconnect phones."""
    return _open_on_mac(SETUP_URL, 'SimSight setup opened in the browser.')


try:
    import sims4.commands

    @console_command(
        'simsight.companion', 'a11y.companion', command_type=sims4.commands.CommandType.Live
    )
    def a11y_companion(_connection: Any = None) -> bool:
        """Open the phone companion's QR code on the Mac."""
        return open_pairing_page()

    @console_command('simsight.setup', command_type=sims4.commands.CommandType.Live)
    def simsight_setup(_connection: Any = None) -> bool:
        """Open SimSight setup on the Mac (phone address, meeting point)."""
        return open_setup_page()
except Exception:
    simsight_log.error('bridge: companion commands')

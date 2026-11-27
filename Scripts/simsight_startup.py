"""Startup-only speech transport (V11): Flash writes, Python only listens.

Before a save loads there is no simulation for PostServerCommand to reach, so
the shared TooltipManager writes its narration lines to a loopback listener
owned by this module. Nothing is ever written back: the V10 request/response
protocol (challenge, config, navigation, ping) coincided with five native
crashes at boot, at the main menu and in CAS, and the only common factor was
inbound socket data being delivered into the Flash VM. Navigation keys before
a save loads are handled natively in Flash; this side just speaks.

Keyboard mode is signalled by the listening port: 45993 keyboard on with
Simplified menus, 45995 keyboard on without them, 45994 speech only. The port
follows the settings while no UI is connected. Loopback connections must
belong to this game process. Only the fixed narration/state channels are
accepted; there is no command execution.
"""

from __future__ import annotations

import functools
import os
import socket
import sys
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_patch

if TYPE_CHECKING:
    from typing import Callable, Tuple

    from server.client import Client
    from zone import Zone

    Post = Tuple[str, str]  # (channel, payload)
    Queued = Tuple[int, str, str]  # (connection generation, channel, payload)
    Handler = Callable[[str], Any]

# macOS port: the peer check below needs Windows' TCP owner table (ctypes),
# and the Mac game's Python crashes when a background thread imports from a
# zip while the simulation thread runs Python. No listener thread on macOS.
IS_MAC = sys.platform == 'darwin'

PORT_KEYBOARD = 45993
PORT_SPEECH = 45994
PORT_KEYBOARD_FULL_MENUS = 45995
MAX_FRAME = 32768
MAX_QUEUE = 48
HELLO = 'HELLO|A11Y_STARTUP_V2'
CHANNELS = frozenset(
    (
        'simsight.speak_enc',
        'simsight.widget_enc',
        'simsight.focus_enc',
        'simsight.widgetname_enc',
        'simsight.dialog_enc',
        'simsight.scope_enc',
        'simsight.event_enc',
        'simsight.context_enc',
        'simsight.inspect_enc',
        'simsight.modalstate',
        'simsight.inputfence',
        'simsight.blocking',
        'simsight.clock_enc',
        'simsight.mood_enc',
        'simsight.stop',
        'simsight.mark',
    )
)
# Channels whose payload must be one of these values.
_ALLOWED_PAYLOADS = {
    'simsight.modalstate': ('00', '01', '10', '11'),
    'simsight.blocking': ('0', '1'),
    'simsight.inputfence': ('',),
}
# Kept even when the queue is full: losing one leaves the key state wrong.
_STATE_CHANNELS = ('simsight.inputfence', 'simsight.modalstate', 'simsight.blocking')
# Channel -> simsight_keys function that takes the payload.
_KEYS_HANDLERS = {
    'simsight.modalstate': 'a11y_modalstate',
    'simsight.blocking': 'a11y_blocking',
}
# Channel -> simsight_narrator function that takes the payload.
_NARRATOR_HANDLERS = {
    'simsight.speak_enc': 'a11y_speak_enc',
    'simsight.widget_enc': 'a11y_widget_enc',
    'simsight.focus_enc': 'a11y_focus_enc',
    'simsight.widgetname_enc': 'a11y_widgetname_enc',
    'simsight.dialog_enc': 'a11y_dialog_enc',
    'simsight.scope_enc': 'a11y_scope_enc',
    'simsight.event_enc': 'a11y_event_enc',
    'simsight.context_enc': 'a11y_context_enc',
    'simsight.inspect_enc': 'a11y_inspect_enc',
    'simsight.clock_enc': 'a11y_clock_enc',
    'simsight.mood_enc': 'a11y_mood_enc',
    'simsight.stop': 'a11y_stop',
}
_PORT_MEANINGS = {
    PORT_KEYBOARD: 'keyboard on, simplified menus on',
    PORT_KEYBOARD_FULL_MENUS: 'keyboard on, simplified menus off',
    PORT_SPEECH: 'speech only',
}
_CHECK_EVERY = 0.5  # seconds between settings checks while serving


class _State:
    """Shared by the listener thread and the simulation thread (under _lock
    where both touch it)."""

    def __init__(self) -> None:
        self.installed = False
        self.connected = False
        self.startup = True
        self.generation = 0
        self.next_tick = 0.0
        self.port: int | None = None
        self.wanted: int | None = (
            None  # the port the settings ask for (a fallback can serve another)
        )
        self.seen: int | None = None  # the latest settings reading, for the two-reading rule
        self.flash_startup = False


_lock = threading.Lock()
_incoming: deque[Queued] = deque()
_stop = threading.Event()
_state = _State()


def log(message: str) -> None:
    simsight_log.line('startup: ' + message)


def enabled() -> bool:
    try:
        import simsight_settings as settings

        return bool(settings.get('startup', True))
    except Exception:
        simsight_log.error('startup: reading the startup setting')
        return True


def listen_port() -> int:
    """The port tells Flash whether keyboard navigation and Simplified menus
    are enabled."""
    try:
        import simsight_settings as settings

        keyboard = bool(settings.get('keyboard', True))
        simplified = bool(settings.get('simplified_menus', True))
    except Exception:
        simsight_log.error('startup: reading the keyboard settings')
        keyboard = simplified = True
    if not keyboard:
        return PORT_SPEECH
    return PORT_KEYBOARD if simplified else PORT_KEYBOARD_FULL_MENUS


class FrameReader:
    """Splits the byte stream into newline-terminated UTF-8 lines."""

    def __init__(self) -> None:
        self.buffer = b''

    def feed(self, data: bytes) -> list[str]:
        """The complete lines in data; ValueError on bad framing."""
        self.buffer += data
        lines = []
        while b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            if len(raw) > MAX_FRAME:
                raise ValueError('frame too long')
            line = raw.decode('utf-8')
            if '\x00' in line or '\r' in line:
                raise ValueError('invalid framing')
            lines.append(line)
        if len(self.buffer) > MAX_FRAME:
            raise ValueError('unfinished frame too long')
        return lines


def parse_hello(line: str) -> str | None:
    """The text after the greeting, or None when line is not the greeting."""
    parts = line.split('|')
    if len(parts) < 2 or '|'.join(parts[:2]) != HELLO:
        return None
    return parts[2] if len(parts) > 2 else ''


def parse_post(line: str) -> Post | None:
    """(channel, payload) of an accepted POST line, else None."""
    parts = line.split('|', 2)
    if len(parts) != 3 or parts[0] != 'POST' or parts[1] not in CHANNELS:
        return None
    channel, payload = parts[1], parts[2]
    allowed = _ALLOWED_PAYLOADS.get(channel)
    if allowed is not None and payload not in allowed:
        return None
    return channel, payload


def _owner_rows() -> list[Any]:
    """Windows' IPv4 TCP owner table (GetExtendedTcpTable), or [] when it
    cannot be read."""
    if sys.platform != 'win32':
        return []  # only Windows has the table (and ctypes in the game)
    import ctypes

    class Row(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulong)
            for name in (
                'state',
                'local_address',
                'local_port',
                'remote_address',
                'remote_port',
                'pid',
            )
        ]

    api = ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    api.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    api.restype = ctypes.c_ulong
    size = ctypes.c_ulong(0)
    result = api(None, ctypes.byref(size), False, 2, 5, 0)
    if result != 122 or size.value < 4 or size.value > 16 * 1024 * 1024:
        return []
    data = ctypes.create_string_buffer(size.value)
    if api(data, ctypes.byref(size), False, 2, 5, 0) != 0:
        return []
    count = ctypes.c_ulong.from_buffer(data).value
    if 4 + count * ctypes.sizeof(Row) > size.value:
        return []
    return [Row.from_buffer(data, 4 + index * ctypes.sizeof(Row)) for index in range(count)]


def same_process_peer(peer_port: int, listener_port: int | None = None) -> bool:
    """Use the documented Windows TCP owner table, never game memory."""
    if IS_MAC:
        return False
    if listener_port is None:
        listener_port = _state.port or PORT_KEYBOARD
    try:
        rows = _owner_rows()
    except Exception:
        simsight_log.error('startup: TCP owner table')
        return False
    loopback = 0x0100007F
    return any(
        row.pid == os.getpid()
        and row.local_address == loopback
        and row.remote_address == loopback
        and socket.ntohs(row.local_port & 0xFFFF) == peer_port
        and socket.ntohs(row.remote_port & 0xFFFF) == listener_port
        for row in rows
    )


def _is_mode_mark(channel: str, payload: object) -> bool:
    return (
        channel == 'simsight.mark'
        and isinstance(payload, str)
        and payload.startswith('startup-v11-')
        and payload.endswith(('-active', '-idle', '-native'))
    )


def note_mark(payload: object) -> None:
    """Flash reports its own mode: '...-active' while it owns the keys before
    a save loads, '...-idle' / '...-native' when the loaded-zone path is used.
    Flash is the source of truth so the poller can never double-drive."""
    if not _is_mode_mark('simsight.mark', payload):
        return
    with _lock:
        _state.flash_startup = str(payload).endswith('-active')


def send_navigation(message_name: object) -> bool:
    """Called from the key poller. True means the message is consumed here.

    While Flash reports startup mode it handles the navigation keys natively
    through its input-action guards, so the poller's message is deliberately
    dropped rather than posted to a pump that has no zone. Otherwise the
    native distributor path is used and this returns False.
    """
    if not isinstance(message_name, str):
        return False
    # Explicit read-only diagnostic commands have no native key equivalent.
    # Let the simulation pump deliver these on travel maps with a loaded zone.
    if message_name == 'A11yInspect':
        return False
    with _lock:
        return _state.connected and _state.flash_startup


def _dispatch(channel: str, payload: str) -> None:
    import simsight_narrator as narrator

    if channel in _KEYS_HANDLERS or channel == 'simsight.inputfence':
        import simsight_keys as keys

        if channel == 'simsight.inputfence':
            keys.a11y_inputfence()
        else:
            getattr(keys, _KEYS_HANDLERS[channel])(payload)
    elif channel == 'simsight.mark':
        note_mark(payload)
        log('shared UI ready: ' + payload[:100])
    elif channel in _NARRATOR_HANDLERS:
        getattr(narrator, _NARRATOR_HANDLERS[channel])(payload)


def _client_present() -> bool:
    import services

    zone: Zone | None = services.current_zone()
    manager = services.client_manager() if zone is not None else None
    client: Client | None = manager.get_first_client() if manager is not None else None
    return client is not None


def tick() -> None:
    """Called only by the game's core.on_tick, which also runs pre-zone."""
    if time.monotonic() < _state.next_tick:
        return
    _state.next_tick = time.monotonic() + 0.02
    startup = not _client_present()
    with _lock:
        changed = startup != _state.startup
        if changed:
            log(
                'transport switched to ' + ('startup Socket' if startup else 'native loaded client')
            )
        _state.startup = startup
        generation = _state.generation
        batch = list(_incoming)
        _incoming.clear()
    for message_generation, channel, payload in batch:
        # Socket posts are narration Flash chose not to send natively; they
        # are dispatched in every game state so a wrong mode guess on the
        # Flash side degrades to key ownership, never to silence.
        if message_generation != generation:
            continue
        # Drop the departing screen's speech, but keep transport ownership.
        # Losing the one native-mode mark leaves the poller suppressed after
        # Flash has already stopped handling startup keys (and vice versa).
        if changed and not _is_mode_mark(channel, payload):
            continue
        try:
            _dispatch(channel, payload)
        except Exception as ex:  # the game's tick: nothing may escape
            log(f'handler failed: {channel} {ex!r}')
            simsight_log.error('startup: handler')


def _greeted() -> None:
    """A UI said hello: a new connection generation starts."""
    with _lock:
        _state.generation += 1
        _incoming.clear()
        _state.connected = True
    log(f'UI Socket connected (outbound only, port {_state.port})')


def _enqueue(post: Post) -> None:
    with _lock:
        if len(_incoming) >= MAX_QUEUE and (post[0] in _STATE_CHANNELS or _is_mode_mark(*post)):
            _incoming.popleft()
        if len(_incoming) < MAX_QUEUE:
            _incoming.append((_state.generation, post[0], post[1]))


class _Session:
    """One UI connection: the greeting first, then POST lines."""

    def __init__(self) -> None:
        self.reader = FrameReader()
        self.greeted = False

    def take(self, data: bytes) -> bool:
        """Act on received bytes. False when the connection must end (no
        greeting first, or a line that is not an accepted POST)."""
        for line in self.reader.feed(data):
            if not self.greeted:
                if parse_hello(line) is None:
                    return False
                _greeted()
                self.greeted = True
                continue
            post = parse_post(line)
            if post is None:
                return False
            _enqueue(post)
        return True


def _receive(conn: Any) -> bytes | None:
    """Bytes from the UI, b'' when it closed, None when nothing came."""
    try:
        return conn.recv(4096)
    except socket.timeout:
        return None


def _connection(conn: Any) -> str | None:
    """Serve one UI connection. Never sends a byte. Returns 'moved' when it
    ended because the settings now ask for another port: a UI copy keeps its
    connection from the main menu through a loaded game and back, so the
    settings are checked here too, every half second."""
    session = _Session()
    conn.settimeout(_CHECK_EVERY)
    next_check = time.monotonic() + _CHECK_EVERY
    try:
        while not _stop.is_set():
            data = _receive(conn)
            if data == b'':
                return None
            now = time.monotonic()
            if now >= next_check:
                next_check = now + _CHECK_EVERY
                if _preference_moved():
                    log(f'UI Socket closed: the settings now ask for port {_state.wanted}')
                    return 'moved'
            if data is not None and not session.take(data):
                return None
    finally:
        with _lock:
            _state.connected = False
            _state.flash_startup = False
            _state.generation += 1
            _incoming.clear()
        if session.greeted:
            log('UI Socket disconnected')
    return None


def _bind(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Exclusive Windows binding prevents a second process from joining
        # the same local endpoint. No external interface is ever bound.
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('127.0.0.1', port))
        listener.listen(2)
        listener.settimeout(_CHECK_EVERY)
    except Exception:
        listener.close()
        raise
    meaning = _PORT_MEANINGS.get(port, 'unknown')
    log(f'same-process-only loopback listener ready on port {port} ({meaning})')
    return listener


def _open_listener(port: int) -> tuple[int, socket.socket]:
    """(port, listener) for the wanted port. With Simplified menus off and
    45995 taken by another program, the default keyboard port still gives
    speech and keys before a save loads; the loaded game then applies the
    real menu setting. Speech only never falls back to a keyboard port."""
    try:
        return port, _bind(port)
    except Exception as ex:
        if port != PORT_KEYBOARD_FULL_MENUS:
            raise
        log(f'port {port} unavailable ({ex!r}); simplified menus stay on until a save loads')
        return PORT_KEYBOARD, _bind(PORT_KEYBOARD)


def _preference_moved() -> bool:
    """A settings change made in a loaded game (simsight.reload or the settings
    menu) must reach the main menu. True when the wanted port has changed:
    the new value must read the same twice in a row, half a second apart, so
    the brief empty store of simsight.reload never moves it."""
    if _state.wanted is None:
        return False  # not serving (a session outside _serve)
    now = listen_port()
    moved = now != _state.wanted and now == _state.seen
    if moved:
        _state.wanted = now
    _state.seen = now
    return moved


def _move_listener(listener: socket.socket) -> socket.socket:
    """Serve the wanted port. A failed move keeps the current listener until
    the settings change again."""
    if _state.wanted is None or _state.wanted == _state.port:
        return listener
    try:
        port, replacement = _open_listener(_state.wanted)
    except OSError as ex:  # the port is taken (socket errors are OSError)
        log(f'listener stays on port {_state.port}: {ex!r}')
        return listener
    listener.close()
    _state.port = port
    return replacement


def _serve_one(conn: Any, address: Any) -> bool:
    """Serve an accepted connection; True when it ended because the settings
    asked for another port."""
    moved = False
    try:
        if address[0] == '127.0.0.1' and same_process_peer(address[1], _state.port):
            moved = _connection(conn) == 'moved'
    except OSError as ex:  # the UI closed or reset the connection
        log(f'Socket session ended: {ex!r}')
    except Exception as ex:  # the listener thread: nothing may end it
        log(f'Socket session ended: {ex!r}')
        simsight_log.error('startup: socket session')
    finally:
        conn.close()
    return moved


def _accept_loop(listener: socket.socket) -> socket.socket:
    """Accept until stopped; the listener in use at the end."""
    while not _stop.is_set():
        try:
            conn, address = listener.accept()
        except socket.timeout:
            if _preference_moved():
                listener = _move_listener(listener)
            continue
        if _serve_one(conn, address):
            # Before the next accept: a UI that reconnects at once must not
            # land on the port it was just closed for.
            listener = _move_listener(listener)
    return listener


def _serve() -> None:
    """The listener thread (Windows only)."""
    listener = None
    try:
        if not enabled():
            log('startup speech disabled in settings; not listening')
            return
        wanted = listen_port()
        _state.wanted = _state.seen = wanted
        _state.port, listener = _open_listener(wanted)
        listener = _accept_loop(listener)
    except OSError as ex:  # no port to listen on
        log(f'Socket unavailable: {ex!r}')
    except Exception as ex:  # the thread's top: log, never raise
        log(f'Socket unavailable: {ex!r}')
        simsight_log.error('startup: listener thread')
    finally:
        if listener is not None:
            listener.close()


def stop() -> None:
    _stop.set()


def install() -> None:
    """Hook the game's core tick and, on Windows, start the listener thread."""
    if _state.installed:
        return
    import sims4.core_services as core

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        @functools.wraps(original)
        def on_tick(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            try:
                tick()
            except Exception:
                simsight_log.error('startup: core tick')
            return result

        return on_tick

    simsight_patch.wrap(core, 'on_tick', make)
    _state.installed = True  # once: the listener thread starts once
    if IS_MAC:
        log('macOS: startup listener not started (main menu and CAS speech unavailable)')
        return
    threading.Thread(target=_serve, name='a11y_startup', daemon=True).start()


try:
    import sims4.core_services  # noqa: F401 (only checks that the game is there)
except ImportError:
    pass
else:
    install()

"""Companion bridge (macOS and Windows): game <-> simsight-bridge <-> phone.

The bridge is a child process started here, on the game's loading thread.
- Speech: on macOS the speech module writes its usual "I..."/"Q..." lines to
  the bridge, which forwards them to the macOS voice (see SpeechPipe). On
  Windows the screen reader speaks (Prism) and each line is copied to the
  bridge for the phone's history (mirror); the bridge's own announcements
  come back as "say" commands, spoken by the screen reader.
- State: a JSON snapshot of the household (needs, mood, moodlets, actions,
  clock, funds), sent when it changes, at most once per second.
- Commands from the phone arrive on the bridge's stdout and run here.

Everything that touches the game runs from the game's own tick on the
simulation thread. No threads: the Mac game crashes when a background
thread imports a module from a zip while the simulation runs Python.
Message format: companion/proto/simsight/v1/pipe.proto (protobuf JSON).
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

try:
    import subprocess
except ImportError:  # not every Python the game ships has it: no companion then
    subprocess = None  # type: ignore[assignment]

from simsight_commands import real_annotations
import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    Json = dict[str, Any]
    # (ok, reason) or (ok, reason, extra fields for the result message)
    Answer = tuple[Any, ...]

IS_MAC = sys.platform == 'darwin'
IS_WINDOWS = sys.platform == 'win32'
BRIDGE = 'simsight-bridge.exe' if IS_WINDOWS else 'simsight-bridge'
CREATE_NO_WINDOW = 0x08000000  # Windows: no console window for the bridge
SPEAKER = 'simsight-say'
STATE_INTERVAL = 1.0  # seconds between state snapshots (sent only if changed)
POLL_INTERVAL = 0.1  # seconds between command polls
LOC_PER_TICK = 20  # string-table keys requested from the UI per poll
RESPAWN_DELAY = 2.0  # seconds before restarting a bridge that exited
READ_SIZE = 65536


class _State:
    """Everything this module remembers between ticks."""

    def __init__(self) -> None:
        self.proc: Any = None  # the bridge process (subprocess.Popen)
        self.voice: Json | None = None  # last voice settings, re-sent to a restarted bridge
        self.last_spawn = 0.0
        self.read_buf = b''
        self.last_state_json: str | None = None
        self.last_state_time = 0.0
        self.last_poll = 0.0
        self.seq = 0
        # Game strings asked for but not known yet (names still loading).
        self.loc_misses = 0
        self.loc_wanted: set[int] = set()
        self.loc_requested: set[int] = set()
        self.house_at = -1e9
        self.house_rooms: dict[str, Json] = {}
        self.problems_at = 0.0
        self.problems: list[Json] = []
        self.route_seen: dict[tuple[Any, str], float] = {}  # (sim id, action) -> time
        self.menus: dict[str, dict[str, tuple[Any, Any]]] = {}  # target -> option -> (aop, context)


_state = _State()
_write_lock = threading.Lock()


def log(message: str) -> None:
    simsight_log.line('bridge: ' + message)


@contextlib.contextmanager
def _logged(where: str) -> Iterator[None]:
    """Run a block of game calls whose failure only leaves a value out:
    the failure is logged (once, then counted) and the caller goes on."""
    try:
        yield
    except Exception:
        simsight_log.error('bridge: ' + where)


def _distance(a: Any, b: Any) -> float:
    """Distance on the ground between two positions (x and z)."""
    return ((float(a.x) - float(b.x)) ** 2 + (float(a.z) - float(b.z)) ** 2) ** 0.5


def _full_name(info: Any) -> str:
    return f'{info.first_name} {info.last_name}'.strip()


def _on_active_lot(obj: Any, default: bool) -> bool:
    """obj.is_on_active_lot(), or `default` for objects that cannot tell."""
    on_lot = getattr(obj, 'is_on_active_lot', None)
    return default if on_lot is None else bool(on_lot())


# ---------------------------------------------------------------- process


def _find(name: str) -> str | None:
    for folder in (speech.MODS_FOLDER, os.path.join(speech._mods_root(), 'SimSight')):
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
            return True
        except (OSError, ValueError) as ex:  # the bridge exited: alive() notices
            log(f'write failed: {ex!r}')
            return False


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


# ---------------------------------------------------------------- tick


def _tick() -> None:
    """Runs on the game's tick (simulation thread), every frame."""
    if not alive() and not start():
        return
    now = time.monotonic()
    if now - _state.last_poll >= POLL_INTERVAL:
        _state.last_poll = now
        _read_commands()
        _request_localization()
        if _dialogs is not None:
            _dialogs.flush_notes()
    if _mackeys is not None:
        try:
            _mackeys.tick(send)
        except Exception:
            simsight_log.error('bridge: keys tick')
    if now - _state.last_state_time >= STATE_INTERVAL:
        _state.last_state_time = now
        _publish_state()


def _install_tick() -> None:
    import sims4.core_services as core

    original = core.on_tick
    if getattr(original, '_simsight_bridge', False):
        return

    def on_tick(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        try:
            _tick()
        except Exception:
            simsight_log.error('bridge: tick')
        return result

    setattr(on_tick, '_simsight_bridge', True)  # noqa: B010 - marker on a function
    core.on_tick = on_tick


def _waiting_bytes() -> int:
    """Windows pipes cannot be made non-blocking: ask how much is waiting
    (PeekNamedPipe), so reading never blocks the game's tick."""
    handle = _msvcrt.get_osfhandle(_state.proc.stdout.fileno())
    avail = _ctypes.c_ulong(0)
    if not _ctypes.windll.kernel32.PeekNamedPipe(
        _ctypes.c_void_p(handle), None, 0, None, _ctypes.byref(avail), None
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


def _read_commands() -> None:
    try:
        chunk = _read_chunk()
    except BlockingIOError:
        return  # nothing waiting
    except OSError as ex:  # the bridge exited: alive() notices
        log(f'read failed: {ex!r}')
        return
    except Exception:
        simsight_log.error('bridge: read')
        return
    if not chunk:
        return
    _state.read_buf += chunk
    while b'\n' in _state.read_buf:
        line, _state.read_buf = _state.read_buf.split(b'\n', 1)
        if line.strip():
            _handle(line)


def _handle(line: bytes) -> None:
    try:
        cmd = json.loads(line.decode('utf-8'))
    except ValueError as ex:  # not UTF-8 or not JSON
        log(f'bad command: {ex!r}')
        return
    cmd_id = cmd.get('id', '')
    extra: Json = {}
    try:
        answer = _run(cmd)
        ok, reason = answer[0], answer[1]
        if len(answer) > 2:
            extra = answer[2]
    except Exception:
        simsight_log.error('bridge: command')
        ok, reason = False, 'error'
    outcome = {'ok': True} if ok else {'reason': reason}
    result = {'id': cmd_id, 'outcome': outcome}
    result.update(extra)
    send({'result': result})
    # Let the phone see the effect right away.
    _state.last_state_time = 0.0


def _in_game() -> bool:
    import services

    zone = services.current_zone()
    return (
        zone is not None
        and getattr(zone, 'is_zone_running', False)
        and services.active_household() is not None
    )


# ---------------------------------------------------------------- commands
# One handler per command: it gets the command's own fields and answers
# (ok, reason) or (ok, reason, extra result fields).

_SPEEDS = {
    'SPEED_PAUSED': 'paused',
    'SPEED_NORMAL': 'one',
    'SPEED_FAST': 'two',
    'SPEED_ULTRA': 'three',
}


def _cmd_set_narration(c: Json) -> Answer:
    narration = c.get('narration', {})
    speech.set_mode('hover' if narration.get('macVoice') else 'off')
    import simsight_settings as settings

    settings.set('sim_actions', bool(narration.get('simActions')))
    return True, ''


def _cmd_say(c: Json) -> Answer:
    # Windows: the bridge's announcements and pairing code, spoken by the
    # screen reader (the Mac bridge has its own voice).
    text = c.get('text', '')
    if text:
        speech.speak(text, interrupt=bool(c.get('interrupt')), source='event', explicit=True)
    return True, ''


def _cmd_set_speed(c: Json) -> Answer:
    name = _SPEEDS.get(c.get('speed', ''))
    if name is None:
        return False, 'error'
    return _set_speed(name), 'error'


def _cmd_select_sim(c: Json) -> Answer:
    import simsight_hub as hub

    return hub.select_sim(int(c.get('simId', '0'))), 'sim_unavailable'


def _cmd_list_targets(_c: Json) -> Answer:
    return True, '', {'targets': list_targets()}


def _cmd_key(k: Json) -> Answer:
    # Keys only reach a loaded game (the in-game check drops the rest).
    if _mackeys is None:
        return False, 'error'
    if k.get('family') == 'focus':
        _mackeys.focus_lost()
        return True, ''
    return _mackeys.handle(
        k.get('family', ''), int(k.get('vk', 0)), bool(k.get('down')), bool(k.get('shift'))
    ), 'not_owned'


def _cmd_list_inventory(_c: Json) -> Answer:
    return list_inventory()


def _cmd_move_item(c: Json) -> Answer:
    return move_item(c.get('itemId', ''), c.get('destinationId', ''))


def _cmd_set_camera(c: Json) -> Answer:
    return _set_camera(c.get('simId', ''), c.get('mode', 'CAMERA_MODE_FOLLOW'))


def _cmd_respond_dialog(c: Json) -> Answer:
    if _dialogs is None:
        return False, 'error'
    return _dialogs.respond(
        c.get('dialogId', ''), c.get('choiceId', 0), c.get('rowIds', []), c.get('text', '')
    )


def _cmd_open_menu(c: Json) -> Answer:
    if c.get('menu') != 'hub':
        return False, 'unknown_command'
    import simsight_hub as hub

    hub.open_hub()
    return True, ''


def _cmd_get_house_map(_c: Json) -> Answer:
    import simsight_housemap as housemap

    misses = _state.loc_misses
    house = housemap.build()
    if house is None:
        return False, 'not_in_game'
    if _state.loc_misses != misses:
        house['namesPending'] = True
    return True, '', {'houseMap': house}


def _cmd_get_interactions(c: Json) -> Answer:
    return get_interactions(c.get('targetId', ''))


def _cmd_run_interaction(c: Json) -> Answer:
    return run_interaction(c.get('targetId', ''), c.get('interactionId', ''))


def _cmd_cancel_action(c: Json) -> Answer:
    import services
    import simsight_hub as hub

    sim = services.get_active_sim()
    if sim is None or str(sim.id) != c.get('simId'):
        return False, 'sim_unavailable'
    return hub.cancel_actions(sim.id, (int(c.get('actionId', '0')),)), 'not_cancelable'


# Checked in this order; the first key the command has decides.
_ANYTIME_COMMANDS: tuple[tuple[str, Callable[[Json], Answer]], ...] = (
    ('setNarration', _cmd_set_narration),
    ('say', _cmd_say),
)
_IN_GAME_COMMANDS: tuple[tuple[str, Callable[[Json], Answer]], ...] = (
    ('setSpeed', _cmd_set_speed),
    ('selectSim', _cmd_select_sim),
    ('listTargets', _cmd_list_targets),
    ('key', _cmd_key),
    ('listInventory', _cmd_list_inventory),
    ('moveItem', _cmd_move_item),
    ('setCamera', _cmd_set_camera),
    ('respondDialog', _cmd_respond_dialog),
    ('openMenu', _cmd_open_menu),
    ('getHouseMap', _cmd_get_house_map),
    ('getInteractions', _cmd_get_interactions),
    ('runInteraction', _cmd_run_interaction),
    ('cancelAction', _cmd_cancel_action),
)


def _run(cmd: Json) -> Answer:
    for key, handler in _ANYTIME_COMMANDS:
        if key in cmd:
            return handler(cmd[key])
    if not _in_game():
        return False, 'not_in_game'
    for key, handler in _IN_GAME_COMMANDS:
        if key in cmd:
            return handler(cmd[key])
    return False, 'unknown_command'


def _set_speed(name: str) -> bool:
    """Through the game's own clock.setspeed command, like the speed buttons."""
    try:
        from server_commands import clock_commands

        clock_commands.set_speed(name)
        return True
    except Exception as ex:
        log(f'clock.setspeed failed, using the clock directly: {ex!r}')
    import services
    from clock import ClockSpeedMode, GameSpeedChangeSource

    mode = {
        'paused': ClockSpeedMode.PAUSED,
        'one': ClockSpeedMode.NORMAL,
        'two': ClockSpeedMode.SPEED2,
        'three': ClockSpeedMode.SPEED3,
    }[name]
    services.game_clock_service().set_clock_speed(mode, source=GameSpeedChangeSource.GAMEPLAY)
    return True


# ---------------------------------------------------------------- localization
# Python only sees string-table keys; the patched UI resolves them in the
# player's language through the scanner's loc channel (A11y|loc / simsight.loc_enc).


def _loc_cache() -> dict[int, str]:
    scanner = sys.modules.get('simsight_scanner')
    return getattr(scanner, '_loc_cache', {}) if scanner else {}


def _request_localization() -> None:
    scanner = sys.modules.get('simsight_scanner')
    if scanner is None or not _state.loc_wanted:
        return
    cache = scanner._loc_cache
    for key in list(_state.loc_wanted)[:LOC_PER_TICK]:
        _state.loc_wanted.discard(key)
        if key in cache or key in _state.loc_requested:
            continue
        _state.loc_requested.add(key)
        scanner._send_ui(f'A11y|loc|{key}|{key}')


def _cached_text(key: int) -> str | None:
    """The game's text for a string-table key, or None (then asked of the UI)."""
    cached = _loc_cache().get(key)
    if cached is None:
        _state.loc_misses += 1
        _state.loc_wanted.add(key)
    return cached


def _loc_value(value: Any) -> Any:
    """A LocalizedString proto from a tuned value (proto or factory)."""
    if value is None:
        return None
    if callable(value) and not hasattr(value, 'hash'):
        try:
            value = value()
        except TypeError:
            return None  # a factory that needs arguments (the Sims it is about)
        except Exception:
            simsight_log.error('bridge: text factory')
            return None
    return value if hasattr(value, 'hash') else None


_TOKEN = re.compile(r'\{([MF])(\d+)\.([^}]*)\}')
_PLURAL = re.compile(r'\{([SP])\.([^}]*)\}')
_FIELD = re.compile(r'\{(\d+)\.(\w+)\}')


def _number_text(t: Any, money: bool) -> str:
    n = getattr(t, 'number', 0) or 0
    text = str(int(n)) if float(n).is_integer() else f'{n:g}'
    return ('§' + text) if money else text


def _field_text(t: Any, name: str) -> str:
    """One {0.Field} placeholder filled from its token."""
    first, last = getattr(t, 'first_name', ''), getattr(t, 'last_name', '')
    if name == 'SimFirstName':
        return first
    if name == 'SimLastName':
        return last
    if name in ('SimName', 'SimFullName'):
        return f'{first} {last}'.strip()
    if name in ('Number', 'Money'):
        return _number_text(t, name == 'Money')
    return _token_text(t)


def _fill_tokens(template: str, tokens: list[Any]) -> str:
    """Fill the common Sims placeholders: {0.SimFirstName}, {M0.he}{F0.she}..."""

    def token(i: int) -> Any:
        return tokens[i] if 0 <= i < len(tokens) else None

    def gender(match: re.Match[str]) -> str:
        t = token(int(match.group(2)))
        female = bool(getattr(t, 'is_female', False)) if t is not None else False
        return match.group(3) if (match.group(1) == 'F') == female else ''

    def field(match: re.Match[str]) -> str:
        t = token(int(match.group(1)))
        return '' if t is None else _field_text(t, match.group(2))

    # {S.Cereal}{P.Bowls of Cereal}: one item, so the singular form.
    text = _PLURAL.sub(lambda m: m.group(2) if m.group(1) == 'S' else '', template)
    text = _TOKEN.sub(gender, text)
    text = _FIELD.sub(field, text)
    return re.sub(r'\{[^}]*\}', '', text).replace('  ', ' ').strip()


def _token_text(t: Any) -> str:
    """A token's own text: a nested game string, raw text, or an object's name."""
    # Not every token is a proto with a text_string field.
    with contextlib.suppress(AttributeError, ValueError):
        if t.HasField('text_string'):
            return text_of(t.text_string)
    for attr in ('raw_text', 'custom_name'):
        value = getattr(t, attr, '') or ''
        if value:
            return value
    key = getattr(t, 'catalog_name_key', 0) or 0
    return _loc_key_text(key) if key else ''


def text_of(value: Any, fallback: str = '') -> str:
    """Game text for a localized value, or fallback until the UI resolves it."""
    loc = _loc_value(value)
    if loc is None:
        return fallback
    try:
        key = int(loc.hash)
    except (TypeError, ValueError):
        return fallback
    if not key:
        return fallback
    cached = _cached_text(key)
    if cached is None:
        return fallback
    tokens = list(getattr(loc, 'tokens', []) or [])
    return _fill_tokens(cached, tokens) or fallback


def _pretty(tuning_name: str, prefixes: tuple[str, ...] = ()) -> str:
    """'Buff_Energized_Coffee' -> 'Energized Coffee' when no game text yet."""
    name = tuning_name or ''
    for p in prefixes:
        if name.lower().startswith(p):
            name = name[len(p) :]
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name.replace('_', ' ').replace('-', ' '))
    return ' '.join(w.capitalize() if w.islower() else w for w in name.split())


# ---------------------------------------------------------------- state

_NEEDS = (
    ('hunger', 'motive_hunger'),
    ('bladder', 'motive_bladder'),
    ('energy', 'motive_energy'),
    ('hygiene', 'motive_hygiene'),
    ('fun', 'motive_fun'),
    ('social', 'motive_social'),
)


def _need_level(p: float) -> str:
    if p < 15:
        return 'NEED_LEVEL_CRITICAL'
    if p < 30:
        return 'NEED_LEVEL_LOW'
    if p < 60:
        return 'NEED_LEVEL_OKAY'
    return 'NEED_LEVEL_GOOD'


def _needs(info: Any) -> list[Json]:
    import simsight_plus_needs as needs

    out = []
    for key, stat in _NEEDS:
        p = needs.percent(info, stat)
        if p is not None:
            out.append({'key': key, 'percent': p, 'level': _need_level(p)})
    return out


def _mood_key(mood: Any) -> str:
    name = getattr(mood, '__name__', '') or ''
    return re.sub(r'^mood_', '', name, flags=re.I).lower()


def _mood(info: Any) -> Json | None:
    buffs = getattr(info, 'Buffs', None)
    if buffs is None:
        return None
    mood = buffs.get_mood()
    if mood is None:
        return None
    intensity = 0
    with _logged('mood intensity'):
        intensity = int(buffs.get_mood_intensity())
    name = ''
    names = getattr(mood, 'mood_names', None)
    if names:
        with _logged('mood name'):
            name = text_of(names[min(intensity, len(names) - 1)])
    return {'key': _mood_key(mood), 'name': name, 'intensity': intensity}


def _moodlet(buff: Any, now: Any) -> Json:
    # Buff instances share the Buff class; the tuning (name, mood, text)
    # is on buff_type.
    btype = getattr(buff, 'buff_type', None) or type(buff)
    tuning = getattr(btype, '__name__', '') or 'Buff'

    def tuned(attr: str) -> Any:
        value = getattr(btype, attr, None)
        return value if value is not None else getattr(buff, attr, None)

    mood_type = tuned('mood_type')
    moodlet = {
        'key': tuning,
        'name': text_of(tuned('buff_name'), _pretty(tuning, ('buff_',))),
        'description': text_of(tuned('buff_description')),
        'moodKey': _mood_key(mood_type) if mood_type else '',
        'weight': int(tuned('mood_weight') or 0),
        'minutesLeft': -1,
    }
    with _logged('moodlet time'):
        timeout = buff.get_timeout_time()
        if timeout:
            moodlet['minutesLeft'] = max(0, int((timeout - now).in_minutes()))
    return moodlet


def _moodlets(info: Any) -> list[Json]:
    import services

    buffs = getattr(info, 'Buffs', None)
    if buffs is None:
        return []
    now = services.time_service().sim_now
    return [_moodlet(buff, now) for buff in buffs if getattr(buff, 'visible', False)]


def _actions(sim: Any) -> list[Json]:
    import simsight_hub as hub

    out = []
    for info, interaction, state in hub._queue_entries(sim):
        fallback = ''
        if interaction is not None and hasattr(interaction, 'affordance'):
            fallback = _pretty(
                getattr(interaction.affordance, '__name__', ''), ('sim-', 'si_', 'mixer_')
            )
        name = text_of(getattr(info, 'display_name', None), fallback)
        if not name:
            continue
        out.append(
            {
                'id': str(info.interaction_id),
                'name': name,
                'state': 'ACTION_STATE_RUNNING' if state == 'running' else 'ACTION_STATE_QUEUED',
                'cancelable': bool(getattr(info, 'user_cancelable', False)),
            }
        )
    return out


def _speed() -> str:
    import services
    from clock import ClockSpeedMode

    speed = services.game_clock_service().clock_speed
    return {
        ClockSpeedMode.PAUSED: 'SPEED_PAUSED',
        ClockSpeedMode.SPEED2: 'SPEED_FAST',
        ClockSpeedMode.SPEED3: 'SPEED_ULTRA',
        ClockSpeedMode.SUPER_SPEED3: 'SPEED_SUPER',
    }.get(speed, 'SPEED_NORMAL')


def _clock() -> Json:
    import services

    clock = {'speed': _speed()}
    with _logged('clock'):
        import simsight_plus_time as t

        now = services.time_service().sim_now
        clock['day'] = t.day_text(now)
        clock['time'] = t.clock_text(now)
    return clock


def _career_title(career: Any, info: Any) -> str:
    title = ''
    with _logged('career title'):
        title = text_of(career.current_level_tuning.get_title(info))
    if not title:
        with _logged('career name'):
            title = text_of(career.display_name(info))
    return title or _pretty(type(career).__name__, ('career_',))


def _career(info: Any, entry: Json) -> None:
    """Job or school (the first one), whether the Sim is there now, until when, next shift."""
    import services
    import simsight_plus_time as t

    tracker = getattr(info, 'career_tracker', None)
    if tracker is None:
        return
    careers = list(tracker.careers.values())
    if not careers:
        return
    career = careers[0]
    word = t._career_word(career)
    entry['career'] = _career_title(career, info)
    performance = _performance(career)
    if performance is not None:
        entry['performance'] = performance
    now = services.time_service().sim_now
    if career.currently_at_work:
        entry['awayAt'] = word
        with _logged('career end'):
            entry['until'] = t.clock_text(now + career.time_until_end_of_work())
        return
    with _logged('next shift'):
        _span, start, _end = career.get_next_work_time()
        if start is not None:
            entry['nextShift'] = f'{t.day_text(start)} {t.clock_text(start)}'


# ---------------------------------------------------------------- goals
# Aspiration milestone, wants and fears, work tasks and promotion steps.
# Read only; every part is optional (packs and ages differ).


def _done(tracker: Any, key: Any, kind: str) -> bool:
    try:
        check = getattr(
            tracker, 'objective_completed' if kind == 'objective' else 'milestone_completed'
        )
        return bool(check(key))
    except Exception:
        simsight_log.error('bridge: goal done')
        return False


def _objectives(aspiration: Any, tracker: Any) -> list[Json]:
    out = []
    for objective in list(getattr(aspiration, 'objectives', None) or ()):
        name = text_of(getattr(objective, 'display_text', None), '')
        if not name:
            continue
        out.append({'name': name, 'done': _done(tracker, objective, 'objective')})
    return out


def _aspiration_goals(info: Any, tracker: Any, goals: Json) -> None:
    """The aspiration and its first milestone not done yet, with its steps."""
    track = getattr(info, 'primary_aspiration', None)
    if track is None or tracker is None:
        return
    with _logged('aspiration'):
        goals['aspiration'] = text_of(getattr(track, 'display_text', None), '')
        aspirations = track.get_aspirations()
        items = aspirations.items() if hasattr(aspirations, 'items') else enumerate(aspirations)
        for _level, milestone in sorted(items, key=lambda kv: kv[0]):
            if not _done(tracker, milestone, 'milestone'):
                goals['milestone'] = text_of(getattr(milestone, 'display_name', None), '')
                goals['milestoneSteps'] = _objectives(milestone, tracker)
                break


def _want(slot: Any) -> Json | None:
    want = None
    with _logged('want'):
        goal = getattr(slot, 'goal_instance', None)
        if goal is not None:
            name = text_of(goal.get_display_name(), '')
            if name:
                kind = getattr(getattr(slot, 'whim_type', None), 'name', '')
                want = {'name': name, 'fear': kind == 'CONFRONTATION'}
    return want


def _wants(info: Any) -> list[Json]:
    whims = getattr(info, 'whim_tracker', None)
    if whims is None:
        return []
    slots = list(getattr(whims, '_whim_slots', None) or ())
    return [want for want in (_want(slot) for slot in slots) if want is not None]


def _work_tasks(level: Any) -> list[Json]:
    tasks = []
    for metric in list(getattr(level.performance_metrics, 'statistic_metrics', None) or ()):
        tip = getattr(metric, 'tooltip_text', None)
        name = text_of(getattr(tip, 'general_description', None), '') if tip is not None else ''
        if name:
            tasks.append({'name': name, 'done': False})
    return tasks


def _work_goals(info: Any, tracker: Any, goals: Json) -> None:
    """Work tasks and promotion steps of the first job."""
    careers = getattr(info, 'career_tracker', None)
    if careers is None:
        return
    for career in list(careers.careers.values())[:1]:
        with _logged('work goals'):
            level = career.current_level_tuning
            tasks = _work_tasks(level)
            if tasks:
                goals['workTasks'] = tasks
            promotion = getattr(level, 'aspiration', None)
            if promotion is not None and tracker is not None:
                steps = _objectives(promotion, tracker)
                if steps:
                    goals['promotionSteps'] = steps


def _goals(info: Any) -> Json:
    goals: Json = {}
    tracker = getattr(info, 'aspiration_tracker', None)
    _aspiration_goals(info, tracker, goals)
    wants = _wants(info)
    if wants:
        goals['wants'] = wants
    _work_goals(info, tracker, goals)
    return goals


MAX_RELATIONSHIPS = 12


def _bit_label(bit: Any, other: Any, info: Any) -> str:
    """A relationship label (Father, Sister...) in the gender of the Sim it
    describes: the game's text takes that Sim as token 0, {M0.Father}{F0.Mother}."""
    name = getattr(bit, 'display_name', None)
    if callable(name) and not hasattr(name, 'hash'):
        with _logged('relationship label'):
            return text_of(name(other, info))
    return text_of(name)


def _relationship_labels(tracker: Any, other: Any, info: Any) -> list[str]:
    labels: list[str] = []
    with _logged('relationship labels'):
        bits = [b for b in tracker.get_all_bits(other.sim_id) if getattr(b, 'visible', False)]
        bits.sort(key=lambda b: -(getattr(b, 'priority', 0) or 0))
        for bit in bits[:3]:
            label = _bit_label(bit, other, info)
            if label and label not in labels:
                labels.append(label)
    return labels


def _relationship(tracker: Any, other: Any, info: Any, tracks: tuple[Any, Any]) -> Json | None:
    """One relationship row, or None when the game hides it."""
    hidden = False
    with _logged('relationship hidden'):
        hidden = bool(tracker.is_hidden(other.sim_id))
    if hidden:
        return None
    friendship, romance = tracks
    f = r = 0
    with _logged('relationship score'):
        f = (
            int(round(tracker.get_relationship_score(other.sim_id, friendship) or 0))
            if friendship
            else 0
        )
        r = int(round(tracker.get_relationship_score(other.sim_id, romance) or 0)) if romance else 0
    return {
        'simId': str(other.sim_id),
        'name': _full_name(other),
        'friendship': f,
        'romance': r,
        'labels': _relationship_labels(tracker, other, info),
    }


def _relationships(info: Any) -> list[Json]:
    """Strongest relationships first: friendship, romance and the game's labels."""
    from relationships.relationship_track import RelationshipTrack

    tracker = info.relationship_tracker
    tracks = (
        getattr(RelationshipTrack, 'FRIENDSHIP_TRACK', None),
        getattr(RelationshipTrack, 'ROMANCE_TRACK', None),
    )
    rows = []
    for other in list(tracker.get_target_sim_infos() or ()):
        if other is None:
            continue
        row = _relationship(tracker, other, info, tracks)
        if row is None:
            continue
        strength = max(abs(row['friendship']), abs(row['romance']))
        rows.append((strength + (100 if row['labels'] else 0), row))
    rows.sort(key=lambda pair: -pair[0])
    return [row for _score, row in rows[:MAX_RELATIONSHIPS]]


_SKILL_PREFIXES = (
    'statistic_skill_adultmajor_',
    'statistic_skill_',
    'skill_',
    'adultmajor_',
)


def _skill(skill: Any) -> Json | None:
    """A skill row, or None for hidden skills and skills not started."""
    row = None
    with _logged('skill'):
        if not getattr(skill, 'hidden', False):
            level = int(skill.get_user_value())
            if level >= 1:
                name = type(skill).__name__
                row = {
                    'key': name,
                    'name': text_of(
                        getattr(skill, 'stat_name', None), _pretty(name, _SKILL_PREFIXES)
                    ),
                    'level': level,
                    'maxLevel': int(getattr(skill, 'max_level', 10) or 10),
                }
    return row


def _skills(info: Any) -> list[Json]:
    out = [row for row in (_skill(skill) for skill in list(info.all_skills())) if row]
    out.sort(key=lambda s: (-s['level'], s['name']))
    return out


def _performance(career: Any) -> int | None:
    """Work performance towards the next promotion, 0-100, or None."""
    try:
        value = float(career.work_performance)
        goal = float(career._get_promote_performance_level())
    except (AttributeError, TypeError, ValueError):
        return None  # careers without performance (school, odd jobs)
    if goal <= 0:
        return None
    return max(0, min(100, int(round(100.0 * value / goal))))


def _add_part(entry: Json, key: str, part: Callable[[], Any]) -> None:
    """entry[key] = part() when it has something; a failure only leaves it out."""
    with _logged(key):
        value = part()
        if value:
            entry[key] = value


def _sim(info: Any, client: Any) -> Json:
    sim = info.get_sim_instance()
    room = _room_ref(sim) if sim is not None else None
    entry: Json = {
        'id': str(info.sim_id),
        'firstName': info.first_name or '',
        'lastName': info.last_name or '',
        'active': client is not None and client.active_sim_info is info,
        'here': sim is not None,
    }
    if room is not None:
        entry['room'] = room
    with _logged('career'):
        _career(info, entry)
    _add_part(entry, 'relationships', lambda: _relationships(info))
    _add_part(entry, 'skills', lambda: _skills(info))
    _add_part(entry, 'goals', lambda: _goals(info))
    _add_part(entry, 'mood', lambda: _mood(info))
    if sim is not None:  # only a Sim on the lot has needs, moodlets and actions now
        _add_part(entry, 'needs', lambda: _needs(info))
        _add_part(entry, 'moodlets', lambda: _moodlets(info))
        _add_part(entry, 'actions', lambda: _actions(sim))
    return entry


def _narration() -> Json:
    try:
        import simsight_settings as settings

        return {
            'macVoice': speech.modes.speech == 'hover',
            'simActions': bool(settings.get('sim_actions')),
        }
    except Exception:
        simsight_log.error('bridge: narration')
        return {}


def build_state() -> Json:
    import services

    if not _in_game():
        return {'inGame': False, 'narration': _narration()}
    household = services.active_household()
    client = services.client_manager().get_first_client()
    return {
        'inGame': True,
        'clock': _clock(),
        'funds': str(int(household.funds.money)),
        'householdName': household.name or '',
        'sims': [_sim(info, client) for info in household.sim_info_gen()],
        'narration': _narration(),
        'dialogs': _open_dialogs(),
        'problems': _problems(),
        'visitors': _visitors(household),
        'billsOwed': str(_bills_owed(household)),
        'cameraFollows': _camera_follows(),
    }


# ---------------------------------------------------------------- where Sims are
# The house map (rooms named as on the phone's House tab), rebuilt now and
# then: it scans the lot, so not every second.

HOUSE_INTERVAL = 20.0


def _house() -> dict[str, Json]:
    now = time.monotonic()
    if now - _state.house_at >= HOUSE_INTERVAL:
        _state.house_at = now
        try:
            import simsight_housemap as housemap

            house = housemap.build()
            _state.house_rooms = {r['id']: r for r in (house or {}).get('rooms', [])}
        except Exception:
            simsight_log.error('bridge: house map')
            _state.house_rooms = {}
    return _state.house_rooms


def _room_ref(obj: Any) -> Json | None:
    """The room an object or Sim is in, as the House tab names it, or None."""
    rooms = _house()
    if not rooms:
        return None
    room = None
    with _logged('room of'):
        import simsight_plus_rooms as plus_rooms

        if not _on_active_lot(obj, True):
            return None
        level = int(obj.level)
        rid = plus_rooms.room_id_at(obj.position.x, obj.position.z, level, obj.position.y)
        room = rooms.get(f'{rid}:{level}') or rooms.get('outside:0')
    if room is None:
        return None
    return {
        'id': room['id'],
        'kind': room['kind'],
        'number': room.get('number', 0),
        'customName': room.get('customName', ''),
        'floor': room.get('floor', 0),
    }


def _visitor(sim: Any, members: set[Any]) -> Json | None:
    """A Sim on the lot who is not in the household, or None."""
    entry: Json | None = None
    with _logged('visitor'):
        info = sim.sim_info
        if info.sim_id not in members and _on_active_lot(sim, False):
            entry = {'simId': str(info.sim_id), 'name': _full_name(info)}
            room = _room_ref(sim)
            if room is not None:
                entry['room'] = room
    return entry


def _visitors(household: Any) -> list[Json]:
    """Sims on the lot who are not in the household."""
    import services

    members = {info.sim_id for info in household.sim_info_gen()}
    out = []
    for sim in services.sim_info_manager().instanced_sims_gen():
        entry = _visitor(sim, members)
        if entry is not None:
            out.append(entry)
    return out[:10]


def _bills_owed(household: Any) -> int:
    try:
        owed = household.bills_manager.current_payment_owed
        owed = owed() if callable(owed) else owed
        return int(owed or 0)
    except (AttributeError, TypeError, ValueError):
        return 0  # no bills manager (some lots)


def _camera_follows() -> str:
    try:
        import camera
    except ImportError:
        return ''  # outside the game (offline checks)
    if getattr(camera, '_follow_mode', False) and getattr(camera, '_sim_id', None):
        return str(camera._sim_id)
    return ''


def _close_camera(sim: Any, client: Any) -> None:
    """Put the camera close behind the Sim's back."""
    import camera
    import sims4.math

    pos, fwd = sim.position, sim.forward
    eye = sims4.math.Vector3(pos.x + fwd.x * 3.0, pos.y + 1.6, pos.z + fwd.z * 3.0)
    camera.focus_on_object_from_position(obj_position=pos, camera_position=eye, client=client)


def _set_camera(sim_id: Any, mode: str) -> Answer:
    """Follow a Sim with the game camera, close up, or let it go."""
    import camera
    import services

    _sim_unused, client = _sim_and_client()
    try:
        info = services.sim_info_manager().get(int(sim_id))
    except (TypeError, ValueError):
        info = None
    sim = info.get_sim_instance() if info is not None else None
    if sim is None:
        return False, 'sim_unavailable'
    if mode == 'CAMERA_MODE_FREE':
        camera.cancel_focus(object=sim)
        return True, ''
    if mode == 'CAMERA_MODE_CLOSE':
        with _logged('close camera'):
            _close_camera(sim, client)
    camera.focus_on_sim(sim=sim, follow=True, client=client)
    return True, ''


# ---------------------------------------------------------------- inventory
# What the controlled Sim carries, and where each item can go (fridge,
# bookcase...): the game's drag and drop between inventories, in one tap.
# A whole stack moves; the destination's own rules decide what it accepts.

MAX_DESTINATIONS = 5


def _stack_count(obj: Any) -> int:
    try:
        return int(obj.stack_count())
    except (AttributeError, TypeError, ValueError):
        return 1  # not a stack


def _inventory_items(sim: Any) -> list[Any]:
    seen, out = set(), []
    for obj in list(sim.inventory_component or ()):
        try:
            stack = obj.inventoryitem_component.get_stack_id()
        except AttributeError:
            stack = obj.id  # no inventory item component: on its own
        if stack in seen:
            continue
        seen.add(stack)
        out.append(obj)
    return out


def _destination(holder: Any, item: Any, d: float) -> Json | None:
    """The holder as a place for the item, or None when it does not take it."""
    row = None
    with _logged('destination'):
        if holder.inventory_component.can_add(item):
            group, kind, _keywords = _classify(holder)
            row = {
                'id': str(holder.id),
                'name': _object_name(holder),
                'group': group,
                'kind': kind,
                'distance': int(round(d)),
            }
    return row


def _destinations(sim: Any, item: Any, holders: list[tuple[float, Any]]) -> list[Json]:
    out = []
    for d, holder in holders:
        row = _destination(holder, item, d)
        if row is not None:
            out.append(row)
        if len(out) >= MAX_DESTINATIONS:
            break
    return out


def _holder_distance(obj: Any, sim: Any) -> float | None:
    """How far an object with an inventory is, or None when it does not count."""
    distance = None
    with _logged('inventory holder'):
        if (
            not getattr(obj, 'is_sim', False)
            and not obj.is_hidden()
            and getattr(obj, 'inventory_component', None) is not None
            and _on_active_lot(obj, True)
        ):
            distance = _distance(obj.position, sim.position)
    return distance


def _inventory_row(sim: Any, item: Any, holders: list[tuple[float, Any]]) -> Json | None:
    try:
        group, kind, _keywords = _classify(item)
        return {
            'item': {
                'id': str(item.id),
                'name': _object_name(item),
                'group': group,
                'kind': kind,
            },
            'count': _stack_count(item),
            'destinations': _destinations(sim, item, holders),
        }
    except Exception:
        simsight_log.error('bridge: inventory item')
        return None


def list_inventory() -> Answer:
    import services

    sim, _client = _sim_and_client()
    if sim is None or getattr(sim, 'inventory_component', None) is None:
        return False, 'sim_unavailable'
    holders = []
    for obj in services.object_manager().get_valid_objects_gen():
        d = _holder_distance(obj, sim)
        if d is not None:
            holders.append((d, obj))
    holders.sort(key=lambda pair: pair[0])
    items = []
    for item in _inventory_items(sim):
        row = _inventory_row(sim, item, holders)
        if row is not None:
            items.append(row)
    return True, '', {'inventory': items}


def move_item(item_id: Any, destination_id: Any) -> Answer:
    import services

    sim, _client = _sim_and_client()
    if sim is None:
        return False, 'sim_unavailable'
    source = sim.inventory_component
    item = next((o for o in _inventory_items(sim) if str(o.id) == str(item_id)), None)
    if item is None:
        return False, 'target_unavailable'
    try:
        holder = services.object_manager().get(int(destination_id))
    except (TypeError, ValueError):
        holder = None
    if holder is None or getattr(holder, 'inventory_component', None) is None:
        return False, 'target_unavailable'
    target = holder.inventory_component
    if not target.can_add(item):
        return False, 'not_accepted'
    if not source.try_remove_object_by_id(item.id, count=_stack_count(item)):
        return False, 'refused'
    if target.player_try_add_object(item):
        return True, ''
    # Put it back rather than lose it.
    source.player_try_add_object(item)
    return False, 'not_accepted'


# ---------------------------------------------------------------- blocked Sims
# When a Sim cannot reach where an action happens (no door, no stairs,
# something in the way), the game cancels the action and only shows a
# "blocked" bubble. interactions.utils.route_fail.route_failure plays it:
# report it to the phone and the Mac voice with the game's reason.

ROUTE_REPEAT = 10.0  # seconds before the same Sim and action is reported again


def _install_route_fail() -> None:
    from interactions.utils import route_fail

    original = route_fail.route_failure
    if getattr(original, '_simsight_phone', False):
        return

    def route_failure(
        sim: Any,
        interaction: Any,
        failure_reason: Any,
        failure_object_id: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            _report_route_fail(sim, interaction, failure_reason, failure_object_id)
        except Exception:
            simsight_log.error('bridge: route failure report')
        return original(sim, interaction, failure_reason, failure_object_id, *args, **kwargs)

    setattr(route_failure, '_simsight_phone', True)  # noqa: B010 - marker on a function
    route_fail.route_failure = route_failure


def _action_name(interaction: Any) -> str:
    action = ''
    with _logged('route failure action'):
        action = text_of(interaction.get_name(), '')
    if not action:
        action = _pretty(
            getattr(getattr(interaction, 'affordance', None), '__name__', ''), ('sim-', 'si_')
        )
    return action


def _failure_object_name(failure_object_id: Any) -> str:
    """What was in the way: an object's name or a Sim's name, or ''."""
    import services

    if not failure_object_id:
        return ''
    obj = services.object_manager().get(failure_object_id)
    if obj is None:
        return ''
    if not getattr(obj, 'is_sim', False):
        return _object_name(obj)
    return _full_name(obj.sim_info)


def _report_route_fail(
    sim: Any, interaction: Any, failure_reason: Any, failure_object_id: Any
) -> None:
    import services

    household = services.active_household()
    info = getattr(sim, 'sim_info', None)
    if info is None or household is None or info.household is not household:
        return
    action = _action_name(interaction)
    now = time.monotonic()
    key = (info.sim_id, action)
    if now - _state.route_seen.get(key, -1e9) < ROUTE_REPEAT:
        return
    _state.route_seen[key] = now
    reason = getattr(failure_reason, 'name', '') or str(failure_reason or '')
    obj_name = _failure_object_name(failure_object_id)
    log(f'route failure: {info.first_name} {action} ({reason}, {obj_name})')
    send(
        {
            'event': {
                'kind': 'EVENT_KIND_ROUTE_FAIL',
                'simId': str(info.sim_id),
                'simName': info.first_name,
                'subject': action,
                'detail': reason,
                'objectName': obj_name,
            }
        }
    )


# ---------------------------------------------------------------- things to deal with
# Puddles, broken objects, spoiled food, fire on the lot: listed for the
# phone's "Needs attention" card. Scanning every object takes a moment, so
# it runs every few seconds and the state reuses the last answer.

PROBLEM_INTERVAL = 4.0
_PROBLEM_ORDER = {'FIRE': 0, 'BROKEN': 1, 'PUDDLE': 2, 'SPOILED': 3, 'FOOD': 4}


def _problems() -> list[Json]:
    now = time.monotonic()
    if now - _state.problems_at < PROBLEM_INTERVAL:
        return _state.problems
    _state.problems_at = now
    try:
        _state.problems = _scan_problems()
    except Exception:
        simsight_log.error('bridge: problems')
        _state.problems = []
    return _state.problems


class _ProblemTypes:
    """The game's classes and states that make an object a problem; each is
    missing (None or empty) when this game version does not have it."""

    def __init__(self) -> None:
        self.puddle: Any = None
        self.fire: Any = None
        self.broken_states: tuple[Any, ...] = ()
        self.spoiled: Any = None
        with contextlib.suppress(ImportError, AttributeError):
            from objects.puddles.puddle import Puddle

            self.puddle = Puddle
        with contextlib.suppress(ImportError, AttributeError):
            from objects.fire.fire import Fire

            self.fire = Fire
        with contextlib.suppress(ImportError, AttributeError, TypeError):
            from objects.components.state import StateComponent

            self.broken_states = tuple(StateComponent.BROKEN_STATE_SET or ())
        with contextlib.suppress(ImportError, AttributeError):
            from crafting.crafting_tunable import CraftingTuning

            self.spoiled = CraftingTuning.SPOILED_STATE_VALUE


def _has_state(obj: Any, value: Any) -> bool:
    if value is None:
        return False
    try:
        return bool(obj.has_state(value.state) and obj.get_state(value.state) is value)
    except Exception:
        simsight_log.error('bridge: object state')
        return False


def _state_active(obj: Any, value: Any) -> bool:
    try:
        return bool(obj.state_value_active(value))
    except Exception:
        simsight_log.error('bridge: object state value')
        return False


def _problem_listable(obj: Any) -> bool:
    """A visible object on the lot, not a Sim and not in an inventory."""
    if getattr(obj, 'is_sim', False) or obj.is_hidden():
        return False
    if not _on_active_lot(obj, True):
        return False
    in_inventory = getattr(obj, 'is_in_inventory', None)
    return in_inventory is None or not in_inventory()


def _problem_kind(obj: Any, types: _ProblemTypes) -> str | None:
    if types.fire is not None and isinstance(obj, types.fire):
        return 'FIRE'
    if types.puddle is not None and isinstance(obj, types.puddle):
        return 'PUDDLE'
    if _has_state(obj, types.spoiled):
        return 'SPOILED'
    if types.broken_states and any(_state_active(obj, v) for v in types.broken_states):
        return 'BROKEN'
    if getattr(obj, 'consumable_component', None) is not None:
        parent = getattr(obj, 'parent', None)
        if parent is not None and getattr(parent, 'is_sim', False):
            return None  # in a Sim's hands: being eaten
        return 'FOOD'
    return None


def _problem(obj: Any, sim: Any, types: _ProblemTypes) -> Json | None:
    entry: Json | None = None
    with _logged('problem object'):
        kind = _problem_kind(obj, types) if _problem_listable(obj) else None
        if kind is not None:
            entry = {'kind': kind, 'targetId': str(obj.id), 'name': _object_name(obj)}
            if sim is not None:
                entry['distance'] = int(round(_distance(obj.position, sim.position)))
    return entry


def _scan_problems() -> list[Json]:
    import services

    sim = services.get_active_sim()
    types = _ProblemTypes()
    out = []
    for obj in services.object_manager().get_valid_objects_gen():
        entry = _problem(obj, sim, types)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda e: (_PROBLEM_ORDER[e['kind']], e.get('distance', 0)))
    # Fire burns in several pieces: one line is enough.
    fires = [e for e in out if e['kind'] == 'FIRE']
    food = [e for e in out if e['kind'] == 'FOOD'][:3]
    rest = [e for e in out if e['kind'] not in ('FIRE', 'FOOD')][:12]
    return fires[:1] + rest + food


def _open_dialogs() -> list[Json]:
    if _dialogs is None:
        return []
    try:
        return _dialogs.active()
    except Exception:
        simsight_log.error('bridge: dialogs')
        return []


def _publish_state() -> None:
    try:
        state = build_state()
    except Exception:
        simsight_log.error('bridge: state')
        return
    body = json.dumps(state, sort_keys=True, ensure_ascii=False)
    if body == _state.last_state_json:
        return
    _state.last_state_json = body
    _state.seq += 1
    state['seq'] = str(_state.seq)
    send({'state': state})


# ---------------------------------------------------------------- playing from the phone
# Targets and pie menus as the game builds them for a click (EA's ChoiceMenu:
# same tests, visibility, names, greyed reasons, social sub-menus), and the
# chosen row run with test_and_execute, like the mod's phone list.

MAX_OBJECTS = 600  # nearest objects listed (a big house has a few hundred)
MAX_DISTANCE = 60.0  # metres
PHONE_ID = 'phone'  # the controlled Sim's phone, a target of its own


def _sim_and_client() -> tuple[Any, Any]:
    import services

    sim = services.get_active_sim()
    client = services.client_manager().get_first_client()
    return sim, client


def _custom_name(obj: Any) -> str:
    """The name the player gave the object, or ''."""
    try:
        if obj.has_custom_name():
            return str(obj.custom_name or '')
    except AttributeError:
        pass  # objects that cannot be renamed
    return ''


def _object_name(obj: Any) -> str:
    custom = _custom_name(obj)
    if custom:
        return custom
    fallback = _pretty(getattr(obj.definition, 'name', '') or type(obj).__name__)
    try:
        raw = obj.catalog_name
        key = int(getattr(raw, 'hash', raw)) & 0xFFFFFFFF
    except (AttributeError, TypeError, ValueError):
        key = 0  # no catalog name: the tuning name will do
    if not key:
        return fallback
    cached = _cached_text(key)
    if cached is None:
        return fallback
    return _fill_tokens(cached, []) or fallback


def _loc_key_text(key: int) -> str:
    """Game text for a string-table key, '' until the UI resolves it."""
    if not key:
        return ''
    cached = _cached_text(key)
    return '' if cached is None else _fill_tokens(cached, [])


def _classify(obj: Any) -> tuple[str, str, str]:
    """(group, kind, keywords) from the scanner's catalog classification."""
    scanner = sys.modules.get('simsight_scanner')
    buycat = sys.modules.get('simsight_buycat')
    if scanner is None:
        return 'MISC', '', ''
    try:
        sub, group, names, _flags = scanner._classification(obj, time.monotonic())
    except Exception:
        simsight_log.error('bridge: classify')
        return 'MISC', '', ''
    kind = ''
    if buycat is not None:
        kind = _loc_key_text(buycat.TAG_LABEL_HASH.get(sub, 0))
    words = {
        _pretty(re.sub(r'^(BuyCat[A-Z]*_|Func_)', '', name))
        for name in names or ()
        if name.startswith(('BuyCat', 'Func_'))
    }
    return group, kind, ' '.join(sorted(words))


def _sim_targets(sim: Any) -> list[Json]:
    """The controlled Sim, its phone, then the other Sims, nearest first."""
    import services

    targets: list[Json] = [
        {
            'id': str(sim.id),
            'name': _full_name(sim.sim_info),
            'isSim': True,
            'isSelf': True,
            'group': 'SIMS',
            'distance': 0,
        },
        {'id': PHONE_ID, 'name': 'Phone', 'group': 'PHONE'},
    ]
    others = []
    for other in services.sim_info_manager().instanced_sims_gen():
        if other is sim:
            continue
        with _logged('target Sim'):
            others.append((_distance(other.position, sim.position), other))
    for d, other in sorted(others, key=lambda pair: pair[0]):
        targets.append(
            {
                'id': str(other.id),
                'name': _full_name(other.sim_info),
                'isSim': True,
                'group': 'SIMS',
                'distance': int(round(d)),
            }
        )
    return targets


def _target_distance(obj: Any, sim: Any) -> float | None:
    """How far an object the Sim can use is, or None when it does not count."""
    distance = None
    with _logged('target object'):
        if getattr(obj, 'is_sim', False) or getattr(obj, 'is_terrain', False) or obj.is_hidden():
            return None
        checker = getattr(obj, 'is_in_inventory', None)
        if (checker is None or not checker()) and list(obj.super_affordances()):
            d = _distance(obj.position, sim.position)
            if d <= MAX_DISTANCE:
                distance = d
    return distance


def _object_target(obj: Any, d: float) -> Json | None:
    try:
        group, kind, keywords = _classify(obj)
        return {
            'id': str(obj.id),
            'name': _object_name(obj),
            'group': group,
            'kind': kind,
            'keywords': keywords,
            'distance': int(round(d)),
        }
    except Exception:
        simsight_log.error('bridge: target row')
        return None


def list_targets() -> list[Json]:
    import services

    sim, _client = _sim_and_client()
    if sim is None:
        return []
    targets = _sim_targets(sim)
    objects = []
    for obj in services.object_manager().get_valid_objects_gen():
        d = _target_distance(obj, sim)
        if d is not None:
            objects.append((d, obj))
    objects.sort(key=lambda pair: pair[0])
    for d, obj in objects[:MAX_OBJECTS]:
        row = _object_target(obj, d)
        if row is not None:
            targets.append(row)
    return targets


def _target(target_id: str, sim: Any) -> Any:
    import services

    try:
        key = int(target_id)
    except ValueError:
        return None
    if key == sim.id:
        return sim
    return services.object_manager().get(key)


def _category_root(category: Any) -> Any:
    depth = 0
    while category is not None and getattr(category, '_parent', None) is not None and depth < 8:
        category = category._parent
        depth += 1
    return category


def _pick_place(obj: Any) -> tuple[int, int]:
    """(lot id, level) for a click on the object."""
    import services

    try:
        lot_id = int(services.active_lot().lot_id)
    except (AttributeError, TypeError, ValueError):
        lot_id = 0  # no active lot
    try:
        level = int(obj.level)
    except (AttributeError, TypeError, ValueError):
        level = 0  # objects without a level
    return lot_id, level


def _add_object_rows(menu: Any, sim: Any, client: Any, obj: Any) -> None:
    """What a click on the object offers (and on what it forwards clicks to)."""
    from server.pick_info import PickInfo, PickType

    parameters = client.get_interaction_parameters() or {}
    lot_id, level = _pick_place(obj)
    pick_type = PickType.PICK_SIM if getattr(obj, 'is_sim', False) else PickType.PICK_OBJECT
    targets = [obj] + [
        t
        for t in (getattr(obj, 'forwarded_pick_targets', None) or ())
        if t is not None and t is not obj
    ]
    for target in targets:
        pick = PickInfo(
            pick_type=pick_type,
            target=target,
            location=obj.position,
            routing_surface=obj.routing_surface,
            lot_id=lot_id,
            level=level,
        )
        context = client.create_interaction_context(sim, pick=pick)
        context.add_preferred_objects((obj,))
        menu.add_potential_aops(
            target, context, target.potential_interactions(context, **parameters), None
        )
        if getattr(target, 'is_sim', False) and target is not sim:
            try:
                sim.fill_choices_menu_with_si_state_aops(target, context, menu, None)
            except Exception as ex:
                log(f'social rows unavailable: {ex!r}')


def get_interactions(target_id: str) -> Answer:
    from interactions.choices import ChoiceMenu

    sim, client = _sim_and_client()
    if sim is None or client is None:
        return False, 'sim_unavailable'
    menu = ChoiceMenu(sim)
    if target_id == PHONE_ID:
        # The Sim's own phone: calls, invitations, travel, services...
        context = client.create_interaction_context(sim)
        menu.add_potential_aops(None, context, sim.potential_phone_interactions(context), None)
        return _menu_rows(target_id, sim, menu)
    obj = _target(target_id, sim)
    if obj is None:
        return False, 'target_unavailable'
    _add_object_rows(menu, sim, client, obj)
    return _menu_rows(target_id, sim, menu)


def _menu_row(sim: Any, option_id: Any, item: Any, categories: Any) -> Json | None:
    """One pie menu row as the game shows it, or None when the game hides it."""
    aop = item.aop
    affordance = aop.affordance
    visible, _suppression = sim.test_pie_menu_modifiers(affordance)
    if not visible:
        return None
    modifier_tooltip = sim.apply_forbid_by_object_tags_pie_menu_modifiers(aop)
    fallback = _pretty(getattr(affordance, '__name__', ''), ('sim-', 'si_', 'mixer_'))
    name = text_of(affordance.get_name(aop.target, item.context, **aop.interaction_parameters), '')
    tooltip = modifier_tooltip or item.result.tooltip
    reason = ''
    if tooltip is not None:
        reason = text_of(
            affordance.create_localized_string(
                tooltip,
                context=item.context,
                target=aop.target,
                **aop.interaction_parameters,
            )
        )
    category = categories.get(item.category_key) if item.category_key else None
    root = _category_root(category)
    return {
        'id': str(option_id),
        'name': name or fallback,
        'category': text_of(root._display_name(sim)) if root is not None else '',
        'enabled': bool(item.result) and not modifier_tooltip,
        'reason': reason,
        # The game's own name is not known yet (the row shows the fallback).
        'pending': not name,
    }


def _menu_rows(target_id: str, sim: Any, menu: Any) -> Answer:
    """Rows of a ChoiceMenu as the game shows them (visible, named, greyed reasons)."""
    import services
    import sims4.resources

    categories = services.get_instance_manager(sims4.resources.Types.PIE_MENU_CATEGORY)
    tutorial = services.get_tutorial_service()
    rows, options = [], {}
    pending = False
    misses = _state.loc_misses
    for option_id, item in menu:
        row = None
        with _logged('menu row'):
            if tutorial is None or tutorial.is_affordance_visible(item.aop.affordance):
                row = _menu_row(sim, option_id, item, categories)
        if row is None:
            continue
        pending = pending or row.pop('pending')
        rows.append(row)
        options[str(option_id)] = (item.aop, item.context)
    _state.menus.clear()  # only the latest menu can be run
    _state.menus[str(target_id)] = options
    return True, '', {'interactions': rows, 'namesPending': pending or _state.loc_misses != misses}


def run_interaction(target_id: str, option_id: str) -> Answer:
    entry = _state.menus.get(str(target_id), {}).get(str(option_id))
    if entry is None:
        return False, 'menu_expired'
    aop, context = entry
    try:
        result = aop.test_and_execute(context)
    except Exception:
        simsight_log.error('bridge: run interaction')
        return False, 'error'
    if not result:
        log(f'interaction refused: {result}')
        return False, 'refused'
    return True, ''


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
        return True
    except Exception:
        simsight_log.error(f'bridge: open {url}')
        return False


def open_pairing_page() -> bool:
    """Show the phone link's QR code and pairing code."""
    return _open_on_mac(PAIR_URL, 'Phone link opened in the browser.')


def open_setup_page() -> bool:
    """SimSight setup: phone address, meeting point, disconnect phones."""
    return _open_on_mac(SETUP_URL, 'SimSight setup opened in the browser.')


try:
    import sims4.commands

    @sims4.commands.Command(
        'simsight.companion', 'a11y.companion', command_type=sims4.commands.CommandType.Live
    )
    @real_annotations
    def a11y_companion(_connection: Any = None) -> bool:
        """Open the phone companion's QR code on the Mac."""
        return open_pairing_page()

    @sims4.commands.Command('simsight.setup', command_type=sims4.commands.CommandType.Live)
    @real_annotations
    def simsight_setup(_connection: Any = None) -> bool:
        """Open SimSight setup on the Mac (phone address, meeting point)."""
        return open_setup_page()
except Exception:
    simsight_log.error('bridge: companion commands')


# ---------------------------------------------------------------- startup
# Windows reads the pipe through msvcrt and ctypes (PeekNamedPipe); the Mac
# takes its keys from the phone (simsight_mackeys). Never ctypes on the Mac.

_dialogs: Any = None
_mackeys: Any = None
_msvcrt: Any = None
_ctypes: Any = None
if IS_WINDOWS:
    try:
        _msvcrt = importlib.import_module('msvcrt')
        _ctypes = importlib.import_module('ctypes')
    except Exception:
        IS_WINDOWS = False  # no way to read the pipe without blocking: no companion
        simsight_log.error('bridge: companion unavailable on Windows')
if IS_MAC:
    try:
        _mackeys = importlib.import_module('simsight_mackeys')
    except Exception:
        _mackeys = None
        simsight_log.error('bridge: keyboard unavailable')
if IS_MAC or IS_WINDOWS:
    try:
        _install_route_fail()
    except Exception:
        simsight_log.error('bridge: route failure hook unavailable')
    try:
        _dialogs = importlib.import_module('simsight_dialogs')
        _dialogs.install()
    except Exception:
        _dialogs = None
        simsight_log.error('bridge: dialogs unavailable')
    try:
        _install_tick()
        start()
    except Exception:
        simsight_log.error('bridge: install')

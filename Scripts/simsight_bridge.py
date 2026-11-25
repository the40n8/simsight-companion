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
import json
import os
import re
import sys
import threading
import time

try:
    import subprocess
except ImportError:
    subprocess = None

import simsight_speech as speech

IS_MAC = sys.platform == 'darwin'
IS_WINDOWS = sys.platform == 'win32'
BRIDGE = 'simsight-bridge.exe' if IS_WINDOWS else 'simsight-bridge'
CREATE_NO_WINDOW = 0x08000000   # Windows: no console window for the bridge
SPEAKER = 'simsight-say'
STATE_INTERVAL = 1.0      # seconds between state snapshots (sent only if changed)
POLL_INTERVAL = 0.1       # seconds between command polls
LOC_PER_TICK = 20         # string-table keys requested from the UI per poll

_proc = None
_voice = None             # last voice settings, re-sent to a restarted bridge
_last_spawn = 0.0
RESPAWN_DELAY = 2.0       # seconds before restarting a bridge that exited
_write_lock = threading.Lock()
_read_buf = b''
_last_state_json = None
_last_state_time = 0.0
_last_poll = 0.0
_seq = 0


def log(message):
    speech.log_line('bridge: ' + message)


# ---------------------------------------------------------------- process

def _find(name):
    for folder in (speech.MODS_FOLDER, os.path.join(speech._mods_root(), 'SimSight')):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def start():
    """Start the bridge if needed. Returns True when it is running."""
    global _proc, _last_spawn, _read_buf, _last_state_json
    if _proc is not None:
        if _proc.poll() is None:
            return True
        # It exited (crash, or replaced during development): start it again,
        # at most every RESPAWN_DELAY seconds.
        if time.monotonic() - _last_spawn < RESPAWN_DELAY:
            return False
        log('restarting (previous one exited with {})'.format(_proc.returncode))
        _proc = None
        _read_buf = b''
        _last_state_json = None   # the new bridge needs a full state
    _last_spawn = time.monotonic()
    path = _find(BRIDGE)
    if not (IS_MAC or IS_WINDOWS) or subprocess is None or path is None:
        return False
    args = [path]
    extra = {}
    if IS_WINDOWS:
        # The screen reader speaks on Windows: the bridge only listens.
        args += ['--speaker', 'none']
        extra['creationflags'] = CREATE_NO_WINDOW
    else:
        speaker = _find(SPEAKER)
        if speaker:
            args += ['--speaker', speaker]
    try:
        _proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, close_fds=True, **extra)
        if not IS_WINDOWS:
            os.set_blocking(_proc.stdout.fileno(), False)
    except Exception as ex:
        log('start failed: {!r}'.format(ex))
        _proc = None
        return False
    log('started (pid {})'.format(_proc.pid))
    send({'hello': {'modVersion': 'windows' if IS_WINDOWS else 'mac', 'gameVersion': _game_version()}})
    if _voice is not None:
        send({'voice': _voice})
    return True


def alive():
    return _proc is not None and _proc.poll() is None


def write_line(data):
    """One complete line to the bridge. Safe from any thread (speech timers)."""
    if not alive():
        return False
    with _write_lock:
        try:
            _proc.stdin.write(data + b'\n')
            _proc.stdin.flush()
            return True
        except Exception as ex:
            log('write failed: {!r}'.format(ex))
            return False


def send(message):
    return write_line(json.dumps(message, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))


def mirror(text, interrupt):
    """Windows: a line the screen reader just said, for the phone's history."""
    if not alive():
        return False
    from urllib.parse import quote
    return write_line((('I' if interrupt else 'Q') + quote(text)).encode('ascii'))


def set_voice(rate, volume, name, lang):
    global _voice
    _voice = {'rate': int(rate), 'volume': int(volume), 'name': name or '', 'lang': lang or ''}
    send({'voice': _voice})


class SpeechPipe(object):
    """Stands in for the fallback-voice process the speech module expects:
    `.stdin.write()/.flush()` send lines to the bridge, `.kill()` (used when
    voice settings change) leaves the bridge running; the speech module then
    calls set_voice again with the new settings."""

    def __init__(self):
        self.stdin = self
        self._buf = b''

    def write(self, data):
        self._buf += data
        while b'\n' in self._buf:
            line, self._buf = self._buf.split(b'\n', 1)
            write_line(line)

    def flush(self):
        pass

    def poll(self):
        return None if alive() else 1

    def kill(self):
        pass


def _game_version():
    try:
        import sims4.common
        return str(sims4.common.get_version() if hasattr(sims4.common, 'get_version') else '')
    except Exception:
        return ''


# ---------------------------------------------------------------- tick

def _tick():
    """Runs on the game's tick (simulation thread), every frame."""
    global _last_poll, _last_state_time
    if not alive() and not start():
        return
    now = time.monotonic()
    if now - _last_poll >= POLL_INTERVAL:
        _last_poll = now
        _read_commands()
        _request_localization()
        if _dialogs is not None:
            _dialogs.flush_notes()
    if _mackeys is not None:
        try:
            _mackeys.tick(send)
        except Exception as ex:
            log('keys tick failed: {!r}'.format(ex))
    if now - _last_state_time >= STATE_INTERVAL:
        _last_state_time = now
        _publish_state()


def _install_tick():
    import sims4.core_services as core
    original = core.on_tick
    if getattr(original, '_simsight_bridge', False):
        return

    def on_tick(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            _tick()
        except Exception as ex:
            log('tick failed: {!r}'.format(ex))
        return result

    on_tick._simsight_bridge = True
    core.on_tick = on_tick


def _waiting_bytes():
    """Windows pipes cannot be made non-blocking: ask how much is waiting
    (PeekNamedPipe), so reading never blocks the game's tick."""
    handle = _msvcrt.get_osfhandle(_proc.stdout.fileno())
    avail = _ctypes.c_ulong(0)
    if not _ctypes.windll.kernel32.PeekNamedPipe(_ctypes.c_void_p(handle), None, 0, None,
                                                  _ctypes.byref(avail), None):
        return 0   # closed: the bridge exited (alive() notices)
    return avail.value


def _read_commands():
    global _read_buf
    try:
        if IS_WINDOWS:
            waiting = _waiting_bytes()
            if not waiting:
                return
            chunk = os.read(_proc.stdout.fileno(), min(waiting, 65536))
        else:
            chunk = os.read(_proc.stdout.fileno(), 65536)
    except BlockingIOError:
        return
    except Exception as ex:
        log('read failed: {!r}'.format(ex))
        return
    if not chunk:
        return
    _read_buf += chunk
    while b'\n' in _read_buf:
        line, _read_buf = _read_buf.split(b'\n', 1)
        if line.strip():
            _handle(line)


def _handle(line):
    try:
        cmd = json.loads(line.decode('utf-8'))
    except Exception as ex:
        log('bad command: {!r}'.format(ex))
        return
    cmd_id = cmd.get('id', '')
    extra = {}
    try:
        answer = _run(cmd)
        ok, reason = answer[0], answer[1]
        if len(answer) > 2:
            extra = answer[2]
    except Exception as ex:
        log('command failed: {!r}'.format(ex))
        ok, reason = False, 'error'
    outcome = {'ok': True} if ok else {'reason': reason}
    result = {'id': cmd_id, 'outcome': outcome}
    result.update(extra)
    send({'result': result})
    # Let the phone see the effect right away.
    global _last_state_time
    _last_state_time = 0.0


def _in_game():
    import services
    zone = services.current_zone()
    return (zone is not None and getattr(zone, 'is_zone_running', False)
            and services.active_household() is not None)


_SPEEDS = {'SPEED_PAUSED': 'paused', 'SPEED_NORMAL': 'one', 'SPEED_FAST': 'two', 'SPEED_ULTRA': 'three'}


def _run(cmd):
    if 'setNarration' in cmd:
        narration = cmd['setNarration'].get('narration', {})
        speech.set_mode('hover' if narration.get('macVoice') else 'off')
        import simsight_settings as settings
        settings.set('sim_actions', bool(narration.get('simActions')))
        return True, ''
    if 'say' in cmd:
        # Windows: the bridge's announcements and pairing code, spoken by the
        # screen reader (the Mac bridge has its own voice).
        text = cmd['say'].get('text', '')
        if text:
            speech.speak(text, interrupt=bool(cmd['say'].get('interrupt')), source='event', explicit=True)
        return True, ''
    if not _in_game():
        return False, 'not_in_game'
    if 'setSpeed' in cmd:
        name = _SPEEDS.get(cmd['setSpeed'].get('speed', ''))
        if name is None:
            return False, 'error'
        return _set_speed(name), 'error'
    if 'selectSim' in cmd:
        import simsight_hub as hub
        return hub.select_sim(int(cmd['selectSim'].get('simId', '0'))), 'sim_unavailable'
    if 'listTargets' in cmd:
        return True, '', {'targets': list_targets()}
    if 'key' in cmd:
        # Keys only reach a loaded game (the in-game check above drops the rest).
        k = cmd['key']
        if _mackeys is None:
            return False, 'error'
        if k.get('family') == 'focus':
            _mackeys.focus_lost()
            return True, ''
        return _mackeys.handle(k.get('family', ''), int(k.get('vk', 0)), bool(k.get('down')),
                               bool(k.get('shift'))), 'not_owned'
    if 'listInventory' in cmd:
        return list_inventory()
    if 'moveItem' in cmd:
        c = cmd['moveItem']
        return move_item(c.get('itemId', ''), c.get('destinationId', ''))
    if 'setCamera' in cmd:
        c = cmd['setCamera']
        return _set_camera(c.get('simId', ''), c.get('mode', 'CAMERA_MODE_FOLLOW'))
    if 'respondDialog' in cmd:
        if _dialogs is None:
            return False, 'error'
        c = cmd['respondDialog']
        return _dialogs.respond(c.get('dialogId', ''), c.get('choiceId', 0), c.get('rowIds', []), c.get('text', ''))
    if 'openMenu' in cmd:
        if cmd['openMenu'].get('menu') != 'hub':
            return False, 'unknown_command'
        import simsight_hub as hub
        hub.open_hub()
        return True, ''
    if 'getHouseMap' in cmd:
        import simsight_housemap as housemap
        misses = _loc_misses
        house = housemap.build()
        if house is None:
            return False, 'not_in_game'
        if _loc_misses != misses:
            house['namesPending'] = True
        return True, '', {'houseMap': house}
    if 'getInteractions' in cmd:
        return get_interactions(cmd['getInteractions'].get('targetId', ''))
    if 'runInteraction' in cmd:
        c = cmd['runInteraction']
        return run_interaction(c.get('targetId', ''), c.get('interactionId', ''))
    if 'cancelAction' in cmd:
        import services
        import simsight_hub as hub
        c = cmd['cancelAction']
        sim = services.get_active_sim()
        if sim is None or str(sim.id) != c.get('simId'):
            return False, 'sim_unavailable'
        return hub.cancel_actions(sim.id, (int(c.get('actionId', '0')),)), 'not_cancelable'
    return False, 'unknown_command'


def _set_speed(name):
    """Through the game's own clock.setspeed command, like the speed buttons."""
    try:
        from server_commands import clock_commands
        clock_commands.set_speed(name)
        return True
    except Exception as ex:
        log('clock.setspeed failed, using the clock directly: {!r}'.format(ex))
    import services
    from clock import ClockSpeedMode, GameSpeedChangeSource
    mode = {'paused': ClockSpeedMode.PAUSED, 'one': ClockSpeedMode.NORMAL,
            'two': ClockSpeedMode.SPEED2, 'three': ClockSpeedMode.SPEED3}[name]
    services.game_clock_service().set_clock_speed(mode, source=GameSpeedChangeSource.GAMEPLAY)
    return True


# ---------------------------------------------------------------- localization
# Python only sees string-table keys; the patched UI resolves them in the
# player's language through the scanner's loc channel (A11y|loc / simsight.loc_enc).

_loc_wanted = set()
_loc_requested = set()


def _loc_cache():
    scanner = sys.modules.get('simsight_scanner')
    return getattr(scanner, '_loc_cache', {}) if scanner else {}


def _request_localization():
    scanner = sys.modules.get('simsight_scanner')
    if scanner is None or not _loc_wanted:
        return
    cache = scanner._loc_cache
    for key in list(_loc_wanted)[:LOC_PER_TICK]:
        _loc_wanted.discard(key)
        if key in cache or key in _loc_requested:
            continue
        _loc_requested.add(key)
        scanner._send_ui('A11y|loc|{}|{}'.format(key, key))


def _loc_value(value):
    """A LocalizedString proto from a tuned value (proto or factory)."""
    if value is None:
        return None
    if callable(value) and not hasattr(value, 'hash'):
        try:
            value = value()
        except Exception:
            return None
    return value if hasattr(value, 'hash') else None


_TOKEN = re.compile(r'\{([MF])(\d+)\.([^}]*)\}')
_PLURAL = re.compile(r'\{([SP])\.([^}]*)\}')
_FIELD = re.compile(r'\{(\d+)\.(\w+)\}')


def _fill_tokens(template, tokens):
    """Fill the common Sims placeholders: {0.SimFirstName}, {M0.he}{F0.she}..."""
    def token(i):
        return tokens[i] if 0 <= i < len(tokens) else None

    def gender(match):
        t = token(int(match.group(2)))
        female = bool(getattr(t, 'is_female', False)) if t is not None else False
        return match.group(3) if (match.group(1) == 'F') == female else ''

    def field(match):
        t = token(int(match.group(1)))
        if t is None:
            return ''
        name = match.group(2)
        first, last = getattr(t, 'first_name', ''), getattr(t, 'last_name', '')
        if name == 'SimFirstName':
            return first
        if name == 'SimLastName':
            return last
        if name in ('SimName', 'SimFullName'):
            return '{} {}'.format(first, last).strip()
        if name in ('Number', 'Money'):
            n = getattr(t, 'number', 0) or 0
            text = str(int(n)) if float(n).is_integer() else '{:g}'.format(n)
            return ('§' + text) if name == 'Money' else text
        return _token_text(t)

    # {S.Cereal}{P.Bowls of Cereal}: one item, so the singular form.
    text = _PLURAL.sub(lambda m: m.group(2) if m.group(1) == 'S' else '', template)
    text = _TOKEN.sub(gender, text)
    text = _FIELD.sub(field, text)
    return re.sub(r'\{[^}]*\}', '', text).replace('  ', ' ').strip()


def _token_text(t):
    """A token's own text: a nested game string, raw text, or an object's name."""
    try:
        if t.HasField('text_string'):
            return text_of(t.text_string)
    except Exception:
        pass
    for attr in ('raw_text', 'custom_name'):
        value = getattr(t, attr, '') or ''
        if value:
            return value
    key = getattr(t, 'catalog_name_key', 0) or 0
    return _loc_key_text(key) if key else ''


_loc_misses = 0   # game strings asked for but not known yet (names still loading)


def text_of(value, fallback=''):
    """Game text for a localized value, or fallback until the UI resolves it."""
    loc = _loc_value(value)
    if loc is None:
        return fallback
    try:
        key = int(loc.hash)
    except Exception:
        return fallback
    if not key:
        return fallback
    cached = _loc_cache().get(key)
    if cached is None:
        global _loc_misses
        _loc_misses += 1
        _loc_wanted.add(key)
        return fallback
    tokens = list(getattr(loc, 'tokens', []) or [])
    return _fill_tokens(cached, tokens) or fallback


def _pretty(tuning_name, prefixes=()):
    """'Buff_Energized_Coffee' -> 'Energized Coffee' when no game text yet."""
    name = tuning_name or ''
    for p in prefixes:
        if name.lower().startswith(p):
            name = name[len(p):]
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name.replace('_', ' ').replace('-', ' '))
    return ' '.join(w.capitalize() if w.islower() else w for w in name.split())


# ---------------------------------------------------------------- state

_NEEDS = (('hunger', 'motive_hunger'), ('bladder', 'motive_bladder'), ('energy', 'motive_energy'),
          ('hygiene', 'motive_hygiene'), ('fun', 'motive_fun'), ('social', 'motive_social'))


def _need_level(p):
    if p < 15:
        return 'NEED_LEVEL_CRITICAL'
    if p < 30:
        return 'NEED_LEVEL_LOW'
    if p < 60:
        return 'NEED_LEVEL_OKAY'
    return 'NEED_LEVEL_GOOD'


def _needs(info):
    import simsight_plus_needs as needs
    out = []
    for key, stat in _NEEDS:
        p = needs.percent(info, stat)
        if p is not None:
            out.append({'key': key, 'percent': p, 'level': _need_level(p)})
    return out


def _mood_key(mood):
    name = getattr(mood, '__name__', '') or ''
    return re.sub(r'^mood_', '', name, flags=re.I).lower()


def _mood(info):
    buffs = getattr(info, 'Buffs', None)
    if buffs is None:
        return None
    mood = buffs.get_mood()
    if mood is None:
        return None
    try:
        intensity = int(buffs.get_mood_intensity())
    except Exception:
        intensity = 0
    name = ''
    names = getattr(mood, 'mood_names', None)
    if names:
        try:
            name = text_of(names[min(intensity, len(names) - 1)])
        except Exception:
            name = ''
    return {'key': _mood_key(mood), 'name': name, 'intensity': intensity}


def _moodlets(info):
    import services
    buffs = getattr(info, 'Buffs', None)
    if buffs is None:
        return []
    now = services.time_service().sim_now
    out = []
    for buff in buffs:
        if not getattr(buff, 'visible', False):
            continue
        # Buff instances share the Buff class; the tuning (name, mood, text)
        # is on buff_type.
        btype = getattr(buff, 'buff_type', None) or type(buff)
        tuning = getattr(btype, '__name__', '') or 'Buff'

        def tuned(attr):
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
        try:
            timeout = buff.get_timeout_time()
            if timeout:
                moodlet['minutesLeft'] = max(0, int((timeout - now).in_minutes()))
        except Exception:
            pass
        out.append(moodlet)
    return out


def _actions(sim):
    import simsight_hub as hub
    out = []
    for info, interaction, state in hub._queue_entries(sim):
        fallback = ''
        if interaction is not None and hasattr(interaction, 'affordance'):
            fallback = _pretty(getattr(interaction.affordance, '__name__', ''), ('sim-', 'si_', 'mixer_'))
        name = text_of(getattr(info, 'display_name', None), fallback)
        if not name:
            continue
        out.append({'id': str(info.interaction_id), 'name': name,
                    'state': 'ACTION_STATE_RUNNING' if state == 'running' else 'ACTION_STATE_QUEUED',
                    'cancelable': bool(getattr(info, 'user_cancelable', False))})
    return out


def _speed():
    import services
    from clock import ClockSpeedMode
    speed = services.game_clock_service().clock_speed
    return {ClockSpeedMode.PAUSED: 'SPEED_PAUSED', ClockSpeedMode.SPEED2: 'SPEED_FAST',
            ClockSpeedMode.SPEED3: 'SPEED_ULTRA', ClockSpeedMode.SUPER_SPEED3: 'SPEED_SUPER'}.get(speed, 'SPEED_NORMAL')


def _clock():
    import services
    clock = {'speed': _speed()}
    try:
        import simsight_plus_time as t
        now = services.time_service().sim_now
        clock['day'] = t.day_text(now)
        clock['time'] = t.clock_text(now)
    except Exception:
        pass
    return clock


def _career(info, entry):
    """Job or school, whether the Sim is there now, until when, next shift."""
    import services
    import simsight_plus_time as t
    tracker = getattr(info, 'career_tracker', None)
    if tracker is None:
        return
    for career in list(tracker.careers.values()):
        word = t._career_word(career)
        title = ''
        try:
            title = text_of(career.current_level_tuning.get_title(info))
        except Exception:
            pass
        if not title:
            try:
                title = text_of(career.display_name(info))
            except Exception:
                title = ''
        entry['career'] = title or _pretty(type(career).__name__, ('career_',))
        performance = _performance(career)
        if performance is not None:
            entry['performance'] = performance
        now = services.time_service().sim_now
        if career.currently_at_work:
            entry['awayAt'] = word
            try:
                entry['until'] = t.clock_text(now + career.time_until_end_of_work())
            except Exception:
                pass
            return
        try:
            span, start, _end = career.get_next_work_time()
            if start is not None:
                entry['nextShift'] = '{} {}'.format(t.day_text(start), t.clock_text(start))
        except Exception:
            pass
        return


# ---------------------------------------------------------------- goals
# Aspiration milestone, wants and fears, work tasks and promotion steps.
# Read only; every part is optional (packs and ages differ).

def _done(tracker, key, kind):
    try:
        check = getattr(tracker, 'objective_completed' if kind == 'objective' else 'milestone_completed')
        return bool(check(key))
    except Exception:
        return False


def _objectives(aspiration, tracker):
    out = []
    for objective in list(getattr(aspiration, 'objectives', None) or ()):
        name = text_of(getattr(objective, 'display_text', None), '')
        if not name:
            continue
        out.append({'name': name, 'done': _done(tracker, objective, 'objective')})
    return out


def _goals(info):
    goals = {}
    tracker = getattr(info, 'aspiration_tracker', None)
    track = getattr(info, 'primary_aspiration', None)
    if track is not None and tracker is not None:
        try:
            goals['aspiration'] = text_of(getattr(track, 'display_text', None), '')
            aspirations = track.get_aspirations()
            items = aspirations.items() if hasattr(aspirations, 'items') else enumerate(aspirations)
            for _level, milestone in sorted(items, key=lambda kv: kv[0]):
                if not _done(tracker, milestone, 'milestone'):
                    goals['milestone'] = text_of(getattr(milestone, 'display_name', None), '')
                    goals['milestoneSteps'] = _objectives(milestone, tracker)
                    break
        except Exception as ex:
            log('aspiration of {} unavailable: {!r}'.format(info.first_name, ex))
    whims = getattr(info, 'whim_tracker', None)
    if whims is not None:
        wants = []
        for slot in list(getattr(whims, '_whim_slots', None) or ()):
            try:
                goal = getattr(slot, 'goal_instance', None)
                if goal is None:
                    continue
                name = text_of(goal.get_display_name(), '')
                if name:
                    kind = getattr(getattr(slot, 'whim_type', None), 'name', '')
                    wants.append({'name': name, 'fear': kind == 'CONFRONTATION'})
            except Exception:
                continue
        if wants:
            goals['wants'] = wants
    careers = getattr(info, 'career_tracker', None)
    if careers is not None:
        for career in list(careers.careers.values())[:1]:
            try:
                level = career.current_level_tuning
                tasks = []
                for metric in list(getattr(level.performance_metrics, 'statistic_metrics', None) or ()):
                    tip = getattr(metric, 'tooltip_text', None)
                    name = text_of(getattr(tip, 'general_description', None), '') if tip is not None else ''
                    if name:
                        tasks.append({'name': name, 'done': False})
                if tasks:
                    goals['workTasks'] = tasks
                promotion = getattr(level, 'aspiration', None)
                if promotion is not None and tracker is not None:
                    steps = _objectives(promotion, tracker)
                    if steps:
                        goals['promotionSteps'] = steps
            except Exception as ex:
                log('work goals of {} unavailable: {!r}'.format(info.first_name, ex))
    return goals


MAX_RELATIONSHIPS = 12


def _bit_label(bit, other, info):
    """A relationship label (Father, Sister...) in the gender of the Sim it
    describes: the game's text takes that Sim as token 0, {M0.Father}{F0.Mother}."""
    name = getattr(bit, 'display_name', None)
    if callable(name) and not hasattr(name, 'hash'):
        try:
            return text_of(name(other, info))
        except Exception:
            pass
    return text_of(name)


def _relationships(info):
    """Strongest relationships first: friendship, romance and the game's labels."""
    from relationships.relationship_track import RelationshipTrack
    tracker = info.relationship_tracker
    friendship = getattr(RelationshipTrack, 'FRIENDSHIP_TRACK', None)
    romance = getattr(RelationshipTrack, 'ROMANCE_TRACK', None)
    rows = []
    for other in list(tracker.get_target_sim_infos() or ()):
        if other is None:
            continue
        try:
            if tracker.is_hidden(other.sim_id):
                continue
        except Exception:
            pass
        f = r = 0
        try:
            f = int(round(tracker.get_relationship_score(other.sim_id, friendship) or 0)) if friendship else 0
            r = int(round(tracker.get_relationship_score(other.sim_id, romance) or 0)) if romance else 0
        except Exception:
            pass
        labels = []
        try:
            bits = [b for b in tracker.get_all_bits(other.sim_id) if getattr(b, 'visible', False)]
            bits.sort(key=lambda b: -(getattr(b, 'priority', 0) or 0))
            for bit in bits[:3]:
                label = _bit_label(bit, other, info)
                if label and label not in labels:
                    labels.append(label)
        except Exception:
            pass
        rows.append((max(abs(f), abs(r)) + (100 if labels else 0),
                     {'simId': str(other.sim_id), 'name': '{} {}'.format(other.first_name, other.last_name).strip(),
                      'friendship': f, 'romance': r, 'labels': labels}))
    rows.sort(key=lambda pair: -pair[0])
    return [row for _score, row in rows[:MAX_RELATIONSHIPS]]


def _skills(info):
    out = []
    for skill in list(info.all_skills()):
        try:
            if getattr(skill, 'hidden', False):
                continue
            level = int(skill.get_user_value())
            if level < 1:
                continue
            name = type(skill).__name__
            out.append({'key': name, 'name': text_of(getattr(skill, 'stat_name', None),
                                                    _pretty(name, ('statistic_skill_adultmajor_', 'statistic_skill_',
                                                                   'skill_', 'adultmajor_'))),
                        'level': level, 'maxLevel': int(getattr(skill, 'max_level', 10) or 10)})
        except Exception:
            continue
    out.sort(key=lambda s: (-s['level'], s['name']))
    return out


def _performance(career):
    """Work performance towards the next promotion, 0-100, or None."""
    try:
        value = float(career.work_performance)
        goal = float(career._get_promote_performance_level())
    except Exception:
        return None
    if goal <= 0:
        return None
    return max(0, min(100, int(round(100.0 * value / goal))))


def _sim(info, client):
    sim = info.get_sim_instance()
    room = _room_ref(sim) if sim is not None else None
    entry = {'id': str(info.sim_id), 'firstName': info.first_name or '', 'lastName': info.last_name or '',
             'active': client is not None and client.active_sim_info is info, 'here': sim is not None}
    if room is not None:
        entry['room'] = room
    try:
        _career(info, entry)
    except Exception as ex:
        log('career of {} unavailable: {!r}'.format(info.first_name, ex))
    for key, fn in (('relationships', lambda: _relationships(info)), ('skills', lambda: _skills(info)),
                    ('goals', lambda: _goals(info))):
        try:
            value = fn()
            if value:
                entry[key] = value
        except Exception as ex:
            log('{} of {} unavailable: {!r}'.format(key, info.first_name, ex))
    for key, fn in (('mood', lambda: _mood(info)), ('needs', lambda: _needs(info)),
                    ('moodlets', lambda: _moodlets(info)), ('actions', lambda: _actions(sim) if sim else [])):
        if key != 'mood' and sim is None:
            continue
        try:
            value = fn()
            if value:
                entry[key] = value
        except Exception as ex:
            log('{} of {} unavailable: {!r}'.format(key, info.first_name, ex))
    return entry


def _narration():
    try:
        import simsight_settings as settings
        return {'macVoice': speech.speech_mode == 'hover', 'simActions': bool(settings.get('sim_actions'))}
    except Exception:
        return {}


def build_state():
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
_house_cache = {'at': -1e9, 'map': None, 'rooms': {}}


def _house():
    now = time.monotonic()
    if now - _house_cache['at'] >= HOUSE_INTERVAL:
        _house_cache['at'] = now
        try:
            import simsight_housemap as housemap
            house = housemap.build()
            _house_cache['map'] = house
            _house_cache['rooms'] = dict((r['id'], r) for r in (house or {}).get('rooms', []))
        except Exception as ex:
            log('house map failed: {!r}'.format(ex))
            _house_cache['rooms'] = {}
    return _house_cache['rooms']


def _room_ref(obj):
    """The room an object or Sim is in, as the House tab names it, or None."""
    rooms = _house()
    if not rooms:
        return None
    try:
        import simsight_plus_rooms as plus_rooms
        on_lot = getattr(obj, 'is_on_active_lot', None)
        if on_lot is not None and not on_lot():
            return None
        level = int(obj.level)
        rid = plus_rooms.room_id_at(obj.position.x, obj.position.z, level, obj.position.y)
        room = rooms.get('{}:{}'.format(rid, level)) or rooms.get('outside:0')
    except Exception:
        return None
    if room is None:
        return None
    return {'id': room['id'], 'kind': room['kind'], 'number': room.get('number', 0),
            'customName': room.get('customName', ''), 'floor': room.get('floor', 0)}


def _visitors(household):
    """Sims on the lot who are not in the household."""
    import services
    out = []
    members = set(info.sim_id for info in household.sim_info_gen())
    for sim in services.sim_info_manager().instanced_sims_gen():
        try:
            if sim.sim_info.sim_id in members:
                continue
            on_lot = getattr(sim, 'is_on_active_lot', None)
            if on_lot is None or not on_lot():
                continue
            entry = {'simId': str(sim.sim_info.sim_id),
                     'name': '{} {}'.format(sim.sim_info.first_name, sim.sim_info.last_name).strip()}
            room = _room_ref(sim)
            if room is not None:
                entry['room'] = room
            out.append(entry)
        except Exception:
            continue
    return out[:10]


def _bills_owed(household):
    try:
        owed = household.bills_manager.current_payment_owed
        owed = owed() if callable(owed) else owed
        return int(owed or 0)
    except Exception:
        return 0


def _camera_follows():
    try:
        import camera
        if getattr(camera, '_follow_mode', False) and getattr(camera, '_sim_id', None):
            return str(camera._sim_id)
    except Exception:
        pass
    return ''


def _set_camera(sim_id, mode):
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
        try:
            import sims4.math
            pos, fwd = sim.position, sim.forward
            eye = sims4.math.Vector3(pos.x + fwd.x * 3.0, pos.y + 1.6, pos.z + fwd.z * 3.0)
            camera.focus_on_object_from_position(obj_position=pos, camera_position=eye, client=client)
        except Exception as ex:
            log('close camera failed: {!r}'.format(ex))
    camera.focus_on_sim(sim=sim, follow=True, client=client)
    return True, ''


# ---------------------------------------------------------------- inventory
# What the controlled Sim carries, and where each item can go (fridge,
# bookcase...): the game's drag and drop between inventories, in one tap.
# A whole stack moves; the destination's own rules decide what it accepts.

MAX_DESTINATIONS = 5


def _stack_count(obj):
    try:
        return int(obj.stack_count())
    except Exception:
        return 1


def _inventory_items(sim):
    seen, out = set(), []
    for obj in list(sim.inventory_component or ()):
        try:
            stack = obj.inventoryitem_component.get_stack_id()
        except Exception:
            stack = obj.id
        if stack in seen:
            continue
        seen.add(stack)
        out.append(obj)
    return out


def _destinations(sim, item, holders):
    out = []
    for d, holder in holders:
        try:
            if holder.inventory_component.can_add(item):
                group, kind, _keywords = _classify(holder)
                out.append({'id': str(holder.id), 'name': _object_name(holder), 'group': group, 'kind': kind,
                            'distance': int(round(d))})
        except Exception:
            continue
        if len(out) >= MAX_DESTINATIONS:
            break
    return out


def list_inventory():
    import services
    sim, _client = _sim_and_client()
    if sim is None or getattr(sim, 'inventory_component', None) is None:
        return False, 'sim_unavailable'
    holders = []
    for obj in services.object_manager().get_valid_objects_gen():
        try:
            if getattr(obj, 'is_sim', False) or obj.is_hidden() or getattr(obj, 'inventory_component', None) is None:
                continue
            on_lot = getattr(obj, 'is_on_active_lot', None)
            if on_lot is not None and not on_lot():
                continue
            d = ((obj.position.x - sim.position.x) ** 2 + (obj.position.z - sim.position.z) ** 2) ** 0.5
            holders.append((d, obj))
        except Exception:
            continue
    holders.sort(key=lambda pair: pair[0])
    items = []
    for item in _inventory_items(sim):
        try:
            group, kind, _keywords = _classify(item)
            items.append({'item': {'id': str(item.id), 'name': _object_name(item), 'group': group, 'kind': kind},
                          'count': _stack_count(item), 'destinations': _destinations(sim, item, holders)})
        except Exception as ex:
            log('inventory item skipped: {!r}'.format(ex))
    return True, '', {'inventory': items}


def move_item(item_id, destination_id):
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

_route_seen = {}     # (sim id, action) -> monotonic time, to skip repeats


def _install_route_fail():
    import interactions.utils.route_fail as route_fail
    original = route_fail.route_failure
    if getattr(original, '_simsight_phone', False):
        return

    def route_failure(sim, interaction, failure_reason, failure_object_id, *args, **kwargs):
        try:
            _report_route_fail(sim, interaction, failure_reason, failure_object_id)
        except Exception as ex:
            log('route failure report failed: {!r}'.format(ex))
        return original(sim, interaction, failure_reason, failure_object_id, *args, **kwargs)

    route_failure._simsight_phone = True
    route_fail.route_failure = route_failure


def _report_route_fail(sim, interaction, failure_reason, failure_object_id):
    import services
    household = services.active_household()
    info = getattr(sim, 'sim_info', None)
    if info is None or household is None or info.household is not household:
        return
    action = ''
    try:
        action = text_of(interaction.get_name(), '')
    except Exception:
        pass
    if not action:
        action = _pretty(getattr(getattr(interaction, 'affordance', None), '__name__', ''), ('sim-', 'si_'))
    now = time.monotonic()
    key = (info.sim_id, action)
    if now - _route_seen.get(key, -1e9) < 10.0:
        return
    _route_seen[key] = now
    reason = getattr(failure_reason, 'name', '') or str(failure_reason or '')
    obj_name = ''
    if failure_object_id:
        obj = services.object_manager().get(failure_object_id)
        if obj is not None and not getattr(obj, 'is_sim', False):
            obj_name = _object_name(obj)
        elif obj is not None:
            obj_name = '{} {}'.format(obj.sim_info.first_name, obj.sim_info.last_name).strip()
    log('route failure: {} {} ({}, {})'.format(info.first_name, action, reason, obj_name))
    send({'event': {'kind': 'EVENT_KIND_ROUTE_FAIL', 'simId': str(info.sim_id), 'simName': info.first_name,
                    'subject': action, 'detail': reason, 'objectName': obj_name}})


# ---------------------------------------------------------------- things to deal with
# Puddles, broken objects, spoiled food, fire on the lot: listed for the
# phone's "Needs attention" card. Scanning every object takes a moment, so
# it runs every few seconds and the state reuses the last answer.

PROBLEM_INTERVAL = 4.0
_problem_cache = {'at': 0.0, 'items': []}


def _problems():
    now = time.monotonic()
    if now - _problem_cache['at'] < PROBLEM_INTERVAL:
        return _problem_cache['items']
    _problem_cache['at'] = now
    try:
        _problem_cache['items'] = _scan_problems()
    except Exception as ex:
        log('problems failed: {!r}'.format(ex))
        _problem_cache['items'] = []
    return _problem_cache['items']


def _scan_problems():
    import services
    sim = services.get_active_sim()
    try:
        from objects.puddles.puddle import Puddle
    except Exception:
        Puddle = None
    try:
        from objects.fire.fire import Fire
    except Exception:
        Fire = None
    try:
        from objects.components.state import StateComponent
        broken_states = tuple(StateComponent.BROKEN_STATE_SET or ())
    except Exception:
        broken_states = ()
    try:
        from crafting.crafting_tunable import CraftingTuning
        spoiled = CraftingTuning.SPOILED_STATE_VALUE
    except Exception:
        spoiled = None

    def has(obj, value):
        try:
            return value is not None and obj.has_state(value.state) and obj.get_state(value.state) is value
        except Exception:
            return False

    out = []
    for obj in services.object_manager().get_valid_objects_gen():
        try:
            if getattr(obj, 'is_sim', False) or obj.is_hidden():
                continue
            on_lot = getattr(obj, 'is_on_active_lot', None)
            if on_lot is not None and not on_lot():
                continue
            in_inventory = getattr(obj, 'is_in_inventory', None)
            if in_inventory is not None and in_inventory():
                continue
            kind = None
            if Fire is not None and isinstance(obj, Fire):
                kind = 'FIRE'
            elif Puddle is not None and isinstance(obj, Puddle):
                kind = 'PUDDLE'
            elif has(obj, spoiled):
                kind = 'SPOILED'
            elif broken_states and any(_state_active(obj, v) for v in broken_states):
                kind = 'BROKEN'
            elif getattr(obj, 'consumable_component', None) is not None:
                parent = getattr(obj, 'parent', None)
                if parent is not None and getattr(parent, 'is_sim', False):
                    continue        # in a Sim's hands: being eaten
                kind = 'FOOD'
            if kind is None:
                continue
            entry = {'kind': kind, 'targetId': str(obj.id), 'name': _object_name(obj)}
            if sim is not None:
                entry['distance'] = int(round(((obj.position.x - sim.position.x) ** 2 +
                                               (obj.position.z - sim.position.z) ** 2) ** 0.5))
            out.append(entry)
        except Exception:
            continue
    order = {'FIRE': 0, 'BROKEN': 1, 'PUDDLE': 2, 'SPOILED': 3, 'FOOD': 4}
    out.sort(key=lambda e: (order[e['kind']], e.get('distance', 0)))
    # Fire burns in several pieces: one line is enough.
    fires = [e for e in out if e['kind'] == 'FIRE']
    food = [e for e in out if e['kind'] == 'FOOD'][:3]
    rest = [e for e in out if e['kind'] not in ('FIRE', 'FOOD')][:12]
    return fires[:1] + rest + food


def _state_active(obj, value):
    try:
        return bool(obj.state_value_active(value))
    except Exception:
        return False


def _open_dialogs():
    if _dialogs is None:
        return []
    try:
        return _dialogs.active()
    except Exception as ex:
        log('dialogs failed: {!r}'.format(ex))
        return []


def _publish_state():
    global _last_state_json, _seq
    try:
        state = build_state()
    except Exception as ex:
        log('state failed: {!r}'.format(ex))
        return
    body = json.dumps(state, sort_keys=True, ensure_ascii=False)
    if body == _last_state_json:
        return
    _last_state_json = body
    _seq += 1
    state['seq'] = str(_seq)
    send({'state': state})


# ---------------------------------------------------------------- playing from the phone
# Targets and pie menus as the game builds them for a click (EA's ChoiceMenu:
# same tests, visibility, names, greyed reasons, social sub-menus), and the
# chosen row run with test_and_execute, like the mod's phone list.

MAX_OBJECTS = 600         # nearest objects listed (a big house has a few hundred)
MAX_DISTANCE = 60.0       # metres
_menus = {}               # target id -> {option id: (aop, context)}
PHONE_ID = 'phone'        # the controlled Sim's phone, a target of its own


def _sim_and_client():
    import services
    sim = services.get_active_sim()
    client = services.client_manager().get_first_client()
    return sim, client


def _object_name(obj):
    try:
        if obj.has_custom_name():
            custom = str(obj.custom_name or '')
            if custom:
                return custom
    except Exception:
        pass
    fallback = _pretty(getattr(obj.definition, 'name', '') or type(obj).__name__)
    try:
        raw = obj.catalog_name
        key = int(getattr(raw, 'hash', raw)) & 0xFFFFFFFF
    except Exception:
        key = 0
    if not key:
        return fallback
    cached = _loc_cache().get(key)
    if cached is None:
        global _loc_misses
        _loc_misses += 1
        _loc_wanted.add(key)
        return fallback
    return _fill_tokens(cached, []) or fallback


def _loc_key_text(key):
    """Game text for a string-table key, '' until the UI resolves it."""
    if not key:
        return ''
    cached = _loc_cache().get(key)
    if cached is None:
        global _loc_misses
        _loc_misses += 1
        _loc_wanted.add(key)
        return ''
    return _fill_tokens(cached, [])


def _classify(obj):
    """(group, kind, keywords) from the scanner's catalog classification."""
    scanner = sys.modules.get('simsight_scanner')
    buycat = sys.modules.get('simsight_buycat')
    try:
        sub, group, names, _flags = scanner._classification(obj, time.monotonic())
    except Exception:
        return 'MISC', '', ''
    kind = ''
    if buycat is not None:
        kind = _loc_key_text(buycat.TAG_LABEL_HASH.get(sub, 0))
    words = set()
    for name in names or ():
        if name.startswith(('BuyCat', 'Func_')):
            words.add(_pretty(re.sub(r'^(BuyCat[A-Z]*_|Func_)', '', name)))
    return group, kind, ' '.join(sorted(words))


def list_targets():
    import services
    sim, _client = _sim_and_client()
    if sim is None:
        return []
    origin = sim.position

    def distance(obj):
        pos = obj.position
        return ((float(pos.x) - float(origin.x)) ** 2 + (float(pos.z) - float(origin.z)) ** 2) ** 0.5

    targets = [{'id': str(sim.id), 'name': '{} {}'.format(sim.sim_info.first_name, sim.sim_info.last_name).strip(),
                'isSim': True, 'isSelf': True, 'group': 'SIMS', 'distance': 0},
               {'id': PHONE_ID, 'name': 'Phone', 'group': 'PHONE'}]
    others = []
    for other in services.sim_info_manager().instanced_sims_gen():
        if other is sim:
            continue
        try:
            others.append((distance(other), other))
        except Exception:
            continue
    for d, other in sorted(others, key=lambda pair: pair[0]):
        info = other.sim_info
        targets.append({'id': str(other.id), 'name': '{} {}'.format(info.first_name, info.last_name).strip(),
                        'isSim': True, 'group': 'SIMS', 'distance': int(round(d))})
    objects = []
    for obj in services.object_manager().get_valid_objects_gen():
        try:
            if getattr(obj, 'is_sim', False) or getattr(obj, 'is_terrain', False) or obj.is_hidden():
                continue
            checker = getattr(obj, 'is_in_inventory', None)
            if checker is not None and checker():
                continue
            if not list(obj.super_affordances()):
                continue
            d = distance(obj)
            if d <= MAX_DISTANCE:
                objects.append((d, obj))
        except Exception:
            continue
    objects.sort(key=lambda pair: pair[0])
    for d, obj in objects[:MAX_OBJECTS]:
        try:
            group, kind, keywords = _classify(obj)
            targets.append({'id': str(obj.id), 'name': _object_name(obj), 'group': group,
                            'kind': kind, 'keywords': keywords, 'distance': int(round(d))})
        except Exception:
            continue
    return targets


def _target(target_id, sim):
    import services
    try:
        key = int(target_id)
    except ValueError:
        return None
    if key == sim.id:
        return sim
    return services.object_manager().get(key)


def _category_root(category):
    depth = 0
    while category is not None and getattr(category, '_parent', None) is not None and depth < 8:
        category = category._parent
        depth += 1
    return category


def get_interactions(target_id):
    import services
    import sims4.resources
    from interactions.choices import ChoiceMenu
    from server.pick_info import PickInfo, PickType
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
    parameters = client.get_interaction_parameters() or {}
    try:
        lot_id = int(services.active_lot().lot_id)
    except Exception:
        lot_id = 0
    try:
        level = int(obj.level)
    except Exception:
        level = 0
    pick_type = PickType.PICK_SIM if getattr(obj, 'is_sim', False) else PickType.PICK_OBJECT
    targets = [obj] + [t for t in (getattr(obj, 'forwarded_pick_targets', None) or ()) if t is not None and t is not obj]
    for target in targets:
        pick = PickInfo(pick_type=pick_type, target=target, location=obj.position,
                        routing_surface=obj.routing_surface, lot_id=lot_id, level=level)
        context = client.create_interaction_context(sim, pick=pick)
        context.add_preferred_objects((obj,))
        menu.add_potential_aops(target, context, target.potential_interactions(context, **parameters), None)
        if getattr(target, 'is_sim', False) and target is not sim:
            try:
                sim.fill_choices_menu_with_si_state_aops(target, context, menu, None)
            except Exception as ex:
                log('social rows unavailable: {!r}'.format(ex))
    return _menu_rows(target_id, sim, menu)


def _menu_rows(target_id, sim, menu):
    """Rows of a ChoiceMenu as the game shows them (visible, named, greyed reasons)."""
    import services
    import sims4.resources
    categories = services.get_instance_manager(sims4.resources.Types.PIE_MENU_CATEGORY)
    tutorial = services.get_tutorial_service()
    rows, options = [], {}
    pending = False
    misses = _loc_misses
    for option_id, item in menu:
        aop = item.aop
        affordance = aop.affordance
        try:
            if tutorial is not None and not tutorial.is_affordance_visible(affordance):
                continue
            visible, _suppression = sim.test_pie_menu_modifiers(affordance)
            if not visible:
                continue
            modifier_tooltip = sim.apply_forbid_by_object_tags_pie_menu_modifiers(aop)
            fallback = _pretty(getattr(affordance, '__name__', ''), ('sim-', 'si_', 'mixer_'))
            name = text_of(affordance.get_name(aop.target, item.context, **aop.interaction_parameters), '')
            pending = pending or not name
            tooltip = modifier_tooltip or item.result.tooltip
            reason = ''
            if tooltip is not None:
                reason = text_of(affordance.create_localized_string(
                    tooltip, context=item.context, target=aop.target, **aop.interaction_parameters))
            category = categories.get(item.category_key) if item.category_key else None
            root = _category_root(category)
            category_name = text_of(root._display_name(sim)) if root is not None else ''
            rows.append({'id': str(option_id), 'name': name or fallback, 'category': category_name,
                         'enabled': bool(item.result) and not modifier_tooltip, 'reason': reason})
            options[str(option_id)] = (aop, item.context)
        except Exception as ex:
            log('menu row skipped: {!r}'.format(ex))
    _menus.clear()            # only the latest menu can be run
    _menus[str(target_id)] = options
    return True, '', {'interactions': rows, 'namesPending': pending or _loc_misses != misses}


def run_interaction(target_id, option_id):
    entry = _menus.get(str(target_id), {}).get(str(option_id))
    if entry is None:
        return False, 'menu_expired'
    aop, context = entry
    try:
        result = aop.test_and_execute(context)
    except Exception as ex:
        log('interaction failed: {!r}'.format(ex))
        return False, 'error'
    if not result:
        log('interaction refused: {}'.format(result))
        return False, 'refused'
    return True, ''


# ---------------------------------------------------------------- pairing page

PAIR_URL = 'http://127.0.0.1:47800/pair'
SETUP_URL = 'http://127.0.0.1:47800/setup'


def _open_on_mac(url, said):
    """Open one of the bridge's pages in the computer's browser (in front of the game)."""
    try:
        if IS_WINDOWS:
            os.startfile(url)
        elif IS_MAC and subprocess is not None:
            subprocess.Popen(['/usr/bin/open', url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        else:
            return False
        speech.speak(said, explicit=True)
        return True
    except Exception as ex:
        log('open {} failed: {!r}'.format(url, ex))
        return False


def open_pairing_page():
    """Show the phone link's QR code and pairing code."""
    return _open_on_mac(PAIR_URL, 'Phone link opened in the browser.')


def open_setup_page():
    """SimSight setup: phone address, meeting point, disconnect phones."""
    return _open_on_mac(SETUP_URL, 'SimSight setup opened in the browser.')


try:
    import sims4.commands

    @sims4.commands.Command('simsight.companion', 'a11y.companion', command_type=sims4.commands.CommandType.Live)
    def a11y_companion(_connection=None):
        """Open the phone companion's QR code on the Mac."""
        return open_pairing_page()

    @sims4.commands.Command('simsight.setup', command_type=sims4.commands.CommandType.Live)
    def simsight_setup(_connection=None):
        """Open SimSight setup on the Mac (phone address, meeting point)."""
        return open_setup_page()
except Exception as _ex:
    log('companion command unavailable: {!r}'.format(_ex))


# ---------------------------------------------------------------- startup

_dialogs = None
_mackeys = None
_msvcrt = _ctypes = None
if IS_WINDOWS:
    try:
        import msvcrt as _msvcrt
        import ctypes as _ctypes
    except Exception as _ex:
        IS_WINDOWS = False   # no way to read the pipe without blocking: no companion
        log('companion unavailable on Windows: {!r}'.format(_ex))
if IS_MAC:
    try:
        import simsight_mackeys as _mackeys
    except Exception as _ex:
        _mackeys = None
        log('keyboard unavailable: {!r}'.format(_ex))
if IS_MAC or IS_WINDOWS:
    try:
        _install_route_fail()
    except Exception as _ex:
        log('route failure hook unavailable: {!r}'.format(_ex))
    try:
        import simsight_dialogs as _dialogs
        _dialogs.install()
    except Exception as _ex:
        _dialogs = None
        log('dialogs unavailable: {!r}'.format(_ex))
    try:
        _install_tick()
        start()
    except Exception as _ex:
        log('install failed: {!r}'.format(_ex))

# SimSight Plus (by Michael Maslo) - core helpers: log, speech, main-thread pump, hotkeys, dialogs.
# Speech goes through the SimSight mod (simsight_speech), so it uses the
# same screen reader connection (JAWS / NVDA through Prism, or a Windows voice).
from __future__ import annotations

import os
import threading
import time
import traceback
from typing import TYPE_CHECKING, Any

import sims4.commands
from simsight_commands import real_annotations
import simsight_log
import simsight_patch

if TYPE_CHECKING:
    from sims.household import Household
    from collections.abc import Callable, Iterable, Sequence

    from sims.sim import Sim
    from sims.sim_info import SimInfo
    from ui.ui_dialog_generic import UiDialogTextInputOkCancel
    from ui.ui_dialog_picker import UiObjectPicker

    KeyReader = Callable[[int], int]

# Merged into SimSight from SimSight Plus 0.7.1 (2026-10-08).
VERSION = '0.7.1'

# ---------------------------------------------------------------- log


def _mods_folder() -> str:
    path = os.path.normpath(__file__)
    low = path.lower()
    marker = '.ts4script'
    if marker in low:
        return os.path.dirname(path[: low.index(marker) + len(marker)])
    return os.path.dirname(path)


MODS_FOLDER = _mods_folder()
# Files (log, settings, room names) are written only from the installed mod:
# importing the module from the kit's Scripts folder (offline tests) writes nothing.
IN_GAME = '.ts4script' in os.path.normpath(__file__).lower()
LOG_PATH = os.path.join(MODS_FOLDER, 'SimSight_Plus.log')
# 0.7.1: the log is also kept in the Windows user folder (outside OneDrive), so it can
# always be found; when the Mods copy cannot be written, that copy says why.
HOME_LOG_PATH = os.path.join(
    os.environ.get('USERPROFILE') or os.path.expanduser('~'), 'SimSight_Plus.log'
)


class _State:
    """Module-private mutable state (log, pump, keys thread, open text box)."""

    def __init__(self) -> None:
        self.log_started = False
        self.alarm: Any = None
        # None: not started; False: not started on purpose (macOS); else the thread.
        self.keys_thread: threading.Thread | bool | None = None
        self.typing: _TypingSession | None = None


_state = _State()
_log_lock = threading.Lock()


def log(message: object) -> None:
    """A line in SimSight_Plus.log (both copies), always written in the game."""
    if not IN_GAME:
        return
    with _log_lock:
        mode = 'a' if _state.log_started else 'w'
        _state.log_started = True
        line = '[{}] {}\n'.format(time.strftime('%H:%M:%S'), message)
        problem = None
        try:
            with open(LOG_PATH, mode, encoding='utf-8') as f:
                f.write(line)
        except (OSError, ValueError) as e:
            problem = f'[could not write {LOG_PATH}: {e}]\n'
        try:
            with open(HOME_LOG_PATH, mode, encoding='utf-8') as f:
                f.write(line if problem is None else problem + line)
        except (OSError, ValueError):
            pass  # no log is better than a mod that stops: nowhere else to report it


def log_exc(where: str) -> str:
    """Call from an except block: the traceback goes to SimSight_Plus.log. Returns
    the label for SimSight.log, so a handler reads `simsight_log.error(core.log_exc(...))`."""
    log(f'ERROR in {where}: {traceback.format_exc()}')
    return f'plus: {where}'


log(f'SimSight Plus {VERSION} loading from {MODS_FOLDER}')

# ---------------------------------------------------------------- settings

SETTINGS_PATH = os.path.join(MODS_FOLDER, 'SimSight_Plus.cfg')
DEFAULTS = {
    'need alerts': 'on',
    'need alerts for whole household': 'on',
    'work reminders': 'on',
    'bill reminders': 'on',
    'speak notifications': 'off',
    'speak game speed': 'on',
}
_settings: dict[str, str] = dict(DEFAULTS)


def _load_settings() -> None:
    try:
        if not os.path.isfile(SETTINGS_PATH):
            _save_settings()
            return
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                (k, v) = line.split('=', 1)
                k = k.strip().lower()
                if k in DEFAULTS:
                    _settings[k] = v.strip().lower()
    except (OSError, ValueError):  # unreadable file: the defaults stay
        simsight_log.error(log_exc('load settings'))


def _save_settings() -> None:
    if not IN_GAME:
        return
    try:
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
            f.write('# SimSight Plus settings. on or off.\n')
            f.writelines(f'{k} = {_settings.get(k, default)}\n' for k, default in DEFAULTS.items())
    except (OSError, ValueError):
        simsight_log.error(log_exc('save settings'))


def setting_on(name: str) -> bool:
    return _settings.get(name, DEFAULTS.get(name, 'off')) == 'on'


def toggle_setting(name: str) -> bool:
    _settings[name] = 'off' if setting_on(name) else 'on'
    _save_settings()
    return setting_on(name)


_load_settings()

# ---------------------------------------------------------------- speech


def speak(text: object, interrupt: bool = True) -> None:
    text = str(text or '').strip()
    if not text:
        return
    log('SAY: ' + text)
    try:
        import simsight_speech as s

        s.speak(text, interrupt=interrupt, explicit=True)
    except Exception:  # speech must never stop the caller: logged
        simsight_log.error(log_exc('speak'))


def with_sim(ls: Any, sim_info: SimInfo) -> Any:
    """Fill a game string's Sim placeholders (his/her, name) with this Sim."""
    try:
        from sims4.localization import _create_localized_string

        return _create_localized_string(int(ls.hash), sim_info)
    except (AttributeError, TypeError, ValueError):  # not a game string: it reads unfilled
        return ls


def raw(text: object) -> Any:
    from sims4.localization import LocalizationHelperTuning

    return LocalizationHelperTuning.get_raw_text(str(text))


# ---------------------------------------------------------------- main-thread pump


class _Ticker:
    def __init__(self, seconds: float, fn: Callable[[], object]) -> None:
        self.seconds = seconds
        self.fn = fn
        self.last_run = 0.0


_queue: list[tuple[Callable[..., object], tuple[Any, ...]]] = []
_queue_lock = threading.Lock()
_tickers: list[_Ticker] = []


class _Owner:
    pass


_owner = _Owner()


def post(fn: Callable[..., object], *args: Any) -> None:
    """Run fn(*args) on the game's main thread at the next pump (any thread may call)."""
    with _queue_lock:
        if len(_queue) > 32:
            del _queue[0]
        _queue.append((fn, args))


def add_ticker(seconds: float, fn: Callable[[], object]) -> None:
    _tickers.append(_Ticker(seconds, fn))


def _run_logged(fn: Callable[..., object], args: tuple[Any, ...], where: str) -> None:
    try:
        fn(*args)
    except Exception:  # posted work and tickers run from the pump alarm
        simsight_log.error(log_exc(where))


def _drain(_handle: Any = None) -> None:
    with _queue_lock:
        batch = _queue[:]
        del _queue[:]
    for fn, args in batch:
        _run_logged(fn, args, 'posted {}'.format(getattr(fn, '__name__', fn)))
    now = time.monotonic()
    for t in _tickers:
        if now - t.last_run >= t.seconds:
            t.last_run = now
            _run_logged(t.fn, (), 'ticker {}'.format(getattr(t.fn, '__name__', t.fn)))


def start_pump() -> None:
    try:
        import alarms
        import clock

        if _state.alarm is not None:
            try:
                alarms.cancel_alarm(_state.alarm)
            except Exception:  # an alarm of an earlier zone: EA's timeline decides
                simsight_log.error('plus: cancel the old pump alarm')
        _state.alarm = alarms.add_alarm_real_time(
            _owner, clock.interval_in_real_seconds(0.1), _drain, repeating=True
        )
        log('pump started')
    except Exception:  # called from the zone hook
        simsight_log.error(log_exc('start pump'))


# ---------------------------------------------------------------- hotkeys (Alt + key)

HOTKEYS: dict[int, tuple[Callable[..., object], str]] = {}  # vk -> (fn, description)

_VK_SHIFT, _VK_CONTROL, _VK_MENU = 0x10, 0x11, 0x12
_VK_LWIN, _VK_RWIN = 0x5B, 0x5C
_VK_BACK, _VK_RETURN, _VK_ESCAPE, _VK_SPACE = 0x08, 0x0D, 0x1B, 0x20


def hotkey(vk: int, description: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        HOTKEYS[vk] = (fn, description)
        return fn

    return deco


def _game_foreground(user32: Any) -> bool:
    import ctypes

    try:
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except (AttributeError, OSError, ctypes.ArgumentError):
        return True  # cannot tell: assume the game has the focus, as before


def _key_name(vk: int) -> str:
    return chr(vk) if 48 <= vk <= 90 else str(vk)


class _KeyPoller:
    """One Windows key poll every 20 ms on the keys thread: Alt hotkeys and typing."""

    def __init__(self, read: KeyReader, user32: Any) -> None:
        self.read = read
        self.user32 = user32
        self.down: set[int] = set()
        self.typing_down: set[int] = set()
        self.alt_seen = 0.0
        self.other_seen = 0.0

    def poll_logged(self) -> None:
        try:
            self.poll()
        except Exception:  # the keys thread must keep running
            simsight_log.error(log_exc('keys loop'))
            time.sleep(1.0)

    def poll(self) -> None:
        read = self.read
        now = time.monotonic()
        a = read(_VK_MENU)
        o = read(_VK_CONTROL) | read(_VK_LWIN) | read(_VK_RWIN)
        states = [(vk, read(vk)) for vk in list(HOTKEYS)]
        if a & 0x8001:
            self.alt_seen = now
        if o & 0x8000:
            self.other_seen = now
        shift = bool(read(_VK_SHIFT) & 0x8000)
        alt = bool(a & 0x8000) or now - self.alt_seen < 0.6
        other = bool(o & 0x8000) or now - self.other_seen < 0.3
        if _state.typing is not None:
            _typing_poll(read, shift, self.typing_down)
            return
        for vk, s in states:
            self._hotkey_state(vk, s, alt, other, shift)

    def _hotkey_state(self, vk: int, s: int, alt: bool, other: bool, shift: bool) -> None:
        pressed = s & 0x8000
        tapped = s & 0x0001
        if (pressed or tapped) and vk not in self.down:
            if pressed:
                self.down.add(vk)
            if alt or 0x41 <= vk <= 0x5A:
                log(f'seen key {chr(vk)} alt={alt} other={other}')
            if alt and not other and _game_foreground(self.user32):
                (fn, desc) = HOTKEYS[vk]
                log(f'key Alt+{_key_name(vk)} ({desc})')
                if wants_shift(fn):
                    post(fn, shift)
                else:
                    post(fn)
        elif not pressed and vk in self.down:
            self.down.discard(vk)


def _key_reader(ctypes: Any, user32: Any) -> KeyReader:
    # PyDLL keeps the GIL during the call. The game's main thread holds the GIL most of
    # the time, so re-acquiring it after every call starves this thread (SimSight does the same).
    try:
        read = ctypes.PyDLL('user32').GetAsyncKeyState
        read.argtypes = [ctypes.c_int]
        read.restype = ctypes.c_short
    except (OSError, AttributeError):
        read = user32.GetAsyncKeyState
        read.restype = ctypes.c_short
    return read


def _keys_loop() -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]  # Windows only
    poller = _KeyPoller(_key_reader(ctypes, user32), user32)
    log(
        'hotkey thread running, keys: {}'.format(
            ', '.join(f'{chr(vk)}={d}' for (vk, (_fn, d)) in sorted(HOTKEYS.items()))
        )
    )
    while True:
        time.sleep(0.02)
        poller.poll_logged()


# ---------------------------------------------------------------- typing capture
# SimSight cannot reach the OK button of text dialogs, so while one of our text boxes
# is open we record the typed characters ourselves; Enter finishes, Escape cancels.


class _TypingSession:
    def __init__(self, done: Callable[[str], None], dialog_id: Any) -> None:
        self.chars: list[str] = []
        self.done = done
        self.dialog_id = dialog_id
        self.armed = False


_TYPING_KEYS = [
    *range(0x30, 0x3A),
    *range(0x41, 0x5B),
    _VK_SPACE,
    0xBD,
    0xDE,
    0xBE,
    _VK_BACK,
    _VK_RETURN,
    _VK_ESCAPE,
]
# vk -> (character, with Shift) for the keys that are not letters
_TYPED_SYMBOLS = {
    _VK_SPACE: (' ', ' '),
    0xBD: ('-', '_'),
    0xDE: ("'", '"'),
    0xBE: ('.', '.'),
}


def _typed_char(vk: int, shift: bool) -> str | None:
    if 0x41 <= vk <= 0x5A:
        return chr(vk) if shift else chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    symbol = _TYPED_SYMBOLS.get(vk)
    if symbol is None:
        return None
    return symbol[1] if shift else symbol[0]


def _newly_pressed(read: KeyReader, held: set[int]) -> Iterable[int]:
    for vk in _TYPING_KEYS:
        if read(vk) & 0x8000:
            if vk in held:
                continue
            held.add(vk)
            yield vk
        else:
            held.discard(vk)


def _typing_poll(read: KeyReader, shift: bool, held: set[int]) -> None:
    session = _state.typing
    if session is not None and not session.armed:
        # Keys still held from opening the box (like Enter) must not count.
        held.clear()
        held.update(vk for vk in _TYPING_KEYS if read(vk) & 0x8000)
        session.armed = True
        return
    for vk in _newly_pressed(read, held):
        t = _state.typing
        if t is None:
            return
        if vk == _VK_RETURN:
            post(_typing_finish, True)
        elif vk == _VK_ESCAPE:
            post(_typing_finish, False)
        elif vk == _VK_BACK:
            if t.chars:
                t.chars.pop()
        else:
            char = _typed_char(vk, shift)
            if char is not None:
                t.chars.append(char)


def _typing_finish(accepted: bool) -> None:
    t = _state.typing
    _state.typing = None
    if t is None:
        return
    _cancel_previous('text')
    text = ''.join(t.chars).strip()
    log(f'typed text: {text!r} accepted={accepted}')
    if not accepted:
        speak('Cancelled.')
        return
    t.done(text)


def wants_shift(fn: Callable[..., object]) -> bool:
    return getattr(fn, '_wants_shift', False)


def start_keys() -> None:
    if _state.keys_thread is not None:
        return
    import sys

    if sys.platform == 'darwin':
        # macOS port: Windows key polling only, and thread-side imports crash there.
        _state.keys_thread = False
        return
    thread = threading.Thread(target=_keys_loop, name='a11yplus_keys', daemon=True)
    _state.keys_thread = thread
    thread.start()


# ---------------------------------------------------------------- game helpers


def active_sim() -> Sim | None:
    import services

    return services.get_active_sim()


def client() -> Any:
    import services

    return services.client_manager().get_first_client()


def sim_name(sim_info: Any) -> str:
    try:
        return ' '.join(p for p in (sim_info.first_name, sim_info.last_name) if p).strip()
    except (AttributeError, TypeError):
        return 'Sim'


def household_sims(instanced_only: bool = True) -> list[SimInfo]:
    import services

    hh: Household | None = services.active_household()
    if hh is None:
        return []
    out = []
    for info in hh.sim_info_gen():
        sim: Sim | None = info.get_sim_instance()
        if instanced_only and sim is None:
            continue
        out.append(info)
    return out


def fence_input() -> None:
    """Stop the key that opened a dialog from also choosing in it (SimSight helper)."""
    try:
        import simsight_keys as keys

        keys.fence_opening_input()
    except Exception:
        simsight_log.error('plus: fence input')


# ---------------------------------------------------------------- dialogs

_open_dialogs: dict[str, Any] = {}


def _cancel_previous(tag: str) -> None:
    old = _open_dialogs.pop(tag, None)
    if old:
        try:
            import services

            services.ui_dialog_service().dialog_cancel(old)
        except Exception:  # a gone dialog is ignored by EA; this runs its listeners
            simsight_log.error(f'plus: cancel the previous {tag} dialog')


def _localized(value: Any) -> Any:
    return raw(value) if isinstance(value, str) else value


def _icon_info(icon_obj: Any) -> Any:
    if icon_obj is None:
        return None
    from distributor.shared_messages import IconInfoData

    # EA's IconInfoData only fills a namedtuple: it does not look at the object.
    return IconInfoData(obj_instance=icon_obj)


def _picker_row(i: int, row: Sequence[Any]) -> tuple[Any, Any]:
    """(value, ObjectPickerRow) for one choose() row."""
    from ui.ui_dialog_picker import ObjectPickerRow

    row = tuple(row)
    (label, desc, value) = (*row, None, None, None)[:3]
    selected = row[3] if len(row) > 3 else False
    icon_obj = row[4] if len(row) > 4 else None
    picker_row = ObjectPickerRow(
        option_id=i,
        name=_localized(label),
        row_description=_localized(desc),
        row_tooltip=None,
        icon=None,
        icon_info=_icon_info(icon_obj),
        tag=i,
        is_selected=bool(selected),
    )
    return value, picker_row


def choose(  # noqa: PLR0913, PLR0917 - public signature, called across the mod
    title: Any,
    text: str,
    rows: Iterable[Sequence[Any]],
    on_pick: Callable[[Any], object],
    tag: str = 'main',
    multi: int = 0,
    min_pick: int = 1,
    on_cancel: Callable[[], object] | None = None,
) -> bool:
    """Show a list. rows: list of (label_or_LocalizedString, description_or_None, value, selected).
    on_pick(value) for single choice; on_pick(list_of_values) when multi > 0."""
    try:
        import services
        from ui.ui_dialog_picker import UiObjectPicker

        owner: Sim | None = services.get_active_sim()
        if owner is None:
            speak('Load a household first.')
            return False
        _cancel_previous(tag)
        dialog = UiObjectPicker.TunableFactory().default(
            owner,
            # A LocalizedString title (a catalog label) is passed through as rows are.
            title=lambda *_a, **_k: _localized(title),
            text=lambda *_a, **_k: raw(text),
            picker_type=UiObjectPicker.UiObjectPickerObjectPickerType.OBJECT,
            min_selectable=min_pick if multi else 1,
            max_selectable=multi or 1,
        )
        values: dict[int, Any] = {}
        for i, row in enumerate(rows):
            values[i], picker_row = _picker_row(i, row)
            dialog.add_row(picker_row)

        def listener(reply: UiObjectPicker) -> None:
            # dialog_id comes from EA's @unique_id class decorator, which the stubs do not see.
            if _open_dialogs.get(tag) == getattr(reply, 'dialog_id'):  # noqa: B009
                _open_dialogs.pop(tag, None)
            if not reply.accepted:
                if on_cancel is not None:
                    post(on_cancel)
                return
            tags = list(reply.get_result_tags())
            picked = [values[t] for t in tags if t in values]
            if multi:
                post(on_pick, picked)
            elif picked:
                post(on_pick, picked[0])

        dialog.add_listener(listener)
        _open_dialogs[tag] = dialog.dialog_id
        fence_input()
        dialog.show_dialog()
    except Exception:  # EA's dialog code: the player hears it failed, the log says why
        simsight_log.error(log_exc('choose ' + str(title)))
        speak('Could not open the list.')
        return False
    return True


def _text_input(max_length: int) -> Any:
    from ui.ui_text_input import UiTextInput, _TextInputLengthFixed

    ti = UiTextInput(sort_order=0)
    ti.default_text = None
    ti.title = None
    ti.restricted_characters = None
    ti.check_profanity = False
    ti.height = None
    ti.initial_value = None
    ti.length_restriction = _TextInputLengthFixed(min_length=None, max_length=max_length)
    return ti


def ask_text(
    title: str,
    text: str,
    initial: str | None,
    on_done: Callable[[str], object],
    max_length: int = 40,
) -> bool:
    """Show a text box. Type, then Enter. Escape cancels. on_done(str).
    With an initial value, Enter on an empty box keeps it."""
    if initial:
        text = f'{text} Press Enter to keep {initial}, or type something else and press Enter.'
    else:
        text = f'{text} Type, then press Enter.'

    def finished(value: str) -> None:
        value = value or (initial or '')
        on_done(value[:max_length])

    try:
        import services
        from sims4.collections import AttributeDict
        from ui.ui_dialog_generic import UiDialogTextInputOkCancel

        owner: Sim | None = services.get_active_sim()
        if owner is None:
            speak('Load a household first.')
            return False
        ti = _text_input(max_length)
        _cancel_previous('text')
        dialog = UiDialogTextInputOkCancel.TunableFactory().default(
            owner,
            title=lambda *_a, **_k: raw(title),
            text=lambda *_a, **_k: raw(text),
            text_inputs=AttributeDict({'value': ti}),
        )
        session = _TypingSession(finished, dialog.dialog_id)

        def listener(reply: UiDialogTextInputOkCancel) -> None:
            _open_dialogs.pop('text', None)
            if _state.typing is not session:
                return  # already finished with Enter or Escape
            _state.typing = None
            if not reply.accepted:
                speak('Cancelled.')
                return
            value = str(reply.text_input_responses.get('value', '') or '').strip()
            post(finished, value)

        dialog.add_listener(listener)
        _open_dialogs['text'] = dialog.dialog_id
        fence_input()
        dialog.show_dialog()
        _state.typing = session
    except Exception:  # EA's dialog code: the player hears it failed, the log says why
        simsight_log.error(log_exc('ask_text ' + title))
        speak('Could not open the text box.')
        return False
    return True


def _int_or_zero(read: Callable[[], Any]) -> int:
    try:
        return int(read())
    except (AttributeError, TypeError, ValueError):
        return 0


def open_interactions(obj: Any) -> bool:
    """Open the pie menu (interactions) for a Sim or object, the same way the SimSight scanner does."""
    try:
        import distributor.ops
        import distributor.system
        import services
        from server.pick_info import PickType

        is_sim = getattr(obj, 'is_sim', False)
        pick_type = PickType.PICK_SIM if is_sim else PickType.PICK_OBJECT
        pos = obj.position
        level = _int_or_zero(lambda: obj.level)
        lot_id = _int_or_zero(lambda: services.active_lot().lot_id)
        first = ''
        if is_sim:
            first = str(obj.sim_info.first_name or '').replace('|', ' ')
        msg = f'A11y|pick|{obj.id}|{int(pick_type)}|{lot_id}|{first}|{float(pos.x):.3f}|{float(pos.y):.3f}|{float(pos.z):.3f}|{level}'
        distributor.system.Distributor.instance().add_op_with_no_owner(
            distributor.ops.SendUIMessage(msg)
        )
    except Exception:  # EA's distributor: the player hears it failed, the log says why
        simsight_log.error(log_exc('open_interactions'))
        speak('Could not open the actions for that.')
        return False
    return True


# ---------------------------------------------------------------- startup

_zone_started_callbacks: list[Callable[[], object]] = []


def on_zone_started(fn: Callable[[], object]) -> Callable[[], object]:
    _zone_started_callbacks.append(fn)
    return fn


def _zone_loaded(zone_obj: Any) -> None:
    log('zone loaded: {}'.format(getattr(zone_obj, 'id', '?')))
    start_pump()
    start_keys()
    for fn in _zone_started_callbacks:
        _run_logged(fn, (), 'zone start ' + getattr(fn, '__name__', '?'))


def _zone_hook(original: Callable[..., Any]) -> Callable[..., Any]:
    def a11yplus_on_loading_screen_animation_finished(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        try:
            _zone_loaded(args[0])
        except Exception:  # a game hook: nothing may reach the game
            simsight_log.error(log_exc('zone hook'))
        return result

    return a11yplus_on_loading_screen_animation_finished


def _install_zone_hook() -> None:
    import zone

    simsight_patch.wrap(zone.Zone, 'on_loading_screen_animation_finished', _zone_hook)


try:
    _install_zone_hook()
    log('zone hook installed')
except Exception:  # module load: the rest of the mod still loads
    simsight_log.error(log_exc('install zone hook'))


@sims4.commands.Command('a11yplus.status', command_type=sims4.commands.CommandType.Live)  # type: ignore[misc]  # EA's decorator is untyped
@real_annotations
def _status(_connection: Any = None) -> bool:
    try:
        out = sims4.commands.CheatOutput(_connection)
        out(
            f'SimSight Plus {VERSION} - pump {_state.alarm is not None}, '
            f'keys {_state.keys_thread is not None}'
        )
    except Exception:  # a console command
        simsight_log.error(log_exc('status'))
    speak(f'SimSight Plus version {VERSION} is running.')
    return True

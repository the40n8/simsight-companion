"""Keyboard requests for EA's native Build cursor and transaction history.

Only the client performs placement. No object creation, funds adjustment,
footprint override, custom undo stack or synthetic success announcement.
The cursor uses screen coordinates, independently of the read-only lot grid.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, TypeVar
from urllib.parse import unquote

import sims4.commands
import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_speech as speech


class _State:
    def __init__(self) -> None:
        self.zone_id: Any = None  # the zone of the last request
        self.serial = 0  # the last request sent to the client
        self.pending: dict[int, Any] = {}  # serial -> zone id of each unanswered request
        self.active = False  # the client confirmed the cursor is on
        self.last_serial = 0  # the newest serial answered or retired
        self.last_feedback = ''  # the client's last hovered-object or error text
        self.feedback_time = 0.0
        self.aim_pending = 0  # serial of an aim still waiting for its reply
        self.aim_until = 0.0
        self.release_serial = 0  # the 'off' sent when Build mode ended; its reply is silent
        self.log_window = 0.0  # client diagnostics burst window start
        self.log_count = 0
        self.installed = False


_state = _State()
_LOG_BURST = 40  # lines per 5 s window; the rest are dropped with one notice
_PENDING_LIMIT = 128

ACTIONS = frozenset(
    (
        'on',
        'off',
        'toggle',
        'left',
        'right',
        'up',
        'down',
        'click',
        'cancel',
        'rotateleft',
        'rotateright',
        'undo',
        'redo',
        'status',
        'fine',
        'coarse',
        'center',
        'grid',
    )
)
ALIASES = {
    'prev': 'up',
    'next': 'down',
    'prevcat': 'left',
    'nextcat': 'right',
    'open': 'click',
    'back': 'cancel',
    'repeat': 'status',
    'where': 'status',
}
# Actions an accessibility menu row may request. Opening that menu retires
# the native cursor (a modal is open), so the client re-enables it at its
# remembered position for this one explicit choice, under its own guards.
MENU_ACTIONS = frozenset(
    (
        'left',
        'right',
        'up',
        'down',
        'click',
        'cancel',
        'rotateleft',
        'rotateright',
        'status',
        'fine',
        'coarse',
        'center',
    )
)
_WITHOUT_CURSOR = ('on', 'off', 'undo', 'redo')  # allowed while the cursor is off
_MOVES = ('left', 'right', 'up', 'down', 'center', 'click', 'cancel')  # clear the feedback
# Replies that end with the cursor's screen position.
_POSITION_REPLIES = ('on', 'aim', 'move', 'status', 'fine', 'coarse', 'center', 'click')
_REPLIES = {
    'on': (
        'Build cursor on. Screen pixels, not lot tiles. ',
        'Kursor budowania włączony. Piksele ekranu, nie pola parceli. ',
    ),
    'off': ('Build cursor off.', 'Kursor budowania wyłączony.'),
    'blocked': (
        'Close the open dialog or text field first.',
        'Najpierw zamknij otwarte okno lub pole tekstowe.',
    ),
    'unavailable': ('Build cursor unavailable.', 'Kursor budowania niedostępny.'),
    'click': ('Select or place. ', 'Wybierz lub umieść. '),
    'cancel': ('Cancel Build tool.', 'Anuluj narzędzie budowania.'),
    'undo': ('Undo.', 'Cofnij.'),
    'redo': ('Redo.', 'Ponów.'),
    'rotateleft': ('Rotate left requested.', 'Wysłano polecenie obrotu w lewo.'),
    'rotateright': ('Rotate right requested.', 'Wysłano polecenie obrotu w prawo.'),
    'toolblocked': (
        'Choose the Select tool or an object from the catalog first.',
        'Najpierw wybierz narzędzie zaznaczania lub obiekt z katalogu.',
    ),
    'nohistory': ('Nothing to undo or redo.', 'Brak dostępnego wpisu w historii gry.'),
    'aim': (
        'Build cursor aimed at the grid point. Press Enter to select or place. ',
        'Kursor budowania ustawiony na rzucie punktu siatki. Naciśnij Enter, aby wybrać lub umieścić obiekt. ',
    ),
    'aimfailed': (
        'The grid point is not visible in the Build viewport. Adjust the camera or floor, then choose the grid point again.',
        'Punkt siatki nie jest widoczny w oknie budowania. Zmień kamerę lub piętro, a następnie ponownie wybierz punkt.',
    ),
}

_F = TypeVar('_F', bound=Callable[..., Any])


def _text(en: str, pl: str) -> str:
    return pl if lang._detect_lang() == 'pl' else en


def _say(en: str, pl: str) -> None:
    # simsight_plus_build replaces this function to read aim replies: every
    # call here looks it up by name.
    speech.speak(_text(en, pl), explicit=True)


def _send_ui(message: str) -> None:
    import distributor.ops
    import distributor.system

    distributor.system.Distributor.instance().add_op_with_no_owner(
        distributor.ops.SendUIMessage(message)
    )


def controls_enabled() -> bool:
    """The Build controls switch: the cursor, the Build actions window and
    the read-only lot grid. Enabled by default;
    turning it off never blocks the release of a cursor already sent."""
    try:
        import simsight_settings as settings

        return bool(settings.get('build_controls'))
    except Exception:
        simsight_log.error('build: controls setting')
        return False


def refuse_disabled() -> bool:
    _say(
        'Build controls are off. Turn them on in the settings menu.',
        'Sterowanie budowaniem jest wyłączone. Włącz je w menu ustawień.',
    )
    return False


def announce_controls() -> bool:
    """Simulation thread: tell every live UI copy the current switch. New
    TooltipManager copies start with it off, so the keyboard heartbeat and
    each activation request send it again."""
    try:
        _send_ui('A11y|buildcontrols|' + ('on' if controls_enabled() else 'off'))
        return True
    except Exception:
        simsight_log.error('build: controls notice failed')
        return False


def _forget_session() -> None:
    """Nothing this session asked for is live any more."""
    _state.active, _state.aim_pending, _state.zone_id = False, 0, None


def apply_setting() -> bool:
    """The switch changed (settings menu or simsight.reload). Off retires the
    grid and any cursor or aim this session asked for, silently, before the
    client hears the new value."""
    if not controls_enabled():
        try:
            import simsight_grid as grid

            grid.retire()
        except Exception:
            simsight_log.error('build: grid retire failed')
        if _state.active or _state.aim_pending or _state.pending or _state.zone_id is not None:
            _state.pending.clear()
            sent = request('off')
            _state.release_serial = _state.serial if sent else 0
        _forget_session()
    return announce_controls()


def _zone() -> Any:
    """The current zone while it is in Build/Buy mode, else None."""
    try:
        import services

        zone = services.current_zone()
        return zone if zone is not None and zone.is_in_build_buy else None
    except Exception:
        simsight_log.error('build: current zone')
        return None


def active() -> bool:
    """A client acknowledgement is required before rerouting scanner keys."""
    zone = _zone()
    if zone is None or zone.id != _state.zone_id or not controls_enabled():
        _state.active = False
    return _state.active


def aiming() -> bool:
    zone = _zone()
    if _state.aim_pending and (
        zone is None
        or zone.id != _state.zone_id
        or time.monotonic() > _state.aim_until
        or not controls_enabled()
    ):
        _state.pending.pop(_state.aim_pending, None)
        _state.aim_pending = 0
    return bool(_state.aim_pending)


def on_key(intent: str) -> bool:
    if aiming():
        return request('off') if intent in ('back', 'cancel', 'off') else False
    return request(ALIASES.get(intent, intent))


def toggle() -> bool:
    return request('off' if active() else 'on')


def next_serial(zone_id: Any) -> int:
    """A new request serial, remembered as pending for zone_id (simsight_plus_build
    numbers its own client requests with it too)."""
    _state.serial += 1
    if len(_state.pending) > _PENDING_LIMIT:
        _state.pending.clear()
    _state.zone_id = zone_id
    _state.pending[_state.serial] = _state.zone_id
    return _state.serial


def aim_grid() -> bool:
    """Aim via native world-to-screen binding; never select/place automatically."""
    if not controls_enabled():
        return refuse_disabled()
    import simsight_grid as grid

    zone = _zone()
    target = grid.cursor()
    if zone is None or target is None or target[2] != zone.id:
        _say(
            'Choose a point on the lot grid in Build mode first.',
            'Najpierw wybierz punkt na siatce parceli w trybie budowania.',
        )
        return False
    position, level, ident = target
    values = (float(position.x), float(position.y), float(position.z))
    if not all(math.isfinite(value) for value in values):
        return False
    _state.pending.clear()
    serial = next_serial(ident)
    _state.active, _state.last_serial = False, serial
    _state.last_feedback = ''
    _state.aim_pending = serial
    _state.aim_until = time.monotonic() + 2.5
    # This leaves the sampled point intact but stops grid key capture.
    grid.leave()
    announce_controls()
    _send_ui(
        f'A11y|buildaim|{serial}|{values[0]:.6f}|{values[1]:.6f}|{values[2]:.6f}|{level}|{ident}'
    )
    return True


def _leave_grid() -> None:
    import simsight_grid as grid

    if grid.active():
        grid.leave()


def _may_request(action: str, zone: Any) -> bool:
    """Speaks why the action cannot be sent now."""
    if zone is None and action != 'off':
        _state.active = False
        _say('Enter Build mode first.', 'Najpierw włącz tryb budowania.')
        return False
    if action not in _WITHOUT_CURSOR and not active():
        _say('Build cursor is off.', 'Kursor budowania jest wyłączony.')
        return False
    return True


def request(action: str) -> bool:
    """Simulation thread only; accepted means sent, never placed successfully."""
    if action not in ACTIONS:
        return False
    if action != 'off' and not controls_enabled():
        return refuse_disabled()
    if action == 'toggle':
        return toggle()
    if action == 'grid':
        return aim_grid()
    zone = _zone()
    if not _may_request(action, zone):
        return False
    if action == 'on':
        _leave_grid()
    if action in ('on', 'off', *_MOVES):
        _state.last_feedback = ''
    serial = next_serial(zone.id if zone is not None else None)
    if action == 'off':
        _state.active = False
        _state.last_serial = serial
        _state.aim_pending = 0
    if action == 'on':
        announce_controls()
    _send_ui(f'A11y|build|{action}|{serial}')
    return True


def request_menu(action: str) -> bool:
    """An explicit Build menu choice, sent after that window has closed.
    The client checks Build mode, the keyboard lease and every modal or
    editing guard itself, enables the native cursor where it was (the mouse
    position the first time in this Build session) and performs the one
    action. Nothing resumes on its own; an unknown action is refused here."""
    if action not in MENU_ACTIONS:
        return False
    if not controls_enabled():
        return refuse_disabled()
    zone = _zone()
    if zone is None:
        _state.active = False
        _say('Enter Build mode first.', 'Najpierw włącz tryb budowania.')
        return False
    _leave_grid()
    if action in _MOVES:
        _state.last_feedback = ''
    serial = next_serial(zone.id)
    announce_controls()
    _send_ui(f'A11y|build|menu:{action}|{serial}')
    return True


def on_build_buy_exit() -> bool:
    """Build mode ended. The client's Build UI can be torn down before its
    own per-frame guard released the native cursor, which would leave that
    cursor enabled in Live mode, so the release is forced from here as well:
    an ordinary 'off', accepted outside Build, answered by whichever UI
    instance is live. Sent only when this Build session made a request; its
    reply is not spoken."""
    try:
        import simsight_place as place

        place.forget()  # a catalog product belongs to its Build session
        import simsight_grid as grid

        if grid.active():
            grid.retire()  # placement ends with Build mode; Ctrl+Alt+T reads the lot in Live
    except Exception:
        simsight_log.error('build: placement product not cleared')
    if not (_state.active or _state.zone_id is not None):
        return False  # this Build session never asked for the cursor
    try:
        sent = request('off')
    except Exception:
        simsight_log.error('build: release on Build exit failed')
        sent = False
    _state.release_serial = _state.serial if sent else 0
    _forget_session()
    simsight_log.line(
        'build: Build mode ended, cursor release {} (serial {})'.format(
            'sent' if sent else 'not sent', _state.serial
        )
    )
    return sent


def on_build_buy_enter() -> bool:
    """Build mode started. A read-only grid left on in Live would otherwise
    become an unannounced placement cursor, and the first Ctrl+Alt+B would
    turn it off (live 2026-09-26). Retire it so Ctrl+Alt+B always starts
    placement with its announcement, as Build exit already does."""
    try:
        import simsight_grid as grid

        if grid.active():
            grid.retire()
            simsight_log.line('build: Build mode started, Live grid retired')
    except Exception:
        simsight_log.error('build: Live grid not retired on Build entry')
    return True


def install() -> bool:
    """Register the Build entry and exit hooks once; EA calls them from
    c_api_buildbuy_session_begin and buildbuy_session_end on the
    simulation thread."""
    if _state.installed:
        return True
    import build_buy

    build_buy.register_build_buy_exit_callback(on_build_buy_exit)
    build_buy.register_build_buy_enter_callback(on_build_buy_enter)
    _state.installed = True
    return True


try:
    install()
except Exception:
    simsight_log.error('build: exit release not installed')


@sims4.commands.Command(
    'simsight.build', 'a11y.build', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def command(action: str = 'toggle', _connection: Any = None) -> bool:
    try:
        return request(action.lower())
    except Exception:
        simsight_log.error('build: command')
        return False


def _info(encoded: str) -> bool:
    zone = _zone()
    if zone is None or zone.id != _state.zone_id or not controls_enabled():
        return False
    try:
        kind, payload = encoded.split('|', 1)
    except ValueError:
        return False
    if kind == 'clear':
        _state.last_feedback = ''
        return True
    if kind not in ('name', 'error'):
        return False
    text = unquote(payload).strip()
    if not text or len(text) > 6000:
        return False
    now = time.monotonic()
    if text == _state.last_feedback and now - _state.feedback_time < 1:
        return True
    _state.last_feedback, _state.feedback_time = text, now
    speech.speak(text, explicit=True)
    return True


@sims4.commands.Command(
    'simsight.buildinfo', 'a11y.buildinfo', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def info(encoded: str = '', _connection: Any = None) -> bool:
    """Native localized hovered-object/error text; never infer placement success."""
    try:
        return _info(encoded)
    except Exception:
        simsight_log.error('build: buildinfo')
        return False


def _client_log(encoded: str) -> bool:
    text = unquote(str(encoded)).strip()
    if not text or len(text) > 400:
        return False
    now = time.monotonic()
    if now - _state.log_window > 5:
        _state.log_window, _state.log_count = now, 0
    _state.log_count += 1
    if _state.log_count > _LOG_BURST:
        if _state.log_count == _LOG_BURST + 1:
            simsight_log.line('build client: further lines dropped for this window')
        return False
    simsight_log.line('build client: ' + text)
    return True


@sims4.commands.Command(
    'simsight.buildlog', 'a11y.buildlog', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def buildlog(encoded: str = '', _connection: Any = None) -> bool:
    """Client lifecycle diagnostics for the Build cursor and the catalog
    product memory: written to the log, never spoken, capped per window."""
    try:
        return _client_log(encoded)
    except Exception:
        simsight_log.error('build: buildlog')
        return False


def _parse_reply(encoded: str) -> tuple[int, int, str, int, int, int] | None:
    """(serial, active flag, result, x, y, step) of a valid client reply."""
    try:
        fields = encoded.split('|')
        if len(fields) != 6:
            return None
        serial, flag = int(fields[0]), int(fields[1])
        if flag not in (0, 1) or (serial != 0 and serial not in _state.pending):
            return None
        if serial == 0 and flag != 0:
            return None
        x, y, step = (int(value) for value in fields[3:])
        if min(x, y) < 0 or step not in (4, 16, 64):
            return None
    except (ValueError, TypeError):
        return None
    return serial, flag, fields[2], x, y, step


def _accept_serial(serial: int, flag: int, zone: Any) -> bool:
    """Retire the answered request; False for a stale or misplaced reply."""
    if serial:
        expected_zone = _state.pending.pop(serial)
        if serial < _state.last_serial or (flag and (zone is None or zone.id != expected_zone)):
            return False
        _state.last_serial = serial
    else:
        # Automatic retirement wins over any older request still in flight.
        _state.last_serial = _state.serial
        _state.pending.clear()
    return True


def _release_silently() -> None:
    """The client turned a cursor on for a request sent before the switch
    went off: never announce it; release it once, silently."""
    _state.active, _state.aim_pending = False, 0
    _state.pending.clear()
    if request('off'):
        _state.release_serial = _state.serial


def _announce_reply(result: str, x: int, y: int, step: int) -> None:
    if result in _POSITION_REPLIES:
        prefix = _text(*_REPLIES[result]) if result in _REPLIES else ''
        feedback = (
            ' ' + _state.last_feedback
            if _state.last_feedback and result in ('click', 'status')
            else ''
        )
        _say(
            prefix + f'Screen x {x}, y {y}. Step {step} pixels.' + feedback,
            prefix + f'Ekran x {x}, y {y}. Krok {step} pikseli.' + feedback,
        )
    elif result in _REPLIES:
        _say(*_REPLIES[result])


def _client_state(encoded: str) -> bool:
    parsed = _parse_reply(encoded)
    if parsed is None:
        return False
    serial, flag, result, x, y, step = parsed
    zone = _zone()
    if not _accept_serial(serial, flag, zone):
        return False
    if flag and not controls_enabled():
        _release_silently()
        return True
    _state.active = bool(flag) and zone is not None
    if serial in (0, _state.aim_pending):
        _state.aim_pending = 0
    if serial and serial == _state.release_serial and not flag:
        return True  # the Build-exit release: nothing to announce in Live mode
    _announce_reply(result, x, y, step)
    return True


@sims4.commands.Command(
    'simsight.buildstate', 'a11y.buildstate', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def state(encoded: str = '', _connection: Any = None) -> bool:
    """Client reply: serial|active|result|x|y|step. Coordinates are pixels."""
    try:
        return _client_state(encoded)
    except Exception:
        simsight_log.error('build: buildstate')
        return False

"""Commands from the phone: one handler per command, and the result
message sent back."""

from __future__ import annotations


import json
from typing import TYPE_CHECKING

import simsight_log
import simsight_speech as speech

from .house import _set_camera
from .inventory import list_inventory, move_item
from .menus import get_interactions, list_targets, run_interaction
from .pipe import send
from .state import _in_game, _state, log


if TYPE_CHECKING:
    from collections.abc import Callable

    from .state import Answer, Json


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
    if _state.mackeys is None:
        return False, 'error'
    if k.get('family') == 'focus':
        _state.mackeys.focus_lost()
        return True, ''
    return _state.mackeys.handle(
        k.get('family', ''), int(k.get('vk', 0)), bool(k.get('down')), bool(k.get('shift'))
    ), 'not_owned'


def _cmd_list_inventory(_c: Json) -> Answer:
    return list_inventory()


def _cmd_move_item(c: Json) -> Answer:
    return move_item(c.get('itemId', ''), c.get('destinationId', ''))


def _cmd_set_camera(c: Json) -> Answer:
    return _set_camera(c.get('simId', ''), c.get('mode', 'CAMERA_MODE_FOLLOW'))


def _cmd_respond_dialog(c: Json) -> Answer:
    if _state.dialogs is None:
        return False, 'error'
    return _state.dialogs.respond(
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
    except Exception:
        simsight_log.error('bridge: clock.setspeed (the clock is set directly)')
    else:
        return True
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

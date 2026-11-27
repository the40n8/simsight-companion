"""Scanner keys: the chord intents, the pie menu request and the menu
flag."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, cast

import sims4.commands

import simsight_availability as availability
import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_pump as pump
import simsight_speech as speech

from .state import Entry, MENU_KEYS, _current, _entries, _state, _subs_in, _touch, enabled
from .loc import send_ui, speak_parts
from .categories import _label_part
from .entries import _entry_parts, _level_of, _sim_name_parts, _sub_label_part
from .tree import _tree_has_sublevel, _tree_parts, _tree_remember, _tree_restore, _tree_speak
from .scan import _ensure_scan, cycle_range, rescan

if TYPE_CHECKING:
    from sims.sim import Sim
    from sims.sim_info import SimInfo

    from .state import Part


def enter_mode() -> None:
    """Keyboard router calls on the simulation thread after mode activation."""
    _touch()
    _ensure_scan()
    _tree_speak(lang.t('scanner') + '. ')


def resume_mode() -> None:
    """Restore the spoken location after the native menu relinquishes input."""
    enter_mode()


def set_camera_live(live: bool) -> None:
    import simsight_camera as camera_follow

    camera_follow.set_live(live)


def update_camera_follow() -> None:
    import simsight_camera as camera_follow

    camera_follow.update()


def _tree_move(step: int) -> None:
    entry = _current()
    if entry is None:
        _tree_speak()
        return
    _tree_remember()
    if _state.tree_level == 'groups':
        _state.group_index = (_state.group_index + step) % len(_state.groups)
        _tree_restore()
    elif _state.tree_level == 'subcategories':
        subs = _subs_in(_entries())
        _tree_restore(subs[(subs.index(entry.category) + step) % len(subs)])
    else:
        indices = [i for i, other in enumerate(_entries()) if other.category == entry.category]
        _state.item_index = indices[(indices.index(_state.item_index) + step) % len(indices)]
    _tree_remember()
    _tree_speak()


def _tree_enter(activate: bool = False) -> None:
    if _current() is None:
        _tree_speak()
        return
    if _state.tree_level == 'groups':
        _state.tree_level = 'subcategories' if _tree_has_sublevel() else 'items'
    elif _state.tree_level == 'subcategories':
        _state.tree_level = 'items'
    elif activate:
        open_pie_menu()
        return
    # Right on a leaf only repeats it; Enter is required for interaction.
    _tree_speak()


def _tree_back() -> None:
    _tree_remember()
    if _state.tree_level == 'items' and _tree_has_sublevel():
        _state.tree_level = 'subcategories'
    else:
        _state.tree_level = 'groups'
    _tree_speak()


def _do_rescan() -> None:
    _state.class_cache.clear()
    availability.recheck()
    if not rescan(preserve=True):
        speech.speak(lang.t('nothing nearby'), source='widget')
        return
    total = sum(len(v) for v in _state.by_group.values())
    parts: list[Part] = [
        '{} {}, {} {}. '.format(total, lang.t('items'), len(_state.groups), lang.t('categories'))
    ]
    speak_parts(parts + _tree_parts())


def _do_step_sub(step: int) -> None:
    """Optional legacy Page key jump; the tree itself needs no Page keys."""
    entry = _current()
    if entry is None:
        _tree_speak()
        return
    _tree_remember()
    subs = _subs_in(_entries())
    _tree_restore(subs[(subs.index(entry.category) + step) % len(subs)])
    _state.tree_level = 'items'
    _tree_remember()
    landed = _current() or entry  # the list is not empty: always the restored entry
    speak_parts([_label_part(landed.category), '. ', *_tree_parts()])


def _do_where() -> None:
    import services

    parts: list[Part] = []
    sim: Sim | None = services.get_active_sim()
    if sim is not None:
        # Sim.sim_info is a property with a setter: the generated stub shows a method.
        name, key = _sim_name_parts(cast('SimInfo', sim.sim_info))
        parts.append(name or ('loc', key))
        parts.append('. ')
    entry = _current()
    if entry is None:
        parts.append(lang.t('nothing nearby'))
    else:
        entries = _entries()
        parts.append(_label_part(entry.group))
        sub = _sub_label_part(entries, entry)
        if sub is not None:
            parts.append(', ')
            parts.append(sub)
        parts.append('. ')
        parts.extend(_entry_parts(entry, _state.item_index % len(entries), len(entries)))
    speak_parts(parts)


def _pick_message(entry: Entry, obj: Any, services: Any) -> str:
    """The A11y|pick message that asks the client for this thing's pie menu."""
    from server.pick_info import PickType

    pick_type = PickType.PICK_SIM if entry.is_sim else PickType.PICK_OBJECT
    pos = obj.position
    level = _level_of(obj)
    try:
        lot_id = int(services.active_lot().lot_id)
    except (AttributeError, TypeError, ValueError):
        lot_id = 0  # no active lot
    first = (entry.first_name or '').replace('|', ' ')
    return f'A11y|pick|{obj.id}|{int(pick_type)}|{lot_id}|{first}|{float(pos.x):.3f}|{float(pos.y):.3f}|{float(pos.z):.3f}|{level}'


def open_pie_menu() -> None:
    """Ask the client to open the game's pie menu on the current thing.

    The request goes to Flash first (A11y|pick|...): the patched PieMenuMain
    posts interactions.choices through the client's own command channel with
    its own request counter, exactly like a mouse click, so the reply is a
    menu the client asked for and it draws it. Python never calls
    generate_choices itself (v1 did, and the client discarded the reply)."""
    entry = _current()
    if entry is None:
        speech.speak(lang.t('nothing nearby'), source='widget')
        return
    import services

    obj = services.object_manager().get(entry.obj_id)
    if obj is None:
        speech.speak(lang.t('no longer here'), source='widget')
        return
    try:
        sent = send_ui(_pick_message(entry, obj, services))
    except Exception:
        simsight_log.error('scanner: open menu')
        sent = False
    simsight_log.line(
        f'scanner: menu requested for {entry.obj_id} (sim={entry.is_sim}) sent={sent}'
    )


@sims4.commands.Command(
    'simsight.menuempty', 'a11y.menuempty', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_menuempty(
    arg: str = '',  # noqa: ARG001 - Flash may post one; not needed
    _connection: Any = None,
) -> bool:
    """Flash: the requested pie menu had no options."""
    try:
        speech.speak(lang.t('no options'), source='widget')
    except Exception:
        simsight_log.error('scanner: menu empty')
    return True


_INTENTS: dict[str, Callable[[], object]] = {
    'rescan': _do_rescan,
    'next': lambda: _tree_move(1),
    'prev': lambda: _tree_move(-1),
    'nextcat': _tree_enter,
    'prevcat': _tree_back,
    'nextsub': lambda: _do_step_sub(1),
    'prevsub': lambda: _do_step_sub(-1),
    'open': lambda: _tree_enter(activate=True),
    'where': _do_where,
    'back': _tree_back,
    'scopeprev': lambda: cycle_range(-1),
    'scopenext': lambda: cycle_range(1),
}


def _dispatch(intent: str) -> None:
    _touch()
    if intent != 'rescan':
        _ensure_scan()
    action = _INTENTS.get(intent)
    if action is None:
        simsight_log.line(f'scanner: unknown intent {intent!r}')
        return
    action()


def on_key(intent: str, on_simulation_thread: bool = False) -> None:
    """Called on the KEY thread. Decides who handles the chord; never
    touches the game here unless its caller explicitly owns the simulation thread."""
    if not enabled():
        return
    if intent == 'repeat':
        speech.repeat_last()
        return
    if intent == 'help':
        import simsight_shortcuts as shortcuts

        shortcuts.speak()
        return
    if _state.menu_open and intent in MENU_KEYS:
        # A pie menu is open: the chords walk it through the Flash walker.
        _log_route('sent to the open menu', intent)
        try:
            import simsight_keys as keys

            keys.send_action(MENU_KEYS[intent])
        except Exception:
            simsight_log.error('scanner: menu key')
        return
    if on_simulation_thread:
        # The world-chord callback has already crossed onto the simulation.
        # Do not defer the same key to a second alarm turn.
        _log_route('dispatched to the scanner', intent)
        _dispatch(intent)
    elif not pump.post(_dispatch, intent):
        speech.speak(lang.t('no lot loaded'), explicit=True)


def _log_route(reason: str, intent: str) -> None:
    """One line per reason per second (fixed reasons, bounded dict)."""
    now = time.monotonic()
    if now - _state.route_logged.get(reason, 0.0) >= 1.0:
        _state.route_logged[reason] = now
        simsight_log.line(f'scanner: chord {intent} {reason}')


def note_menu_open() -> None:
    """Any pie menu opened (ours or a mouse click)."""
    if not _state.menu_open:
        simsight_log.line('scanner: menu flag set')
    _state.menu_open = True
    availability.pause()


@sims4.commands.Command(
    'simsight.menuclosed', 'a11y.menuclosed', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_menuclosed(
    arg: str = '',  # noqa: ARG001 - Flash may post one; not needed
    _connection: Any = None,
) -> bool:
    if _state.menu_open:
        simsight_log.line('scanner: menu flag cleared')
    _state.menu_open = False
    return True

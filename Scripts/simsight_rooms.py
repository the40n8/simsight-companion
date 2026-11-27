"""Room labels for the Build cursor. Contributed by DaRainbowGirlXX
(2026-10-16), merged into SimSight 2026-11-12.

Ctrl+Alt+J (Build cursor on) opens a list of labels for the room under the
cursor; simsight.roomlabel <name> sets a custom one. Labels are kept per save,
lot, floor and room in SimSight_RoomLabels.json next to the log, and the
Build cursor names labelled rooms as it enters them and across walls.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    RoomKey = tuple[Any, Any]  # (room id, level)

PRESETS = (
    'bedroom',
    'bathroom',
    'kitchen',
    'living room',
    'dining room',
    'office',
    'nursery',
    'hallway',
    'utility room',
    'garage',
    'clear label',
)
_MAX_LABEL = 60


class _State:
    def __init__(self) -> None:
        self.labels: dict[str, str] = {}  # 'slot:zone:level:room' -> label
        self.loaded = False
        self.picker_id: Any = None  # the open label list's dialog id
        self.picker_room: RoomKey | None = None  # the room it was opened for


_state = _State()


def _path() -> str:
    return os.path.join(os.path.dirname(simsight_log.PATH), 'SimSight_RoomLabels.json')


def _load() -> None:
    if _state.loaded:
        return
    _state.loaded = True
    try:
        with open(_path(), encoding='utf-8') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        simsight_log.error('rooms: label load failed')
        return
    if isinstance(data, dict):
        _state.labels = {str(k): str(v)[:_MAX_LABEL] for k, v in data.items() if str(v).strip()}


def _save() -> bool:
    path = _path()
    temporary = path + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(_state.labels, stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, path)
    except OSError:
        simsight_log.error('rooms: label save failed')
        with contextlib.suppress(OSError):  # the save already failed: nothing more to do
            if os.path.isfile(temporary):
                os.remove(temporary)
        return False
    return True


def _save_slot() -> int:
    try:
        import services

        return int(services.get_persistence_service().get_save_slot_proto_buff().slot_id)
    except (AttributeError, TypeError, ValueError):  # no save slot yet (a new game): slot 0
        return 0


def _key(room_id: Any, level: Any) -> str:
    import services

    return f'{_save_slot()}:{services.current_zone_id()}:{int(level)}:{int(room_id)}'


def label_for(room_id: Any, level: Any) -> str | None:
    if room_id is None:
        return None
    _load()
    try:
        return _state.labels.get(_key(room_id, level))
    except Exception:  # no zone to look up: no label
        simsight_log.error('rooms: label lookup')
        return None


def display_name(room_id: Any, level: Any) -> str | None:
    label = label_for(room_id, level)
    if label:
        return label
    try:
        from simsight_room_bridge import inferred_name

        return inferred_name(room_id, level)
    except Exception:
        simsight_log.error('rooms: inferred room name unavailable')
        return None


def _room_at(i: int, j: int, level: Any) -> tuple[Any, Any]:
    """(room id, outside) of a Build grid tile, (None, None) without a frame."""
    import simsight_tiles as tiles

    frame = tiles.frame()
    if frame is None:
        return None, None
    return tiles.room(frame, i, j, level), tiles.ground(frame, i, j, level).get('outside')


def room_name(room_id: Any, outside: Any, level: Any) -> str | None:
    """'outside', the room's label, or None for an unlabelled room."""
    if outside:
        return 'outside'
    return display_name(room_id, level)


def current_room() -> RoomKey | None:
    import simsight_grid as grid

    room_id, outside = _room_at(grid.location.tile[0], grid.location.tile[1], grid.location.level)
    if room_id is None or outside:
        return None
    return room_id, grid.location.level


def _clean(label: object) -> str:
    return ' '.join(str(label or '').strip().split())[:_MAX_LABEL]


def set_current_label(label: object) -> bool:
    _load()
    room = current_room()
    if room is None:
        speech.speak('The cursor is not in a labelable room.', explicit=True)
        return False
    value = _clean(label)
    clear = not value or value.lower() == 'clear label'
    key = _key(*room)
    old = _state.labels.get(key)
    if clear:
        _state.labels.pop(key, None)
    else:
        _state.labels[key] = value
    if not _save():
        if old is None:
            _state.labels.pop(key, None)
        else:
            _state.labels[key] = old
        speech.speak('The room label could not be saved.', explicit=True)
        return False
    speech.speak('Room label cleared.' if clear else f'Room labelled {value}.', explicit=True)
    return True


def _picker_result(dialog: Any) -> None:
    if dialog.dialog_id != _state.picker_id:
        return
    _state.picker_id = None
    expected = _state.picker_room
    _state.picker_room = None
    if not dialog.accepted or current_room() != expected:
        return
    try:
        tags = list(dialog.get_result_tags())
        if tags:
            set_current_label(PRESETS[int(tags[0])])
    except Exception:
        simsight_log.error('rooms: picker result failed')


def _open_picker(room: RoomKey) -> bool:
    import simsight_keys as keys
    import simsight_menu as menu
    from ui.ui_dialog_picker import BasePickerRow

    dialog = menu.picker(
        'Room label',
        'Choose a label for the room at the Build cursor. For a custom name, use simsight.roomlabel followed by the name in the cheat console.',
        text_only=True,
    )
    if dialog is None:
        return False
    current = label_for(room[0], room[1])
    for index, name in enumerate(PRESETS):
        dialog.add_row(
            BasePickerRow(
                option_id=index,
                name=menu.raw_text(name),
                row_description=None,
                row_tooltip=None,
                icon=None,
                tag=index,
                is_selected=(name == current),
            )
        )
    dialog.add_listener(_picker_result)
    keys.fence_opening_input()
    dialog.show_dialog()
    _state.picker_id = dialog.dialog_id
    _state.picker_room = room
    return True


def show_picker() -> bool:
    """Ctrl+Alt+J with the Build cursor on. Simulation thread."""
    room = current_room()
    if room is None:
        speech.speak('The cursor is not in a labelable room.', explicit=True)
        return False
    try:
        return _open_picker(room)
    except Exception:
        simsight_log.error('rooms: picker failed')
        speech.speak('The room label list could not be opened.', explicit=True)
        return False


def _separated(frame: Any, i: int, j: int, level: Any) -> tuple[list[tuple[int, str, Any]], bool]:
    """The sides with a wall or door between two rooms, as (side, direction,
    room name or None), and whether any room across one is named."""
    import simsight_tiles as tiles
    import simsight_wall as wall

    sides = tiles.walls(frame, i, j, level)
    doors = tiles.doors(frame, i, j, level)
    separated = []
    named = False
    for side, (di, dj) in enumerate(tiles.STEPS):
        if (side in doors or wall.blocked_side(sides.get(side))) and wall.separates_rooms(
            frame, side
        ):
            there_id, there_outside = _room_at(i + di, j + dj, level)
            there_name = room_name(there_id, there_outside, level)
            named = named or (there_name is not None and not there_outside)
            separated.append((side, tiles.direction_name(side), there_name))
    return separated, named


def _facing_part(here: str, separated: list[tuple[int, str, Any]]) -> str | None:
    """Which room a wall object about to be placed faces into, when it faces a wall."""
    import simsight_grid as grid
    import simsight_place as place

    item = place.product()
    faces_wall = any(side == int(grid.location.facing) % 4 for side, _d, _t in separated)
    if item is not None and place.requirement(item['id']) == 'wall' and faces_wall:
        return lang.text(
            'Selected object faces into {}. ', 'Wybrany obiekt jest skierowany do: {}. '
        ).format(here)
    return None


def _wall_parts(frame: Any, parts: list[str]) -> None:
    import simsight_grid as grid

    i, j = grid.location.tile
    level = grid.location.level
    here_id, here_outside = _room_at(i, j, level)
    unnamed = lang.text('unlabelled room', 'pomieszczenie bez nazwy')
    here_name = room_name(here_id, here_outside, level)
    separated, named_across = _separated(frame, i, j, level)
    named = (here_name is not None and not here_outside) or named_across
    if not separated or not named:
        return
    here = here_name or unnamed
    parts.append(lang.text('Current room: {}. ', 'Bieżące pomieszczenie: {}. ').format(here))
    for _side, direction, there in separated:
        parts.append(
            lang.text('Across {}: {}. ', 'Za ścianą {}: {}. ').format(direction, there or unnamed)
        )
    facing = _facing_part(here, separated)
    if facing is not None:
        parts.append(facing)


def wall_context(frame: Any) -> list[str]:
    """The rooms on either side of the walls and doors around the cursor,
    for the Build cursor's tile reading. Said only once a room involved has
    a label, so unlabelled houses read as before."""
    parts: list[str] = []
    try:
        _wall_parts(frame, parts)
    except Exception:  # the tile is read without its room context
        simsight_log.error('rooms: wall room context failed')
    return parts


def _roomlabel(label: str = '', _connection: Any = None) -> bool:
    return set_current_label(label)


# The game's console reads each argument's type from the annotations, which
# `from __future__ import annotations` turns into strings: give it the real ones.
_roomlabel.__annotations__ = {'label': str}

try:
    import sims4.commands

    roomlabel_command = sims4.commands.Command(
        'simsight.roomlabel', 'a11y.roomlabel', command_type=sims4.commands.CommandType.Live
    )(_roomlabel)
except Exception:
    simsight_log.error('rooms: room label command unavailable')

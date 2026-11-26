"""Room labels for the Build cursor. Contributed by DaRainbowGirlXX
(2026-10-16), merged into SimSight 2026-11-12.

Ctrl+Alt+J (Build cursor on) opens a list of labels for the room under the
cursor; simsight.roomlabel <name> sets a custom one. Labels are kept per save,
lot, floor and room in SimSight_RoomLabels.json next to the log, and the
Build cursor names labelled rooms as it enters them and across walls.
"""

import json
import os

import simsight_speech as speech


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
_labels = {}
_loaded = False
_picker_id = None
_picker_room = None


def _log(text):
    speech.log_line('rooms: ' + text)


def _path():
    return os.path.join(os.path.dirname(speech.LOG_PATH), 'SimSight_RoomLabels.json')


def _load():
    global _loaded, _labels
    if _loaded:
        return
    _loaded = True
    try:
        with open(_path(), encoding='utf-8') as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            _labels = {str(k): str(v)[:60] for k, v in data.items() if str(v).strip()}
    except FileNotFoundError:
        pass
    except Exception as ex:
        _log(f'label load failed: {ex!r}')


def _save():
    path = _path()
    temporary = path + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(_labels, stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(temporary, path)
        return True
    except Exception as ex:
        _log(f'label save failed: {ex!r}')
        try:
            if os.path.isfile(temporary):
                os.remove(temporary)
        except Exception:
            pass
        return False


def _save_slot():
    try:
        import services

        return int(services.get_persistence_service().get_save_slot_proto_buff().slot_id)
    except Exception:
        return 0


def _key(room_id, level):
    import services

    return f'{_save_slot()}:{services.current_zone_id()}:{int(level)}:{int(room_id)}'


def label_for(room_id, level):
    if room_id is None:
        return None
    _load()
    try:
        return _labels.get(_key(room_id, level))
    except Exception:
        return None


def display_name(room_id, level):
    label = label_for(room_id, level)
    if label:
        return label
    try:
        from simsight_room_bridge import inferred_name

        return inferred_name(room_id, level)
    except Exception as exc:
        _log(f'inferred room name unavailable: {exc!r}')
        return None


def _room_at(i, j, level):
    import simsight_tiles as tiles

    frame = tiles.frame()
    if frame is None:
        return None, None
    return tiles.room(frame, i, j, level), tiles.ground(frame, i, j, level).get('outside')


def room_name(room_id, outside, level):
    """'outside', the room's label, or None for an unlabelled room."""
    if outside:
        return 'outside'
    return display_name(room_id, level)


def current_room():
    import simsight_grid as grid

    room_id, outside = _room_at(grid._tile[0], grid._tile[1], grid._level)
    if room_id is None or outside:
        return None
    return room_id, grid._level


def set_current_label(label):
    _load()
    room = current_room()
    if room is None:
        speech.speak('The cursor is not in a labelable room.', explicit=True)
        return False
    room_id, level = room
    value = ' '.join(str(label or '').strip().split())[:60]
    key = _key(room_id, level)
    if not value or value.lower() == 'clear label':
        old = _labels.pop(key, None)
        if not _save():
            if old is not None:
                _labels[key] = old
            speech.speak('The room label could not be saved.', explicit=True)
            return False
        speech.speak('Room label cleared.', explicit=True)
        return True
    old = _labels.get(key)
    _labels[key] = value
    if not _save():
        if old is None:
            _labels.pop(key, None)
        else:
            _labels[key] = old
        speech.speak('The room label could not be saved.', explicit=True)
        return False
    speech.speak(f'Room labelled {value}.', explicit=True)
    return True


def _picker_result(dialog):
    global _picker_id, _picker_room
    if dialog.dialog_id != _picker_id:
        return
    _picker_id = None
    expected = _picker_room
    _picker_room = None
    if not dialog.accepted or current_room() != expected:
        return
    try:
        tags = list(dialog.get_result_tags())
        if tags:
            set_current_label(PRESETS[int(tags[0])])
    except Exception as ex:
        _log(f'picker result failed: {ex!r}')


def show_picker():
    """Ctrl+Alt+J with the Build cursor on. Simulation thread."""
    global _picker_id, _picker_room
    room = current_room()
    if room is None:
        speech.speak('The cursor is not in a labelable room.', explicit=True)
        return False
    try:
        import simsight_menu as menu
        from ui.ui_dialog_picker import BasePickerRow

        dialog = menu._picker(
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
                    name=menu._raw(name),
                    row_description=None,
                    row_tooltip=None,
                    icon=None,
                    tag=index,
                    is_selected=(name == current),
                )
            )
        dialog.add_listener(_picker_result)
        menu._keys().fence_opening_input()
        dialog.show_dialog()
        _picker_id = dialog.dialog_id
        _picker_room = room
        return True
    except Exception as ex:
        _log(f'picker failed: {ex!r}')
        speech.speak('The room label list could not be opened.', explicit=True)
        return False


def wall_context(frame):
    """The rooms on either side of the walls and doors around the cursor,
    for the Build cursor's tile reading. Said only once a room involved has
    a label, so unlabelled houses read as before."""
    import simsight_grid as grid
    import simsight_tiles as tiles
    import simsight_wall as wall

    parts = []
    try:
        i, j = grid._tile
        here_id, here_outside = _room_at(i, j, grid._level)
        unnamed = grid._text('unlabelled room', 'pomieszczenie bez nazwy')
        here_name = room_name(here_id, here_outside, grid._level)
        sides = tiles.walls(frame, i, j, grid._level)
        doors = tiles.doors(frame, i, j, grid._level)
        separated, named = [], here_name is not None and not here_outside
        for side, (di, dj) in enumerate(tiles.STEPS):
            if (side in doors or wall._blocked(sides.get(side))) and wall.separates_rooms(
                frame, side
            ):
                there_id, there_outside = _room_at(i + di, j + dj, grid._level)
                there_name = room_name(there_id, there_outside, grid._level)
                named = named or (there_name is not None and not there_outside)
                separated.append((side, tiles.direction_name(side), there_name or unnamed))
        here = here_name or unnamed
        if separated and named:
            parts.append(
                grid._text('Current room: {}. ', 'Bieżące pomieszczenie: {}. ').format(here)
            )
            for _side, direction, there in separated:
                parts.append(
                    grid._text('Across {}: {}. ', 'Za ścianą {}: {}. ').format(direction, there)
                )
            import simsight_place as place

            item = place.product()
            faces_wall = any(side == int(grid._facing) % 4 for side, _d, _t in separated)
            if item is not None and place.requirement(item['id']) == 'wall' and faces_wall:
                parts.append(
                    grid._text(
                        'Selected object faces into {}. ', 'Wybrany obiekt jest skierowany do: {}. '
                    ).format(here)
                )
    except Exception as ex:
        _log(f'wall room context failed: {ex!r}')
    return parts


try:
    import sims4.commands

    @sims4.commands.Command(
        'simsight.roomlabel', 'a11y.roomlabel', command_type=sims4.commands.CommandType.Live
    )
    def roomlabel_command(label: str = '', _connection=None):
        return set_current_label(label)
except Exception as ex:
    _log(f'room label command unavailable: {ex!r}')

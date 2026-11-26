"""Visual settings window, opened with simsight.menu [window|open|close].

Pick a row to cycle its value and persist/apply it immediately. Numeric
values wrap at their maximum so every supported value remains reachable.
Scanner chords always belong to the scanner. The old simsight.scanview command
is retained as a diagnostic; captions are the narration display.
First-install automatic opening is deferred by the user (2026-09-15).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import sims4.commands

import simsight_lang as lang
import simsight_log
import simsight_pump as pump
import simsight_settings as settings
import simsight_speech as speech
from simsight_narrator import live_command


@dataclass(frozen=True)
class Row:
    key: str
    kind: str  # bool | choice | number | voice | action
    term: str  # simsight_lang term for the label
    choices: tuple[str, ...] = ()
    lo: float = 0
    hi: float = 0
    step: float = 1
    help: str = ''  # settings-window description (visual only)


ROWS: list[Row] = [
    Row(
        'speech_mode',
        'choice',
        'speech mode',
        choices=('hover', 'manual', 'beeps', 'off'),
        help='hover speaks what the pointer touches; manual reads only on the read key; beeps; off',
    ),
    Row(
        'braille_mode',
        'choice',
        'braille mode',
        choices=('concise', 'full', 'only', 'off'),
        help='concise: speak everything, braille the label; full; only: braille only; off',
    ),
    Row(
        'verbosity',
        'choice',
        'verbosity',
        choices=('full', 'brief'),
        help='full says what a thing is before its name, such as Category or Dialog; brief says only the names',
    ),
    Row(
        'keyboard',
        'bool',
        'keyboard navigation',
        help='Tab, arrows, Enter, Space and Escape; opening a panel pulls focus inside',
    ),
    Row(
        'simplified_menus',
        'bool',
        'simplified menus',
        help='arrows and Tab step through every option of a menu, dialog or picker as one list; off follows the screen layout',
    ),
    Row(
        'numpad_navigation',
        'bool',
        'numpad navigation',
        help='optional numpad 4/6 navigation, 5 activate, 8 repeat, decimal back',
    ),
    Row(
        'scanner',
        'bool',
        'scanner',
        help='Ctrl+Alt scanner: browse the Sims and objects on the lot',
    ),
    Row('object_names', 'bool', 'object names', help='name tooltips on plain objects'),
    Row('sim_actions', 'bool', 'sim actions', help='announce what the active Sim starts doing'),
    Row(
        'tooltip_delay',
        'number',
        'tooltip delay',
        lo=0.0,
        hi=10.0,
        step=0.5,
        help='seconds before a long description is read; 0 reads it at once',
    ),
    Row('captions', 'bool', 'captions', help='show narration as an on-screen caption'),
    Row(
        'startup',
        'bool',
        'startup speech',
        help='speech and keyboard at the main menu and Create a Sim before a save loads; applies at the next launch',
    ),
    Row(
        'travel_list',
        'bool',
        'travel list',
        help='with keyboard navigation on, choose travel destinations from a world and place list; the map stays one row away',
    ),
    Row(
        'scanner_mode',
        'bool',
        'scanner mode',
        help='plain arrows browse the scanner and never move the camera; Control Alt Space toggles',
    ),
    Row(
        'build_controls',
        'bool',
        'build controls',
        help='place Build catalog objects from the lot grid in Build mode with Control Alt B; read-only lot grid in Live mode',
    ),
    Row(
        'scanner_range',
        'choice',
        'scanner range',
        choices=(
            '6 meters',
            '12 meters',
            '24 meters',
            '40 meters',
            'whole lot',
            'this floor',
            'this room',
        ),
        help='how far the scanner looks; Control Alt comma and Control Alt period change it',
    ),
    Row(
        'surface_layer_focus',
        'bool',
        'layer focus',
        help='Build cursor: Control Alt U and D choose a layer; number 1 focuses objects on it, number 4 names nearby layers',
    ),
    Row(
        'chair_snapping',
        'bool',
        'experimental chair snapping',
        help='dining and desk chairs: face a nearby table or focus it with 1; the game validates its native seat slot',
    ),
    Row(
        'experimental_basement',
        'bool',
        'experimental basement creation',
        help='Build helper basements use the native Basement Tool from ground level and verify the result below',
    ),
    Row(
        'chair_alignment_hints',
        'bool',
        'chair-to-table orientation hints',
        help='for dining and desk chairs, names the nearest table or desk and facing relation',
    ),
    Row(
        'experimental_bare_ground',
        'bool',
        'experimental bare-ground placement',
        help='Build cursor: needs natural ground; the game still checks the spot',
    ),
    Row(
        'experimental_water',
        'bool',
        'experimental water placement',
        help='Build cursor: needs detected water; the game still checks the spot',
    ),
    Row(
        'experimental_ceiling',
        'bool',
        'experimental ceiling placement',
        help='Build cursor: uses a sampled ceiling point; the game still checks the spot',
    ),
    Row(
        'experimental_fence',
        'bool',
        'experimental fence placement',
        help='Build cursor: uses the edge you face; the game still checks the spot',
    ),
    Row('logging', 'bool', 'logging', help='diagnostic log next to the ts4script'),
    Row(
        'sapi_rate',
        'number',
        'voice rate',
        lo=-10,
        hi=10,
        step=1,
        help='Windows voice only (no screen reader running): -10 to 10',
    ),
    Row(
        'sapi_volume',
        'number',
        'voice volume',
        lo=0,
        hi=100,
        step=5,
        help='Windows voice only: 0 to 100',
    ),
    Row(
        'sapi_voice', 'voice', 'voice name', help='Windows voice only: one of the installed voices'
    ),
    Row('reload', 'action', 'reload settings file', help='re-read SimSight.cfg after a hand edit'),
    Row('close', 'action', 'close menu', help='close this window'),
]
_SETTING_ROWS = [row for row in ROWS if row.kind != 'action']
# Shown when the installed voices cannot be listed.
_FALLBACK_VOICES = ['david', 'zira', 'hazel']


class _State:
    def __init__(self) -> None:
        self.index = 0  # row of the settings window to select
        self.visual_id: Any = None  # settings window dialog id
        self.scan_visual_id: Any = None  # scanner window dialog id
        self.voices: list[str] | None = None  # cached installed-voice list
        self.picker_factory: Any = None
        self.text_picker_factory: Any = None


_state = _State()


def is_open() -> bool:
    return _state.visual_id is not None


# ------------------------------------------------------------------ values


def _keys() -> Any:
    import simsight_keys as keys

    return keys


def _objectnames() -> Any:
    import simsight_objectnames as objectnames

    return objectnames


# Options whose live value is not (only) the setting.
_LIVE_VALUES: dict[str, Callable[[], Any]] = {
    'speech_mode': lambda: speech.speech_mode,
    'braille_mode': lambda: speech.braille_mode,
    'keyboard': lambda: bool(_keys()._enabled),
    'object_names': lambda: bool(_objectnames()._enabled),
    'logging': lambda: bool(simsight_log.enabled),
}


def _value(row: Row) -> Any:
    live = _LIVE_VALUES.get(row.key)
    if live is not None:
        try:
            return live()
        except (ImportError, AttributeError):
            pass  # module not loaded yet: the saved setting stands in
    value = settings.get(row.key)
    if row.kind == 'bool':
        return bool(value)
    if row.kind == 'voice':
        return str(value or 'default')
    return value


def _value_text(row: Row) -> str:
    value = _value(row)
    if row.kind == 'bool':
        return lang.t('on') if value else lang.t('off')
    if row.key == 'verbosity':
        return lang.t('verbosity ' + str(value))
    if row.key == 'tooltip_delay':
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if number <= 0:
            return lang.t('off')
        return '{:g} {}'.format(number, lang.t('seconds'))
    return str(value)


def _label(row: Row) -> str:
    return str(lang.t(row.term))


# ------------------------------------------------------------------ applying


def _apply_speech_mode(value: Any) -> None:
    speech.speech_mode = value
    settings.set('speech_mode', value)
    simsight_log.line(f'speech mode -> {value} (menu)')


def _apply_braille_mode(value: Any) -> None:
    speech.braille_mode = value
    settings.set('braille_mode', value)


def _apply_keyboard(value: Any) -> None:
    keys = _keys()
    keys._enabled = bool(value)
    keys._send('A11yKeyboardOn' if value else 'A11yKeyboardOff')
    settings.set('keyboard', bool(value))
    if value:
        keys.start()


def _apply_captions(value: Any) -> None:
    settings.set('captions', bool(value))
    if not value:
        speech.clear_caption()


def _apply_simplified_menus(value: Any) -> None:
    settings.set('simplified_menus', bool(value))
    import simsight_narrator as narrator

    narrator.push_simplified_menus()


def _apply_travel_list(value: Any) -> None:
    settings.set('travel_list', bool(value))
    try:
        import simsight_travel as travel

        travel.apply_setting()
    except Exception:
        simsight_log.error('menu: travel setting notice')


def _apply_scanner_mode(value: Any) -> None:
    try:
        _keys().set_scanner_mode(bool(value))
    except Exception:
        simsight_log.error('menu: scanner mode')


def _apply_build_controls(value: Any) -> None:
    settings.set('build_controls', bool(value))
    try:
        import simsight_build as build

        build.apply_setting()
    except Exception:
        simsight_log.error('menu: build controls notice')


def _apply_scanner(value: Any) -> None:
    settings.set('scanner', bool(value))
    if value:
        _keys().start_swallow()


def _apply_object_names(value: Any) -> None:
    settings.set('object_names', bool(value))
    _objectnames().enable(bool(value))


def _apply_logging(value: Any) -> None:
    if value:
        simsight_log.enabled = True
        settings.set('logging', True)
        simsight_log.line('logging enabled via the settings menu')
    else:
        simsight_log.line('logging disabled via the settings menu')
        simsight_log.enabled = False
        settings.set('logging', False)


def _apply_sapi_rate(value: Any) -> None:
    settings.set('sapi_rate', int(value))
    speech.restart_sapi()


def _apply_sapi_volume(value: Any) -> None:
    settings.set('sapi_volume', int(value))
    speech.restart_sapi()


def _apply_sapi_voice(value: Any) -> None:
    settings.set('sapi_voice', str(value))
    speech.restart_sapi()


def _apply_tooltip_delay(value: Any) -> None:
    settings.set('tooltip_delay', float(value))


def _apply_scanner_range(value: Any) -> None:
    settings.set('scanner_range', value)
    import simsight_scanner as scanner

    scanner.apply_range_setting()


# Options that do more than save the setting; others are saved as they are
# (bool rows as bool).
_APPLY: dict[str, Callable[[Any], None]] = {
    'speech_mode': _apply_speech_mode,
    'braille_mode': _apply_braille_mode,
    'keyboard': _apply_keyboard,
    'captions': _apply_captions,
    'simplified_menus': _apply_simplified_menus,
    'travel_list': _apply_travel_list,
    'scanner_mode': _apply_scanner_mode,
    'build_controls': _apply_build_controls,
    'scanner': _apply_scanner,
    'object_names': _apply_object_names,
    'logging': _apply_logging,
    'sapi_rate': _apply_sapi_rate,
    'sapi_volume': _apply_sapi_volume,
    'sapi_voice': _apply_sapi_voice,
    'tooltip_delay': _apply_tooltip_delay,
    'scanner_range': _apply_scanner_range,
}


def _apply(row: Row, value: Any) -> None:
    """Set one option live and persist it (the same paths the a11y.*
    commands use). Simulation thread."""
    apply = _APPLY.get(row.key)
    if apply is not None:
        apply(value)
    else:
        settings.set(row.key, bool(value) if row.kind == 'bool' else value)


def _voice_list() -> list[str]:
    if _state.voices is None:
        try:
            voices = list(speech.list_voices())
        except Exception:
            simsight_log.error('menu: list voices')
            voices = []
        _state.voices = voices or list(_FALLBACK_VOICES)
    return _state.voices


def _next_number(row: Row, current: float, direction: int) -> float:
    """One step from current, wrapping at the row's limits."""
    value = current + direction * row.step
    if value > row.hi:
        value = row.lo
    elif value < row.lo:
        value = row.hi
    if row.key == 'tooltip_delay':
        return round(value, 1)
    return int(round(value))


def _change_number(row: Row, direction: int) -> bool:
    try:
        current = float(_value(row))
    except (TypeError, ValueError):
        current = 0.0
    value = _next_number(row, current, direction)
    if value == current:
        return False
    _apply(row, value)
    return True


def _voice_index(names: list[str], current: str) -> int:
    """Position of the voice matching current (partial, either way), or -1."""
    for position, name in enumerate(names):
        if current and (current in name.lower() or name.lower() in current):
            return position
    return -1


def _change(row: Row, direction: int) -> bool:
    """Step the value. Returns True when something changed."""
    if row.kind == 'bool':
        _apply(row, not _value(row))
        return True
    if row.kind == 'choice':
        current = _value(row)
        choices = list(row.choices)
        index = choices.index(current) if current in choices else 0
        _apply(row, choices[(index + direction) % len(choices)])
        return True
    if row.kind == 'number':
        return _change_number(row, direction)
    if row.kind == 'voice':
        names = _voice_list()
        index = _voice_index(names, str(_value(row)).lower())
        _apply(row, names[(index + direction) % len(names)])
        return True
    return False


# ------------------------------------------------------------------ speaking


def _say(text: str) -> None:
    speech.speak(text, explicit=True)


def _open_menu() -> bool:
    """Simulation thread: show only the visual settings window."""
    _state.index = 0
    return _show_settings_window()


def _close() -> None:
    _cancel_dialog(_state.visual_id)
    _state.visual_id = None
    simsight_log.line('menu: closed')


def _toggle() -> None:
    if is_open():
        _close()
    else:
        _open_menu()


def _enter(row: Row) -> bool:
    if row.kind == 'action':
        return _run_action(row)
    return _change(row, 1)


def _reapply(row: Row) -> None:
    """Apply the re-read value of one option; one failure does not stop the rest."""
    try:
        _apply(row, settings.get(row.key))
    except Exception:
        simsight_log.error(f'menu: reload apply {row.key}')


def _reload_settings() -> None:
    settings.reload()
    for setting_row in _SETTING_ROWS:
        _reapply(setting_row)
    try:
        speech.retry_screen_reader()
    except Exception:
        simsight_log.error('menu: reload screen reader retry')
    warnings = list(settings.load_errors)
    text = lang.t('settings reloaded') + '.'
    if warnings:
        text += ' {} {}. {}'.format(len(warnings), 'warnings', ' '.join(warnings))
    simsight_log.line(text)


def _run_action(row: Row) -> bool:
    if row.key == 'reload':
        _reload_settings()
        return True
    if row.key == 'close':
        _close()
    return False


# ------------------------------------------------------------------ dialogs


def _raw(text: object) -> Any:
    from sims4.localization import LocalizationHelperTuning

    return LocalizationHelperTuning.get_raw_text(str(text))


def _ls_hash(key: int) -> Any:
    """A LocalizedString for a string-table key the client will render."""
    try:
        from sims4.localization import _create_localized_string

        return _create_localized_string(int(key))
    except Exception:  # any failure of EA's helper: build the proto by hand
        from protocolbuffers.Localization_pb2 import LocalizedString

        result = LocalizedString()
        result.hash = int(key)
        return result


def _join(*strings: Any) -> Any:
    """Comma-join LocalizedStrings; falls back to the first one."""
    present = [s for s in strings if s is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    try:
        from sims4.localization import LocalizationHelperTuning

        return LocalizationHelperTuning.get_comma_separated_list(*present)
    except Exception:
        simsight_log.error('menu: comma list')
        return present[0]


def _cancel_dialog(dialog_id: Any) -> None:
    if not dialog_id:
        return
    try:
        import services

        services.ui_dialog_service().dialog_cancel(dialog_id)
    except Exception:
        simsight_log.error('menu: cancel dialog')


def _picker(title: str, text: str, text_only: bool = False, localized_title: Any = None) -> Any:
    """localized_title: a game string used as the title as it is (a phone
    category, for example); title is then only a fallback."""
    import services
    from ui.ui_dialog_picker import UiItemPicker, UiObjectPicker

    owner = services.get_active_sim()
    if owner is None:
        return None
    if text_only:
        # Item pickers carry BasePickerRow data. An ObjectPickerRow with no
        # definition still asks the client for a thumbnail of definition 0.
        if _state.text_picker_factory is None:
            _state.text_picker_factory = UiItemPicker.TunableFactory()
        return _state.text_picker_factory.default(
            owner,
            title=lambda *args, **kwargs: _raw(title),
            text=lambda *args, **kwargs: _raw(text),
            min_selectable=1,
            max_selectable=1,
        )
    if _state.picker_factory is None:
        _state.picker_factory = UiObjectPicker.TunableFactory()
    return _state.picker_factory.default(
        owner,
        title=lambda *args, **kwargs: (
            localized_title if localized_title is not None else _raw(title)
        ),
        text=lambda *args, **kwargs: _raw(text),
        picker_type=UiObjectPicker.UiObjectPickerObjectPickerType.OBJECT,
        min_selectable=1,
        max_selectable=1,
    )


def _settings_row_name(row: Row) -> str:
    if row.kind == 'action':
        return _label(row)
    return f'{_label(row)}: {_value_text(row)}'


def _show_settings_window() -> bool:
    """Simulation thread. Replaces any earlier settings window."""
    _cancel_dialog(_state.visual_id)
    _state.visual_id = None
    try:
        from ui.ui_dialog_picker import BasePickerRow

        dialog = _picker(
            lang.t('settings menu'),
            'Pick a row to change it. Numbers cycle back to their minimum after the maximum. '
            'Open this window with simsight.menu window.',
            text_only=True,
        )
        if dialog is None:
            simsight_log.line('menu: no active Sim, settings window skipped')
            return False
        for position, row in enumerate(ROWS):
            dialog.add_row(
                BasePickerRow(
                    option_id=position,
                    name=_raw(_settings_row_name(row)),
                    row_description=_raw(row.help),
                    row_tooltip=None,
                    icon=None,
                    tag=position,
                    is_selected=(position == _state.index % len(ROWS)),
                )
            )
        dialog.add_listener(_on_settings_pick)
        _keys().fence_opening_input()
        dialog.show_dialog()
        _state.visual_id = dialog.dialog_id
        return True
    except Exception:
        simsight_log.error('menu: settings window')
        return False


def _picked_tag(dialog: Any, where: str) -> Any:
    """The first result tag of an accepted picker reply, or None."""
    if not dialog.accepted:
        return None
    try:
        tags = list(dialog.get_result_tags())
    except Exception:
        simsight_log.error(where)
        return None
    return tags[0] if tags else None


def _on_settings_pick(dialog: Any) -> None:
    """Picker reply (simulation thread)."""
    try:
        if dialog.dialog_id != _state.visual_id:
            return  # a window we already replaced
        _state.visual_id = None
        tag = _picked_tag(dialog, 'menu: pick result')
        if tag is None:
            return
        position = int(tag) % len(ROWS)
        row = ROWS[position]
        _state.index = position
        if row.key == 'close':
            _close()
            return
        _enter(row)
        _show_settings_window()
    except Exception:
        simsight_log.error('menu: settings pick')


def _add_scanner_rows(dialog: Any, groups: list[Any], current: tuple[Any, Any]) -> int:
    """One row per group and per entry; returns the number of rows."""
    import services
    import simsight_scanner as scanner
    from ui.ui_dialog_picker import ObjectPickerRow

    current_group, current_entry = current
    manager = services.object_manager()
    option = 0
    for group_key, entries in groups:
        subs = scanner.subs_in(entries)
        label = scanner.GROUP_TERMS.get(group_key)
        dialog.add_row(
            ObjectPickerRow(
                option_id=option,
                name=_raw(lang.t(label)) if label else _ls_hash(scanner.label_hash(group_key)),
                row_description=_raw(
                    '{} items, {} subcategories{}'.format(
                        len(entries),
                        len(subs),
                        '  (current)' if group_key == current_group else '',
                    )
                ),
                row_tooltip=None,
                icon=None,
                tag=('group', group_key),
            )
        )
        option += 1
        for position, entry in enumerate(entries):
            name, icon_info = _entry_name(entry, manager)
            where = f'{scanner.place_text(entry)}. {position + 1} of {len(entries)}'
            if entry is current_entry:
                where = '>> ' + where
            if len(subs) > 1:
                description = _join(_ls_hash(scanner.label_hash(entry.category)), _raw(where))
            else:
                description = _raw(where)
            dialog.add_row(
                ObjectPickerRow(
                    option_id=option,
                    name=name,
                    row_description=description,
                    row_tooltip=None,
                    icon=None,
                    icon_info=icon_info,
                    tag=('item', group_key, entry.obj_id),
                    is_selected=(entry is current_entry),
                )
            )
            option += 1
    return option


def _show_scanner_window(group: Any = None) -> bool:
    """Simulation thread. What the scanner sees, one row per thing."""
    _cancel_dialog(_state.scan_visual_id)
    _state.scan_visual_id = None
    try:
        import simsight_scanner as scanner

        if not scanner.ensure_scan():
            _say(lang.t('nothing nearby'))
            return False
        groups, current_group, current_entry = scanner.snapshot()
        if group is not None:
            groups = [(key, entries) for key, entries in groups if key == group]
        if not groups:
            _say(lang.t('nothing nearby'))
            return False
        total = sum(len(entries) for _key, entries in groups)
        dialog = _picker(
            'Scanner',
            f'{total} things in {len(groups)} categories around the active Sim. Pick a row to move the scanner there.',
        )
        if dialog is None:
            return False
        option = _add_scanner_rows(dialog, groups, (current_group, current_entry))
        dialog.add_listener(_on_scanner_pick)
        dialog.show_dialog()
        _state.scan_visual_id = dialog.dialog_id
        simsight_log.line(f'menu: scanner window with {option} rows')
        return True
    except Exception:
        simsight_log.error('menu: scanner window')
        return False


def _fallback_name(entry: Any, icon_info: Any) -> tuple[Any, Any]:
    if entry.name:
        return _raw(entry.name), icon_info
    if entry.name_hash:
        return _ls_hash(entry.name_hash), icon_info
    return _raw('?'), icon_info


def _object_name(entry: Any, obj: Any) -> tuple[Any, Any]:
    """Name and icon of a scanner entry whose object still exists."""
    from sims4.localization import LocalizationHelperTuning

    try:
        from distributor.shared_messages import IconInfoData

        icon_info = IconInfoData(obj_instance=obj)
    except Exception:  # no icon for this object: the row goes without one
        icon_info = None
    if entry.name:
        return _raw(entry.name), icon_info
    try:
        return LocalizationHelperTuning.get_object_name(obj), icon_info
    except Exception:
        simsight_log.error('menu: object name')
    return _fallback_name(entry, icon_info)


def _entry_name(entry: Any, manager: Any) -> tuple[Any, Any]:
    """LocalizedString name (and an icon when the game has one) for a
    scanner entry."""
    if entry.is_sim:
        if entry.name:
            return _raw(entry.name), None
        if entry.name_hash:
            return _ls_hash(entry.name_hash), None
        return _raw('Sim'), None
    try:
        obj = manager.get(entry.obj_id)
    except Exception:  # the object went away while the window was built
        obj = None
    if obj is not None:
        return _object_name(entry, obj)
    return _fallback_name(entry, None)


def _on_scanner_pick(dialog: Any) -> None:
    try:
        if dialog.dialog_id != _state.scan_visual_id:
            return
        _state.scan_visual_id = None
        tag = _picked_tag(dialog, 'menu: scanner pick')
        if tag is None:
            return
        import simsight_scanner as scanner

        if tag[0] == 'group':
            scanner.select_group(tag[1])
        elif tag[0] == 'item':
            scanner.select_item(tag[1], tag[2])
    except Exception:
        simsight_log.error('menu: scanner select')


# First-install popup deferred. Forget dialog ids when changing zones.
def _install_zone_hook() -> None:
    import zone

    original = zone.Zone.on_loading_screen_animation_finished

    def a11y_menu_on_loading_screen_animation_finished(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        _state.visual_id = None
        _state.scan_visual_id = None
        return result

    zone.Zone.on_loading_screen_animation_finished = a11y_menu_on_loading_screen_animation_finished


# ------------------------------------------------------------------ commands

_MENU_ACTIONS: dict[str, Callable[[], Any]] = {
    'open': _open_menu,
    'close': _close,
    'window': _show_settings_window,
    'toggle': _toggle,
}


@live_command('simsight.menu', 'a11y.menu')
def a11y_menu(action: str = 'toggle', _connection: Any = None) -> bool:
    """simsight.menu [open|close|toggle|window] - visual settings."""
    try:
        output = sims4.commands.CheatOutput(_connection)
        run = _MENU_ACTIONS.get(action.lower())
        if run is None:
            output('Usage: simsight.menu [open|close|toggle|window]')
            return False
        run()
        output(
            'simsight: settings window {} (row {} of {}), pump {}'.format(
                'open' if is_open() else 'closed', _state.index + 1, len(ROWS), pump.status()
            )
        )
        return True
    except Exception:
        simsight_log.error('menu: menu command')
        return False


@live_command('simsight.scanview', 'a11y.scanview')
def a11y_scanview(_connection: Any = None) -> bool:
    """Show the scanner window: what the scanner sees, with the cursor."""
    try:
        output = sims4.commands.CheatOutput(_connection)
        shown = _show_scanner_window()
        output('simsight: scanner window {}'.format('shown' if shown else 'not shown (see log)'))
        return True
    except Exception:
        simsight_log.error('menu: scanview command')
        return False


try:
    _install_zone_hook()
except Exception:
    simsight_log.error('menu: zone hook install')

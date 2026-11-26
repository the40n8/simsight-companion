"""Persistent options for the SimSight mod.

Stored as a hand-editable, commented INI file (SimSight.cfg)
next to the ts4script, MCCC-style. Values are friendly words (on/off, key
names) rather than raw numbers. The in-game a11y.* commands write through
immediately; simsight.reload applies a hand-edit while the game runs; deleting
the file resets everything. The file is fully regenerated on every write,
so user-added comments are not preserved.

This module deliberately imports nothing from the rest of the mod but
simsight_log (which imports nothing), so any module can import it without
cycles. Load problems are collected in
`load_errors` for the narrator to surface instead of raising: a broken
settings file must never take down the mod.

Adding an option: default in DEFAULTS, a line + mapping in _TEMPLATE and
_FILE_MAP (and a parser in _PARSERS unless it is on/off or free text), read
it at the apply site (get()), persist from its command (set()), and a line
in simsight.settings' readout.
"""

from __future__ import annotations

import configparser
import os
import sys
from typing import Any, Callable

import simsight_log


DEFAULTS: dict[str, Any] = {
    'sim_actions': True,  # announce what the active Sim starts doing
    'object_names': True,  # name tooltips on gameplay objects
    'numpad_navigation': False,  # optional alternate keyboard layout
    'keyboard': True,  # keyboard focus navigation
    'simplified_menus': True,  # arrows and Tab step through a menu's options as one list
    'scanner': True,  # Ctrl+Alt world scanner (Sims and objects on the lot)
    'captions': False,  # on-screen copy of accepted narration
    'startup': True,  # speech (and keyboard) at the main menu and CAS before a save loads
    'travel_list': True,  # keyboard mode answers travel requests from a world/place list instead of the map
    'build_controls': True,  # object placement from the lot grid in Build mode, read-only grid in Live
    'scanner_mode': False,  # plain arrows browse the scanner; arrows never move the camera
    'scanner_range': '40 meters',  # Ctrl+Alt+comma/period: 6/12/24/40 meters, whole lot, this floor, this room
    # Build cursor (contributed by DaRainbowGirlXX): target-layer focus and
    # experimental placement on bare ground, water, ceilings and fences
    'surface_layer_focus': True,
    'chair_alignment_hints': True,
    'chair_snapping': True,
    'experimental_basement': True,  # Build helper: basements through the game's Basement tool
    'experimental_bare_ground': True,
    'experimental_water': True,
    'experimental_ceiling': True,
    'experimental_fence': True,
    'braille_mode': 'concise',  # concise | full | only | off
    'logging': False,  # mod-debugging log
    'speech_mode': 'hover',  # hover | manual | beeps | off
    'verbosity': 'full',  # full: say what a thing is (Category, Dialog) before its name; brief: names only
    'auto_focus': False,  # focus ring seizes new pie menus/dialogs
    'tooltip_delay': 1.5,  # seconds to hold back a data row's description
    # (needs/skills/mood read name+value at once);
    # 0 = read descriptions immediately
    'sapi_rate': 3,  # SAPI fallback voice only: -10..10
    'sapi_volume': 70,  # SAPI fallback voice only: 0..100
    'sapi_voice': 'zira',  # fallback voice, case-insensitive partial name
}

# macOS port defaults: narration for a shared session where a sighted player
# drives with the mouse. The Windows keyboard hooks do not exist on macOS, so
# keyboard-driven features start off; spoken events stay on. A stored
# SimSight.cfg still wins over these.
if sys.platform == 'darwin':
    DEFAULTS.update(
        {
            'keyboard': False,
            'simplified_menus': False,
            'scanner': False,
            'travel_list': False,
            'build_controls': False,
            'numpad_navigation': False,
            'verbosity': 'brief',
            'sapi_voice': '',
            'logging': True,
        }
    )

# Friendly key names for the cfg file and simsight.keybind (single source; the
# keyboard module imports this).
KEY_NAMES: dict[str, int] = {
    'numpad0': 0x60,
    'numpad1': 0x61,
    'numpad2': 0x62,
    'numpad3': 0x63,
    'numpad4': 0x64,
    'numpad5': 0x65,
    'numpad6': 0x66,
    'numpad7': 0x67,
    'numpad8': 0x68,
    'numpad9': 0x69,
    'numpadplus': 0x6B,
    'numpadminus': 0x6D,
    'numpadstar': 0x6A,
    'numpadslash': 0x6F,
    'numpaddot': 0x6E,
    'left': 0x25,
    'up': 0x26,
    'right': 0x27,
    'down': 0x28,
    'insert': 0x2D,
    'delete': 0x2E,
    'backslash': 0xDC,
    'backtick': 0xC0,
    'semicolon': 0xBA,
    'quote': 0xDE,
    'comma': 0xBC,
    'period': 0xBE,
    'slash': 0xBF,
    'minus': 0xBD,
    'equals': 0xBB,
    'tab': 0x09,
    'enter': 0x0D,
    'escape': 0x1B,
    'space': 0x20,
}
# file (section, option) -> internal key, for plain values
_FILE_MAP: dict[tuple[str, str], str] = {
    ('features', 'sim actions'): 'sim_actions',
    ('features', 'object names'): 'object_names',
    ('features', 'keyboard'): 'keyboard',
    ('features', 'simplified menus'): 'simplified_menus',
    ('features', 'numpad navigation'): 'numpad_navigation',
    ('features', 'scanner'): 'scanner',
    ('features', 'captions'): 'captions',
    ('features', 'startup speech'): 'startup',
    ('features', 'travel list'): 'travel_list',
    ('features', 'build controls'): 'build_controls',
    ('features', 'scanner mode'): 'scanner_mode',
    ('features', 'scanner range'): 'scanner_range',
    ('build cursor', 'layer focus'): 'surface_layer_focus',
    ('build cursor', 'chair alignment hints'): 'chair_alignment_hints',
    ('build cursor', 'chair snapping'): 'chair_snapping',
    ('build cursor', 'basement'): 'experimental_basement',
    ('build cursor', 'bare ground'): 'experimental_bare_ground',
    ('build cursor', 'water'): 'experimental_water',
    ('build cursor', 'ceiling'): 'experimental_ceiling',
    ('build cursor', 'fence'): 'experimental_fence',
    # legacy: the contribution's own section (October 2026), still read
    ('contribution', 'scanner scope'): 'scanner_range',
    ('contribution', 'surface layer focus'): 'surface_layer_focus',
    ('contribution', 'chair alignment hints'): 'chair_alignment_hints',
    ('contribution', 'experimental basement'): 'experimental_basement',
    ('contribution', 'experimental bare ground'): 'experimental_bare_ground',
    ('contribution', 'experimental water'): 'experimental_water',
    ('contribution', 'experimental ceiling'): 'experimental_ceiling',
    ('contribution', 'experimental fence'): 'experimental_fence',
    ('speech', 'mode'): 'speech_mode',
    ('speech', 'braille'): 'braille_mode',
    ('speech', 'verbosity'): 'verbosity',
    ('speech', 'logging'): 'logging',
    ('speech', 'tooltip delay'): 'tooltip_delay',
    ('voice', 'rate'): 'sapi_rate',
    ('voice', 'volume'): 'sapi_volume',
    ('voice', 'name'): 'sapi_voice',
    ('behavior', 'auto focus'): 'auto_focus',  # legacy: still read, no longer written
}
_BOOL_KEYS = (
    'numpad_navigation',
    'sim_actions',
    'object_names',
    'keyboard',
    'simplified_menus',
    'scanner',
    'captions',
    'startup',
    'travel_list',
    'build_controls',
    'scanner_mode',
    'logging',
    'auto_focus',
    'surface_layer_focus',
    'experimental_bare_ground',
    'experimental_water',
    'experimental_ceiling',
    'experimental_fence',
    'chair_alignment_hints',
    'chair_snapping',
    'experimental_basement',
)
SCANNER_RANGES = (
    '6 meters',
    '12 meters',
    '24 meters',
    '40 meters',
    'whole lot',
    'this floor',
    'this room',
)

_TEMPLATE = """# SimSight options. Edit in any text editor, then type simsight.reload in
# the cheat console (or restart the game). Delete this file to reset.

[features]
# Keyboard navigation of menus, dialogs and panels (on/off)
keyboard = {keyboard}
# Simplified menus: arrows and Tab step through every option of a menu, dialog
# or picker as one list; off keeps the full screen layout navigation (on/off)
simplified menus = {simplified_menus}
# Enable the world scanner: Sims and objects on the lot (required for Scanner mode) (on/off)
scanner = {scanner}
# Scanner mode, Live mode only: plain arrows browse the scanner instead of moving
# the camera, and the camera follows the active Sim. Ctrl+Alt+Space switches it (on/off)
scanner mode = {scanner_mode}
# Scanner range, also Ctrl+Alt+comma and Ctrl+Alt+period: 6 meters, 12 meters,
# 24 meters, 40 meters, whole lot, this floor or this room
scanner range = {scanner_range}
# With the keyboard, choose travel destinations from a list, not the map (on/off)
travel list = {travel_list}
# Read the names of objects under the mouse (on/off)
object names = {object_names}
# Say what the active Sim starts doing (on/off)
sim actions = {sim_actions}
# Speech and keyboard at the main menu and in Create a Sim (on/off)
startup speech = {startup}
# Numpad 4 and 6 move, 5 activates, 8 repeats, decimal goes back (on/off)
numpad navigation = {numpad_navigation}
# Place Build catalog objects from the lot grid in Build mode (on/off)
build controls = {build_controls}

[build cursor]
# Ctrl+Alt+U and Ctrl+Alt+D choose a target layer (ground, six wall heights,
# ceiling); number 1 focuses objects on it and number 4 names each nearby
# object's layer (on/off)
layer focus = {surface_layer_focus}
# Optional chair/table and desk orientation announcements (on/off)
chair alignment hints = {chair_alignment_hints}
# Experimental native chair seats: face a nearby table/desk or focus it with 1
chair snapping = {chair_snapping}
# Experimental basements from the Build helper (Alt+B), through the game's Basement tool (on/off)
basement = {experimental_basement}
# Experimental placement; the game's own placement test still decides (on/off)
bare ground = {experimental_bare_ground}
water = {experimental_water}
ceiling = {experimental_ceiling}
fence = {experimental_fence}

[speech]
# hover, manual, beeps or off
mode = {speech_mode}
# concise, full, only or off
braille = {braille_mode}
# full says what a thing is before its name (Category, Subcategory, Dialog,
# Menu open); brief says only the names
verbosity = {verbosity}
# Seconds before a long description is read (0 reads it at once)
tooltip delay = {tooltip_delay}
# Diagnostic log next to the mod (on/off)
logging = {logging}

[voice]
# Fallback voice (Windows SAPI; on macOS the simsight-say companion).
# On macOS, name is part of a voice name, e.g. Thomas or Amelie for French.
# rate -10 to 10
rate = {sapi_rate}
# volume 0 to 100
volume = {sapi_volume}
# david, zira or hazel
name = {sapi_voice}
"""


MODS_FOLDER = simsight_log.MODS_FOLDER
PATH = os.path.join(MODS_FOLDER, 'SimSight.cfg')
# Only auto-create the file when actually running from inside the game's
# ts4script zip; importing this module from plain source (the build script)
# must stay side-effect free.
_IN_GAME = '.ts4script' in os.path.normpath(__file__).lower()

_stored: dict[str, Any] = {}
load_errors: list[str] = []

_TRUE_WORDS = ('on', 'true', 'yes', '1')
_FALSE_WORDS = ('off', 'false', 'no', '0')
# Earlier builds' speech mode names.
_LEGACY_SPEECH_MODES = {'talk': 'hover', 'ondemand': 'manual', 'on_demand': 'manual'}


def _parse_bool(raw: str, option: str) -> bool | None:
    low = raw.strip().lower()
    if low in _TRUE_WORDS:
        return True
    if low in _FALSE_WORDS:
        return False
    load_errors.append(f'"{raw}" is not on/off for {option}; keeping default')
    return None


def _parse_scanner_range(raw: str) -> str | None:
    name = ' '.join(raw.lower().split()).replace('metres', 'meters')
    if name in SCANNER_RANGES:
        return name
    load_errors.append(f'unknown scanner range "{raw}"; keeping default')
    return None


def _parse_speech_mode(raw: str) -> str | None:
    mode = raw.lower().replace('-', '_').replace(' ', '_')
    mode = _LEGACY_SPEECH_MODES.get(mode, mode)
    if mode in ('hover', 'manual', 'beeps', 'off'):
        return mode
    load_errors.append(f'unknown speech mode "{raw}"; keeping default')
    return None


def _parse_braille_mode(raw: str) -> str | None:
    mode = raw.lower()
    if mode in ('concise', 'full', 'only', 'off'):
        return mode
    load_errors.append(f'unknown braille mode "{raw}"; keeping default')
    return None


def _parse_verbosity(raw: str) -> str | None:
    level = raw.lower()
    if level in ('full', 'brief'):
        return level
    load_errors.append(f'unknown verbosity "{raw}"; keeping default')
    return None


def _parse_tooltip_delay(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        load_errors.append(f'tooltip delay must be a number of seconds, not "{raw}"')
        return None
    return max(0.0, min(10.0, value))


def _int_parser(key: str, low: int, high: int) -> Callable[[str], int | None]:
    def parse(raw: str) -> int | None:
        try:
            value = int(raw)
        except ValueError:
            load_errors.append(f'{key} must be a number, not "{raw}"')
            return None
        return max(low, min(high, value))

    return parse


# Key -> parser for values that are neither on/off nor free text (sapi_voice).
# A parser returns None, after noting why in load_errors, to keep the default.
_PARSERS: dict[str, Callable[[str], Any]] = {
    'scanner_range': _parse_scanner_range,
    'speech_mode': _parse_speech_mode,
    'braille_mode': _parse_braille_mode,
    'verbosity': _parse_verbosity,
    'tooltip_delay': _parse_tooltip_delay,
    'sapi_rate': _int_parser('sapi_rate', -10, 10),
    'sapi_volume': _int_parser('sapi_volume', 0, 100),
}


def _apply_value(key: str, raw: str) -> None:
    """Parse one cfg value into _stored; empty means 'leave unset'."""
    raw = raw.strip()
    if '\n' in raw or '\r' in raw:
        # An accidentally indented line below this option was glued onto its
        # value by INI continuation rules; recover the first line and say so.
        first = raw.splitlines()[0].strip()
        load_errors.append(f'{key} had an extra indented line glued to it; using "{first}"')
        raw = first
    if not raw:
        return
    value: Any
    if key in _BOOL_KEYS:
        value = _parse_bool(raw, key)
    else:
        parser = _PARSERS.get(key)
        value = parser(raw) if parser is not None else raw  # sapi_voice: free text
    if value is not None:
        _stored[key] = value


def _read_cfg() -> configparser.ConfigParser | None:
    """Forgiving parse: strict=False tolerates duplicate lines (last wins), a
    ParsingError keeps everything that DID parse and only skips the bad lines
    (reported with line numbers). None when the file cannot be read."""
    parser = configparser.ConfigParser(
        inline_comment_prefixes=(';', '#'),
        interpolation=None,
        strict=False,
        empty_lines_in_values=False,
    )
    try:
        with open(PATH, encoding='utf-8') as handle:
            try:
                parser.read_file(handle)
            except configparser.ParsingError as ex:
                for lineno, line in getattr(ex, 'errors', []):
                    load_errors.append(
                        f'line {lineno} is not "option = value" and was skipped: {line}'
                    )
    except Exception as ex:
        load_errors.append(f'settings file unreadable ({ex!r}); using defaults')
        return None
    return parser


def _load_cfg() -> None:
    """Section/option names are matched after collapsing case and stray spaces."""
    parser = _read_cfg()
    if parser is None:
        return
    normalized = {}
    for section in parser.sections():
        sec_key = ' '.join(section.lower().split())
        for option, value in parser.items(section):
            opt_key = ' '.join(option.lower().split())
            normalized[(sec_key, opt_key)] = value
    for (section, option), key in _FILE_MAP.items():
        if (section, option) in normalized:
            _apply_value(key, normalized[(section, option)])


def _load() -> None:
    if os.path.isfile(PATH):
        _load_cfg()
        return
    # A default copy ships in the release zip; if the user deleted it (or a
    # dev build never had one), materialize it on launch so there is always
    # a file to edit in the mod folder.
    if _IN_GAME:
        _save()


def _fmt_seconds(value: Any) -> str:
    """Render a seconds value without a trailing .0 (the file is meant to be
    read and hand-edited)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f'{number:g}'


def _render(stored: dict[str, Any]) -> str:
    """The cfg content for these stored values (defaults for the rest)."""

    def sget(name: str) -> Any:
        return stored[name] if name in stored else DEFAULTS.get(name)

    fields: dict[str, Any] = {key: 'on' if sget(key) else 'off' for key in _BOOL_KEYS}
    fields.update(
        {
            'scanner_range': sget('scanner_range'),
            'speech_mode': str(sget('speech_mode')),
            'braille_mode': sget('braille_mode'),
            'verbosity': sget('verbosity'),
            'sapi_rate': sget('sapi_rate'),
            'sapi_volume': sget('sapi_volume'),
            'sapi_voice': sget('sapi_voice') or '',
            'tooltip_delay': _fmt_seconds(sget('tooltip_delay')),
        }
    )
    return _TEMPLATE.format(**fields)


def _save() -> bool:
    try:
        content = _render(_stored)
        # Temp name ends in .cfg (ModGuard whitelist) rather than .tmp, so
        # the atomic write stays legal even on exotic user paths that happen
        # to contain one of ModGuard's prohibited substrings.
        tmp = PATH[: -len('.cfg')] + '.tmp.cfg'
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.replace(tmp, PATH)
        return True
    except Exception as ex:
        # Log only (per user decision, no audible warning). MCCC's most-
        # reported settings failure is this exact class: OneDrive write-
        # permission errors on the Mods folder.
        simsight_log.line(f'settings: save failed: {ex!r}')
        return False


def get(name: str, fallback: Any = None) -> Any:
    """Stored value if present, else the given fallback (lets a build supply
    its own default, e.g. release logging), else the DEFAULTS entry."""
    if name in _stored:
        return _stored[name]
    if fallback is not None:
        return fallback
    return DEFAULTS.get(name)


def brief() -> bool:
    """Verbosity brief: leave out the kind of thing before its name."""
    return get('verbosity') == 'brief'


def set(name: str, value: Any) -> bool:
    """Store a value and rewrite the file; False when the file could not be written."""
    _stored[name] = value
    return _save()


def reload() -> bool:
    """Re-read the cfg (for simsight.reload after a hand-edit)."""
    del load_errors[:]
    _stored.clear()
    _load()
    return True


_load()

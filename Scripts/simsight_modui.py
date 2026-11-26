"""Mod menu census (logging only).

Script mods build their menus from EA's dialog classes (pickers, text
inputs, OK/Cancel dialogs, notifications); none of the mods surveyed on
2026-10-04 ships its own Flash. To learn which of those windows SimSight
reads and which stay silent, every dialog a mod shows is written to
SimSight.log while diagnostic logging is on (Ctrl+Alt+L), with the prefix
"modui:":

    modui: MC Command Center (mc_cmd_center.mc_ui) UiItemPicker
           title "MC Command Center" rows 12: "MC Settings" | "MC Cheats" | ...

The calling mod is the first stack frame outside EA's code
(T:\\InGame\\...) and outside SimSight. Text is the raw text a mod passes
(LocalizationHelperTuning.get_raw_text); string-table text is logged as its
key. The log's existing "UI owner:" and "SPEAK" lines show what the client
opened and what was read, so the two together show which windows need work.
Nothing is spoken and nothing changes the game.
"""

from __future__ import annotations

import contextlib
import sys
from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from types import FrameType

EA_PREFIXES = ('T:\\InGame', 'T:/InGame', '<')
MAX_ROWS = 8


class _State:
    installed = False


_state = _State()


def _log(text: str) -> None:
    simsight_log.line('modui: ' + text)


def _source() -> tuple[str | None, str | None]:
    """(label, module) of the first mod frame that led to this dialog."""
    frame: FrameType | None = sys._getframe(2)
    while frame is not None:
        module = frame.f_globals.get('__name__', '') or ''
        filename = frame.f_code.co_filename or ''
        if (
            not filename.startswith(EA_PREFIXES)
            and not module.startswith('simsight')
            and module not in ('sims4.commands', __name__)
        ):
            return module, filename
        frame = frame.f_back
    return None, None


def ls_text(value: Any, depth: int = 0) -> str:
    """Readable text of a LocalizedString proto: its raw-text tokens, or its key."""
    if value is None or depth > 3:
        return ''
    if isinstance(value, str):
        return value
    parts = []
    for token in getattr(value, 'tokens', ()) or ():
        raw = getattr(token, 'raw_text', '')
        if raw:
            parts.append(str(raw))
        nested = getattr(token, 'text_string', None)
        if nested is not None and getattr(nested, 'hash', None):
            inner = ls_text(nested, depth + 1)
            if inner:
                parts.append(inner)
    if parts:
        return ' '.join(parts)
    key = getattr(value, 'hash', 0)
    return f'key 0x{key:08X}' if key else ''


def _rows(dialog: Any) -> tuple[int, list[str]]:
    rows = list(getattr(dialog, 'picker_rows', None) or ())
    names = [ls_text(getattr(row, 'name', None)) or '?' for row in rows[:MAX_ROWS]]
    return len(rows), names


def _field_text(msg: Any, field: str) -> str:
    try:
        if msg is not None and msg.HasField(field):
            return ls_text(getattr(msg, field, None))
    except (AttributeError, TypeError, ValueError):
        pass  # a message without that field: nothing to show
    return ''


def describe(dialog: Any, msg: Any) -> str:
    parts = [type(dialog).__name__]
    for field in ('title', 'text'):
        text = _field_text(msg, field)
        if text:
            parts.append(f'{field} "{text[:120]}"')
    count, names = _rows(dialog)
    if count:
        parts.append('rows {}: {}'.format(count, ' | '.join(f'"{n[:60]}"' for n in names)))
    inputs = getattr(dialog, 'text_inputs', None)
    if inputs:
        with contextlib.suppress(TypeError):  # names that do not sort: leave them out
            parts.append(f'text inputs {sorted(inputs)}')
    picker_type = getattr(dialog, 'picker_type', None)
    if picker_type is not None:
        parts.append('picker_type {}'.format(getattr(picker_type, 'name', picker_type)))
    return ' '.join(parts)


def install() -> bool:
    if _state.installed:
        return True
    _state.installed = True
    from ui.ui_dialog import UiDialog

    original = UiDialog.build_msg

    def build_msg(self: Any, *args: Any, **kwargs: Any) -> Any:
        msg = original(self, *args, **kwargs)
        if simsight_log.enabled:
            try:
                module, filename = _source()
                if module is not None:
                    _log(f'{module} ({filename}) {describe(self, msg)}')
            except Exception:
                simsight_log.error('modui: census')
        return msg

    build_msg.__name__ = original.__name__
    UiDialog.build_msg = build_msg
    return True


try:
    install()
except Exception:
    simsight_log.error('modui: install')

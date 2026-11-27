"""SimSight: console commands that receive UI text from the patched
Flash layer and speak it through a screen reader / TTS.

The patched TooltipManager (UI override package) posts:
    simsight.speak_enc <percent-encoded-utf8>
where the payload is a \\x01-separated list of "y|x|text" entries, one per
visible TextField in the tooltip. This module sorts them into visual reading
order, glues labels to values, curates known tooltip shapes, and speaks.

    simsight.mark <library-name>     (once; identifies which movie's patched
                                  TooltipManager copy won the app domain)
Manual / scripted narration can use:
    simsight.speak <free text>
Diagnostics:
    simsight.test     speak a test sentence and report the backend
    simsight.status   report backend, DLL presence and log path

Submodules, each depending only on the ones above it: base (command
registration), decode (percent decoding, glyphs), state, fields (tooltip
payloads into reading order), channels (the Flash text channels), mood,
inspection, dialogs, switches (Flash switches and the zone-load push), voice,
console.
"""

from __future__ import annotations

# The modules the narrator used, loaded in the order the single module loaded them.
import sims4.commands  # noqa: F401 - load order

import simsight_lang  # noqa: F401 - load order
import simsight_commands  # noqa: F401 - load order
import simsight_log
import simsight_patch  # noqa: F401 - load order
import simsight_settings  # noqa: F401 - load order
import simsight_speech  # noqa: F401 - load order

from .base import live_command
from .decode import decode_percent
from .fields import FIELD_SEP
from .channels import (
    a11y_clock_enc,
    a11y_context_enc,
    a11y_focus_enc,
    a11y_rawdiag,
    a11y_speak_enc,
    a11y_timewidget_enc,
    a11y_widget_enc,
)
from .mood import a11y_mood_enc
from .inspection import a11y_inspect_enc, a11y_inspect_inventory, a11y_inspect_ui
from .dialogs import (
    a11y_dialog_enc,
    a11y_event_enc,
    a11y_menuopen,
    a11y_scope_enc,
    a11y_simaction_enc,
    a11y_simactions,
    a11y_stop,
    a11y_widgetname_enc,
)
from .switches import (
    _install_zone_hook,
    a11y_autofocus,
    a11y_mark,
    a11y_speechmode,
    a11y_tooltipdelay,
    push_simplified_menus,
)
from .voice import a11y_voice
from .console import (
    a11y_braille,
    a11y_captions,
    a11y_log,
    a11y_reload,
    a11y_settings,
    a11y_speak,
    a11y_status,
    a11y_test,
)

__all__ = [
    'FIELD_SEP',
    'a11y_autofocus',
    'a11y_braille',
    'a11y_captions',
    'a11y_clock_enc',
    'a11y_context_enc',
    'a11y_dialog_enc',
    'a11y_event_enc',
    'a11y_focus_enc',
    'a11y_inspect_enc',
    'a11y_inspect_inventory',
    'a11y_inspect_ui',
    'a11y_log',
    'a11y_mark',
    'a11y_menuopen',
    'a11y_mood_enc',
    'a11y_rawdiag',
    'a11y_reload',
    'a11y_scope_enc',
    'a11y_settings',
    'a11y_simaction_enc',
    'a11y_simactions',
    'a11y_speak',
    'a11y_speak_enc',
    'a11y_speechmode',
    'a11y_status',
    'a11y_stop',
    'a11y_test',
    'a11y_timewidget_enc',
    'a11y_tooltipdelay',
    'a11y_voice',
    'a11y_widget_enc',
    'a11y_widgetname_enc',
    'decode_percent',
    'live_command',
    'push_simplified_menus',
]

try:
    _install_zone_hook()
except Exception:
    simsight_log.error('narrator zone hook install')

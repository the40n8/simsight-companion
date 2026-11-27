"""Companion bridge (macOS and Windows): game <-> simsight-bridge <-> phone.

The bridge is a child process started here, on the game's loading thread.
- Speech: on macOS the speech module writes its usual "I..."/"Q..." lines to
  the bridge, which forwards them to the macOS voice (see SpeechPipe). On
  Windows the screen reader speaks (Prism) and each line is copied to the
  bridge for the phone's history (mirror); the bridge's own announcements
  come back as "say" commands, spoken by the screen reader.
- State: a JSON snapshot of the household (needs, mood, moodlets, actions,
  clock, funds), sent when it changes, at most once per second.
- Commands from the phone arrive on the bridge's stdout and run here.

Everything that touches the game runs from the game's own tick on the
simulation thread. No threads: the Mac game crashes when a background
thread imports a module from a zip while the simulation runs Python.
Message format: companion/proto/simsight/v1/pipe.proto (protobuf JSON).

Parts (each imports only parts listed above it):
  state       what the bridge remembers between ticks, shared helpers
  pipe        the bridge process, its NDJSON pipe, the bridge's own pages
  text        game text, Sims placeholders, object names and classification
  sim_status  needs, mood, moodlets, actions, skills
  sim_life    job or school, goals, relationships
  house       rooms, visitors, bills, camera
  problems    things to deal with on the lot
  snapshot    the state sent to the phone
  inventory   moving items between inventories
  routefail   blocked Sims reported to the phone
  menus       targets and pie menus played from the phone
  commands    the phone's commands and their results
  tick        the game's tick
  startup     hooks and the bridge process, once at load
"""

from __future__ import annotations

from . import pipe
from .inventory import list_inventory, move_item
from .menus import get_interactions, list_targets, run_interaction
from .pipe import (
    BRIDGE,
    CREATE_NO_WINDOW,
    IS_MAC,
    PAIR_URL,
    SETUP_URL,
    SpeechPipe,
    alive,
    mirror,
    open_pairing_page,
    open_setup_page,
    send,
    set_voice,
    start,
    write_line,
)
from .snapshot import build_state
from .startup import install as _install
from .state import _state as _state
from .state import log
from .text import classify, object_name, text_of

__all__ = [
    'BRIDGE',
    'CREATE_NO_WINDOW',
    'IS_MAC',
    'IS_WINDOWS',
    'PAIR_URL',
    'SETUP_URL',
    'SpeechPipe',
    'alive',
    'build_state',
    'classify',
    'get_interactions',
    'list_inventory',
    'list_targets',
    'log',
    'mirror',
    'move_item',
    'object_name',
    'open_pairing_page',
    'open_setup_page',
    'run_interaction',
    'send',
    'set_voice',
    'start',
    'text_of',
    'write_line',
]

# Last, as the old module's own last lines: simsight_dialogs looks the
# package up in sys.modules and finds every name above already there.
_install()
IS_WINDOWS = pipe.IS_WINDOWS  # False when Windows cannot read the pipe: no companion

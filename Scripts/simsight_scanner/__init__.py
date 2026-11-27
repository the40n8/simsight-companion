"""World scanner: keyboard access to the Sims and objects on the lot.

The UI walker can reach anything the client draws, but the world itself is
not a widget. This module lists what is around the active Sim from the
simulation's own data, lets the player step through it by category, and
opens the game's REAL pie menu on the chosen thing so the existing menu
narration and focus walker take over from there. The original scanner design
comes from glaroc's a11yscan (see A11YSCAN-REVIEW.md); the tree navigation
and integration are ours.

How it stays in the game's own words:

- Object names are catalog-name localisation keys (obj.catalog_name). Python
  cannot turn a key into text, so the hash goes to the Flash layer in a
  message name ("A11y|loc|<hash>|<hash>"), the client resolves it in the
  player's language, and the text comes back on simsight.loc_enc.
- Yourself and other Sims are separate groups. Nearby Objects are grouped
  by the object's build/buy tag or function, using the game's own labels.
  The subcategory is the
  object's build/buy tag label (Beds, Sofas, Doors, or a function label for
  spawned and crafted things), from simsight_buycat (generated from the
  tuning dump and the client's KeysBB), resolved the same way. Sims use the
  game's "Sims" string.
- Interactions are never listed here. The client asks for the real pie menu
  itself (see open_pie_menu), and from then on it is an ordinary pie menu.
- Only distance and bearing are words the game does not have; those come
  from simsight_lang like the rest of the mod's vocabulary.

Threading: keys arrive on the poller thread and go through
simsight_pump so every game call below runs on the simulation thread.
The loc replies arrive as Live commands, also on the simulation thread.

Tree keys: Up/Down previous/next sibling, Right enter a container,
Left/Backspace parent, Enter enter a container or open an object's menu.
The keyboard router provides scanner mode and equivalent Ctrl+Alt chords.
Native menus own navigation while open.

Submodules, each depending only on the ones above it: state (entries, the
state object, the loc cache, the cursor), loc (speaking loc keys), categories,
entries (one Sim or object and its words), tree (the tree cursor), ranges,
scan (building the lists), refresh (the refresh alarm), window (the scanner
window's view), intents (the keys), hooks (simsight.scan and the zone reset).
"""

from __future__ import annotations

# The modules the scanner used, loaded in the order the single module loaded them.
import sims4.commands  # noqa: F401 - load order

import simsight_availability  # noqa: F401 - load order
import simsight_buycat  # noqa: F401 - load order
import simsight_lang  # noqa: F401 - load order
import simsight_commands  # noqa: F401 - load order
import simsight_log
import simsight_patch  # noqa: F401 - load order
import simsight_pump  # noqa: F401 - load order
import simsight_settings  # noqa: F401 - load order
import simsight_speech  # noqa: F401 - load order

from .categories import classification, tag_names
from .categories import classification as _classification  # noqa: F401 - simsight_bridge reads it
from .entries import PATIENT_STAGES, bearing_index, entry_for, name_part, place_part
from .hooks import _install_zone_hook, _queue_extensions, a11y_scan
from .intents import (
    a11y_menuclosed,
    a11y_menuempty,
    enter_mode,
    note_menu_open,
    on_key,
    open_pie_menu,
    resume_mode,
    set_camera_live,
    update_camera_follow,
)
from .loc import a11y_loc_enc, send_ui, speak_parts
from .ranges import RANGE_NAMES, RANGES
from .scan import apply_range_setting, cycle_range, rescan
from .state import (
    BUILD,
    COMPASS,
    GROUP_TERMS,
    MENU_KEYS,
    MISC,
    OBJECTS,
    SIMS,
    YOURSELF,
    Entry,
    enabled,
    loc_cache,
    menu_open,
)
from .window import (
    ensure_scan,
    label_hash,
    place_text,
    select_group,
    select_item,
    snapshot,
    subs_in,
)

__all__ = [
    'BUILD',
    'COMPASS',
    'GROUP_TERMS',
    'MENU_KEYS',
    'MISC',
    'OBJECTS',
    'PATIENT_STAGES',
    'RANGES',
    'RANGE_NAMES',
    'SIMS',
    'YOURSELF',
    'Entry',
    'a11y_loc_enc',
    'a11y_menuclosed',
    'a11y_menuempty',
    'a11y_scan',
    'apply_range_setting',
    'bearing_index',
    'classification',
    'cycle_range',
    'enabled',
    'ensure_scan',
    'enter_mode',
    'entry_for',
    'label_hash',
    'loc_cache',
    'menu_open',
    'name_part',
    'note_menu_open',
    'on_key',
    'open_pie_menu',
    'place_part',
    'place_text',
    'rescan',
    'resume_mode',
    'select_group',
    'select_item',
    'send_ui',
    'set_camera_live',
    'snapshot',
    'speak_parts',
    'subs_in',
    'tag_names',
    'update_camera_follow',
]

try:
    _install_zone_hook()
except Exception:
    simsight_log.error('scanner: zone hook install')

_queue_extensions()

"""Keyboard travel list: World, place and unit windows instead of the map.

Python map picker travel requests (the Sim menu, phone and street Travel
chains, Invite To and the other tuned map pickers) end in EA's
UiMapViewPicker dialog. The rows (one per eligible zone) are computed by EA
before the dialog is shown; the native map only answers it with a zone id.
With keyboard navigation on, that request is answered from three native
picker windows instead, and the original dialog is completed through EA's
own dialog service, so companions, eligibility, situation creation, loading
and cancellation stay native. The M key opens the map through the client's
own GoToNeighborhoodTravel path with no Python dialog; it is not handled
here and keeps its native behaviour (simsight.travel offers the tuned Travel
chain for a keyboard entry instead).

Worlds follow the client's own visibility rule: the pack region list in
UiTuning marks each region player facing or not, and the native map draws
only player-facing regions, so the list drops the others (career and hidden
lots that EA's row filter still offers). World names come from the same
list, localized.

Ordinary travel and vacation requests are handled. A vacation adds a Stay
window (1 to 7 days with the cost, as EA's rental panel computes it) and
rents the lot through EA's travel group manager before the dialog is
answered, exactly as the native panel does. Purchase and change-venue
requests, and anything unexpected, fall through to the native map. A row on
the world and place windows opens the native map for the same request, and
lots where the map would ask which small business to visit are handed to
the map as well.

Lifecycle rules: one pending request at a time; every deferred step carries
the request it belongs to and refuses any other; EA's dialog is never left
open without a window (it is cancelled natively instead); picked results are
cleared before any cancel, because EA's picker listener acts on results
without checking the response.

Submodules, each depending only on the ones above it: state (request
records, the state object, shared lookups), prices, places, outcomes,
window, rental, flow (the windows themselves), front (Go alone and EA's
Travel push), entry (the keyboard M lease and requests), hooks.
"""

from __future__ import annotations

import simsight_log

from .entry import (
    a11y_travel,
    a11y_travel_cancel,
    announce_setting,
    apply_setting,
    open_travel,
    start_lease,
    stop_lease,
)
from .flow import PLACE, UNIT, WORLD
from .front import TRAVEL_AFFORDANCE, push_travel, say_refusal, show_front
from .hooks import install
from .places import a11y_travel_probe
from .prices import a11y_lot_value, a11y_lotprice_enc
from .state import TRAVEL, VACATION, enabled, simplified
from .state import _state as _state  # the one state object, as before

__all__ = [
    'PLACE',
    'TRAVEL',
    'TRAVEL_AFFORDANCE',
    'UNIT',
    'VACATION',
    'WORLD',
    'a11y_lot_value',
    'a11y_lotprice_enc',
    'a11y_travel',
    'a11y_travel_cancel',
    'a11y_travel_probe',
    'announce_setting',
    'apply_setting',
    'enabled',
    'install',
    'open_travel',
    'push_travel',
    'say_refusal',
    'show_front',
    'simplified',
    'start_lease',
    'stop_lease',
]

try:
    install()
except Exception:
    simsight_log.error('travel: install failed')

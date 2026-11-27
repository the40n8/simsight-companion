"""Buy and place the Build catalog's selected object at the lot grid cursor.

Ported from glaroc's a11yscan build 3 placement engine (a11yscan_build.py,
2026-09-07), with every EA call checked against the 1.128.90 decompile:

  * the object comes from EA's own catalog: the client reports the product it
    selected (simsight.buildproduct), and only that definition is ever created;
  * placement.find_good_location picks the spot with the object's footprint
    and the build/buy tests, within SEARCH_RADIUS of the cursor; when that
    finds nothing the placement is refused (glaroc's untested retry is gone);
  * nothing is created until the client confirms it put down EA's held
    product (A11y|buildhand|drop|id, answered by simsight.buildhand id|ok);
  * objects that belong on a surface go into a free slot the game validates
    for that object (RuntimeSlot.is_valid_for_placement(obj=...));
  * build_buy.add_object_to_buildbuy_system takes the object id;
  * money moves through household funds with FUNDS_OBJECT_BUY and
    FUNDS_OBJECT_SELL, checked against the balance; a failed payment
    removes the object again, so nothing is ever free;
  * every placement can be undone with a refund, and selling takes two
    presses.

Simulation thread only (the key poller reaches it through the pump).

EA's own operations here (creating, moving, registering and destroying an
object, moving money, finding a location) can fail in many ways; each such
call catches Exception, logs it and refuses or gives the money back, so a
purchase is never left half done.

Submodules, each depending only on the ones above it: state (the state
objects and shared lookups), catalog (the product), money, needs, slots,
spot (EA's placement calls), area (guided placement's areas), announce,
placing, guided, selling, commands (the client's console commands).
"""

from __future__ import annotations

from .state import (
    ACK_SECONDS,
    MAX_HISTORY,
    REPEAT_GUARD_SECONDS,
    SELL_CONFIRM_SECONDS,
    household_id,
    in_build_mode,
    last_placement,
    lot_zone_id,
    say_text,
    tile_key,
)
from .catalog import forget, on_product, product, set_product
from .money import funds, refund, settle_owed
from .needs import (
    FLAG_CEILING,
    FLAG_FENCE,
    FLAG_SLOT,
    FLAG_TERRAIN,
    FLAG_WALL,
    FLAG_WATER,
    NEEDS,
    requirement,
)
from .slots import SLOT_SEARCH, SURFACE_RANK
from .spot import SEARCH_RADIUS, destroy_object, good_location
from .area import LotArea
from .placing import acknowledged, cancel_pending, place
from .guided import guided_placement
from .selling import CANNOT_SELL, last_position, object_value, rotate, sell, sell_refusal, undo
from .commands import buildhand, buildproduct, catalogleft

__all__ = [
    'ACK_SECONDS',
    'CANNOT_SELL',
    'FLAG_CEILING',
    'FLAG_FENCE',
    'FLAG_SLOT',
    'FLAG_TERRAIN',
    'FLAG_WALL',
    'FLAG_WATER',
    'MAX_HISTORY',
    'NEEDS',
    'REPEAT_GUARD_SECONDS',
    'SEARCH_RADIUS',
    'SELL_CONFIRM_SECONDS',
    'SLOT_SEARCH',
    'SURFACE_RANK',
    'LotArea',
    'acknowledged',
    'buildhand',
    'buildproduct',
    'cancel_pending',
    'catalogleft',
    'destroy_object',
    'forget',
    'funds',
    'good_location',
    'guided_placement',
    'household_id',
    'in_build_mode',
    'last_placement',
    'last_position',
    'lot_zone_id',
    'object_value',
    'on_product',
    'place',
    'product',
    'refund',
    'requirement',
    'rotate',
    'say_text',
    'sell',
    'sell_refusal',
    'set_product',
    'settle_owed',
    'tile_key',
    'undo',
]

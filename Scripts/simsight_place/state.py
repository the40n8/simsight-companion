"""The shared state of placing: the product, the purchase history, the
request waiting for the client, and the small game lookups every part uses."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

import simsight_lang as lang
import simsight_speech as speech

if TYPE_CHECKING:
    from sims.household import Household
    from simsight_chair_snap import ChairRequest

    from .area import LotArea

    TileKey = tuple[int, int, int]  # x, z, level


REPEAT_GUARD_SECONDS = 4.0  # a second Enter on the same tile must be meant
SELL_CONFIRM_SECONDS = 6.0
ACK_SECONDS = 1.5  # the client must put down EA's held product first
MAX_HISTORY = 20


class Placement(NamedTuple):
    """One placement request: where, and how to find the exact spot."""

    position: Any
    level: int
    routing_surface: Any
    angle: float
    key: TileKey
    exact: bool
    area: LotArea | None = None


class Purchase(NamedTuple):
    obj_id: int
    price: int
    name: str
    household: Any
    zone: Any


class Request:
    """The placement waiting for the client to put down EA's held product."""

    def __init__(self) -> None:
        self.serial = 0
        self.at = 0.0
        self.placement: Placement | None = None  # None: nothing waiting
        self.zone: Any = None
        self.product: dict[str, Any] | None = None
        self.household: Any = None
        self.surface_id: Any = None  # the focused surface to put it on
        self.chair_request: ChairRequest | None = None  # the chair seat to snap to


class State:
    def __init__(self) -> None:
        self.product: dict[str, Any] | None = None  # {'id', 'name', 'zone'} from the catalog
        self.history: list[Purchase] = []
        self.repeat_keys: tuple[TileKey, ...] = ()  # aimed and landed tiles of the last one
        self.repeat_time = 0.0
        self.sell_id: Any = None  # the object awaiting a second sell press
        self.sell_time = 0.0
        self.owed: dict[Any, int] = {}  # household id -> refunds that failed and are still due
        # Told first of each product the catalog selects (see on_product).
        self.product_listener: Callable[[Any, Any], object] | None = None


state = State()


pending = Request()


# Read and written by simsight_plus_decor and simsight_plus_furnish too.
last_placement: dict[str, Any] = {'obj_id': None, 'position': None, 'household': None, 'zone': None}


def say(parts: list[Any]) -> None:
    import simsight_scanner as scanner

    scanner.speak_parts(parts)


def say_text(en: str, pl: str) -> None:
    speech.speak(lang.text(en, pl), explicit=True)


def lot_zone_id() -> Any:
    try:
        import services

        return services.current_zone_id()
    except AttributeError:  # no zone manager while the game loads
        return None


def in_build_mode() -> bool:
    try:
        import services

        zone = services.current_zone()
        return zone is not None and bool(zone.is_in_build_buy)
    except AttributeError:  # no zone manager while the game loads
        return False


def active_household() -> Household | None:
    try:
        import services

        return services.active_household()
    except AttributeError:  # no client manager while the game loads
        return None


def household_id() -> Any:
    household = active_household()
    return getattr(household, 'id', None) if household is not None else None


def tile_key(position: Any, level: float) -> TileKey:
    return (math.floor(float(position.x)), math.floor(float(position.z)), int(level))


def build_only() -> bool:
    """Changing the lot's objects is a Build mode action, as in the game."""
    if in_build_mode():
        return True
    say_text('Enter Build mode first.', 'Najpierw włącz tryb budowania.')
    return False


def floor_name(level: int) -> str:
    if level == 0:
        return lang.text('Ground floor', 'Parter')
    return lang.text(f'Floor {level}', f'Piętro {level}')

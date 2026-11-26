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
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, TypeVar

import sims4.commands
import simsight_lang as lang
import simsight_log
import simsight_speech as speech

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from simsight_chair_snap import ChairRequest

    _TileKey = tuple[int, int, int]  # x, z, level
    _Area = tuple[Any, 'LotArea', str | None]  # centre, area, room name

SEARCH_RADIUS = 2.5  # metres: "place here" means near here
SLOT_SEARCH = 1.2  # metres around the cursor to look for surfaces
REPEAT_GUARD_SECONDS = 4.0  # a second Enter on the same tile must be meant
SELL_CONFIRM_SECONDS = 6.0
ACK_SECONDS = 1.5  # the client must put down EA's held product first
MAX_HISTORY = 20

# build_buy.PlacementFlags (build_buy.py): what an object needs under it.
FLAG_WALL = 1 | 2  # CENTER_ON_WALL | EDGE_AGAINST_WALL = REQUIRES_WALL
FLAG_CEILING = 8
FLAG_FENCE = 128
FLAG_SLOT = 1024
FLAG_TERRAIN = 65536
FLAG_WATER = 2097152
_REQUIREMENTS = (
    (FLAG_SLOT, 'slot'),
    (FLAG_WALL, 'wall'),
    (FLAG_CEILING, 'ceiling'),
    (FLAG_FENCE, 'fence'),
    (FLAG_WATER, 'water'),
    (FLAG_TERRAIN, 'terrain'),
)

# Surfaces ranked as in glaroc's build: a computer on a desk works, the same
# computer on a decorative island slot has no usable posture.
SURFACE_RANK = (
    (('desk',), 0),
    (('table',), 1),
    (('counter', 'island'), 3),
    (('shelf', 'bookcase', 'dresser', 'endtable', 'nightstand'), 4),
    (('lamp', 'light'), 9),
)
_DEFAULT_RANK = 2


class _Placement(NamedTuple):
    """One placement request: where, and how to find the exact spot."""

    position: Any
    level: int
    routing_surface: Any
    angle: float
    key: _TileKey
    exact: bool
    area: LotArea | None = None


class _Purchase(NamedTuple):
    obj_id: int
    price: int
    name: str
    household: Any
    zone: Any


class _Request:
    """The placement waiting for the client to put down EA's held product."""

    def __init__(self) -> None:
        self.serial = 0
        self.at = 0.0
        self.placement: _Placement | None = None  # None: nothing waiting
        self.zone: Any = None
        self.product: dict[str, Any] | None = None
        self.household: Any = None
        self.surface_id: Any = None  # the focused surface to put it on
        self.chair_request: ChairRequest | None = None  # the chair seat to snap to


class _State:
    def __init__(self) -> None:
        self.product: dict[str, Any] | None = None  # {'id', 'name', 'zone'} from the catalog
        self.history: list[_Purchase] = []
        self.repeat_keys: tuple[_TileKey, ...] = ()  # aimed and landed tiles of the last one
        self.repeat_time = 0.0
        self.sell_id: Any = None  # the object awaiting a second sell press
        self.sell_time = 0.0
        self.owed: dict[Any, int] = {}  # household id -> refunds that failed and are still due


_state = _State()
_pending = _Request()
# Read and written by simsight_plus_decor and simsight_plus_furnish too.
_last: dict[str, Any] = {'obj_id': None, 'position': None, 'household': None, 'zone': None}

_F = TypeVar('_F', bound=Callable[..., Any])
_ARG_TYPES = {'str': str, 'int': int, 'float': float, 'bool': bool}


def _command_arg_types(func: _F) -> _F:
    """The game converts console arguments by their annotations, which the
    __future__ import keeps as strings: give it the real types back."""
    func.__annotations__ = {
        name: _ARG_TYPES[hint]
        for name, hint in func.__annotations__.items()
        if name != 'return' and hint in _ARG_TYPES
    }
    return func


def _text(en: str, pl: str) -> str:
    return pl if lang._detect_lang() == 'pl' else en


def _say(parts: list[Any]) -> None:
    import simsight_scanner as scanner

    scanner._speak_parts(parts)


def _say_text(en: str, pl: str) -> None:
    speech.speak(_text(en, pl), explicit=True)


def _zone_id() -> Any:
    try:
        import services

        return services.current_zone_id()
    except Exception:  # no zone while the game loads
        return None


def in_build_mode() -> bool:
    try:
        import services

        zone = services.current_zone()
        return zone is not None and bool(zone.is_in_build_buy)
    except Exception:  # no zone while the game loads
        return False


# ---------------------------------------------------------------- the product


def set_product(instance_text: Any, name: Any) -> bool:
    """The native catalog selected (or dropped) a product."""
    text = str(instance_text or '').strip()
    if not text.isdigit() or int(text) == 0:
        _state.product = None
        _pending.placement = None
        return True
    def_id = int(text)
    try:
        import services

        definition = services.definition_manager().get(def_id, get_fallback_definition_id=False)
    except Exception as ex:
        simsight_log.line(f'place: definition lookup failed for {def_id}: {ex!r}')
        definition = None
    if definition is None or getattr(definition, 'id', None) != def_id:
        simsight_log.line(f'place: catalog product {def_id} is not an object definition')
        _state.product = None
        return False
    item = {'id': def_id, 'name': str(name or '').strip()[:200], 'zone': _zone_id()}
    _state.product = item
    _pending.placement = None
    simsight_log.line('place: product {} {!r}'.format(def_id, item['name']))
    return True


def product() -> dict[str, Any] | None:
    """The remembered product, if it still belongs to this zone."""
    if _state.product is not None and _state.product['zone'] != _zone_id():
        _state.product = None
    return _state.product


def forget() -> None:
    _state.product = None
    _state.repeat_keys = ()
    _state.sell_id = None
    _pending.placement = None


def _product_name() -> str:
    item = product()
    if item and item['name']:
        return item['name']
    return _text('the object', 'obiekt')


# ------------------------------------------------------------------- the funds


def _household() -> Any:
    try:
        import services

        return services.active_household()
    except Exception:  # no household while the game loads
        return None


def _household_id() -> Any:
    household = _household()
    return getattr(household, 'id', None) if household is not None else None


def funds() -> int | None:
    household = _household()
    try:
        return int(household.funds.money)
    except Exception:  # no household or no funds yet: unknown, never guessed
        return None


def _reason(name: str) -> int | None:
    try:
        from protocolbuffers import Consts_pb2

        value = getattr(Consts_pb2, name, None)
        return None if value is None else int(value)
    except Exception as ex:
        simsight_log.line(f'place: no funds reason {name} ({ex!r})')
        return None


def _charge(amount: Any) -> bool:
    """Take exactly amount. True only when the balance fell by exactly that.
    A partial change (an exception half way) is given back; a change we
    cannot see is treated as not charged only when the balance is unknown
    and the call raised."""
    amount = int(amount or 0)
    if amount <= 0:
        return True
    household = _household()
    reason = _reason('FUNDS_OBJECT_BUY')
    if household is None or reason is None:
        return False
    before = funds()
    error = None
    try:
        taken = bool(household.funds.try_remove(amount, reason, None))
    except Exception as ex:
        taken, error = False, ex
    after = funds()
    if before is not None and after is not None:
        taken_now = before - after
        ok = taken_now == amount
        if not ok and taken_now > 0:
            simsight_log.line(f'place: partial charge {taken_now} of {amount}; giving it back')
            _refund(taken_now)
    else:
        ok = taken and error is None
    simsight_log.line(f'place: charge {amount} -> {ok} ({before} to {after}, error {error!r})')
    return ok


def _refund(amount: Any) -> bool:
    """Give amount back. Whatever could not be paid is kept as a debt of
    this household that its next build action pays first (settle_owed)."""
    amount = int(amount or 0)
    if amount <= 0:
        return True
    household = _household()
    reason = _reason('FUNDS_OBJECT_SELL')
    paid = 0
    error = None
    before = funds()
    if household is not None and reason is not None:
        try:
            household.funds.add(amount, reason, None, count_as_earnings=False)
        except Exception as ex:
            error = ex
        after = funds()
        if before is not None and after is not None:
            paid = max(0, min(amount, after - before))
        elif error is None:
            paid = amount
    else:
        after = before
    owner = _household_id()
    owed = _state.owed
    if paid < amount and owner is not None:
        owed[owner] = owed.get(owner, 0) + amount - paid
    simsight_log.line(
        f'place: refund {amount} paid {paid} ({before} to {after}, error {error!r}, owed {owed.get(owner, 0)})'
    )
    return paid == amount


def settle_owed() -> int:
    """Pay this household any refund that failed earlier. Returns the
    amount paid now. Another household's debt is never touched."""
    owner = _household_id()
    owed = _state.owed.pop(owner, 0) if owner is not None else 0
    if owed <= 0:
        return 0
    if _refund(owed):
        simsight_log.line(f'place: settled {owed} owed to household {owner}')
        return owed
    return 0


# ------------------------------------------------------------ what it needs


def requirement(def_id: Any) -> str | None:
    """'slot', 'wall', 'ceiling', 'fence', 'terrain', 'water', None (the floor
    will do) or 'unknown' when the game would not say."""
    try:
        import build_buy

        flags = int(build_buy.get_object_placement_flags(def_id))
    except Exception as ex:
        simsight_log.line(f'place: no placement flags for {def_id} ({ex!r})')
        return 'unknown'
    for mask, need in _REQUIREMENTS:
        if flags & mask:
            return need
    return None


_NEEDS = {
    'wall': (
        'It hangs on a wall. Use the Build cursor, Control Alt B, to hang it.',
        'To wisi na ścianie. Użyj kursora budowania, Control Alt B, aby to powiesić.',
    ),
    'ceiling': (
        'It hangs from a ceiling. Ceiling placement is not available from the grid yet.',
        'To wisi pod sufitem. Umieszczanie pod sufitem nie jest jeszcze dostępne z siatki.',
    ),
    'fence': (
        'It attaches to a fence. Fence placement is not available from the grid yet.',
        'To mocuje się do płotu. Umieszczanie na płocie nie jest jeszcze dostępne z siatki.',
    ),
    'water': ('It needs a water surface.', 'To wymaga powierzchni wody.'),
    'terrain': ('It needs bare ground.', 'To wymaga gołej ziemi.'),
    'unknown': (
        'The game did not say where this object can go, so nothing was bought.',
        'Gra nie podała, gdzie można postawić ten obiekt, więc nic nie kupiono.',
    ),
}


# ------------------------------------------------------------------- surfaces


def _surface_rank(obj: Any) -> int:
    try:
        name = str(obj.definition.name or '').lower()
    except AttributeError:  # no definition: rank by its class
        name = type(obj).__name__.lower()
    for words, rank in SURFACE_RANK:
        if any(word in name for word in words):
            return rank
    return _DEFAULT_RANK


def _accepts_object(slot: Any, obj: Any) -> bool:
    try:
        return not slot.is_deco_slot() and slot.empty and bool(slot.is_valid_for_placement(obj=obj))
    except Exception:  # EA's native slot check can raise anything: not this slot
        return False


def _free_slot_on(surface: Any, obj: Any) -> Any:
    try:
        slots = list(surface.get_runtime_slots_gen())
    except Exception:  # a surface without slots
        return None
    for slot in slots:
        if _accepts_object(slot, obj):
            return slot
    return None


def _free_slot(
    obj: Any, position: Any, level: int, accepts: Callable[[Any], bool] | None = None
) -> tuple[Any, Any] | None:
    """(surface, slot) near the cursor that the game accepts for this object."""
    import services

    lot = services.active_lot()
    household = _household_id()

    def entry(surface: Any) -> tuple[int, float, Any] | None:
        """(rank, distance squared, surface) when surface may take obj."""
        try:
            if surface is obj or getattr(surface, 'is_sim', False) or surface.is_in_inventory():
                return None
            if surface.is_hidden() or not lot.is_position_on_lot(surface.position):
                return None
            owner = surface.get_household_owner_id()
            if owner and owner != household:
                return None
            if int(surface.level) != int(level):
                return None
            dx = float(surface.position.x) - float(position.x)
            dz = float(surface.position.z) - float(position.z)
            if accepts is None and (abs(dx) > SLOT_SEARCH or abs(dz) > SLOT_SEARCH):
                return None
            if accepts is not None and not accepts(surface.position):
                return None
            return _surface_rank(surface), dx * dx + dz * dz, surface
        except Exception:  # an object in an odd state is simply not a candidate
            return None

    nearby = [row for row in map(entry, list(services.object_manager().values())) if row]
    nearby.sort(key=lambda item: (item[0], item[1]))
    for _rank, _distance, surface in nearby:
        slot = _free_slot_on(surface, obj)
        if slot is not None:
            return surface, slot
    return None


def _put_in_slot(obj: Any, surface: Any, slot: Any) -> bool:
    for label, call in (
        ('add_child', lambda: slot.add_child(obj)),
        ('slot_object', lambda: surface.slot_object(parent_slot=slot, slotting_object=obj)),
    ):
        try:
            call()
        except Exception as ex:
            simsight_log.line(f'place: {label} failed ({ex!r})')
            continue
        if getattr(obj, 'parent', None) is not None:
            return True
    return False


# ------------------------------------------------------------------ the spot


def _good_location(
    obj: Any, position: Any, orientation: Any, routing_surface: Any, ignore: Sequence[Any] = ()
) -> tuple[Any, Any] | None:
    """(translation, orientation) found by the game near position, or None."""
    try:
        import placement

        start = placement.create_starting_location(
            position=position, orientation=orientation, routing_surface=routing_surface
        )
        context = placement.create_fgl_context_for_object(
            start,
            obj,
            test_buildbuy_allowed=True,
            max_distance=SEARCH_RADIUS,
            ignored_object_ids=list(ignore) or None,
        )
        result = placement.find_good_location(context)
    except Exception as ex:
        simsight_log.line(f'place: fgl raised {ex!r}')
        return None
    if result and result[0] is not None:
        simsight_log.line(f'place: fgl chose {result[0]}')
        return result[0], result[1]
    simsight_log.line(
        f'place: fgl found nothing: {result[2] if result and len(result) > 2 else result}'
    )
    return None


def _yaw(angle: float) -> Any:
    import sims4.math

    return sims4.math.angle_to_yaw_quaternion(angle)


def _destroy(obj: Any) -> bool:
    try:
        obj.destroy(source=obj, cause='SimSight placement undo')
        return True
    except Exception as ex:
        simsight_log.line(f'place: destroy failed ({ex!r})')
        return False


def _register(obj: Any) -> bool:
    try:
        import build_buy

        build_buy.add_object_to_buildbuy_system(obj.id)
        return True
    except Exception as ex:
        simsight_log.line(f'place: add_object_to_buildbuy_system failed ({ex!r})')
        return False


def _drop_native_hand(serial: int) -> bool:
    """Ask the client to put down whatever EA's own placement tool holds, so
    the same product is never bought twice (here, and by a later native
    click). The object is created only after the client answers."""
    try:
        import distributor.ops
        import distributor.system

        distributor.system.Distributor.instance().add_op_with_no_owner(
            distributor.ops.SendUIMessage(f'A11y|buildhand|drop|{serial}')
        )
        return True
    except Exception as ex:
        simsight_log.line(f'place: could not ask the client to drop its tool ({ex!r})')
        return False


def _where(obj: Any, cursor_position: Any, level: int) -> str:
    import simsight_scanner as scanner

    try:
        entry = scanner._entry_for(obj, cursor_position, level, False, '', '')
        return scanner._place_part(entry)
    except Exception:  # a detail of the report: never stops it
        return ''


def _name_parts(obj: Any) -> Any:
    import simsight_scanner as scanner

    try:
        entry = scanner._entry_for(obj, obj.position, int(obj.level), False, '', '')
        return scanner._name_part(entry)
    except Exception:  # a detail of the report: never stops it
        return _text('the object', 'obiekt')


def tile_key(position: Any, level: int) -> _TileKey:
    return (int(math.floor(float(position.x))), int(math.floor(float(position.z))), int(level))


class LotArea:
    """A room or a section of the unroomed lot on a particular floor."""

    def __init__(
        self,
        index: int | None,
        centers: Sequence[Any],
        points: list[Any],
        room_at: Callable[[Any], Any],
        room_id: Any,
    ) -> None:
        self.index = index
        self.centers = centers
        self.points = points
        self.room_at = room_at
        self.room_id = room_id

    @staticmethod
    def distance(point: Any, center: Any) -> float:
        return (point.x - center.x) ** 2 + (point.z - center.z) ** 2

    def contains(self, point: Any) -> bool:
        room_id = self.room_at(point)
        if self.room_id or room_id:
            return room_id == self.room_id
        return (
            min(range(len(self.centers)), key=lambda i: self.distance(point, self.centers[i]))
            == self.index
        )


def _floor_point(x: float, z: float, routing_surface: Any) -> Any:
    """The terrain point at (x, z), or None where the height is unknown."""
    import terrain
    from sims4.math import Vector3

    height = terrain.get_terrain_height(x, z, routing_surface=routing_surface)
    if height is None or not math.isfinite(height):
        return None
    return Vector3(x, height, z)


def _sections(
    lot: Any,
    level: int,
    routing_surface: Any,
    centers: Sequence[Any],
    room_at: Callable[[Any], Any],
) -> dict[int, list[Any]]:
    """Points outside the named rooms, grouped by the nearest of the 3x3 centres."""
    import build_buy

    sections: dict[int, list[Any]] = {}
    for sample in lot.get_uniform_sampling_of_points(
        min(33, max(9, math.ceil(lot.size_x / 2))), min(33, max(9, math.ceil(lot.size_z / 2)))
    ):
        if not lot.is_position_on_lot(sample) or build_buy.is_location_pool(sample, level):
            continue
        if level != 0 and not build_buy.has_floor_at_location(sample, level):
            continue
        point = _floor_point(sample.x, sample.z, routing_surface)
        if point is None:
            continue
        if room_at(point) is None:
            index = min(range(len(centers)), key=lambda i: LotArea.distance(point, centers[i]))
            sections.setdefault(index, []).append(point)
    return sections


def _room_points(lot: Any, level: int, routing_surface: Any, room: Any) -> list[Any]:
    import build_buy

    points = []
    for x, z in room.tiles:
        point = _floor_point(x, z, routing_surface)
        if point is None:
            continue
        if lot.is_position_on_lot(point) and not build_buy.is_location_pool(point, level):
            points.append(point)
    return points


def _guided_areas(lot: Any, level: int, routing_surface: Any) -> Iterator[_Area]:
    """The floor's named rooms, then the rest of the lot in up to nine sections."""
    import build_buy
    import simsight_plus_rooms as room_reader
    from sims4.math import Vector3

    centers = lot.get_uniform_sampling_of_points(3, 3)
    zone_id = _zone_id()
    named_rooms = {room.rid: room for room in room_reader.scan_rooms() if room.level == level}

    def room_at(point: Any) -> Any:
        room_id = build_buy.get_room_id(zone_id, point, level)
        return room_id if room_id in named_rooms else None

    sections = _sections(lot, level, routing_surface, centers, room_at)
    for room_id, room in named_rooms.items():
        points = _room_points(lot, level, routing_surface, room)
        if not points:
            continue
        x, z = room.center
        center = Vector3(x, points[0].y, z)
        points.sort(key=lambda point: LotArea.distance(point, center))
        yield center, LotArea(None, centers, points, room_at, room_id), room.name
    for index, points in sorted(sections.items()):
        center = centers[index]
        points.sort(key=lambda point: LotArea.distance(point, center))
        yield center, LotArea(index, centers, points, room_at, None), None


def guided_placement() -> bool:
    """Choose a floor, then choose an area and confirm the purchase."""
    import services
    import simsight_build as build
    import simsight_hub as hub
    import simsight_menu as menu

    if not build.controls_enabled():
        return build.refuse_disabled()
    if not _build_only():
        return False
    item = product()
    if item is None:
        _say_text(
            'Choose an object in the Build catalog first.',
            'Najpierw wybierz obiekt w katalogu trybu budowania.',
        )
        return False
    need = requirement(item['id'])
    if need in _NEEDS:
        _say_text(*_NEEDS[need])
        return False
    lot = services.active_lot()
    if lot is None:
        return False
    owner = _household_id()
    rows = [
        (menu._raw(_floor_name(level)), None, lambda value=level: _choose_area(item, owner, value))
        for level in range(int(lot.min_level), int(lot.max_level) + 1)
    ]
    if not rows:
        _say_text('No lot floors available.', 'Brak dostępnych pięter.')
        return False
    return hub._window('guided placement', 'choose a floor to search', rows)


def _floor_name(level: int) -> str:
    if level == 0:
        return _text('Ground floor', 'Parter')
    return _text(f'Floor {level}', f'Piętro {level}')


def _guided_current(item: dict[str, Any], owner: Any) -> bool:
    import simsight_build as build
    import simsight_keys as keys

    if (
        item is product()
        and owner == _household_id()
        and in_build_mode()
        and build.controls_enabled()
        and not keys._editing_active
    ):
        return True
    _say_text(
        'Placement selection expired. Open guided placement again.',
        'Wybór wygasł. Otwórz ponownie umieszczanie wspomagane.',
    )
    return False


def _area_labels(lot: Any, areas: list[_Area]) -> list[str]:
    """Each area's compass direction from the lot centre, with its room."""
    import simsight_scanner as scanner

    labels = []
    room_number = 0
    for center, area, room_name in areas:
        dx, dz = center.x - lot.center.x, center.z - lot.center.z
        label = (
            _text('Centre of the lot', 'Środek parceli')
            if area.index == 4
            else lang.t(scanner._COMPASS[scanner._bearing(dx, dz)])
        )
        if area.room_id is not None:
            room_number += 1
            label = '{}, {}'.format(
                room_name or _text('Room {}', 'Pokój {}').format(room_number), label
            )
        labels.append(label)
    return labels


def _choose_area(item: dict[str, Any], owner: Any, level: int) -> bool:
    import routing
    import services
    import simsight_hub as hub
    import simsight_menu as menu

    if not _guided_current(item, owner):
        return False
    lot = services.active_lot()
    surface = routing.SurfaceIdentifier(_zone_id(), level, routing.SurfaceType.SURFACETYPE_WORLD)
    try:
        definition = services.definition_manager().get(item['id'], get_fallback_definition_id=False)
        price = int(definition.price)
        areas = list(_guided_areas(lot, level, surface))
    except Exception as ex:
        simsight_log.line(f'place: could not sample the lot ({ex!r})')
        areas = []
    if not areas:
        _say_text('No areas found on that floor.', 'Nie znaleziono obszarów na tym piętrze.')
        return False

    def buy(area: LotArea) -> bool:
        return _guided_current(item, owner) and place(area.points[0], level, surface, area=area)

    labels = _area_labels(lot, areas)
    detail = _text(
        'Buy {} for {} simoleons if it fits.', 'Kup {} za {} simoleonów, jeśli się zmieści.'
    ).format(_product_name(), price)
    rows = []
    for label, (center, area, _) in zip(labels, areas):
        shown = label
        if labels.count(label) > 1:
            shown += _text(', {} metres from centre', ', {} metrów od środka').format(
                round(math.sqrt(LotArea.distance(center, lot.center)), 1)
            )
        rows.append((menu._raw(shown), menu._raw(detail), lambda value=area: buy(value)))
    return hub._window('guided placement', 'choose an area to buy and place', rows)


# -------------------------------------------------------------------- actions


def _repeated(item: dict[str, Any], key: _TileKey, guard: bool) -> bool:
    """A second Enter on the tile just placed: asks for a third (spoken)."""
    if not (
        guard
        and key in _state.repeat_keys
        and time.monotonic() - _state.repeat_time < REPEAT_GUARD_SECONDS
    ):
        return False
    _state.repeat_time = 0.0
    _say(
        [
            _text(
                'Already placed here. Press Enter again to place another ',
                'Już umieszczono tutaj. Naciśnij Enter ponownie, aby umieścić kolejny: ',
            ),
            item['name'] or _product_name(),
            '.',
        ]
    )
    return True


def _catalog_price(item: dict[str, Any]) -> int | None:
    """The product's price, or None (spoken, product forgotten) when it is gone."""
    try:
        import services

        definition = services.definition_manager().get(item['id'], get_fallback_definition_id=False)
        price = int(getattr(definition, 'price', 0) or 0)
    except Exception:  # treated as a product that is gone
        definition, price = None, 0
    if definition is None:
        forget()
        _say_text(
            'That catalog object is no longer available.',
            'Ten obiekt z katalogu nie jest już dostępny.',
        )
        return None
    return price


def _point_refused(position: Any, level: int, water: bool) -> bool:
    """The point is off the lot or in a pool (unless water is wanted); spoken."""
    try:
        import build_buy
        import services

        if not services.active_lot().is_position_on_lot(position):
            _say_text('That point is not on this lot.', 'Ten punkt nie należy do tej parceli.')
            return True
        if build_buy.is_location_pool(position, level) and not water:
            _say_text('That point is in a pool.', 'Ten punkt jest w basenie.')
            return True
    except Exception as ex:
        simsight_log.line(f'place: tile check failed ({ex!r})')
        _say_text(
            'This point could not be checked, so nothing was bought.',
            'Nie udało się sprawdzić tego punktu, więc nic nie kupiono.',
        )
        return True
    return False


def _checks(
    position: Any, level: int, key: _TileKey, guard: bool, exact: bool = False
) -> tuple[dict[str, Any], int, str | None] | None:
    """Everything that can refuse before an object exists. Speaks the refusal;
    returns (item, price, need) or None."""
    item = product()
    if item is None:
        _say_text(
            'Choose an object in the Build catalog first.',
            'Najpierw wybierz obiekt w katalogu trybu budowania.',
        )
        return None
    if not in_build_mode():
        _say_text('Enter Build mode first.', 'Najpierw włącz tryb budowania.')
        return None
    if _repeated(item, key, guard):
        return None
    need = requirement(item['id'])
    # Bare ground, water, ceiling and fence objects go through here only from
    # the Build cursor's experimental placement (simsight_surfaces), with
    # exact=True, so the game's own placement test still decides.
    import simsight_surfaces as surfaces

    experimental = exact and surfaces.handles(need)
    if need in _NEEDS and not experimental:
        _say_text(*_NEEDS[need])
        return None
    price = _catalog_price(item)
    if price is None:
        return None
    money = funds()
    if money is not None and price > money:
        _say_text(
            f'Not enough money. It costs {price} and you have {money}.',
            f'Za mało pieniędzy. Kosztuje {price}, a masz {money}.',
        )
        return None
    if _point_refused(position, level, water=experimental and need == 'water'):
        return None
    return item, price, need


def _start_ack_timer(serial: int) -> None:
    try:
        import threading

        import simsight_pump as pump

        timer = threading.Timer(ACK_SECONDS, lambda: pump.post(_ack_timeout, serial))
        timer.daemon = True
        timer.start()
    except Exception as ex:
        simsight_log.line(f'place: no acknowledgement timer ({ex!r})')


def place(  # noqa: PLR0913, PLR0917 - its callers (grid, surfaces, chairs, furnishing) pass these
    position: Any,
    level: int,
    routing_surface: Any,
    angle: float = 0.0,
    exact: bool = False,
    surface_id: Any = None,
    chair_request: ChairRequest | None = None,
    area: LotArea | None = None,
) -> bool:
    """Ctrl+Alt+Enter on the grid: ask the client to put down EA's held
    product; the object is bought and placed when it answers (simsight.buildhand).
    exact (the Build cursor): put it at position and angle as they are,
    after the game's own placement test, instead of searching nearby.
    True when the request went out."""
    key = tile_key(position, level)
    now = time.monotonic()
    if _pending.placement is not None and now - _pending.at < ACK_SECONDS:
        _say_text('Still placing the last one.', 'Trwa umieszczanie poprzedniego obiektu.')
        return False
    settle_owed()
    if _checks(position, level, key, guard=area is None, exact=exact) is None:
        return False
    _pending.serial += 1
    serial = _pending.serial
    _pending.at = now
    _pending.placement = _Placement(position, level, routing_surface, angle, key, exact, area)
    _pending.zone = _zone_id()
    _pending.product = product()
    _pending.household = _household_id()
    _pending.surface_id = surface_id
    _pending.chair_request = chair_request
    if not _drop_native_hand(serial):
        _pending.placement = None
        _say_text(
            'The Build tool is not answering. Nothing was bought.',
            'Narzędzie budowania nie odpowiada. Nic nie kupiono.',
        )
        return False
    _start_ack_timer(serial)
    return True


def _ack_timeout(serial: int) -> None:
    if serial == _pending.serial and _pending.placement is not None:
        _pending.placement = None
        simsight_log.line(f'place: no buildhand acknowledgement for {serial}')
        _say_text(
            'The Build tool did not answer. Nothing was bought.',
            'Narzędzie budowania nie odpowiedziało. Nic nie kupiono.',
        )


def cancel_pending() -> None:
    """A mode, product or grid change: an unanswered placement is dropped."""
    _pending.placement = None


def acknowledged(serial: int, ok: bool) -> bool:
    """The client answered drop request serial. Place only when it put the
    product down (ok) and nothing changed meanwhile: same zone, still Build
    mode, same product."""
    placement = _pending.placement
    if serial != _pending.serial or placement is None:
        return False
    _pending.placement = None
    if time.monotonic() - _pending.at > ACK_SECONDS:
        # A backlogged pump may run this before the timer's own turn.
        simsight_log.line(f'place: acknowledgement {serial} arrived too late')
        _say_text(
            'The Build tool did not answer. Nothing was bought.',
            'Narzędzie budowania nie odpowiedziało. Nic nie kupiono.',
        )
        return False
    if not ok:
        _say_text(
            'Press Escape to put down the current Build tool, then try again. Nothing was bought.',
            'Naciśnij Escape, aby odłożyć bieżące narzędzie budowania, i spróbuj ponownie. Nic nie kupiono.',
        )
        return False
    item = product()
    if (
        _pending.zone != _zone_id()
        or not in_build_mode()
        or item is not _pending.product
        or _household_id() != _pending.household
    ):
        simsight_log.line(f'place: acknowledgement {serial} arrived after a change; dropped')
        return False
    return _execute(placement)


def _execute(p: _Placement) -> bool:
    checked = _checks(p.position, p.level, p.key, guard=False, exact=p.exact)
    if checked is None:
        return False
    item, price, need = checked
    try:
        from objects.system import create_object

        obj = create_object(item['id'])
    except Exception as ex:
        simsight_log.line(f'place: create_object raised {ex!r}')
        obj = None
    if obj is None:
        _say_text('The game could not create that object.', 'Gra nie mogła utworzyć tego obiektu.')
        return False
    try:
        return _finish(obj, item, price, need, p)
    except Exception as ex:
        simsight_log.error('place: placement')
        simsight_log.line(f'place: placement raised {ex!r}; removing the object')
        _destroy(obj)
        _say_text(
            'The game could not place that object, so nothing was bought.',
            'Gra nie mogła umieścić tego obiektu, więc nic nie kupiono.',
        )
        return False


def _area_location(obj: Any, area: LotArea, level: int, routing_surface: Any) -> Any:
    import build_buy
    import services
    import sims4.math

    lot = services.active_lot()
    # Spread the attempts across the area when the sampling grid found many
    # anchors. Try the most central first and still search near its edges.
    step = max(1, math.ceil(len(area.points) / 9))
    for point in area.points[::step]:
        if not lot.is_position_on_lot(point) or build_buy.is_location_pool(point, level):
            continue
        for turn in range(4):
            spot = _good_location(obj, point, _yaw(turn * sims4.math.PI / 2), routing_surface)
            if (
                spot is not None
                and area.contains(spot[0])
                and lot.is_position_on_lot(spot[0])
                and not build_buy.is_location_pool(spot[0], level)
                and (level == 0 or build_buy.has_floor_at_location(spot[0], level))
            ):
                return spot
    return None


def _refuse(obj: Any, en: str, pl: str) -> None:
    """Remove the unpaid object and say why."""
    _destroy(obj)
    _say_text(en, pl)


def _chosen_slot(obj: Any, item: dict[str, Any], p: _Placement) -> tuple[Any, Any] | None:
    """The chair seat or focused-surface slot the request named, rechecked."""
    chair_request = _pending.chair_request
    if chair_request is not None:
        import simsight_chair_snap as chair_snap

        found = chair_snap.validate(chair_request, obj, item, p.position, p.level)
        if found is None:
            _refuse(
                obj,
                'The chair seat changed or the game refused it. Nothing was bought.',
                'Miejsce na krzesło zmieniło się albo gra je odrzuciła. Nic nie kupiono.',
            )
        return found
    import simsight_layers as layers

    found = layers.selected_surface_slot(_pending.surface_id, obj, p.level)
    if found is None:
        _refuse(
            obj,
            'The focused surface changed or has no compatible free slot. Nothing was bought.',
            'Wybrana powierzchnia zmieniła się albo nie ma zgodnego wolnego miejsca. Nic nie kupiono.',
        )
    return found


def _chair_attached(surface: Any, slot: Any, obj: Any) -> bool:
    """No chair request, or the chair sits in its seat."""
    if _pending.chair_request is None:
        return True
    import simsight_chair_snap as chair_snap

    return chair_snap.attached(surface, slot, obj)


def _into_slot(obj: Any, p: _Placement, found: tuple[Any, Any] | None) -> tuple[Any, Any] | None:
    """Put obj into the named slot, or a free one nearby (in the area)."""
    if found is None:
        found = (
            _free_slot(obj, p.position, p.level)
            if p.area is None
            else _free_slot(obj, p.position, p.level, accepts=p.area.contains)
        )
    if found is None or not _put_in_slot(obj, found[0], found[1]):
        if p.area is None:
            _refuse(
                obj,
                'It needs a free spot on a desk or table. Move the cursor onto one.',
                'To wymaga wolnego miejsca na biurku lub stole. Przesuń kursor na taki mebel.',
            )
        else:
            _refuse(
                obj,
                'No compatible free surface in that area. Nothing was bought.',
                'Brak pasującej wolnej powierzchni w tym obszarze. Nic nie kupiono.',
            )
        return None
    if not _chair_attached(found[0], found[1], obj):
        _refuse(
            obj,
            'The chair did not attach to its seat. Nothing was bought.',
            'Krzesło nie zostało przypięte do miejsca. Nic nie kupiono.',
        )
        return None
    return found


def _move(obj: Any, what: str, move: Callable[[], Any]) -> bool:
    try:
        move()
        return True
    except Exception as ex:
        simsight_log.line(f'place: {what} raised {ex!r}')
        _refuse(
            obj, 'The game could not position that object.', 'Gra nie mogła ustawić tego obiektu.'
        )
        return False


def _exactly(obj: Any, item: dict[str, Any], p: _Placement) -> bool:
    """At the Build cursor's position and angle, if the game's test agrees."""
    import simsight_tiles as tiles

    ok, why = tiles.test_fit(obj, item['id'], p.position, p.angle, p.level)
    if ok is False:
        _refuse(
            obj,
            f'It no longer fits there ({why}), so nothing was bought.',
            f'Już się tam nie mieści ({why}), więc nic nie kupiono.',
        )
        return False
    return _move(
        obj,
        'exact move_to',
        lambda: obj.move_to(
            translation=p.position, orientation=_yaw(p.angle), routing_surface=p.routing_surface
        ),
    )


def _searched(obj: Any, p: _Placement) -> bool:
    """Where the game finds room near the cursor, or anywhere in the area."""
    spot = (
        _good_location(obj, p.position, _yaw(p.angle), p.routing_surface)
        if p.area is None
        else _area_location(obj, p.area, p.level, p.routing_surface)
    )
    if spot is None:
        if p.area is None:
            _refuse(
                obj,
                'No room for it here. Try another point.',
                'Brak tu miejsca. Wybierz inny punkt.',
            )
        else:
            _refuse(
                obj,
                'It could not fit in that area. Nothing was bought.',
                'Obiekt nie mieści się w tym obszarze. Nic nie kupiono.',
            )
        return False
    return _move(
        obj,
        'move_to',
        lambda: obj.move_to(
            translation=spot[0], orientation=spot[1], routing_surface=p.routing_surface
        ),
    )


def _position(
    obj: Any, item: dict[str, Any], need: str | None, p: _Placement
) -> tuple[Any, Any] | None:
    """Put obj in place: (surface, slot) for a slot, (None, None) elsewhere;
    None when refused (obj removed, refusal spoken)."""
    found = None
    if _pending.chair_request is not None or _pending.surface_id is not None:
        found = _chosen_slot(obj, item, p)
        if found is None:
            return None
        need = 'slot'
    if need == 'slot':
        return _into_slot(obj, p, found)
    placed = _exactly(obj, item, p) if p.exact else _searched(obj, p)
    return (None, None) if placed else None


def _finish(obj: Any, item: dict[str, Any], price: int, need: str | None, p: _Placement) -> bool:
    """Put a freshly created obj in place, then own, register and pay for
    it. Any refusal removes it; an exception is handled by the caller."""
    placed = _position(obj, item, need, p)
    if placed is None:
        return False
    surface, slot = placed
    owner = _household_id()
    if owner is None:
        _refuse(
            obj,
            'No active household to own it, so nothing was bought.',
            'Brak aktywnej rodziny, która mogłaby go posiadać, więc nic nie kupiono.',
        )
        return False
    obj.set_household_owner_id(owner)
    if not _register(obj):
        _refuse(
            obj,
            'The lot would not accept it, so nothing was bought.',
            'Parcela go nie przyjęła, więc nic nie kupiono.',
        )
        return False
    if not _chair_attached(surface, slot, obj):
        _refuse(
            obj,
            'The game did not retain the chair attachment. Nothing was bought.',
            'Gra nie zachowała przypięcia krzesła. Nic nie kupiono.',
        )
        return False
    if price and not _charge(price):
        _refuse(
            obj,
            'Payment failed, so nothing was bought.',
            'Płatność nie powiodła się, więc nic nie kupiono.',
        )
        return False
    # Paid: from here on the object is the household's. Nothing below may
    # raise to the caller, whose clean-up would remove a paid object.
    _after_payment(obj, item, price, owner, surface, p)
    return True


def _record(obj: Any, item: dict[str, Any], price: int, owner: Any, key: _TileKey) -> None:
    _state.history.append(_Purchase(obj.id, price, item['name'], owner, _zone_id()))
    del _state.history[:-MAX_HISTORY]
    _last.update(obj_id=obj.id, household=owner, zone=_zone_id(), position=None)
    _state.repeat_keys, _state.repeat_time = (key,), time.monotonic()


def _after_payment(
    obj: Any, item: dict[str, Any], price: int, owner: Any, surface: Any, p: _Placement
) -> None:
    try:
        _record(obj, item, price, owner, p.key)
    except Exception as ex:  # paid: nothing may raise past this point
        simsight_log.line(f'place: placed, but recording it failed ({ex!r})')
    simsight_log.line(
        'place: placed {} {!r} for {}'.format(getattr(obj, 'id', '?'), item.get('name'), price)
    )
    try:
        if _pending.chair_request is not None and surface is not None:
            import simsight_chair_snap as chair_snap

            chair_snap.announce(obj, item, price, surface)
        elif p.exact and surface is None:
            _announce_exact(obj, item, price, p.level, p.key)
        else:
            _announce_placed(obj, item, price, surface, p)
    except Exception as ex:  # paid: nothing may raise past this point
        simsight_log.line(f'place: placed, but the announcement failed ({ex!r})')
        try:
            _say_text('Placed.', 'Umieszczono.')
        except Exception:
            simsight_log.error('place: placed announcement')


def _note_landed(obj: Any, level: int, key: _TileKey) -> None:
    """Remember where it landed: the repeat guard covers that tile too."""
    try:
        _last['position'] = obj.position
        _state.repeat_keys = (key, tile_key(obj.position, int(getattr(obj, 'level', level))))
    except Exception as ex:
        simsight_log.line(f'place: landed position unavailable ({ex!r})')


def _placed_where(obj: Any, surface: Any, p: _Placement) -> list[Any]:
    if surface is not None:
        return [_text(' placed on ', ' umieszczono na: '), _name_parts(surface)]
    if p.area is not None:
        return [
            _text(' placed in the chosen area, ', ' umieszczono w wybranym obszarze, '),
            _floor_name(p.level),
        ]
    where = _where(obj, p.position, p.level)
    if not where or where == lang.t('here'):
        return [_text(' placed at the cursor', ' umieszczono przy kursorze')]
    return [_text(' placed', ' umieszczono'), ', ', where, _text(' from the cursor', ' od kursora')]


def _announce_placed(
    obj: Any, item: dict[str, Any], price: int, surface: Any, p: _Placement
) -> None:
    _note_landed(obj, p.level, p.key)
    parts = [item['name'] or _product_name(), *_placed_where(obj, surface, p)]
    parts.append(_text(f', {price} simoleons.', f', {price} simoleonów.'))
    if _follow(obj, p.level):
        parts.append(_text(' The cursor moved to it.', ' Kursor przesunięto do niego.'))
    _say(parts)


def _facing_part(f: Any, obj: Any) -> str | None:
    import simsight_tiles as tiles

    try:
        import sims4.math

        facing = f.facing_of(sims4.math.yaw_quaternion_to_angle(obj.orientation))
        return _text(
            f', facing {tiles.direction_name(facing)}',
            f', skierowany na {tiles.direction_name(facing)}',
        )
    except Exception:  # a detail of the report: never stops it
        return None


def _covered_part(f: Any, obj: Any, level: int) -> str | None:
    import simsight_tiles as tiles

    covered = tiles.covered_tiles(f, obj, level)
    if len(covered) <= 1:
        return None
    base = f.tile_of(obj.position.x, obj.position.z)
    others = [tiles.relative(ci - base[0], cj - base[1]) for ci, cj in covered if (ci, cj) != base][
        :5
    ]
    return _text(
        ', covering {} tiles: this one and {}'.format(len(covered), '; '.join(others)),
        ', zajmuje pól: {}: to i {}'.format(len(covered), '; '.join(others)),
    )


def _announce_exact(obj: Any, item: dict[str, Any], price: int, level: int, key: _TileKey) -> None:
    """The Build cursor placed it exactly: facing and the tiles it covers,
    counted from its own tile; the cursor moves to it."""
    import simsight_tiles as tiles

    _note_landed(obj, level, key)
    parts = [item['name'] or _product_name(), _text(' placed', ' umieszczono')]
    f = tiles.frame()
    if f is not None:
        details = (_facing_part(f, obj), _covered_part(f, obj, level))
        parts.extend(part for part in details if part is not None)
    parts.append(_text(f', {price} simoleons.', f', {price} simoleonów.'))
    if _follow(obj, level):
        parts.append(_text(' The cursor is on it.', ' Kursor jest na nim.'))
    _say(parts)


def _follow(obj: Any, level: int) -> bool:
    """Put the grid cursor on the placed object, on its floor."""
    try:
        import simsight_grid as grid

        return bool(grid.focus_position(obj.position, int(getattr(obj, 'level', level))))
    except Exception as ex:
        simsight_log.line(f'place: cursor did not follow ({ex!r})')
        return False


def last_position() -> Any:
    return _last['position']


def _last_object() -> Any:
    obj_id = _last['obj_id']
    if obj_id is None or _last['household'] != _household_id() or _last['zone'] != _zone_id():
        return None
    try:
        import services

        return services.object_manager().get(obj_id)
    except Exception:  # no object manager while the game loads
        return None


def _build_only() -> bool:
    """Changing the lot's objects is a Build mode action, as in the game."""
    if in_build_mode():
        return True
    _say_text('Enter Build mode first.', 'Najpierw włącz tryb budowania.')
    return False


def rotate(step: int) -> bool:
    """Turn the last placed object a quarter turn (step -1 or 1), where it fits."""
    if not _build_only():
        return False
    obj = _last_object()
    if obj is None:
        _say_text(
            'Place an object first, then rotate it.', 'Najpierw umieść obiekt, a potem go obróć.'
        )
        return False
    if getattr(obj, 'parent', None) is not None:
        _say_text(
            'It sits on a surface and keeps its direction.',
            'Stoi na powierzchni i zachowuje swój kierunek.',
        )
        return False
    import sims4.math

    try:
        angle = sims4.math.yaw_quaternion_to_angle(obj.orientation) + step * sims4.math.PI / 2
        spot = _good_location(obj, obj.position, _yaw(angle), obj.routing_surface, ignore=(obj.id,))
        if spot is None:
            _say_text('No room to turn it here.', 'Brak miejsca, aby go tu obrócić.')
            return False
        obj.move_to(translation=spot[0], orientation=spot[1])
        _last['position'] = obj.position
    except Exception as ex:
        simsight_log.line(f'place: rotate failed ({ex!r})')
        _say_text('Could not rotate it.', 'Nie udało się go obrócić.')
        return False
    _say_text(
        'Rotated a quarter turn {}.'.format('left' if step < 0 else 'right'),
        'Obrócono o ćwierć obrotu w {}.'.format('lewo' if step < 0 else 'prawo'),
    )
    return True


def _say_removed(name: Any, price: int, paid: bool) -> None:
    if price and not paid:
        _say(
            [
                name,
                _text(
                    ' removed. The refund failed and will be retried with your next Build action.',
                    ' usunięto. Zwrot pieniędzy nie powiódł się i zostanie ponowiony przy następnej akcji.',
                ),
            ]
        )
    else:
        _say(
            [
                name,
                _text(
                    f' removed, {price} simoleons refunded.',
                    f' usunięto, zwrócono {price} simoleonów.',
                ),
            ]
        )


def _undo_purchase(index: int, obj: Any) -> bool:
    """Remove the purchase at history index and refund it."""
    purchase = _state.history[index]
    if _occupied(obj):
        _say_text(*_CANNOT_SELL['occupied'])
        return False
    if not _destroy(obj):
        _say_text('Could not remove it.', 'Nie udało się go usunąć.')
        return False
    del _state.history[index]
    paid = _refund(purchase.price)
    if _last['obj_id'] == purchase.obj_id:
        _last['obj_id'] = None
    _state.repeat_keys = ()
    _say_removed(purchase.name, purchase.price, paid)
    return True


def undo() -> bool:
    """Remove the most recent placement and give the money back."""
    if not _build_only():
        return False
    import services

    settle_owed()
    owner, zone = _household_id(), _zone_id()
    history = _state.history
    for index in range(len(history) - 1, -1, -1):
        purchase = history[index]
        if purchase.household != owner or purchase.zone != zone:
            continue  # another family's or another lot's purchase
        obj = services.object_manager().get(purchase.obj_id)
        if obj is None:
            del history[index]  # sold or removed some other way
            continue
        return _undo_purchase(index, obj)
    _say_text('Nothing to undo.', 'Nie ma czego cofnąć.')
    return False


def _value(obj: Any) -> int:
    """What the game says obj is worth now. Its current value when it has
    one, zero included (a worthless object sells for nothing); the catalog
    price only when the object reports no value at all."""
    for getter in (lambda: obj.current_value, lambda: obj.definition.price):
        try:
            value = getter()
        except Exception:  # a value property can fail any way: try the next
            continue
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):  # not a number: try the next
            continue
    return 0


_CANNOT_SELL = {
    'sim': ('Sims cannot be sold.', 'Simów nie można sprzedać.'),
    'offlot': ('That is not on this lot.', 'To nie znajduje się na tej parceli.'),
    'hidden': ('That object cannot be sold from here.', 'Tego obiektu nie można stąd sprzedać.'),
    'protected': (
        'The game does not allow deleting that object.',
        'Gra nie pozwala usunąć tego obiektu.',
    ),
    'foreign': ('That belongs to another household.', 'To należy do innej rodziny.'),
    'occupied': (
        'Something is on it, inside it or using it. Remove that first.',
        'Coś na nim stoi, jest w nim albo ktoś go używa. Najpierw to usuń.',
    ),
}


def _occupied(obj: Any) -> bool:
    """True when removing obj would take something else with it for obj's
    price only (live 2026-09-26: X offered a desk with a computer on it):
    any child, hidden ones included (an object in a slot, a Sim using it),
    or anything in its own inventory, which EA purges with its owner
    (InventoryComponent.on_remove; shared inventories survive)."""
    try:
        if getattr(obj, 'children', None):
            return True
        inventory = getattr(obj, 'inventory_component', None)
        if inventory is not None and not inventory.is_shared_inventory:
            return any(True for _item in inventory)
    except Exception as ex:
        simsight_log.line(f'place: occupancy check failed ({ex!r})')
        return True  # cannot tell: never risk what it holds
    return False


def _refusal(obj: Any) -> str | None:
    if getattr(obj, 'is_sim', False):
        return 'sim'
    if obj.is_in_inventory() or obj.is_hidden():
        return 'hidden'
    if getattr(obj, 'non_deletable', False) or getattr(obj, 'non_deletable_by_user', False):
        return 'protected'
    if _occupied(obj):
        return 'occupied'
    import services

    if not services.active_lot().is_position_on_lot(obj.position):
        return 'offlot'
    owner = obj.get_household_owner_id()
    if owner and owner != _household_id():
        return 'foreign'
    return None


def sell_refusal(obj: Any) -> str | None:
    """Why obj may not be sold here, or None. Uses the game's own flags."""
    try:
        return _refusal(obj)
    except Exception as ex:
        simsight_log.line(f'place: sell check failed ({ex!r})')
        return 'hidden'


def _say_sold(name: Any, value: int, paid: bool) -> None:
    if value and not paid:
        _say(
            [
                name,
                _text(
                    ' removed. The money did not come back yet and will be retried with your next Build action.',
                    ' usunięto. Pieniądze jeszcze nie wróciły; zwrot zostanie ponowiony przy następnej akcji.',
                ),
            ]
        )
    else:
        _say(
            [
                name,
                _text(
                    f' sold for {value} simoleons.',
                    f' sprzedano za {value} simoleonów.',
                ),
            ]
        )


def sell(obj: Any) -> bool:
    """Sell obj; asks for a second press within SELL_CONFIRM_SECONDS."""
    if not _build_only():
        _state.sell_id = None
        return False
    settle_owed()
    if obj is None:
        _say_text('Nothing here to sell.', 'Nie ma tu nic do sprzedania.')
        return False
    refusal = sell_refusal(obj)
    if refusal is not None:
        _state.sell_id = None
        _say_text(*_CANNOT_SELL[refusal])
        return False
    now = time.monotonic()
    value = _value(obj)
    name = _name_parts(obj)
    if _state.sell_id != obj.id or now - _state.sell_time > SELL_CONFIRM_SECONDS:
        _state.sell_id, _state.sell_time = obj.id, now
        _say(
            [
                _text('Sell ', 'Sprzedać: '),
                name,
                _text(
                    f' for {value} simoleons? Press again to confirm.',
                    f' za {value} simoleonów? Naciśnij ponownie, aby potwierdzić.',
                ),
            ]
        )
        return False
    _state.sell_id = None
    if not _destroy(obj):
        _say_text('Could not sell it.', 'Nie udało się tego sprzedać.')
        return False
    paid = _refund(value)
    if _last['obj_id'] == obj.id:
        _last['obj_id'] = None
    _say_sold(name, value, paid)
    return True


@sims4.commands.Command(
    'simsight.buildproduct', 'a11y.buildproduct', command_type=sims4.commands.CommandType.Live
)
@_command_arg_types
def buildproduct(encoded: str = '', _connection: Any = None) -> bool:
    """Client: the Build catalog selected a product ('<instance>|<name>') or
    dropped it ('0|'). Names arrive percent-encoded."""
    from urllib.parse import unquote

    try:
        text = str(encoded)
        if '|' not in text or len(text) > 800:
            return False
        instance, name = text.split('|', 1)
        # By name: simsight_plus_build wraps set_product to learn the product.
        return set_product(instance, unquote(name))
    except Exception:
        simsight_log.error('place: buildproduct')
        return False


def _catalog_left() -> bool:
    item = product()
    try:
        import simsight_grid as grid

        placing = grid.build_cursor()
    except Exception:  # treated as not placing
        placing = False
    if placing:
        name = item['name'] if item is not None and item['name'] else ''
        _say_text(
            'Back to Build placement. {}'.format(
                name + ' chosen.' if name else 'No object chosen.'
            ),
            'Powrót do umieszczania. {}'.format(
                'Wybrano: ' + name + '.' if name else 'Nie wybrano obiektu.'
            ),
        )
        return True
    if item is not None and item['name']:
        _say_text(
            'Left the Build catalog. {} chosen. Control Alt B places it. Tab returns to the catalog.'.format(
                item['name']
            ),
            'Opuszczono katalog budowania. Wybrano: {}. Control Alt B umieszcza. Tab wraca do katalogu.'.format(
                item['name']
            ),
        )
    else:
        _say_text(
            'Left the Build catalog. Tab returns to the catalog.',
            'Opuszczono katalog budowania. Tab wraca do katalogu.',
        )
    return True


@sims4.commands.Command(
    'simsight.catalogleft', 'a11y.catalogleft', command_type=sims4.commands.CommandType.Live
)
@_command_arg_types
def catalogleft(_state: str = '', _connection: Any = None) -> bool:
    """Client: Escape released the Build catalog's keys (TooltipManager
    A11yReleaseBuildCatalog). The chosen product is unchanged."""
    try:
        return _catalog_left()
    except Exception:
        simsight_log.error('place: catalogleft')
        return False


@sims4.commands.Command(
    'simsight.buildhand', 'a11y.buildhand', command_type=sims4.commands.CommandType.Live
)
@_command_arg_types
def buildhand(encoded: str = '', _connection: Any = None) -> bool:
    """Client: '<serial>|ok' once EA's placement tool holds nothing (it put
    the product down, or held none), '<serial>|denied' when it could not."""
    try:
        text = str(encoded)
        if '|' not in text or len(text) > 40:
            return False
        serial, state = text.split('|', 1)
        if not serial.isdigit() or state not in ('ok', 'denied'):
            return False
        return acknowledged(int(serial), state == 'ok')
    except Exception:
        simsight_log.error('place: buildhand')
        return False

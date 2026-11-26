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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

import sims4.commands

import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_menu as menu
import simsight_pump as pump
import simsight_settings as settings
import simsight_speech as speech

if TYPE_CHECKING:
    from collections.abc import Callable

    # A picker row: (localized name, localized description or None, tag).
    Row = tuple[Any, Any, Any]
    # A world entry: (world key, name, place count, localized label or None).
    World = tuple[int, str, int, Any]
    Step = Callable[..., Any]

_F = TypeVar('_F')

_ALONE, _WITH = 'alone', 'with'

WORLD, PLACE, UNIT = 'world', 'place', 'unit'
_MAP_ROW = 'map'
_BACK_ROW = 'back'
_RESIDENTIAL_BUSINESS_MIN = 1
_VENUE_BUSINESS_MIN = 2
_TRAVEL_AFFORDANCE = 423375  # self_SimPicker_TravelWith (Sim menu Travel)
_STAY_ROW = 'stay'
_MAX_STAY_DAYS = 7  # GERentalPanelTunables.MAX_RENTAL_DAYS
TRAVEL, VACATION = 'travel', 'vacation'
_MAX_DAILY_PRICE = 2**31 - 1  # the client reports an unsigned int; anything larger is not a price

# Readiness lease for the keyboard M entry. Flash routes M to Python only
# while a recent lease says the pump is alive; the heartbeat runs from inside
# the pump drain, so a dead pump stops refreshing by construction. The lease
# value is a session epoch that only ever grows; Flash judges freshness by
# the age of the last heartbeat, not by the value.
_LEASE_INTERVAL = 2.0
_REQUEST_DEADLINE = 3.0
_REQUEST_HISTORY = 64


class _Pending:
    """One travel request from EA, while its windows are open."""

    __slots__ = (
        'dialog',
        'msg_type',
        'dialog_msg',
        'kwargs',
        'places',
        'worlds',
        'fallback_used',
        'mode',
        'party',
        'rented',
        'generation',
        'prices',
    )

    def __init__(
        self,
        dialog: Any,
        msg_type: Any,
        dialog_msg: Any,
        kwargs: dict[str, Any],
        mode: str = TRAVEL,
    ) -> None:
        self.dialog = dialog
        self.msg_type = msg_type
        self.dialog_msg = dialog_msg
        self.kwargs = kwargs
        self.places: list[_Place] = []
        self.worlds: list[World] = []
        self.fallback_used = False
        self.mode = mode
        self.party: list[Any] = []  # sim infos travelling: the actor plus companions
        self.rented = False  # a vacation was created: no native fallback after this
        self.generation = 0  # price bridge token: replies for other requests are ignored
        self.prices: dict[int, int] = {}  # zone id -> per-day price the client reported


class _Place:
    """One destination row (or a building grouping unit rows)."""

    __slots__ = (
        'zone_id',
        'name',
        'world_key',
        'world_name',
        'world_label',
        'venue',
        'household',
        'household_id',
        'master',
        'units',
        'residential',
        'visible',
        'region',
        'residents',
        'rental',
        'price',
    )

    def __init__(self, zone_id: int, name: str) -> None:
        self.zone_id = zone_id
        self.name = name
        self.world_key = 0
        self.world_name = ''
        self.world_label: Any = None
        self.venue: Any = None
        self.household: Any = None
        self.household_id = 0
        self.master = zone_id
        self.units: list[_Place] = []
        self.residential: bool | None = None
        self.visible: bool | None = None
        self.region = ''
        self.residents: tuple[str, ...] = ()
        self.rental: bool | None = None
        self.price: int | None = None

    def copy(self) -> _Place:
        """The same row facts, without units."""
        other = _Place(self.zone_id, self.name)
        for slot in _Place.__slots__:
            if slot not in ('zone_id', 'name', 'units'):
                setattr(other, slot, getattr(self, slot))
        return other

    def label(self) -> str:
        """Occupied homes read family first; venues and empty lots lot first."""
        if self.residential is True and self.household:
            return f'{self.household}, {self.name}'
        return self.name


class _Request:
    """A keyboard entry request from Flash, kept to answer it exactly once."""

    __slots__ = ('key', 'state', 'when')

    def __init__(self, key: tuple[int, int], state: str, when: float) -> None:
        self.key = key  # (epoch, request id)
        self.state = state  # 'pending', 'cancelled' or '<outcome>[:<reason>]'
        self.when = when  # ingress time (time.monotonic)


class _State:
    """Everything this module remembers between calls."""

    def __init__(self) -> None:
        self.original_distribute: Callable[..., Any] | None = None
        self.original_sim_distribute: Callable[..., Any] | None = None
        self.pending: _Pending | None = None
        self.window_id: int | None = None
        self.last_rows: list[_Place] = []
        self.front_id: int | None = None  # the Go alone / Choose who comes window
        # Our pushed Travel interaction for one Go alone; held until EA shows
        # its picker, the interaction ends or a new Travel is pushed (a paused
        # game can hold it for as long as it likes).
        self.alone: Any = None
        self.alone_push: int | None = None  # the Sim id while our own Go alone push runs
        self.alone_claimed = False  # the picker was claimed inside that push
        self.price_generation = 0  # grows per vacation request; a reply must carry it
        self.epoch = 0
        self.lease_sent = 0.0
        self.lease_running = False
        self.lease_generation = 0  # bumped by every start and stop; retires queued heartbeats
        self.requests: list[_Request] = []  # newest last


_state = _State()


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


def enabled() -> bool:
    return bool(settings.get('keyboard', True)) and bool(settings.get('travel_list', True))


def simplified() -> bool:
    return bool(settings.get('simplified_menus', True))


def _request_mode(dialog: Any) -> str | None:
    try:
        modes = type(dialog).MapViewMode
        mode = dialog.map_view_mode.mode
    except AttributeError:
        return None  # not a map view request this list knows
    if mode == modes.TRAVEL:
        return TRAVEL
    if mode == modes.VACATION:
        return VACATION
    return None


def _lot_values(zone_id: int) -> list[tuple[str, Any]]:
    """Candidate furnished lot values for a zone that may not be loaded, as
    (source, value) pairs. EA's own code reads an unloaded lot through the
    zone manager's uninstantiated zone data (the home lot while away); the
    build-buy call is only valid for a loaded lot and returns a sentinel
    otherwise, so it comes second and only counts when positive. A failure
    is recorded as the value: this list is also the lot value diagnostic."""
    found: list[tuple[str, Any]] = []
    try:
        import services

        zone = services.get_zone(zone_id, allow_uninstantiated_zones=True)
        lot = getattr(zone, 'lot', None)
        if lot is not None:
            found.append(('zone', lot.furnished_lot_value))
    except Exception as ex:
        found.append(('zone', f'error {ex!r}'))
    try:
        import build_buy

        found.append(('build_buy', build_buy.get_lot_value(zone_id, True)[0]))
    except Exception as ex:
        found.append(('build_buy', f'error {ex!r}'))
    return found


def _positive(value: Any) -> float | None:
    """A finite positive number, or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # NaN fails every comparison, so it is refused here too.
    if not 0 < number < float('inf'):
        return None
    return number


def _request_client_price(pending: _Pending, zone_id: int) -> bool:
    """Ask the client for the lot's rental price (the map's own numbers:
    GetNeighborhoodLotData and GetLotRentalPriceCalculator). The answer
    arrives on simsight.lotprice_enc with this request's generation and is kept
    on the request only, never across vacations."""
    return _ui(f'A11y|lotprice|{int(zone_id)}|{pending.generation}')


def _daily_price(zone_id: int, pending: _Pending | None = None) -> int | None:
    """EA's rental panel price. The client's answer for this request is used
    when it has arrived; otherwise the furnished lot value times the tuned
    modifiers, truncated. None when no positive value is available, so the
    row stays unpriced and picking it hands the request to the native map."""
    if pending is not None:
        cached = _positive(pending.prices.get(int(zone_id)))
        if cached is not None:
            return int(cached)
    try:
        from world.rentable_lot_tuning import RentableZoneTuning

        modifiers = RentableZoneTuning.PRICE_MODIFIERS
    except Exception:
        simsight_log.error('travel: rental price modifiers')
        return None
    for _source, value in _lot_values(zone_id):
        number = _positive(value)
        if number is None:
            continue
        price = _positive(number * modifiers.multiply + modifiers.add)
        if price is not None:
            return int(price)
    return None


def _party(dialog: Any) -> list[Any]:
    """Sim infos the request travels with: the actor plus companions."""
    sims = [getattr(dialog, 'owner', None), *list(getattr(dialog, 'traveling_sims', None) or ())]
    infos: list[Any] = []
    for sim in sims:
        info = getattr(sim, 'sim_info', None) if sim is not None else None
        if info is None or info in infos:
            continue
        infos.append(info)
    return infos


def _funds() -> int | None:
    import services

    try:
        return int(services.active_household().funds.money)
    except AttributeError:
        return None  # no active household
    except Exception:
        simsight_log.error('travel: household funds')
        return None


def _zone_loaded() -> bool:
    try:
        import services

        return services.current_zone() is not None
    except Exception:
        simsight_log.error('travel: current zone')
        return False


def _service() -> Any:
    import services

    return services.ui_dialog_service()


def _dialog_active(dialog: Any) -> bool:
    """True while EA still holds this exact dialog as active."""
    try:
        service = _service()
        lookup = getattr(service, 'get_dialog', None)
        if lookup is not None:
            return lookup(dialog.dialog_id) is dialog
        return service._active_dialogs.get(dialog.dialog_id) is dialog
    except AttributeError:
        return False  # no dialog service (zone unloading)
    except Exception:
        simsight_log.error('travel: dialog lookup')
        return False


# ------------------------------------------------------------ zone facts


def _venue(zone_id: int) -> Any:
    try:
        import build_buy
        import services
        import sims4.resources

        venue_id = build_buy.get_current_venue(zone_id)
        return services.get_instance_manager(sims4.resources.Types.VENUE).get(venue_id)
    except Exception:
        simsight_log.error('travel: venue of a zone')
        return None


def _venue_flag(zone_id: int, flag: str) -> bool | None:
    """A venue's is_residential or is_rental; None when it cannot be read."""
    venue = _venue(zone_id)
    if venue is None:
        return None
    try:
        return bool(getattr(venue, flag))
    except AttributeError:
        return None


def _residential(zone_id: int) -> bool | None:
    """True, False, or None when the venue cannot be read."""
    return _venue_flag(zone_id, 'is_residential')


def _rental(zone_id: int) -> bool | None:
    """True for a rentable vacation lot, False for another venue, None unknown."""
    return _venue_flag(zone_id, 'is_rental')


def _full_name(info: Any) -> str:
    return ' '.join(
        str(part)
        for part in (getattr(info, 'first_name', ''), getattr(info, 'last_name', ''))
        if part
    )


def _residents(household_id: int) -> tuple[str, ...]:
    """Full names of the Sims living in a household, in household order."""
    try:
        import services

        household = services.household_manager().get(household_id)
        if household is None:
            return ()
        return tuple(name for name in (_full_name(info) for info in household) if name)
    except Exception:
        simsight_log.error('travel: household residents')
        return ()


def _region_table() -> dict[Any, tuple[bool, Any]]:
    """Region tuning instance -> (player facing, localized name), from the
    same pack region list the client's world selector uses."""
    table: dict[Any, tuple[bool, Any]] = {}
    try:
        from ui.ui_tuning import UiTuning
        from world.region import Region

        for pack_data in UiTuning.PACK_SPECIFIC_DATA.values():
            for region_data in getattr(pack_data, 'region_list', ()) or ():
                instance = Region.REGION_DESCRIPTION_TUNING_MAP.get(region_data.region_resource)
                if instance is not None:
                    table[instance] = (
                        bool(getattr(region_data, 'is_player_facing', True)),
                        getattr(region_data, 'region_name', None),
                    )
    except Exception as ex:
        simsight_log.line(f'travel: region table unavailable ({ex!r})')
    return table


def _region_of(zone_id: int) -> Any:
    try:
        from world import region

        return region.get_region_instance_from_zone_id(zone_id)
    except Exception:
        simsight_log.error('travel: region of a zone')
        return None


def _plex_service() -> Any:
    import services

    try:
        return services.get_plex_service()
    except Exception:
        simsight_log.error('travel: plex service')
        return None


def _master_zone(plex: Any, zone_id: int) -> int:
    """The building a unit belongs to (the zone itself for a plain lot)."""
    try:
        return int(plex.get_master_zone_id(zone_id))
    except Exception:
        simsight_log.error('travel: master zone')
        return zone_id


def _world_name(persistence: Any, neighborhood_id: int) -> str:
    label = ''
    try:
        proto = (
            persistence.get_neighborhood_proto_buff(neighborhood_id) if neighborhood_id else None
        )
        label = str(getattr(proto, 'name', '') or '')
    except Exception:
        simsight_log.error('travel: neighborhood name')
        label = ''
    return label or lang.t('other worlds')


def _row_place(row: Any, regions: dict[Any, tuple[bool, Any]]) -> tuple[_Place, int] | None:
    """The place for one of EA's rows and its neighborhood id; None for a row
    without a zone. Raises for a region with no visibility data, so the
    native map decides."""
    zone_id = getattr(row, 'zone_id', None) or getattr(row, 'option_id', None)
    if not zone_id:
        return None
    place = _Place(int(zone_id), str(getattr(row, 'name', '') or ''))
    data = getattr(row, 'tag', None)
    neighborhood_id = getattr(data, 'neighborhood_id', 0) or 0
    world_id = getattr(row, 'world_id', 0) or getattr(data, 'world_id', 0) or 0
    place.world_key = neighborhood_id or world_id
    region = _region_of(place.zone_id)
    place.region = getattr(region, '__name__', '') if region is not None else ''
    info = regions.get(region) if region is not None else None
    if info is None:
        raise ValueError(f'region without list data: {place.region or place.world_key}')
    place.visible = info[0]
    place.world_label = info[1]
    return place, neighborhood_id


def _fill_household(place: _Place, row: Any) -> None:
    place.venue = getattr(row, 'venue_type_name', None)
    place.household = getattr(row, 'household_name', None) or None
    place.household_id = getattr(getattr(row, 'tag', None), 'household_id', 0) or 0
    place.residential = _residential(place.zone_id)
    if place.residential is True and place.household_id:
        place.residents = _residents(place.household_id)


def _fill_rental(place: _Place, pending: _Pending | None) -> bool:
    """Vacation facts; False for a lot that is not a vacation rental."""
    place.rental = _rental(place.zone_id)
    if place.rental is not True:
        # Sims on vacation may only stay on lots designated as vacation
        # rentals (the native map refuses the others).
        return False
    place.price = _daily_price(place.zone_id, pending)
    if place.price is None and pending is not None:
        _request_client_price(pending, place.zone_id)
    return True


def _collect(dialog: Any, vacation: bool = False, pending: _Pending | None = None) -> list[_Place]:
    """One entry per row of EA's dialog, grouped by world; rows in regions the
    client never draws are dropped."""
    import services

    persistence = services.get_persistence_service()
    plex = _plex_service()
    regions = _region_table()
    if not regions:
        raise ValueError('region visibility data unavailable')
    places: list[_Place] = []
    names: dict[int, str] = {}
    hidden = 0
    skipped = 0
    for row in dialog.picker_rows:
        found = _row_place(row, regions)
        if found is None:
            continue
        place, neighborhood_id = found
        if place.visible is False:
            hidden += 1
            continue
        if place.world_key not in names:
            names[place.world_key] = _world_name(persistence, neighborhood_id)
        place.world_name = names[place.world_key]
        _fill_household(place, row)
        if vacation and not _fill_rental(place, pending):
            skipped += 1
            continue
        if plex is not None:
            place.master = _master_zone(plex, place.zone_id)
        places.append(place)
    if skipped:
        simsight_log.line(f'travel: {skipped} non-rental rows left to the native rules')
    if hidden:
        simsight_log.line(f'travel: {hidden} rows in non player-facing regions dropped')
    return places


def _worlds(places: list[_Place]) -> list[World]:
    """One entry per world with its place count, sorted by name."""
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    labels: dict[int, Any] = {}
    for place in places:
        counts[place.world_key] = counts.get(place.world_key, 0) + 1
        names[place.world_key] = place.world_name
        labels.setdefault(place.world_key, place.world_label)
    return sorted(
        ((key, names[key], counts[key], labels[key]) for key in counts),
        key=lambda item: item[1].lower(),
    )


def _buildings(places: list[_Place]) -> list[_Place]:
    """Fresh top-level entries for one world: plain lots, and one entry per
    multi-unit building carrying copies of its unit rows. Never alters the
    collected places, so repeated visits group identically."""
    top: list[_Place] = []
    by_master: dict[int, _Place] = {}
    for place in places:
        if place.master == place.zone_id:
            entry = place.copy()
            by_master[place.zone_id] = entry
            top.append(entry)
    for place in places:
        if place.master == place.zone_id:
            continue
        building = by_master.get(place.master)
        if building is None:
            building = _Place(place.master, _zone_name(place.master) or place.world_name)
            building.world_key = place.world_key
            building.world_name = place.world_name
            by_master[place.master] = building
            top.append(building)
        if not building.units and any(p.zone_id == building.zone_id for p in places):
            building.units.append(building.copy())  # a plain row, not the building entry itself
        building.units.append(place)
    return top


def _zone_name(zone_id: int) -> str:
    try:
        import services

        proto = services.get_persistence_service().get_zone_proto_buff(zone_id)
        return str(getattr(proto, 'name', '') or '')
    except Exception:
        simsight_log.error('travel: zone name')
        return ''


def _units_of(pending: _Pending, master: int) -> list[_Place]:
    """Unit rows to offer for a building: every offered zone of it, the
    master included when EA offered it (a master can be a real unit, not
    only a shell); an offered master alone is a plain lot (empty list)."""
    group = [p for p in pending.places if p.master == master]
    if len(group) < 2:
        return []
    return group


def _find(pending: _Pending, zone_id: int) -> _Place | None:
    for place in pending.places:
        if place.zone_id == zone_id:
            return place
    return None


# --------------------------------------------------- small business gate


def _allowed_zones(manager: Any) -> tuple[Any, ...]:
    zones = getattr(manager, 'allowed_zone_ids', None)
    if zones is None:
        getter = getattr(manager, 'get_allowed_zone_ids', None)
        zones = getter() if getter is not None else ()
    elif callable(zones):
        zones = zones()
    return tuple(zones or ())


def _household_trackers(business: Any, owner: int) -> list[Any]:
    lookup = getattr(business, 'get_business_trackers_for_household', None)
    found = lookup(owner) if lookup is not None else None
    if found is None:
        found = [
            t
            for t in business.business_trackers_gen()
            if getattr(t, 'owner_household_id', None) == owner
        ]
    elif isinstance(found, dict):
        found = list(found.values())
    return list(found)


def _business_count(place: _Place, owner: int) -> int:
    """How many of the owner's small businesses are assigned to this lot."""
    import services

    count = 0
    for tracker in _household_trackers(services.business_service(), owner):
        managers = list(getattr(tracker, 'business_managers', {}).values())
        managers += list(getattr(tracker, 'zoneless_business_managers', {}).values())
        count += sum(1 for manager in managers if place.zone_id in _allowed_zones(manager))
    return count


def _needs_native_map(place: _Place) -> bool:
    """The native map asks which of another household's small businesses to
    visit when enough of them are assigned to the lot (one on a residential
    lot, two on a venue); the list cannot ask, so such lots are handed to the
    map. When the check itself fails for a lot another household owns, the
    native route is kept rather than skipping the business choice."""
    import services

    owner = int(place.household_id or 0)
    if not owner:
        return False
    try:
        if owner == services.active_household_id():
            return False
        count = _business_count(place, owner)
        if count == 0:
            return False
        residential = place.residential
        if residential is None:
            residential = _residential(place.zone_id)
        minimum = _VENUE_BUSINESS_MIN if residential is False else _RESIDENTIAL_BUSINESS_MIN
        return count >= minimum
    except Exception as ex:
        simsight_log.line(
            f'travel: small business check failed for zone {place.zone_id} ({ex!r}); native map kept'
        )
        return True


# ---------------------------------------------------------------- windows


def _icon() -> Any:
    try:
        import services
        from distributor.shared_messages import IconInfoData

        sim = services.get_active_sim()
        if sim is not None:
            return IconInfoData(obj_instance=sim)
    except Exception:
        simsight_log.error('travel: window icon')
    return None


def _detail(place: _Place) -> Any:
    parts = []
    if place.units:
        parts.append(menu._raw(lang.t('units count').format(len(place.units))))
    if place.venue is not None:
        parts.append(place.venue)
    if place.household and place.label() == place.name:
        parts.append(menu._raw(str(place.household)))
    if place.residents:
        parts.append(menu._raw(lang.t('residents').format(', '.join(place.residents))))
    if place.price is not None:
        parts.append(menu._raw(lang.t('price per day').format(place.price)))
    return menu._join(*parts) if parts else None


def _fence_input() -> None:
    """Keys still down from opening a window must not answer it."""
    try:
        import simsight_keys as keys

        keys.fence_opening_input()
    except Exception:
        simsight_log.error('travel: input fence')


def _show_picker(dialog: Any, rows: list[Row] | tuple[Row, ...]) -> None:
    """Fill a native picker with rows and show it."""
    from ui.ui_dialog_picker import ObjectPickerRow

    icon_info = _icon()
    for index, (name, detail, tag) in enumerate(rows):
        dialog.add_row(
            ObjectPickerRow(
                option_id=index,
                name=name,
                row_description=detail,
                row_tooltip=None,
                icon=None,
                icon_info=icon_info,
                tag=tag,
                is_selected=False,
            )
        )


def _listener(on_pick: Step, pending: _Pending) -> Callable[[Any], None]:
    """EA calls this with the reply; errors stay here."""

    def heard(reply: Any) -> None:
        try:
            on_pick(pending, reply)
        except Exception:
            simsight_log.error('travel: window reply')

    return heard


def _open_window(pending: _Pending, title: str, text: str, rows: list[Row], on_pick: Step) -> bool:
    """rows: (localized name, localized description or None, tag). A window
    that cannot open cancels the request rather than leaving EA's dialog
    open with nothing on screen."""
    if _state.pending is not pending:
        return False
    try:
        menu._cancel_dialog(_state.window_id)
        _state.window_id = None
        dialog = menu._picker(lang.t(title), lang.t(text))
        if dialog is None:
            raise ValueError('no active Sim')
        _show_picker(dialog, rows)
        dialog.add_listener(_listener(on_pick, pending))
        _fence_input()
        dialog.show_dialog()
        _state.window_id = dialog.dialog_id
        return True
    except Exception as ex:
        simsight_log.line(f'travel: window failed ({ex!r}); request cancelled')
        _cancel(pending)
        return False


def _reply(pending: _Pending, dialog: Any) -> Any:
    """Common picker-reply gate: this request, our current window, one
    answer only, settings still on."""
    if _state.pending is not pending or dialog.dialog_id != _state.window_id:
        return None
    _state.window_id = None
    if not dialog.accepted or not enabled():
        _cancel(pending)
        return None
    try:
        tags = list(dialog.get_result_tags())
    except Exception as ex:
        simsight_log.line(f'travel: pick result failed: {ex!r}')
        tags = []
    if len(tags) != 1:
        _cancel(pending)
        return None
    return tags[0]


def _later(pending: _Pending, fn: Step, *args: Any) -> None:
    """Continue on the simulation pump after EA has finished handling the
    reply. A dead pump cancels the request instead of running inline. The
    scanner keeps quiet meanwhile: our next window is on its way."""
    try:
        import simsight_keys as keys

        keys.hold_scanner_resume()
    except Exception:
        simsight_log.error('travel: scanner hold')

    def step(owner: _Pending = pending) -> None:
        if _state.pending is not owner:
            return
        if enabled():
            fn(owner, *args)
        else:
            _cancel(owner)  # switched off between the reply and this step

    if not pump.post(step):
        simsight_log.line('travel: pump unavailable; request cancelled')
        _cancel(pending)


def _map_row() -> Row:
    return (menu._raw(lang.t('open the map instead')), None, (_MAP_ROW, 0))


def _show_worlds(pending: _Pending) -> bool:
    rows: list[Row] = [
        (
            label if label is not None else menu._raw(name),
            menu._raw(lang.t('places count').format(count)),
            (WORLD, key),
        )
        for key, name, count, label in pending.worlds
    ]
    rows.append(_map_row())
    return _open_window(pending, 'travel destinations', 'choose a world', rows, _on_world)


def _on_world(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _MAP_ROW:
        _later(pending, _fallback)
    else:
        _later(pending, _show_places, key)


def _show_places(pending: _Pending, world_key: int) -> bool:
    places = [p for p in pending.places if p.world_key == world_key]
    if not places:
        return _show_worlds(pending)
    if pending.mode == VACATION:
        for place in places:
            place.price = _daily_price(place.zone_id, pending)
    rows: list[Row] = [(menu._raw(lang.t('back to worlds')), None, (_BACK_ROW, 0))]
    rows.extend(
        (menu._raw(entry.label()), _detail(entry), (PLACE, entry.zone_id))
        for entry in sorted(_buildings(places), key=lambda p: p.label().lower())
    )
    rows.append(_map_row())
    return _open_window(pending, 'travel destinations', 'choose a place', rows, _on_place)


def _on_place(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _BACK_ROW:
        _later(pending, _show_worlds)
    elif kind == _MAP_ROW:
        _later(pending, _fallback)
    elif _units_of(pending, key):
        _later(pending, _show_units, key)
    else:
        _choose(pending, _find(pending, key))


def _show_units(pending: _Pending, master: int) -> bool:
    units = _units_of(pending, master)
    if not units:
        return _show_worlds(pending)
    rows: list[Row] = [(menu._raw(lang.t('back to places')), None, (_BACK_ROW, units[0].world_key))]
    rows.extend(
        (menu._raw(place.label()), _detail(place), (UNIT, place.zone_id))
        for place in sorted(units, key=lambda p: p.label().lower())
    )
    return _open_window(pending, 'travel destinations', 'choose a unit', rows, _on_unit)


def _on_unit(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _BACK_ROW:
        _later(pending, _show_places, key)
    else:
        _choose(pending, _find(pending, key))


def _choose(pending: _Pending, place: _Place | None) -> None:
    if place is None:
        _cancel(pending)
        return
    if pending.mode == VACATION:
        if place.price is None:
            place.price = _daily_price(place.zone_id, pending)  # the client answer may have arrived
        if place.rental is True and place.price is not None:
            _later(pending, _show_stay, place.zone_id)
        else:
            # No usable price: the native panel decides, before anything is created.
            simsight_log.line(f'travel: zone {place.zone_id} has no rental price; native map')
            _later(pending, _fallback)
        return
    if _needs_native_map(place):
        simsight_log.line(f'travel: zone {place.zone_id} needs the native business selector')
        _say('map required here')
        _later(pending, _fallback)
        return
    _later(pending, _commit, place.zone_id)


def _stay_row(zone_id: int, price: int, days: int, funds: int | None) -> Row:
    cost = int(price * days)
    label = (
        lang.t('stay one day').format(cost) if days == 1 else lang.t('stay days').format(days, cost)
    )
    detail = menu._raw(lang.t('cannot afford')) if funds is not None and cost > funds else None
    return (menu._raw(label), detail, (_STAY_ROW, (zone_id, days, cost)))


def _show_stay(pending: _Pending, zone_id: int) -> bool:
    place = _find(pending, zone_id)
    if place is not None:
        place.price = _daily_price(zone_id, pending)  # the client may have answered since
    if place is None or place.price is None:
        return _fallback(pending)
    price = place.price
    funds = _funds()
    rows: list[Row] = [(menu._raw(lang.t('back to places')), None, (_BACK_ROW, place.world_key))]
    rows.extend(_stay_row(zone_id, price, days, funds) for days in range(1, _MAX_STAY_DAYS + 1))
    return _open_window(pending, 'travel destinations', 'choose a stay', rows, _on_stay)


def _on_stay(pending: _Pending, dialog: Any) -> None:
    tag = _reply(pending, dialog)
    if tag is None:
        return
    kind, key = tag
    if kind == _BACK_ROW:
        _later(pending, _show_places, key)
    else:
        zone_id, days, quoted = key
        _later(pending, _rent_and_commit, zone_id, days, quoted)


# ---------------------------------------------------------------- rental


def _grouped(info: Any) -> bool:
    """EA's manager drops every Sim whose household already has a member in
    a travel group, so the whole party is checked at household level."""
    household = getattr(info, 'household', None)
    check = getattr(household, 'any_member_in_travel_group', None)
    if check is not None and check():
        return True
    return bool(info.is_in_travel_group())


def _created_group(manager: Any, party: list[Any], zone_id: int) -> Any:
    """The travel group the rental just created for this party, verified by
    membership and zone; None when it cannot be identified."""
    try:
        group = manager.get_travel_group_by_sim_info(party[0])
        if group is None:
            return None
        from world.street import get_vacation_zone_id

        expected = (zone_id, get_vacation_zone_id(zone_id))
        if getattr(group, 'zone_id', None) not in expected:
            return None
        if any(info not in group for info in party):
            return None
        return group
    except Exception:
        simsight_log.error('travel: created travel group')
        return None


def _rollback_rental(manager: Any, group: Any, household: Any, zone_id: int, cost: int) -> bool:
    """Undo a rental whose dialog answer was refused: release the exact
    group created for this request and return the money to the household
    that paid. Anything unverifiable is reported, never guessed."""
    if group is None:
        simsight_log.line(f'travel: rental of zone {zone_id} could not be identified for rollback')
        return False
    try:
        if not manager.destroy_travel_group_and_release_zone(group):
            simsight_log.line(f'travel: rental of zone {zone_id} could not be released')
            return False
        if cost:
            from protocolbuffers import Consts_pb2

            household.funds.add(cost, Consts_pb2.FUNDS_MONEY_VACATION, count_as_earnings=False)
        simsight_log.line(f'travel: rental of zone {zone_id} rolled back, {cost} returned')
        return True
    except Exception as ex:
        simsight_log.line(f'travel: rental rollback failed for zone {zone_id} ({ex!r})')
        return False


class _Rental:
    """What a rental needs afterwards: to answer the dialog or roll back."""

    __slots__ = ('manager', 'payer', 'service')

    def __init__(self, manager: Any, payer: Any, service: Any) -> None:
        self.manager = manager
        self.payer = payer
        self.service = service


def _rental_refusal(party: list[Any], cost: int) -> str | None:
    """The spoken term when the party may not rent, after logging why."""
    if not party:
        raise ValueError('no travelling Sims')
    for info in party:
        if _grouped(info):
            simsight_log.line(
                'travel: household of {} already has a travel group; vacation refused'.format(
                    getattr(info, 'sim_id', info)
                )
            )
            return 'action unavailable'
    funds = _funds()
    if cost and (funds is None or funds < cost):
        simsight_log.line(f'travel: vacation cost {cost} exceeds funds {funds}; refused')
        return 'cannot afford'
    return None


def _rent(pending: _Pending, zone_id: int, days: int, cost: int) -> _Rental | None:
    """Every check, the zone picked on EA's dialog, then the rental through
    EA's travel group manager. None, after speaking why, when refused."""
    dialog = pending.dialog
    party = pending.party
    try:
        import services

        refusal = _rental_refusal(party, cost)
        if refusal is not None:
            _say(refusal)
            return None
        service = _service()
        if not service.dialog_pick_result(dialog.dialog_id, [zone_id]):
            simsight_log.line(
                f'travel: zone {zone_id} refused by dialog {dialog.dialog_id} before renting; request cancelled'
            )
            _say('action unavailable')
            return None
        import clock

        now = services.time_service().sim_now
        end = now + clock.interval_in_sim_days(days) if days else None
        manager = services.travel_group_manager()
        payer = services.active_household()
        created = manager.create_travel_group_and_rent_zone(
            sim_infos=list(party),
            zone_id=zone_id,
            played=True,
            create_timestamp=now,
            end_timestamp=end,
            cost=cost,
        )
    except Exception as ex:
        simsight_log.line(f'travel: vacation setup failed ({ex!r}); request cancelled')
        _say('action unavailable')
        return None
    if not created:
        simsight_log.line(f'travel: rental of zone {zone_id} refused by the travel group manager')
        _say('action unavailable')
        return None
    return _Rental(manager, payer, service)


def _answer_ok(dialog: Any, service: Any) -> bool:
    try:
        from ui.ui_dialog import ButtonType

        return _dialog_active(dialog) and bool(
            service.dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_OK)
        )
    except Exception as ex:
        simsight_log.line(f'travel: answering dialog {dialog.dialog_id} failed ({ex!r})')
        return False


def _rent_and_commit(pending: _Pending, zone_id: int, days: int, quoted: int | None = None) -> bool:
    """Validate the answer, rent the lot through EA's travel group manager
    exactly as the native rental panel does, then answer the dialog. Every
    check happens before the rental; a refused answer after the rental is
    rolled back, and there is never a native fallback after a rental."""
    if _state.pending is not pending or pending.rented:
        return False
    place = _find(pending, zone_id)
    dialog = pending.dialog
    if place is None or not _dialog_active(dialog):
        _say('action unavailable')
        return _cancel(pending)
    # The client's answer for this request may have arrived, changed or been
    # withdrawn since the stay was quoted: read it again before any sum.
    price = place.price = _daily_price(zone_id, pending)
    if days and price is None:
        simsight_log.line(f'travel: zone {zone_id} has no usable price at commit; native map')
        _say('map required here')
        return _fallback(pending)
    cost = int(price * days) if days and price is not None else 0
    if quoted is not None and cost != quoted:
        # The price moved after the stay was quoted: never charge an amount
        # the player has not heard; show the stay window again instead.
        simsight_log.line(
            f'travel: quoted {quoted} but current cost {cost} for zone {zone_id}; re-quoting'
        )
        _say('price changed')
        return _show_stay(pending, zone_id)
    rental = _rent(pending, zone_id, days, cost)
    if rental is None:
        return _cancel(pending)
    pending.rented = True
    group = _created_group(rental.manager, pending.party, zone_id)
    simsight_log.line(
        'travel: zone {} rented for {} days at {} (group {})'.format(
            zone_id, days, cost, getattr(group, 'id', 'unverified')
        )
    )
    _state.pending = None
    if _answer_ok(dialog, rental.service):
        simsight_log.line(f'travel: dialog {dialog.dialog_id} answered with zone {zone_id}')
        return True
    simsight_log.line(
        f'travel: dialog {dialog.dialog_id} did not accept OK after the rental; rolling back'
    )
    _rollback_rental(rental.manager, group, rental.payer, zone_id, cost)
    _say('action unavailable')
    _cancel_dialog(dialog)
    return False


# ------------------------------------------------------- dialog outcomes


def _clear_picks(dialog: Any) -> None:
    # EA's picker listener pushes continuations from the picked results
    # without checking the response, so a cancel must never carry picks.
    try:
        dialog.picked_results = []
    except Exception:
        simsight_log.error('travel: clearing picks')


def _commit(pending: _Pending, zone_id: int) -> bool:
    """Answer EA's dialog with the chosen zone; EA does the rest."""
    if _state.pending is not pending:
        return False
    _state.pending = None
    dialog = pending.dialog
    if not _dialog_active(dialog):
        simsight_log.line(f'travel: dialog {dialog.dialog_id} no longer active, nothing sent')
        _say('action unavailable')
        return False
    from ui.ui_dialog import ButtonType

    service = _service()
    if not service.dialog_pick_result(dialog.dialog_id, [zone_id]):
        simsight_log.line(
            f'travel: zone {zone_id} refused by dialog {dialog.dialog_id}; request cancelled'
        )
        _say('action unavailable')
        _cancel_dialog(dialog)
        return False
    if service.dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_OK):
        simsight_log.line(f'travel: dialog {dialog.dialog_id} answered with zone {zone_id}')
        return True
    simsight_log.line(f'travel: dialog {dialog.dialog_id} did not accept OK; request cancelled')
    _say('action unavailable')
    _cancel_dialog(dialog)
    return False


def _cancel_dialog(dialog: Any) -> bool:
    """Cancel EA's dialog natively if it is still active, with no picks."""
    if not _dialog_active(dialog):
        return False
    _clear_picks(dialog)
    try:
        from ui.ui_dialog import ButtonType

        return bool(_service().dialog_respond(dialog.dialog_id, ButtonType.DIALOG_RESPONSE_CANCEL))
    except Exception as ex:
        simsight_log.line(f'travel: cancelling dialog {dialog.dialog_id} failed ({ex!r})')
        return False


def _cancel(pending: _Pending) -> bool:
    """The request ends without a destination: cancel EA's dialog natively."""
    if _state.pending is not pending:
        return False
    _state.pending = None
    if _state.window_id is not None:
        menu._cancel_dialog(_state.window_id)
        _state.window_id = None
    simsight_log.line(f'travel: dialog {pending.dialog.dialog_id} cancelled from the list')
    result = _cancel_dialog(pending.dialog)
    _say('travel cancelled')
    return result


def _native_map(dialog: Any, msg_type: Any, dialog_msg: Any, kwargs: dict[str, Any]) -> Any:
    """EA's own map for this request."""
    original = _state.original_distribute
    if original is None:
        return None  # never installed: nothing replaced EA's map
    return original(dialog, msg_type, dialog_msg, **kwargs)


def _fallback(pending: _Pending) -> bool:
    """Hand the same request to the native map, once."""
    if _state.pending is not pending or pending.fallback_used or pending.rented:
        return False
    _state.pending = None
    pending.fallback_used = True
    if not _dialog_active(pending.dialog):
        _say('action unavailable')
        return False
    simsight_log.line(f'travel: dialog {pending.dialog.dialog_id} handed to the native map')
    _native_map(pending.dialog, pending.msg_type, pending.dialog_msg, pending.kwargs)
    return True


def _prepare(pending: _Pending) -> None:
    """Collect the request's destinations; raises when the list cannot be
    offered, so the native map takes it."""
    dialog = pending.dialog
    vacation = pending.mode == VACATION
    if vacation:
        pending.party = _party(dialog)
        if not pending.party:
            raise ValueError('no travelling Sims')
        import services

        services.travel_group_manager()  # raises when vacations are unavailable
        if _funds() is None:
            raise ValueError('household funds unavailable')
        _state.price_generation += 1
        pending.generation = _state.price_generation
    pending.places = _collect(dialog, vacation=vacation, pending=pending)
    _state.last_rows = list(pending.places)
    pending.worlds = _worlds(pending.places)
    if not pending.worlds:
        raise ValueError('no destinations')


def _distribute(dialog: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    mode = _request_mode(dialog)
    if not (enabled() and mode is not None and _zone_loaded()):
        simsight_log.line(
            'travel: dialog {} to the native map (list {}, mode {}, zone {})'.format(
                getattr(dialog, 'dialog_id', '?'),
                'on' if enabled() else 'off',
                mode,
                'loaded' if _zone_loaded() else 'none',
            )
        )
        return _native_map(dialog, msg_type, dialog_msg, kwargs)
    if _state.pending is not None:
        # A second request while one list is open: the older one is
        # cancelled natively so no invisible interaction stays behind.
        simsight_log.line(
            f'travel: dialog {_state.pending.dialog.dialog_id} superseded by dialog {dialog.dialog_id}'
        )
        _cancel(_state.pending)
    pending = _Pending(dialog, msg_type, dialog_msg, kwargs, mode)
    try:
        _prepare(pending)
    except Exception as ex:
        simsight_log.line(f'travel: list unavailable ({ex!r}), native map instead')
        return _native_map(dialog, msg_type, dialog_msg, kwargs)
    _state.pending = pending
    if _show_worlds(pending):
        simsight_log.line(
            f'travel: dialog {dialog.dialog_id} listed as {len(pending.places)} places in {len(pending.worlds)} worlds'
        )
    return None


# --------------------------------------------------------------- commands


def _hidden_flag(zone_id: int) -> Any:
    try:
        import services

        proto = services.get_persistence_service().get_zone_proto_buff(zone_id)
        world_desc = services.get_world_description_id(proto.world_id)
        return services.get_hide_from_lot_picker(proto.lot_id, world_desc)
    except Exception as ex:
        return f'error {ex!r}'  # a diagnostic: the failure is the answer


def _client_price(payload: str) -> bool:
    try:
        parts = str(payload).split('|')
        if len(parts) != 3:
            return False
        zone_id = int(parts[0])
        value = _positive(float(parts[1]))
        generation = int(parts[2])
    except (TypeError, ValueError):
        return False
    if value is not None and not 1 <= int(value) <= _MAX_DAILY_PRICE:
        value = None
    pending = _state.pending
    if pending is None or pending.mode != VACATION or generation != pending.generation:
        simsight_log.line(
            f'travel: client price for zone {zone_id} ignored (generation {generation})'
        )
        return False
    if value is None:
        pending.prices.pop(zone_id, None)
        simsight_log.line(f'travel: client price for zone {zone_id} unusable ({payload!r})')
        return False
    pending.prices[zone_id] = int(value)
    return True


@sims4.commands.Command(
    'simsight.lotprice_enc', 'a11y.lotprice_enc', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_lotprice_enc(payload: str = '', _connection: Any = None) -> bool:
    """Client bridge reply: <zone id>|<per-day price>|<generation> as the map
    computes it. Only the open vacation request that asked (same
    generation) may use it; anything else is dropped."""
    try:
        return _client_price(payload)
    except Exception:
        simsight_log.error('travel: simsight.lotprice_enc')
        return False


@sims4.commands.Command(
    'simsight.lot_value', 'a11y.lot_value', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_lot_value(zone: str = '0', _connection: Any = None) -> bool:
    """Log every lot value source for a zone and the price the list would use."""
    try:
        output = sims4.commands.CheatOutput(_connection)
        try:
            zone_id = int(zone, 0)
        except (TypeError, ValueError):
            output('a11y lot value: usage simsight.lot_value <zone id>')
            return False
        line = f'travel lot value: zone {zone_id} sources {_lot_values(zone_id)} price {_daily_price(zone_id)}'
        simsight_log.line(line)
        output(line)
        return True
    except Exception:
        simsight_log.error('travel: simsight.lot_value')
        return False


@sims4.commands.Command(
    'simsight.travel_probe', 'a11y.travel_probe', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_travel_probe(_connection: Any = None) -> bool:
    """Log every row of the last travel request with its region facts."""
    try:
        output = sims4.commands.CheatOutput(_connection)
        rows = _state.last_rows
        if not rows:
            output('a11y travel probe: no travel request listed yet')
            return True
        for place in rows:
            simsight_log.line(
                f'travel probe: zone {place.zone_id} name {place.name!r} world {place.world_name!r} region {place.region!r} facing {place.visible} hide {_hidden_flag(place.zone_id)} venue {place.venue is not None} residential {place.residential} household {place.household!r} master {place.master}'
            )
        output(f'a11y travel probe: {len(rows)} rows logged')
        return True
    except Exception:
        simsight_log.error('travel: simsight.travel_probe')
        return False


def _ui(message: str) -> bool:
    """Encoded UI message on the existing A11y| channel (Flash side: Codex)."""
    try:
        import distributor.ops
        import distributor.system

        distributor.system.Distributor.instance().add_op_with_no_owner(
            distributor.ops.SendUIMessage(message)
        )
        return True
    except Exception as ex:
        simsight_log.line(f'travel: ui message failed ({ex!r})')
        return False


# ------------------------------------------------- keyboard M entry lease


def announce_setting() -> bool:
    return _ui('A11y|travellist|' + ('on' if settings.get('travel_list', True) else 'off'))


def apply_setting() -> bool:
    """Settings changed (menu or simsight.reload): an open list is cancelled
    natively when it was switched off (the same contract as a reply that
    arrives after the switch), every UI copy hears the value at once and
    the M lease starts a new epoch."""
    if not enabled() and _state.pending is not None:
        _cancel(_state.pending)
    announce_setting()
    return start_lease()


def _publish_lease() -> bool:
    if enabled() and _zone_loaded():
        return _ui(f'A11y|travelready|{_state.epoch}')
    # Flash drops its pending request on a zero lease, so nothing queued
    # here may survive to push later.
    _invalidate_requests('stale')
    return _ui('A11y|travelready|0')


def _refresh_lease(generation: int, force: bool = False) -> bool:
    """Pump-driven heartbeat: re-posts itself while the pump drains; sends at
    most one readiness message per interval. Exactly one chain is live: a
    callback queued before the latest start or stop ends without re-posting
    and leaves the live chain's state alone."""
    if generation != _state.lease_generation:
        return False
    import time

    now = time.monotonic()
    if force or now - _state.lease_sent >= _LEASE_INTERVAL:
        _publish_lease()
        _state.lease_sent = now
    _state.lease_running = pump.post(_refresh_lease, generation)
    return _state.lease_running


def start_lease() -> bool:
    """A new session epoch (zone load, pump start, setting change). Requests
    from an earlier epoch are refused from here on."""
    _state.epoch += 1
    _state.lease_generation += 1
    _invalidate_requests('stale')
    _state.lease_running = False
    return _refresh_lease(_state.lease_generation, force=True)


def stop_lease() -> None:
    """Pump stopped or zone unloading: publish no lease, keep the epoch."""
    _state.lease_generation += 1
    _state.lease_running = False
    _invalidate_requests('stale')
    _ui('A11y|travelready|0')


def _entry(key: tuple[int, int]) -> _Request | None:
    for entry in _state.requests:
        if entry.key == key:
            return entry
    return None


def _send_result(epoch: int, request: int, outcome: str, reason: str = '') -> None:
    text = f'A11y|travelresult|{epoch}|{request}|{outcome}'
    if reason:
        text += '|' + reason
    _ui(text)


def _invalidate_requests(reason: str) -> None:
    """Settle every queued request as refused and tell Flash, so nothing
    queued can push later and no pending request is left unanswered."""
    for entry in _state.requests:
        if entry.state == 'pending':
            entry.state = 'refused:' + reason
            epoch, request = entry.key
            _send_result(epoch, request, 'refused', reason)


def _remember(key: tuple[int, int], state: str, when: float) -> None:
    _state.requests.append(_Request(key, state, when))
    del _state.requests[:-_REQUEST_HISTORY]


def _result(request: int, outcome: str, reason: str = '', epoch: int | None = None) -> bool:
    """Explicit reply Flash waits for; the epoch lets it ignore replies to a
    request from an earlier session. The settled outcome is kept on the
    request entry so a lost reply can be answered again."""
    epoch = _state.epoch if epoch is None else epoch
    entry = _entry((epoch, request))
    if entry is not None and entry.state == 'pending':
        entry.state = outcome + (':' + reason if reason else '')
    _send_result(epoch, request, outcome, reason)
    return outcome == 'ok'


def _resend(entry: _Request) -> None:
    """Answer again from the settled state of an entry (lost reply, duplicate
    cancel, replayed request); nothing is queued or pushed."""
    epoch, request = entry.key
    state = entry.state
    if state == 'pending':
        return
    if state == 'cancelled':
        _send_result(epoch, request, 'refused', 'cancelled')
        return
    outcome, _, reason = state.partition(':')
    _send_result(epoch, request, outcome, reason)


def _away(sim: Any) -> bool:
    """The Sim is on another lot than the loaded zone."""
    import services

    try:
        return sim.zone_id != services.current_zone_id()
    except Exception:
        simsight_log.error('travel: active Sim zone')
        return False  # unknown: EA's own tests decide


def _ready_sim() -> tuple[Any, str]:
    """The active Sim when a keyboard entry may start, or (None, reason)
    after speaking why not."""
    import services

    if not enabled() or not _zone_loaded():
        _say('action unavailable')
        return None, 'refused'
    sim = services.get_active_sim()
    if sim is None:
        _say('load a household first')
        return None, 'no_sim'
    if _away(sim):
        _say('sim unavailable')
        return None, 'away'
    return sim, ''


def _start_entry(sim: Any, request: int, epoch: int) -> bool:
    if simplified():
        # The request is answered once our window is up; the choice pushes
        # EA's Travel on its own, like a second press of M would.
        if _show_front(sim):
            simsight_log.line(
                f'travel: keyboard entry for sim {sim.sim_id} offers alone or with others (request {request})'
            )
            return _result(request, 'ok', '', epoch)
        _say('action unavailable')
        return _result(request, 'refused', 'error', epoch)
    outcome = _push_travel(sim)
    if outcome == 'ok':
        simsight_log.line(f'travel: keyboard entry pushed for sim {sim.sim_id} (request {request})')
        return _result(request, 'ok', '', epoch)
    return _result(request, 'refused', outcome, epoch)


def open_travel(request: int = 0, epoch: int = 0) -> bool:
    """Keyboard entry: the tuned Sim menu Travel chain for the active Sim
    (companion picker, then this list). EA's own tests gate it: the save
    lock, the career and multi-unit event blacklists, venue rules. Runs on
    the pump; the request must still be current, uncancelled and younger
    than the deadline when it gets here. Every refusal is reported to Flash
    and spoken."""
    import time

    entry = _entry((epoch, request))
    if entry is None:
        return _result(request, 'refused', 'stale', epoch)
    if entry.state != 'pending':
        return False  # settled already (cancelled, stale or answered)
    if epoch != _state.epoch or time.monotonic() - entry.when > _REQUEST_DEADLINE:
        simsight_log.line(
            f'travel: keyboard entry request {request} expired (epoch {epoch} vs {_state.epoch})'
        )
        return _result(request, 'refused', 'stale', epoch)
    sim, reason = _ready_sim()
    if sim is None:
        return _result(request, 'refused', reason, epoch)
    return _start_entry(sim, request, epoch)


def _note_alone(result: Any) -> None:
    """After a Go alone push: remember a queued Travel, which shows its
    companion picker later.

    EA never picks a companion by itself here (1.128.90 bytecode of
    SimPickerInteraction._run_interaction_gen): a single eligible Sim is
    preselected only for tuning with single_choice_display_name, which 423375
    does not set, and nothing is passed in picked_item_ids. With nobody
    eligible (min_selectable 0) EA travels alone without a picker. Otherwise
    its picker is shown and answered by _sim_distribute."""
    if _state.alone_claimed:
        return  # already claimed inside the push
    interaction = getattr(result, 'interaction', None)
    if interaction is not None:
        _state.alone = interaction
    else:
        simsight_log.line('travel: no interaction to match; the companion picker will show')


def _push_travel(sim: Any, alone: bool = False) -> str:
    """EA's own Travel (the companion picker, then the map picker this
    module lists). 'ok', 'refused' or 'error'; a refusal speaks EA's reason
    when its test gives one."""
    import services

    _state.alone = None  # a new Travel replaces any earlier Go alone
    _state.alone_claimed = False
    try:
        import sims4.resources
        from interactions.context import InteractionContext
        from interactions.priority import Priority

        affordance = services.get_instance_manager(sims4.resources.Types.INTERACTION).get(
            _TRAVEL_AFFORDANCE
        )
        if affordance is None:
            raise ValueError('travel affordance missing')
        client = services.client_manager().get_first_client()
        context = InteractionContext(
            sim, InteractionContext.SOURCE_PIE_MENU, Priority.High, client=client, pick=None
        )
        # 423375 is an ImmediateSuperInteraction: it runs, and EA shows its
        # companion picker, inside this push (2026-09-26 live, BFF household:
        # the picker appeared before the push returned and was not claimed).
        _state.alone_push = sim.sim_id if alone else None
        try:
            result = sim.push_super_affordance(affordance, sim, context)
        finally:
            _state.alone_push = None
        if not result:
            simsight_log.line(f'travel: keyboard entry refused: {result}')
            _say_refusal(result, sim)
            return 'refused'
        if alone:
            _note_alone(result)
        return 'ok'
    except Exception as ex:
        simsight_log.line(f'travel: keyboard entry failed: {ex!r}')
        _say('action unavailable')
        return 'error'


def _say_refusal(result: Any, sim: Any) -> Any:
    """The game's own reason for a refused Travel (for example a social
    event in progress), after the spoken line."""
    import simsight_native as native

    return native.say_refusal(result, sim)


def _front_picked(reply: Any, sim_id: int) -> None:
    """The Go alone / Choose who comes window answered."""
    if reply.dialog_id != _state.front_id:
        return
    _state.front_id = None
    if not reply.accepted:
        return
    try:
        tags = list(reply.get_result_tags())
    except Exception:
        simsight_log.error('travel: front window result')
        tags = []
    if len(tags) != 1 or tags[0] not in (_ALONE, _WITH):
        return
    # After EA has finished with this window; never inline.
    if not pump.post(_front_chosen, sim_id, tags[0] == _ALONE):
        _say('action unavailable')


def _show_front(sim: Any) -> bool:
    """Simplified menus: Go alone or Choose who comes, before EA's own
    companion picker. Escape closes it and nothing is pushed."""
    try:
        menu._cancel_dialog(_state.front_id)
        _state.front_id = None
        dialog = menu._picker(lang.t('travel'), lang.t('travel front instructions'))
        if dialog is None:
            return False
        rows = (
            ('travel alone', 'travel alone detail', _ALONE),
            ('travel with others', 'travel with others detail', _WITH),
        )
        _show_picker(
            dialog,
            [
                (menu._raw(lang.t(name)), menu._raw(lang.t(detail)), tag)
                for name, detail, tag in rows
            ],
        )
        sim_id = sim.sim_id

        def picked(reply: Any) -> None:
            try:
                _front_picked(reply, sim_id)
            except Exception:
                simsight_log.error('travel: front window reply')

        dialog.add_listener(picked)
        _fence_input()
        dialog.show_dialog()
        _state.front_id = dialog.dialog_id
        return True
    except Exception as ex:
        simsight_log.line(f'travel: front window failed ({ex!r})')
        return False


def _front_chosen(sim_id: int, alone: bool) -> bool:
    """Pump: the same checks as the M entry, then EA's Travel."""
    import services

    if not enabled() or not _zone_loaded():
        _say('action unavailable')
        return False
    sim = services.get_active_sim()
    if sim is None or sim.sim_id != sim_id or _away(sim):
        _say('sim unavailable')
        return False
    outcome = _push_travel(sim, alone=alone)
    simsight_log.line(
        'travel: {} chosen, Travel {}'.format('alone' if alone else 'with others', outcome)
    )
    return outcome == 'ok'


def _listeners(dialog: Any) -> list[Any]:
    try:
        return list(getattr(dialog, '_listeners', ()) or ())
    except TypeError:
        return []  # not a list of listeners: not ours


def _ours(dialog: Any, interaction: Any) -> bool:
    """EA's companion picker belongs to our Go alone push: its listener is
    the pushed interaction's own _on_picker_selected."""
    return any(
        getattr(listener, '__self__', None) is interaction for listener in _listeners(dialog)
    )


def _ours_in_push(dialog: Any, sim_id: int) -> bool:
    """Inside our own Go alone push: the picker's listener belongs to a
    Travel (423375) interaction of that same Sim."""
    for listener in _listeners(dialog):
        owner = getattr(listener, '__self__', None)
        if owner is None or getattr(owner, 'guid64', None) != _TRAVEL_AFFORDANCE:
            continue
        if getattr(getattr(owner, 'sim', None), 'sim_id', None) == sim_id:
            return True
    return False


def _claims(dialog: Any) -> bool:
    """Whether this Sim picker is the companion picker of our Go alone:
    shown inside our push (an immediate Travel), or later by the queued
    Travel interaction we pushed."""
    if _state.alone_push is not None and _ours_in_push(dialog, _state.alone_push):
        _state.alone_push = None
        _state.alone_claimed = True
        return True
    interaction = _state.alone
    if interaction is None:
        return False
    if getattr(interaction, 'is_finishing', False) or getattr(
        interaction, 'has_been_canceled', False
    ):
        _state.alone = None
        simsight_log.line('travel: Go alone ended before the companion picker')
        return False
    if _ours(dialog, interaction):
        _state.alone = None
        return True
    return False


def _native_sim_picker(dialog: Any, msg_type: Any, dialog_msg: Any, kwargs: dict[str, Any]) -> Any:
    original = _state.original_sim_distribute
    if original is None:
        return None  # never installed: nothing replaced EA's picker
    return original(dialog, msg_type, dialog_msg, **kwargs)


def _sim_distribute(dialog: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    """EA's companion picker for our Go alone request is answered with
    nobody through EA's dialog service, only when the picker allows an
    empty choice; every other Sim picker is shown as always. EA has already
    registered the dialog when it distributes it, so the answer can wait for
    the pump."""
    if _claims(dialog):
        minimum = getattr(dialog, 'min_selectable', None)
        if (
            minimum == 0
            and enabled()
            and simplified()
            and pump.post(_answer_alone, dialog, msg_type, dialog_msg, kwargs)
        ):
            simsight_log.line(f'travel: companion picker {dialog.dialog_id} queued for nobody')
            return None
        simsight_log.line(f'travel: companion picker {dialog.dialog_id} shown (minimum {minimum})')
    return _native_sim_picker(dialog, msg_type, dialog_msg, kwargs)


def _answer_alone(dialog: Any, msg_type: Any, dialog_msg: Any, kwargs: dict[str, Any]) -> bool:
    """Pump: an empty pick, then OK, through EA's dialog service (its own
    validation and listener). If EA does not take it, the picker is shown
    after all, so no invisible dialog stays behind."""
    import simsight_native as native

    if not _dialog_active(dialog):
        simsight_log.line(f'travel: companion picker {dialog.dialog_id} already gone')
        return False
    if native.answer_picker(dialog, []):
        simsight_log.line(f'travel: companion picker {dialog.dialog_id} answered: nobody')
        return True
    simsight_log.line(f'travel: companion picker {dialog.dialog_id} did not take nobody; shown')
    if _dialog_active(dialog):
        _native_sim_picker(dialog, msg_type, dialog_msg, kwargs)
    return False


def _travel_ingress(lease: str, request: str) -> bool:
    import time

    try:
        epoch = int(lease)
        request_id = int(request)
    except (TypeError, ValueError):
        return _result(0, 'refused', 'error', 0)
    key = (epoch, request_id)
    entry = _entry(key)
    if entry is not None:
        if entry.state != 'pending':
            _resend(entry)  # cancelled tombstone or a lost reply
        return False  # still queued: only its eventual outcome may answer
    _remember(key, 'pending', time.monotonic())
    if epoch != _state.epoch or not _state.lease_running:
        return _result(request_id, 'refused', 'stale', epoch)
    if not enabled() or not _zone_loaded():
        return _result(request_id, 'refused', 'refused', epoch)
    if not pump.post(open_travel, request_id, epoch):
        _say('action unavailable')
        return _result(request_id, 'refused', 'no_pump', epoch)
    return True


@sims4.commands.Command(
    'simsight.travel', 'a11y.travel', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_travel(lease: str = '0', request: str = '0', _connection: Any = None) -> bool:
    """Flash posts simsight.travel <epoch> <request> after its native guards.
    Refusals here happen at ingress, before anything is queued."""
    try:
        return _travel_ingress(lease, request)
    except Exception:
        simsight_log.error('travel: simsight.travel')
        return False


def _travel_cancel(request: str, lease: str) -> bool:
    try:
        request_id = int(request)
        epoch = int(lease) if lease else _state.epoch
    except (TypeError, ValueError):
        return False
    import time

    key = (epoch, request_id)
    entry = _entry(key)
    if entry is None:
        # Cancel arrived before the request: leave a tombstone so the late
        # request is refused, and acknowledge now.
        _remember(key, 'cancelled', time.monotonic())
        _send_result(epoch, request_id, 'refused', 'cancelled')
        return True
    if entry.state == 'pending':
        entry.state = 'cancelled'
        simsight_log.line(f'travel: keyboard entry request {request_id} cancelled')
        _send_result(epoch, request_id, 'refused', 'cancelled')
        return True
    _resend(entry)  # duplicate cancel or a finished request: answer again
    return True


@sims4.commands.Command(
    'simsight.travel_cancel', 'a11y.travel_cancel', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_travel_cancel(request: str = '0', lease: str = '', _connection: Any = None) -> bool:
    """Flash invalidates one exact request (another M, Escape, zone change,
    its bounded timeout). Applied at ingress, never queued, so it always
    beats a push still waiting on a stalled pump; the acknowledgement is
    sent at once and Flash keeps the request pending until it arrives."""
    try:
        return _travel_cancel(request, lease)
    except Exception:
        simsight_log.error('travel: simsight.travel_cancel')
        return False


# ------------------------------------------------------------------ hooks


def _map_hook(self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    """UiMapViewPicker.distribute_dialog. A failure before the list owns the
    request leaves it to the native map, so EA's dialog is never lost."""
    try:
        return _distribute(self, msg_type, dialog_msg, **kwargs)
    except Exception:
        simsight_log.error('travel: map picker hook')
        owned = _state.pending is not None and _state.pending.dialog is self
        return None if owned else _native_map(self, msg_type, dialog_msg, kwargs)


def _sim_picker_hook(self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any) -> Any:
    """UiSimPicker.distribute_dialog; any failure shows EA's picker as always."""
    try:
        return _sim_distribute(self, msg_type, dialog_msg, **kwargs)
    except Exception:
        simsight_log.error('travel: companion picker hook')
        return _native_sim_picker(self, msg_type, dialog_msg, kwargs)


def install() -> bool:
    if _state.original_distribute is not None:
        return True
    from ui.ui_dialog_picker import UiMapViewPicker

    _state.original_distribute = UiMapViewPicker.distribute_dialog

    def a11y_travel_distribute_dialog(
        self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any
    ) -> Any:
        return _map_hook(self, msg_type, dialog_msg, **kwargs)

    UiMapViewPicker.distribute_dialog = a11y_travel_distribute_dialog
    try:
        from ui.ui_dialog_picker import UiSimPicker

        _state.original_sim_distribute = UiSimPicker.distribute_dialog

        def a11y_travel_sim_distribute_dialog(
            self: Any, msg_type: Any, dialog_msg: Any, **kwargs: Any
        ) -> Any:
            return _sim_picker_hook(self, msg_type, dialog_msg, **kwargs)

        UiSimPicker.distribute_dialog = a11y_travel_sim_distribute_dialog
    except Exception as ex:
        simsight_log.line(f'travel: companion picker hook unavailable ({ex!r})')
    return True


try:
    install()
except Exception:
    simsight_log.error('travel: install failed')

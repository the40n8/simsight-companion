"""Destinations: one place per row of EA's dialog, with its world, venue,
household and building, and the small business gate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_names as names

from .state import _Pending, _Place, _state
from .prices import _daily_price, _request_client_price

if TYPE_CHECKING:
    from .state import World


_RESIDENTIAL_BUSINESS_MIN = 1
_VENUE_BUSINESS_MIN = 2


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


def _residents(household_id: int) -> tuple[str, ...]:
    """Full names of the Sims living in a household, in household order."""
    try:
        import services

        household = services.household_manager().get(household_id)
        if household is None:
            return ()
        return tuple(name for name in (names.full_name(info) for info in household) if name)
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
    except Exception:
        simsight_log.error('travel: region table unavailable')
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
    except Exception:
        simsight_log.error('travel: small business check failed; native map kept')
        return True
    else:
        return count >= minimum


# --------------------------------------------------------------- commands


def _hidden_flag(zone_id: int) -> Any:
    try:
        import services

        proto = services.get_persistence_service().get_zone_proto_buff(zone_id)
        world_desc = services.get_world_description_id(proto.world_id)
        return services.get_hide_from_lot_picker(proto.lot_id, world_desc)
    except Exception as ex:
        simsight_log.error('travel: probe hide flag')
        return f'error {ex!r}'  # a diagnostic: the failure is the answer


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
    except Exception:
        simsight_log.error('travel: simsight.travel_probe')
        return False
    else:
        return True

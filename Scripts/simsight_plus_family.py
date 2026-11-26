"""SimSight Plus: start a brand-new family from the keyboard (Alt+C, "Start a brand-new family").

You make 1 to 8 Sims (age, gender, name, traits, aspiration, how they are
related), choose an empty lot or a house, and the game moves the new family
in and switches to playing them, the same way the game's own "split
household" does. The family you were playing stays in the town.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, NamedTuple

import simsight_log
import simsight_plus_core as core
import simsight_plus_sims as sims

if TYPE_CHECKING:
    from typing import Callable

    Lot = dict[str, Any]  # a free lot, as free_lots() lists it
    Relation = tuple[str, int]  # (kind, index of the earlier member)

STARTING_FUNDS = 20000  # what Create a Sim gives every new household
GROWN_UPS = ('YOUNGADULT', 'ADULT', 'ELDER')
AGE_ORDER = ('TODDLER', 'CHILD', 'TEEN', 'YOUNGADULT', 'ADULT', 'ELDER')
_PRICE_GENERATION = '424242'  # marks the lot price requests this module sends
_PRICE_WAIT_SECONDS = 3.0
_RELATION_TEXT = {
    'spouse': ', married to {}',
    'child': ', child of {}',
    'parent': ', parent of {}',
    'sibling': ', brother or sister of {}',
}


class _Member:
    def __init__(self) -> None:
        self.age: Any = None
        self.gender: Any = None
        self.first = ''
        self.traits: list[Any] = []
        self.aspiration: Any = None
        self.relation: Relation | None = None


class _Family:
    def __init__(self) -> None:
        self.last = ''
        self.members: list[_Member] = []
        self.lot: Lot | None = None


class _PriceWait(NamedTuple):
    deadline: float
    zones: list[Any]
    then: Callable[[], object]


class _Arrival(NamedTuple):
    household_id: Any
    zone_id: Any
    empty: bool


class _State:
    def __init__(self) -> None:
        self.family: _Family | None = None  # the family being made
        # Lot prices only live in the game's interface. SimSight can ask it for a lot's
        # rental price (land + house + furniture, times a fixed rate); we ask for that
        # and turn it back into the buying price.
        self.prices: dict[Any, int] = {}  # zone_id -> buying price, about
        self.price_wait: _PriceWait | None = None
        self.hooked = False  # the lot price command is wrapped
        self.arrival: _Arrival | None = None  # the family on its way to its new home


_state = _State()


def _fam() -> _Family:
    family = _state.family
    if family is None:
        raise LookupError('no new family is being made')
    return family


def _age_label(age: Any) -> str:
    return sims.age_name(age)


def _gender_word(m: _Member) -> str:
    from sims.sim_info_types import Gender

    return 'female' if m.gender == Gender.FEMALE else 'male'


def _member_line(m: _Member) -> str:
    return f'{m.first} {_fam().last}, {_age_label(m.age)} {_gender_word(m)}'


def _age_rank(age: Any) -> int:
    return AGE_ORDER.index(age.name)


# ---------------------------------------------------------------- start


def start() -> None:
    import services

    if services.active_household() is None or core.active_sim() is None:
        core.speak('Load a game first. Any family will do, your new family gets its own home.')
        return
    rows = [
        (
            'Start',
            (
                'Step 1: the family name. Step 2: make each Sim. Step 3: choose a home. '
                'Nothing changes in the game until you confirm the move at the end.'
            ),
            'go',
        ),
        ('Cancel', None, None),
    ]
    core.choose(
        'Start a brand-new family',
        'You make 1 to 8 new Sims and choose an empty lot to build on, or a house. '
        f'The family gets {STARTING_FUNDS} simoleons, like in Create a Sim. Then the game switches to your new family. '
        'The family you are playing now stays in the town.',
        rows,
        _started,
        tag='family',
    )


def _started(value: Any) -> None:
    if value != 'go':
        return
    _state.family = _Family()

    def got_last(last: str) -> None:
        if not last:
            core.speak('A family name is needed. Cancelled.')
            return
        _fam().last = last
        core.post(_new_member)

    core.ask_text('Family name', "Type the family's last name, for example Smith.", '', got_last)


# ---------------------------------------------------------------- one member


def _new_member() -> None:
    n = len(_fam().members) + 1
    if n == 1:
        rows = sims.age_rows(GROWN_UPS)
        text = 'Sim 1. Choose an age. The first Sim must be a young adult or older, to look after the home.'
    else:
        rows = sims.age_rows()
        text = f'Sim {n}. Choose an age.'
    core.choose(
        f'Sim {n}: age',
        text,
        rows,
        _got_age,
        tag='family',
        on_cancel=_family_menu if n > 1 else None,
    )


def _got_age(key: str) -> None:
    m = _Member()
    m.age = sims.age_enum(key)
    fam = _fam()
    fam.members.append(m)
    core.choose(
        f'Sim {len(fam.members)}: gender',
        'Choose a gender.',
        list(sims.GENDER_ROWS),
        _got_gender,
        tag='family',
        on_cancel=_drop_last,
    )


def _drop_last() -> None:
    fam = _fam()
    if fam.members:
        fam.members.pop()
    core.speak('That Sim was not added.')
    if fam.members:
        core.post(_family_menu)


def _got_gender(key: str) -> None:
    from sims.sim_info_types import Gender

    fam = _fam()
    m = fam.members[-1]
    m.gender = getattr(Gender, key)

    def got_first(first: str) -> None:
        if not first:
            core.post(_drop_last)
            return
        m.first = first
        core.post(_choose_traits)

    core.ask_text(f'Sim {len(fam.members)}: first name', 'Type the first name.', '', got_first)


def _choose_traits() -> None:
    m = _fam().members[-1]
    limit = sims.TRAIT_LIMITS.get(m.age.name, 3)
    token = core.active_sim().sim_info

    def done(chosen: list[Any]) -> None:
        m.traits = list(chosen)
        core.post(_choose_aspiration)

    sims.pick_traits(sims.TraitPick(f'{m.first}: traits', m.age, m.gender, limit, token, done))


def _choose_aspiration() -> None:
    m = _fam().members[-1]
    if m.age.name == 'TODDLER':
        core.post(_choose_relation)
        return
    tracks = sims.aspiration_tracks(sims.FakeSim(m.age, m.gender))
    if not tracks:
        core.post(_choose_relation)
        return
    token = core.active_sim().sim_info
    rows = [(t.display_text, core.with_sim(t.description_text, token), t) for t in tracks]

    def picked(track: Any) -> None:
        m.aspiration = track
        core.post(_choose_relation)

    core.choose(
        f'{m.first}: aspiration',
        'An aspiration is a life goal. It gives the Sim goals to work on and rewards. Escape skips it.',
        rows,
        picked,
        tag='family',
        on_cancel=_choose_relation,
    )


def _relation_options(new: _Member, old_index: int) -> list[tuple[str, str]]:
    """What the new Sim can be to an earlier Sim, as (label, kind)."""
    old = _fam().members[old_index]
    out = []
    if new.age.name in GROWN_UPS and old.age.name in GROWN_UPS:
        out.append((f'Spouse, married to {old.first}', 'spouse'))
    if _age_rank(new.age) < _age_rank(old.age) and old.age.name in GROWN_UPS:
        out.append((f'Child of {old.first}', 'child'))
    if _age_rank(new.age) > _age_rank(old.age) and new.age.name in GROWN_UPS:
        out.append((f'Parent of {old.first}', 'parent'))
    out.append((f'Brother or sister of {old.first}', 'sibling'))
    return out


def _is_married(i: int) -> bool:
    for k, x in enumerate(_fam().members[:-1]):
        if x.relation and x.relation[0] == 'spouse' and i in (k, x.relation[1]):
            return True
    return False


def _choose_relation() -> None:
    fam = _fam()
    m = fam.members[-1]
    if len(fam.members) == 1:
        core.post(_member_done)
        return
    rows: list[tuple[str, str | None, Relation | None]] = [
        ('No family tie, a roommate', 'Lives in the house but is not related.', None)
    ]
    for i in range(len(fam.members) - 1):
        rows.extend(
            (label, None, (kind, i))
            for label, kind in _relation_options(m, i)
            if not (kind == 'spouse' and _is_married(i))
        )

    def picked(value: Relation | None) -> None:
        m.relation = value
        core.post(_member_done)

    core.choose(
        f'{m.first}: family tie',
        f'How is {m.first} related to the family? Choose one.',
        rows,
        picked,
        tag='family',
        on_cancel=_member_done,
    )


def _member_done() -> None:
    m = _fam().members[-1]
    core.speak(f'{_member_line(m)} added.')
    core.post(_family_menu)


# ---------------------------------------------------------------- family menu


def _family_menu() -> None:
    fam = _fam()
    n = len(fam.members)
    rows: list[tuple[str, str | None, str]] = []
    if n < sims.MAX_HOUSEHOLD:
        rows.append(
            (
                'Add another Sim',
                f'The family has {sims.plural(n, "Sim")} now. A family can have up to 8.',
                'add',
            )
        )
    rows.append(('Done, choose a home', 'Choose an empty lot to build on, or a house.', 'home'))
    rows.append(('Hear the family', 'Everyone you have made so far.', 'list'))
    if n > 1:
        rows.append((f'Remove the last Sim, {fam.members[-1].first}', None, 'drop'))
    rows.append(('Cancel the whole family', 'Nothing is changed in the game.', 'cancel'))
    core.choose(
        f'The {fam.last} family',
        f'{sims.plural(n, "Sim")} so far. What next?',
        rows,
        _family_pick,
        tag='family',
        on_cancel=_family_cancelled,
    )


def _family_cancelled() -> None:
    core.speak(
        'Family menu closed. Your Sims are kept until you start a new family. Press Alt+C and choose Continue the new family.'
    )


def _hear_family() -> None:
    _speak_family()
    core.post(_family_menu)


def _drop_member() -> None:
    fam = _fam()
    gone = fam.members.pop()
    for m in fam.members:
        if m.relation and m.relation[1] >= len(fam.members):
            m.relation = None
    core.speak(f'{gone.first} removed.')
    core.post(_family_menu)


def _cancel_family() -> None:
    _state.family = None
    core.speak('New family cancelled. Nothing was changed.')


def _family_pick(value: str) -> None:
    actions: dict[str, Callable[[], None]] = {
        'add': _new_member,
        'home': _choose_lot_kind,
        'list': _hear_family,
        'drop': _drop_member,
        'cancel': _cancel_family,
    }
    action = actions.get(value)
    if action is not None:
        action()


def _relation_text(m: _Member) -> str:
    if not m.relation:
        return ''
    (kind, i) = m.relation
    return _RELATION_TEXT[kind].format(_fam().members[i].first)


def _speak_family() -> None:
    fam = _fam()
    core.speak(
        f'The {fam.last} family: '
        + '. '.join(_member_line(m) + _relation_text(m) for m in fam.members)
        + '.'
    )


def has_draft() -> bool:
    return _state.family is not None and bool(_state.family.members)


def resume() -> None:
    if has_draft():
        _family_menu()
    else:
        start()


# ---------------------------------------------------------------- lots


def _regions() -> dict[Any, bool]:
    """Region -> whether players can live there."""
    table = {}
    try:
        from ui.ui_tuning import UiTuning
        from world.region import Region

        for pack_data in UiTuning.PACK_SPECIFIC_DATA.values():
            for region_data in getattr(pack_data, 'region_list', ()) or ():
                inst = Region.REGION_DESCRIPTION_TUNING_MAP.get(region_data.region_resource)
                if inst is not None:
                    table[inst] = bool(getattr(region_data, 'is_player_facing', True))
    except Exception:  # every region counts as one players can live in
        simsight_log.error('plus family: regions')
    return table


def _lotprice_enc(original: Callable[..., Any]) -> Callable[..., Any]:
    """The lot price command, answering this module's requests and passing
    every other one to SimSight's own (simsight_travel)."""

    def a11yplus_lotprice_enc(payload: str = '', _connection: Any = None) -> Any:
        try:
            (zone, value, generation) = str(payload).split('|')
            if generation == _PRICE_GENERATION:
                _got_price(int(zone), float(value))
                return True
        except ValueError:  # not one of ours: SimSight's own command answers it
            pass
        return original(payload, _connection=_connection)

    # The game's console reads each argument's type from the annotations, which
    # `from __future__ import annotations` turns into strings: give it the real ones.
    a11yplus_lotprice_enc.__annotations__ = {'payload': str}
    return a11yplus_lotprice_enc


def _hook_prices() -> bool:
    if _state.hooked:
        return True
    try:
        import sims4.commands
        import simsight_travel as travel

        sims4.commands.Command(
            'simsight.lotprice_enc',
            'a11y.lotprice_enc',
            command_type=sims4.commands.CommandType.Live,
        )(_lotprice_enc(travel.a11y_lotprice_enc))
    except Exception:
        simsight_log.error('plus family: price hook')
        return False
    _state.hooked = True
    return True


def buying_price(rental: float, add: float, multiply: float) -> int | None:
    """The lot's buying price, to 10 simoleons, from the rental price the interface shows."""
    if rental <= 0 or not multiply:
        return None
    # The interface rounds the rental price down, so the true value is up to one simoleon higher.
    total = (rental + 0.5 - add) / multiply
    return int(round(total / 10.0)) * 10


def _got_price(zone_id: int, rental: float) -> None:
    try:
        from world.rentable_lot_tuning import RentableZoneTuning

        m = RentableZoneTuning.PRICE_MODIFIERS
        price = buying_price(rental, m.add, m.multiply)
    except Exception:  # the lot stays without a price
        simsight_log.error('plus family: price')
        return
    if price is not None:
        _state.prices[zone_id] = price


def _ask_prices(zone_ids: list[Any], then: Callable[[], object]) -> None:
    import distributor.ops
    import distributor.system

    if not _hook_prices():
        core.post(then)
        return
    for z in zone_ids:
        if z not in _state.prices:
            distributor.system.Distributor.instance().add_op_with_no_owner(
                distributor.ops.SendUIMessage(f'A11y|lotprice|{z}|{_PRICE_GENERATION}')
            )
    _state.price_wait = _PriceWait(time.monotonic() + _PRICE_WAIT_SECONDS, list(zone_ids), then)


def _price_tick() -> None:
    wait = _state.price_wait
    if wait is None:
        return
    if time.monotonic() >= wait.deadline or all(z in _state.prices for z in wait.zones):
        _state.price_wait = None
        wait.then()


core.add_ticker(0.3, _price_tick)


def _is_plex(zone_id: Any) -> bool:
    import services

    try:
        return bool(services.get_plex_service().is_zone_a_plex(zone_id))
    except Exception:  # no plex service in this game: not a plex
        return False


def _is_free_home(zone: Any, current: Any, regions: dict[Any, bool]) -> bool:
    """A residential lot nobody lives on, in a region players can live in, not a plex."""
    import services
    from world import region

    zone_id = zone.zone_id
    if zone_id == current or zone.household_id:
        return False
    venue = services.venue_service().get_venue_tuning(zone_id)
    if venue is None or not venue.is_residential:
        return False
    reg = region.get_region_instance_from_zone_id(zone_id)
    if reg is not None and regions.get(reg) is False:
        return False
    return not _is_plex(zone_id)


def _world_name(persistence: Any, worlds: dict[Any, str], nid: Any) -> str:
    if nid not in worlds:
        try:
            worlds[nid] = str(persistence.get_neighborhood_proto_buff(nid).name or '')
        except Exception:  # shown as 'Other town'
            worlds[nid] = ''
    return worlds[nid]


def _lot_entry(zone: Any, world: str) -> Lot:
    zone_id = zone.zone_id
    beds, baths = int(zone.bedroom_count), int(zone.bathroom_count)
    return {
        'zone_id': zone_id,
        'name': str(zone.name or 'Unnamed lot'),
        'world': world or 'Other town',
        'beds': beds,
        'baths': baths,
        'empty': beds == 0 and baths == 0,
        'price': _state.prices.get(zone_id),
    }


def _free_lot(
    zone: Any, persistence: Any, worlds: dict[Any, str], current: Any, regions: dict[Any, bool]
) -> Lot | None:
    """The zone as a free lot, or None when someone lives there or it is no home."""
    try:
        if not _is_free_home(zone, current, regions):
            return None
        return _lot_entry(zone, _world_name(persistence, worlds, zone.neighborhood_id))
    except Exception:  # a lot of an odd shape is left out
        simsight_log.error('plus family: free lot')
        return None


def free_lots() -> list[Lot]:
    """Every residential lot nobody lives on: dicts with zone_id, name, world, empty, rooms, price."""
    import services

    persistence = services.get_persistence_service()
    regions = _regions()
    current = services.current_zone_id()
    worlds: dict[Any, str] = {}
    out = []
    for zone in persistence.zone_proto_buffs_gen():
        lot = _free_lot(zone, persistence, worlds, current, regions)
        if lot is not None:
            out.append(lot)
    return out


def _choose_lot_kind() -> None:
    rows = [
        (
            'Empty lots, build your own house',
            'Cheap land with nothing on it. You build the house in Build mode with Alt+B.',
            True,
        ),
        ('Houses ready to live in', 'A built and furnished house. Costs more.', False),
    ]
    core.choose(
        'Choose a home',
        f'The family has {STARTING_FUNDS} simoleons to spend.',
        rows,
        _choose_lot,
        tag='family',
        on_cancel=_family_menu,
    )


def _lot_label(lot: Lot) -> str:
    if lot['empty']:
        rooms = ''
    else:
        rooms = ' {}, {}.'.format(
            sims.plural(lot['beds'], 'bedroom'), sims.plural(lot['baths'], 'bathroom')
        )
    price = (
        'about {} simoleons'.format(lot['price']) if lot['price'] is not None else 'price unknown'
    )
    return '{}, {}.{} {}'.format(lot['name'], lot['world'], rooms, price)


def _lot_description(lot: Lot) -> str:
    price = lot['price'] or 0
    if lot['price'] is None:
        return 'The price could not be found.'
    if price > STARTING_FUNDS:
        return f'Too expensive, the family has {STARTING_FUNDS} simoleons.'
    return f'{STARTING_FUNDS - price} simoleons left after buying.'


def _sort_key(lot: Lot) -> tuple[str, int]:
    """By town, then cheapest first; lots without a price last."""
    return (lot['world'], lot['price'] if lot['price'] is not None else 10**9)


def _choose_lot(empty: bool) -> None:
    try:
        lots = [lot for lot in free_lots() if lot['empty'] == empty]
    except Exception:  # said as no free lots
        simsight_log.error('plus family: free lots')
        lots = []
    if lots and any(lot['price'] is None for lot in lots) and _state.price_wait is None:
        core.speak('Getting prices.')
        _ask_prices([lot['zone_id'] for lot in lots], lambda: _choose_lot(empty))
        return
    lots.sort(key=_sort_key)
    if not lots:
        core.speak('There are no free {} in this game.'.format('empty lots' if empty else 'houses'))
        core.post(_choose_lot_kind)
        return
    core.choose(
        'Empty lots' if empty else 'Houses',
        f'{len(lots)} free. Sorted by town, cheapest first.',
        [(_lot_label(lot), _lot_description(lot), lot) for lot in lots],
        _picked_lot,
        tag='family',
        on_cancel=_choose_lot_kind,
    )


def _picked_lot(lot: Lot) -> None:
    fam = _fam()
    price = lot['price'] or 0
    if lot['price'] is None:
        core.speak('The price of that lot could not be found. Choose another one.')
        core.post(_choose_lot, lot['empty'])
        return
    if price > STARTING_FUNDS:
        core.speak('The family cannot afford that one.')
        core.post(_choose_lot, lot['empty'])
        return
    fam.lot = lot
    rows = [
        (
            f'Yes, move in and play the {fam.last} family',
            'The game loads the new home. This takes a minute, like any trip.',
            'yes',
        ),
        ('No, choose another home', None, 'no'),
    ]
    core.choose(
        'Move in?',
        'Move the {} family, {}, to {} in {} for {} simoleons. They keep {} simoleons.'.format(
            fam.last,
            sims.plural(len(fam.members), 'Sim'),
            lot['name'],
            lot['world'],
            price,
            STARTING_FUNDS - price,
        ),
        rows,
        _confirmed,
        tag='family',
        on_cancel=_choose_lot_kind,
    )


def _confirmed(value: Any) -> None:
    if value != 'yes':
        _choose_lot_kind()
        return
    try:
        _create_and_move()
    except Exception:
        simsight_log.error('plus family: create family')
        core.speak(
            'Sorry, something went wrong making the family. Nothing was moved. Send the SimSight_Plus.log file from the Mods folder for help.'
        )


# ---------------------------------------------------------------- create, move in, play


def _give_aspiration(info: Any, m: _Member) -> None:
    try:
        info.primary_aspiration = m.aspiration
        trait = getattr(m.aspiration, 'primary_trait', None)
        if trait is not None and not info.has_trait(trait):
            info.add_trait(trait)
    except Exception:  # the Sim starts without that aspiration
        simsight_log.error('plus family: aspiration ' + m.first)


def _validate_grandparents(info: Any) -> None:
    try:
        info.genealogy.validate_grandparent_relations()
    except Exception:  # family tree checks only: the family is made anyway
        simsight_log.error('plus family: grandparents')


def _set_up_household(fam: _Family, household: Any, infos: list[Any]) -> None:
    """Name, traits, aspirations and family ties, in the order the game expects."""
    try:
        household.name = fam.last
    except Exception:
        simsight_log.error('plus family: family name')
    for m, info in zip(fam.members, infos):
        sims.set_exact_traits(info, m.traits)
        if m.aspiration is not None:
            _give_aspiration(info, m)
    _set_relations(fam, infos)
    try:
        household.set_default_relationships()
    except Exception:
        simsight_log.error('plus family: default relationships')
    for info in infos:
        _validate_grandparents(info)
    try:
        household.set_played_household(True)
    except Exception:
        simsight_log.error('plus family: played household')


def _create_and_move() -> None:
    import distributor.ops
    import distributor.system
    from sims.sim_spawner import SimCreator, SimSpawner

    fam = _fam()
    lot = fam.lot
    if lot is None:
        raise LookupError('no home chosen for the new family')
    zone_id = lot['zone_id']
    client = core.client()
    funds = STARTING_FUNDS - (lot['price'] or 0)
    creators = [
        SimCreator(
            gender=m.gender, age=m.age, first_name=m.first, last_name=fam.last, traits=m.traits
        )
        for m in fam.members
    ]
    (infos, household) = SimSpawner.create_sim_infos(
        creators,
        starting_funds=funds,
        account=client.account,
        zone_id=zone_id,
        family_name=fam.last,
        creation_source='SimSight Plus: new family',
    )
    core.log(f'new family {fam.last} household {household.id} sims {[i.sim_id for i in infos]}')
    _set_up_household(fam, household, infos)
    household.move_into_zone(zone_id)
    core.log(f'moved household {household.id} into zone {zone_id}')
    core.speak(
        'The {} family is moving in to {}. Loading the new home, please wait.'.format(
            fam.last, lot['name']
        )
    )
    _state.arrival = _Arrival(household.id, zone_id, lot['empty'])
    op = distributor.ops.TravelLiveToNhdToLive(infos[0].sim_id, household.id, 0)
    distributor.system.Distributor.instance().add_op_with_no_owner(op)
    _state.family = None


def _parent_index(gender: Any) -> Any:
    """MOTHER for a female parent, FATHER otherwise."""
    from sims.genealogy_tracker import FamilyRelationshipIndex
    from sims.sim_info_types import Gender

    if gender == Gender.FEMALE:
        return FamilyRelationshipIndex.MOTHER
    return FamilyRelationshipIndex.FATHER


def _by_id(infos: list[Any], sim_id: Any) -> Any:
    return next((x for x in infos if x.id == sim_id), None)


def _marry(me: Any, other: Any) -> None:
    from relationships.global_relationship_tuning import RelationshipGlobalTuning

    me.update_spouse_sim_id(other.id)
    other.update_spouse_sim_id(me.id)
    bit = RelationshipGlobalTuning.MARRIAGE_RELATIONSHIP_BIT
    me.relationship_tracker.add_relationship_bit(other.id, bit, force_add=True)
    other.relationship_tracker.add_relationship_bit(me.id, bit, force_add=True)


def _make_child(parent: Any, child: Any, infos: list[Any]) -> None:
    """child of parent, and of parent's spouse when that is the other parent kind."""
    rel = _parent_index(parent.gender)
    child.set_parent_relation(rel, parent)
    spouse = _by_id(infos, parent.spouse_sim_id)
    if spouse is not None:
        rel2 = _parent_index(spouse.gender)
        if rel2 != rel:
            child.set_parent_relation(rel2, spouse)


def _make_siblings(me: Any, other: Any, infos: list[Any]) -> None:
    """me shares other's parents; with none in the family, the game gives them shared ones."""
    from sims.genealogy_tracker import FamilyRelationshipIndex

    shared = False
    for rel in (FamilyRelationshipIndex.MOTHER, FamilyRelationshipIndex.FATHER):
        pid = other.genealogy.get_parent_relation(rel)
        parent = _by_id(infos, pid) if pid else None
        if parent is not None:
            me.set_parent_relation(rel, parent)
            shared = True
    if not shared:
        me.genealogy.set_parent_of_parentless_siblings(other)


def _relate(kind: str, me: Any, other: Any, infos: list[Any]) -> None:
    if kind == 'spouse':
        _marry(me, other)
    elif kind == 'child':
        _make_child(other, me, infos)
    elif kind == 'parent':
        _make_child(me, other, infos)
    elif kind == 'sibling':
        _make_siblings(me, other, infos)


def _relate_member(m: _Member, me: Any, infos: list[Any]) -> None:
    if not m.relation:
        return
    (kind, j) = m.relation
    try:
        _relate(kind, me, infos[j], infos)
    except Exception:  # the Sims are made without that tie
        simsight_log.error(f'plus family: relation {kind} {m.first}')


def _add_second_parent(child: Any, infos: list[Any]) -> None:
    """A child of one married parent is also the child of the other parent."""
    try:
        _add_second_parent_kinds(child, infos)
    except Exception:  # the child keeps one parent
        simsight_log.error('plus family: second parent')


def _add_second_parent_kinds(child: Any, infos: list[Any]) -> None:
    from sims.genealogy_tracker import FamilyRelationshipIndex

    for rel in (FamilyRelationshipIndex.MOTHER, FamilyRelationshipIndex.FATHER):
        pid = child.genealogy.get_parent_relation(rel)
        parent = _by_id(infos, pid) if pid else None
        spouse = next(
            (x for x in infos if parent is not None and x.id == parent.spouse_sim_id), None
        )
        if spouse is None:
            continue
        rel2 = _parent_index(spouse.gender)
        if rel2 != rel and child.genealogy.get_parent_relation(rel2) is None:
            child.set_parent_relation(rel2, spouse)


def _set_relations(fam: _Family, infos: list[Any]) -> None:
    for i, m in enumerate(fam.members):
        _relate_member(m, infos[i], infos)
    for child in infos:
        _add_second_parent(child, infos)


# ---------------------------------------------------------------- arrival


def _lot_size_text() -> str:
    import services

    try:
        lot = services.active_lot()
        return f' It is {int(lot.size_x)} by {int(lot.size_z)} meters.'
    except Exception:  # said without the size
        return ''


@core.on_zone_started
def _arrived() -> None:
    arrival = _state.arrival
    if arrival is None:
        return
    import services

    if services.current_zone_id() != arrival.zone_id:
        return
    _state.arrival = None
    household = services.household_manager().get(arrival.household_id)
    if household is None:
        return
    try:
        money = int(household.funds.money)
    except Exception:  # said as 0
        money = 0
    if arrival.empty:
        tip = (
            'The lot is empty.'
            + _lot_size_text()
            + ' Press F2 for Build mode, then Alt+B for the build helper. A good start is Catalog, Furnished rooms.'
        )
    else:
        tip = 'Press Alt+R to hear the rooms of your new house.'
    core.speak(f'Welcome home, {household.name} family. You have {money} simoleons. {tip}')

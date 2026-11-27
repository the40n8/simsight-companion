"""SimSight Plus: make and edit Sims without Create a Sim (Alt+C).

New Sims join the active household with a random look made by the game.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_plus_core as core

if TYPE_CHECKING:
    from typing import Callable

    from sims.household import Household
    from sims.sim import Sim
    from sims.sim_info import SimInfo

    Row = tuple[Any, ...]  # (label, description, value[, selected]) for core.choose

TRAIT_LIMITS = {'TODDLER': 1, 'CHILD': 1, 'TEEN': 2, 'YOUNGADULT': 3, 'ADULT': 3, 'ELDER': 3}
AGE_CHOICES = (
    ('Young Adult', 'YOUNGADULT', 'The usual age to start with. Can have a full career.'),
    ('Adult', 'ADULT', 'Grown up. Can have a full career.'),
    ('Elder', 'ELDER', 'Older Sim. Can still work, will die of old age eventually.'),
    ('Teen', 'TEEN', 'Goes to high school. Can have a part time job.'),
    ('Child', 'CHILD', 'Goes to grade school. Needs a grown up in the household.'),
    ('Toddler', 'TODDLER', 'Very young. Needs lots of care from a grown up.'),
)
GENDER_ROWS = (('Female', None, 'FEMALE'), ('Male', None, 'MALE'))
MAX_HOUSEHOLD = 8


def plural(n: int, word: str) -> str:
    """'1 trait', '2 traits'."""
    return '{} {}{}'.format(n, word, '' if n == 1 else 's')


class _Draft:
    """The new Sim being made, step by step."""

    def __init__(self) -> None:
        self.age: Any = None
        self.gender: Any = None
        self.first = ''
        self.last = ''
        self.traits: list[Any] = []


class FakeSim:
    """Stands in for a Sim when checking which traits fit an age and gender."""

    def __init__(self, age: Any, gender: Any) -> None:
        from sims.sim_info_types import Species

        self.age = age
        self.gender = gender
        self.species = Species.HUMAN


class _State:
    def __init__(self) -> None:
        self.draft = _Draft()


_state = _State()


def age_enum(name: str) -> Any:
    from sims.sim_info_types import Age

    return getattr(Age, name)


def age_name(age: Any) -> str:
    for label, key, _ in AGE_CHOICES:
        if age_enum(key) == age:
            return label
    return str(age)


def _gender_enum(name: str) -> Any:
    from sims.sim_info_types import Gender

    return getattr(Gender, name)


def age_rows(keys: tuple[str, ...] | None = None) -> list[Row]:
    """AGE_CHOICES as list rows (all ages, or only those in keys)."""
    return [(label, desc, key) for (label, key, desc) in AGE_CHOICES if keys is None or key in keys]


def _is_personality_trait(trait: Any, fake: FakeSim) -> bool:
    try:
        if not trait.is_personality_trait:
            return False
        if getattr(trait, 'is_npc_only', False):
            return False
        if trait.display_name is None:
            return False
        if not trait.is_valid_trait(fake):
            return False
        name = trait.__name__.lower()
    except Exception:  # a trait of an odd shape is left out
        simsight_log.error('plus sims: trait')
        return False
    return 'reward' not in name and 'hos_' not in name


def personality_traits(age: Any, gender: Any) -> list[Any]:
    import services
    import sims4.resources

    fake = FakeSim(age, gender)
    manager = services.get_instance_manager(sims4.resources.Types.TRAIT)
    out = [trait for trait in manager.types.values() if _is_personality_trait(trait, fake)]
    out.sort(key=lambda t: t.__name__.lower().replace('trait_', ''))
    return out


class TraitPick:
    """What pick_traits asks for: list title, the Sim's age and gender, how
    many traits, a Sim for the game text, and what to do with the choice."""

    def __init__(
        self,
        title: str,
        age: Any,
        gender: Any,
        limit: int,
        token_sim: Any,
        on_done: Callable[[list[Any]], object],
    ) -> None:
        self.title = title
        self.age = age
        self.gender = gender
        self.limit = limit
        self.token_sim = token_sim
        self.on_done = on_done


def pick_traits(pick: TraitPick, chosen: list[Any] | None = None) -> None:
    """Choose traits one at a time with Enter (no Done button needed).
    Traits that clash with ones already chosen are left out."""
    chosen = list(chosen or [])
    if len(chosen) >= pick.limit:
        pick.on_done(chosen)
        return
    available = [
        t
        for t in personality_traits(pick.age, pick.gender)
        if t not in chosen and not _conflict([*chosen, t])
    ]
    rows = _trait_rows(available, pick.token_sim)
    if chosen:
        # At the end, so pressing Enter on the first row never finishes by accident.
        rows.append(
            (
                'Done, keep ' + plural(len(chosen), 'trait'),
                'Finish without choosing more traits.',
                'done',
            )
        )

    def picked(value: Any) -> None:
        if value == 'done':
            pick.on_done(chosen)
            return
        chosen.append(value)
        if len(chosen) >= pick.limit:
            pick.on_done(chosen)
        else:
            core.post(pick_traits, pick, chosen)

    if chosen:
        hint = 'To stop here, choose Done at the end of the list.'
    else:
        hint = 'Press Enter on a trait to choose it. Traits that clash with ones already chosen are left out.'
    core.choose(
        pick.title, f'Trait {len(chosen) + 1} of {pick.limit}. {hint}', rows, picked, tag='sims'
    )


def _trait_rows(traits: list[Any], token_sim: Any) -> list[Row]:
    rows: list[Row] = []
    for t in traits:
        try:
            name = t.display_name(token_sim)
        except Exception:  # EA's text factory: the tuning name instead (logged)
            simsight_log.error('plus sims: trait name')
            name = core.raw(t.__name__)
        try:
            desc = t.trait_description(token_sim) if t.trait_description is not None else None
        except Exception:  # EA's text factory: no description (logged)
            simsight_log.error('plus sims: trait description')
            desc = None
        rows.append((name, desc, t, False))
    return rows


def set_exact_traits(info: SimInfo, chosen: list[Any]) -> None:
    """The game fills new Sims up with random traits. Keep exactly the chosen ones."""
    chosen = list(chosen)
    try:
        for t in list(info.trait_tracker.personality_traits):
            if t not in chosen:
                info.remove_trait(t)
        for t in chosen:
            if not info.has_trait(t):
                info.add_trait(t)
    except Exception:
        simsight_log.error('plus sims: set traits')


def _conflict(traits: list[Any]) -> bool:
    for a in traits:
        for b in traits:
            if a is not b and (a.is_conflicting(b) or b.is_conflicting(a)):
                return True
    return False


# ---------------------------------------------------------------- main menu


@core.hotkey(ord('C'), 'create and edit Sims')
def open_menu() -> None:
    sim = core.active_sim()
    if sim is None:
        core.speak('No active Sim.')
        return
    name = sim.sim_info.first_name
    import simsight_plus_family as family

    rows = [
        (
            'Continue the new family' if family.has_draft() else 'Start a brand-new family',
            'Make your own Sims from scratch, choose an empty lot or a house, and play them.',
            'family',
        ),
        (
            'Add a new Sim to this household',
            'Choose age, gender, name and traits. The game gives the Sim a random look.',
            'new',
        ),
        (f"Change {name}'s name", 'Type a new first and last name.', 'name'),
        (f"Change {name}'s traits", 'Choose new personality traits.', 'traits'),
        (f"Change {name}'s aspiration", 'Choose a new life goal.', 'aspiration'),
        (f'Describe {name}', 'Age, gender, traits, aspiration and career.', 'describe'),
        (
            f"Open the game's Create a Sim for {name}",
            'The full game screen, mostly needs a mouse. Use it only with sighted help.',
            'cas',
        ),
    ]
    core.choose('Create and edit Sims', 'Choose what to do.', rows, _menu_pick, tag='sims')


def _resume_family(_sim: Any) -> None:
    import simsight_plus_family as family

    family.resume()


def _open_cas(sim: Sim) -> None:
    import sims4.commands

    client = core.client()
    if client is None:
        core.speak('Could not open Create a Sim.')
        return
    try:
        sims4.commands.client_cheat(
            f'sims.exit2cas {sim.id} {sim.sim_info.household_id} {sim.id}', client.id
        )
    except Exception:
        simsight_log.error('plus sims: open cas')
        core.speak('Could not open Create a Sim.')


_MENU: dict[str, Callable[[Sim], None]] = {
    'family': _resume_family,
    'new': lambda _sim: start_new(),
    'name': lambda sim: _rename(sim.sim_info),
    'traits': lambda sim: _edit_traits(sim.sim_info),
    'aspiration': lambda sim: choose_aspiration(sim.sim_info),
    'describe': lambda sim: describe(sim.sim_info),
    'cas': _open_cas,
}


def _menu_pick(value: str) -> None:
    sim = core.active_sim()
    if sim is None:
        return
    action = _MENU.get(value)
    if action is not None:
        action(sim)


# ---------------------------------------------------------------- describe


def _gender_word(gender: Any) -> str:
    return 'female' if gender == _gender_enum('FEMALE') else 'male'


def _add_career_rows(info: SimInfo, rows: list[Row]) -> None:
    for career in info.career_tracker.careers.values():
        try:
            title = career.current_level_tuning.get_title(info)
        except Exception:  # EA's career tuning: the career's name instead (logged)
            simsight_log.error('plus sims: career title')
            title = None
        rows.append((title if title is not None else career.display_name(info), 'Career', None))


def _about_rows(info: SimInfo) -> list[Row]:
    """Traits, aspiration and career: game text, shown as a list the reader can read."""
    rows: list[Row] = []
    try:
        rows.extend(
            (t.display_name(info), 'Trait', None) for t in info.trait_tracker.personality_traits
        )
    except Exception:
        simsight_log.error('plus sims: describe traits')
    try:
        asp = info.primary_aspiration
        if asp is not None:
            rows.append((asp.display_text, 'Aspiration', None))
    except Exception:
        simsight_log.error('plus sims: describe aspiration')
    try:
        _add_career_rows(info, rows)
    except Exception:
        simsight_log.error('plus sims: describe career')
    return rows


def describe(info: SimInfo) -> None:
    parts = [core.sim_name(info), age_name(info.age)]
    try:
        parts.append(_gender_word(info.gender))
    except Exception:  # said without the gender
        simsight_log.error('plus sims: describe gender')
    core.speak(', '.join(parts) + '.')
    rows = _about_rows(info)
    if rows:
        core.choose(
            f'About {info.first_name}',
            f'{core.sim_name(info)}. {age_name(info.age)}.',
            rows,
            lambda _value: None,
            tag='sims',
        )


# ---------------------------------------------------------------- rename


def _rename(info: SimInfo) -> None:
    def got_first(first: str) -> None:
        if not first:
            core.speak('Name not changed.')
            return

        def got_last(last: str) -> None:
            info.first_name = first
            if last:
                info.last_name = last
            try:
                info.resend_full_name()
            except Exception:  # the name is set; the interface shows it later
                simsight_log.error('plus sims: resend name')
            core.speak(f'Renamed to {core.sim_name(info)}.')

        core.ask_text('Last name', 'Type the last name.', info.last_name, got_last)

    core.ask_text('First name', 'Type the first name.', info.first_name, got_first)


# ---------------------------------------------------------------- traits


def _replace_traits(info: SimInfo, current: list[Any], chosen: list[Any]) -> None:
    for t in current:
        if t not in chosen and info.has_trait(t):
            info.remove_trait(t)
    for t in chosen:
        if not info.has_trait(t):
            info.add_trait(t)
    core.speak(
        'Traits changed. {} has {} new {}.'.format(
            info.first_name, len(chosen), 'trait' if len(chosen) == 1 else 'traits'
        )
    )


def _edit_traits(info: SimInfo) -> None:
    limit = TRAIT_LIMITS.get(info.age.name, 3)
    try:
        current = list(info.trait_tracker.personality_traits)
    except Exception:  # no trait tracker: no current traits
        simsight_log.error('plus sims: current traits')
        current = []

    rows: list[Row] = [
        (
            'Choose new traits',
            f"Replaces all of {info.first_name}'s current traits. You choose them one at a time.",
            'new',
        )
    ]
    rows.extend((t.display_name(info), 'Current trait', None) for t in current)

    def start(value: Any) -> None:
        if value == 'new':
            pick_traits(
                TraitPick(
                    f'New traits for {info.first_name}',
                    info.age,
                    info.gender,
                    limit,
                    info,
                    lambda chosen: _replace_traits(info, current, chosen),
                )
            )

    core.choose(
        f'Traits for {info.first_name}',
        f"{info.first_name} has these traits now. Choose 'Choose new traits' to replace them.",
        rows,
        start,
        tag='sims',
    )


# ---------------------------------------------------------------- aspiration


def _is_offered(track: Any, info: Any) -> bool:
    try:
        if getattr(track, 'is_hidden_unlockable', False):
            return False
        if track.__name__.lower().startswith('ftue'):
            return False  # the tutorial's own aspirations
        return bool(track.is_available() and track.is_valid_for_sim(info))
    except Exception:  # an aspiration of an odd shape is left out
        simsight_log.error('plus sims: aspiration track')
        return False


def aspiration_tracks(info: Any) -> list[Any]:
    """The aspirations a Sim (or a FakeSim) can choose, sorted as the game sorts them."""
    import services
    import sims4.resources

    manager = services.get_instance_manager(sims4.resources.Types.ASPIRATION_TRACK)
    out = [track for track in manager.types.values() if _is_offered(track, info)]
    out.sort(key=lambda t: (getattr(t.category, 'ui_sort_order', 0), t.__name__))
    return out


def _set_aspiration(info: SimInfo, track: Any) -> None:
    old = info.primary_aspiration
    old_trait = getattr(old, 'primary_trait', None) if old is not None else None
    if old_trait is not None and info.has_trait(old_trait):
        info.remove_trait(old_trait)
    info.primary_aspiration = track
    trait = getattr(track, 'primary_trait', None)
    if trait is not None and not info.has_trait(trait):
        info.add_trait(trait)


def choose_aspiration(info: SimInfo, then: Callable[[], object] | None = None) -> None:
    tracks = aspiration_tracks(info)
    if not tracks:
        core.speak('There are no aspirations for this age.')
        if then:
            core.post(then)
        return
    current = getattr(info, 'primary_aspiration', None)
    rows = [
        (
            track.display_text,
            core.with_sim(track.description_text, info),
            track,
            track is current,
        )
        for track in tracks
    ]

    def picked(track: Any) -> None:
        try:
            _set_aspiration(info, track)
            core.speak('Aspiration set.')
        except Exception:
            simsight_log.error('plus sims: set aspiration')
            core.speak('Could not set the aspiration.')
        if then:
            core.post(then)

    core.choose(
        f'Aspiration for {info.first_name}',
        'An aspiration is a life goal. It gives the Sim goals to work on and rewards.',
        rows,
        picked,
        tag='sims',
        on_cancel=then,
    )


# ---------------------------------------------------------------- new Sim


def start_new() -> None:
    import services

    hh: Household | None = services.active_household()
    if hh is None:
        core.speak('Load a household first.')
        return
    if hh.household_size >= MAX_HOUSEHOLD:
        core.speak('This household already has 8 Sims, the most the game allows.')
        return
    _state.draft = _Draft()
    core.choose('New Sim: age', 'Step 1 of 5. Choose an age.', age_rows(), _got_age, tag='sims')


def _got_age(key: str) -> None:
    _state.draft.age = age_enum(key)
    core.choose(
        'New Sim: gender',
        'Step 2 of 5. Choose a gender.',
        list(GENDER_ROWS),
        _got_gender,
        tag='sims',
    )


def _default_last_name() -> str:
    """The active Sim's last name, else the household's name."""
    import services

    hh: Household | None = services.active_household()
    try:
        sim = core.active_sim()
        if sim is not None:
            return str(sim.sim_info.last_name)
        return str((hh.name if hh is not None else '') or '')
    except Exception:  # EA's name fields: no name to offer (logged)
        simsight_log.error('plus sims: default last name')
        return ''


def _got_gender(key: str) -> None:
    _state.draft.gender = _gender_enum(key)

    def got_first(first: str) -> None:
        if not first:
            core.speak('A first name is needed. Cancelled.')
            return
        _state.draft.first = first
        default_last = _default_last_name()

        def got_last(last: str) -> None:
            _state.draft.last = last or default_last
            _choose_new_traits()

        core.ask_text('New Sim: last name', 'Step 4 of 5. Last name.', default_last, got_last)

    core.ask_text('New Sim: first name', 'Step 3 of 5. Type the first name.', '', got_first)


def _choose_new_traits() -> None:
    d = _state.draft
    limit = TRAIT_LIMITS.get(d.age.name, 3)
    sim = core.active_sim()
    if sim is None:  # the Sim went away while the dialogs were open
        core.speak('No active Sim.')
        return
    token = sim.sim_info

    def done(chosen: list[Any]) -> None:
        d.traits = list(chosen)
        core.post(_create)

    pick_traits(TraitPick('New Sim: traits, step 5 of 5', d.age, d.gender, limit, token, done))


def _create() -> None:
    import services
    from sims.sim_spawner import SimCreator, SimSpawner

    d = _state.draft
    hh: Household | None = services.active_household()
    client = core.client()
    sim = core.active_sim()
    if client is None:
        core.speak('Sorry, the game could not create the Sim.')
        return
    try:
        creator = SimCreator(
            gender=d.gender, age=d.age, first_name=d.first, last_name=d.last, traits=d.traits
        )
        (infos, _) = SimSpawner.create_sim_infos(
            [creator],
            household=hh,
            tgt_client=client,
            account=client.account,
            zone_id=services.current_zone_id(),
            creation_source='SimSight Plus: new Sim',
        )
        info = infos[0]
        set_exact_traits(info, d.traits)
        position = sim.position if sim is not None else None
        SimSpawner.spawn_sim(info, position)
        client.add_selectable_sim_info(info)
        core.log(f'created sim {core.sim_name(info)} ({info.sim_id})')
        core.speak(f'{core.sim_name(info)} has joined the household.')
    except Exception:
        simsight_log.error('plus sims: create sim')
        core.speak('Sorry, the game could not create the Sim.')
        return
    if d.age.name not in ('TODDLER', 'INFANT', 'BABY'):
        core.post(choose_aspiration, info)

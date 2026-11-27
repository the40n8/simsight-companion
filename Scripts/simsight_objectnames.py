"""Give gameplay objects a hover tip so their names can be narrated.

The game client only sends hover-tip messages to the UI for objects that
declare a hover-tip style (gardening, crafted food, owned/heirloom items...).
Plain furniture declares none, so nothing appears and nothing can be spoken.

Two stamping paths, chosen by whether EA gave the object a tooltip component
(77% of tuned objects have one):

- WITHOUT a tooltip component: set `hover_tip` and write a recipe_name ui
  metadata entry directly, then push it. Nothing else manages these objects'
  tooltips, so the direct write is safe.
- WITH a tooltip component: the component owns the metadata lifecycle. It
  rebuilds lazily on the client's first-hover request, tracks its own
  handles, and DISABLES the hover tip whenever it ends up with no handles of
  its own (GameObject.has_ui_metadata_handles is component-forwarded, so
  handles written directly are invisible to that check and the tip dies on
  first hover; writing directly also duplicates recipe_name against the
  component's own fields on rich objects). Route the name through EA's
  external-field channel instead (update_tooltip_field): the rebuild merges
  external fields only where the component produced nothing itself, so EA's
  rich data always wins and our name only fills the gap. Never distribute a
  hover_tip style on these objects before their first-hover handshake: the
  engine then believes the tooltip already exists and never requests the
  rebuild, killing EA's own tips (assigned beds). The style for name-only
  objects is applied AFTER each rebuild by a wrap on update_object_tooltip.

Only objects with at least one player-facing interaction get a name; rugs,
curtains and other pure deco stay clean. An interaction counts if it is not
debug/cheat, is visible, allows user direction, and is not in the frozen
exclusion set of situational universals that EA tunes onto swaths of
otherwise inert objects. The set was computed by tools/affordance_census.py
over all 6,755 object tunings (game 1.126, 2026-08-01); regenerate it after
major EA patches.

PLAYER-VISIBLE: this makes a small name tooltip appear when hovering gameplay
objects that previously had none. Toggle with the `simsight.objectnames` cheat
command; the setting persists for the session.

The simsight.objectnames on/off choice persists via the settings file
(`object_names` key).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import services
import sims4.commands
from indexed_manager import CallbackTypes
from objects.components import types as component_types
from objects.hovertip import TooltipFields
from protocolbuffers import UI_pb2 as ui_protocols
from sims4.localization import LocalizationHelperTuning

from simsight_commands import real_annotations
import simsight_log
import simsight_patch
import simsight_settings as settings

if TYPE_CHECKING:
    from objects.components.inventory_item import InventoryItemComponent
    from objects.components.tooltip_component import TooltipComponent
    from zone import Zone


_DIAG_MAX_OBJECTS = 8  # simsight.objdiag lists at most this many matches


def _speak(text: str) -> None:
    # Only used for command feedback, which must talk in every speech mode.
    try:
        import simsight_speech as speech
    except ImportError:
        return  # no speech module: the cheat console output still answers
    speech.speak(text, explicit=True)


# Style 2 renders name (+ optional description) in the standard hover panel.
_STYLE = ui_protocols.UiObjectMetadata.HOVER_TIP_CONSUMABLE_CRAFTABLE

# Situational universals: interactions EA tunes onto broad swaths of unrelated
# objects that only apply with a special actor (ghost possession/powers, fame
# perks), a special object state (burnt/scratched replacement), or dev tooling
# (simRay). Their presence does not make an object "interactive".
_EXCLUDED_AFFORDANCES = frozenset(
    (
        74457,  # fire_ReplaceBurntObject
        102255,  # Ghost_Possess_Repair
        102258,  # Ghost_Possess
        102433,  # Ghost_Possess_Break_Electrocution
        102774,  # Ghost_Possess_Break_Plumbing
        105971,  # simRay_Frozen_View
        106094,  # simRay_MindControlReaction_Panicked
        106095,  # simRay_MindControlReaction_Escape
        106859,  # simRay_Transform_Object
        116720,  # simRay_Transform_Object_WallObject
        121648,  # Ghost_Possess_Bonfire
        143965,  # Ghost_Possess_BubbleBlower
        163419,  # scratch_ReplaceScratchedObject
        168502,  # Ghost_Pet_Possess
        168664,  # simRay_ChangeClothes_Upgrade_CompletedDisplay
        168665,  # simRay_Clean_Upgrade_CompletedDisplay
        168666,  # simRay_Eat_Upgrade_CompletedDisplay
        168667,  # simRay_Panic_Upgrade_CompletedDisplay
        168668,  # simRay_Sit_Upgrade_CompletedDisplay
        168669,  # simRay_Sleep_Upgrade_CompletedDisplay
        168670,  # simRay_TransformObject_Upgrade_CompletedDisplay
        168671,  # simRay_TransformSim_Upgrade_CompletedDisplay
        182291,  # Ghost_Possess_Fire
        194245,  # famePerks_Influencer_OpenEnvelope
        194246,  # famePerks_Influencer_OpenPackage
        196759,  # famePerks_GoodReputation_GivingBack_DonateObject
        196854,  # famePerks_Influencer_OpenEnvelope_FromWorld
        196855,  # famePerks_Influencer_OpenPackage_FromWorld
        260638,  # simRay_Transform_Object_ScientistVenueGrassOnly
        324988,  # Ghost_Horse_Possess
        385718,  # ghostPowers_Abilities_Chaos_ProduceMaterial
        385719,  # ghostPowers_Abilities_Helpful_RemoveMaterial
        391304,  # ghostPowers_Abilities_Scary_Self_FlickerLights_Room
        391312,  # ghostPowers_Abilities_Scary_MusicalPossess
        392250,  # ghostPowers_Abilities_Scary_GhostlyBang_Terrain
        393624,  # ghostPowers_Abilities_Comfort_ImproveQuality
        393625,  # ghostPowers_Abilities_Comfort_Unspoil
        393630,  # ghostPowers_Abilities_Comfort_NurturePlantLife
        393658,  # ghostPowers_Abilities_Helpful_NoPossess_Instantly_Clean
        393662,  # ghostPowers_Abilities_Helpful_Instantly_NoPossess_Repair
        393663,  # ghostPowers_Abilities_Helpful_Instantly_Possess_Repair
        393664,  # ghostPowers_Abilities_Helpful_Instantly_Possess_Upgrade
        395021,  # ghostPowers_WellofSouls_AbilityReset
        395070,  # ghostPowers_Abilities_Helpful_RemoveMaterial_EmptyTrash
        415212,  # ghostPowers_Abilities_Helpful_RemoveMaterial_Mold
    )
)


class _State:
    """What this module remembers while the game runs."""

    def __init__(self) -> None:
        self.enabled = bool(settings.get('object_names', True))
        self.registered = False  # our ON_OBJECT_ADD callback is registered
        self.stamped: set[int] = set()  # ids of objects we gave a name


_state = _State()


def enabled() -> bool:
    """Whether object names are on (the mod menu and the narrator read it)."""
    return _state.enabled


def _is_player_affordance(sa: Any) -> bool:
    """Mirrors the pie menu's own no-shift gate (not debug and not cheat),
    plus visibility/user-direction, minus the situational universals."""
    try:
        return not (
            sa.debug
            or sa.cheat
            or not sa.visible
            or not sa.allow_user_directed
            or sa.guid64 in _EXCLUDED_AFFORDANCES
        )
    except AttributeError:
        return False


def _has_interactions(obj: Any) -> bool:
    """True if the object offers at least one player-facing interaction.
    super_affordances() covers both the tuned list and component-provided
    affordances; None context is safe (the behavior-affordance leg guards
    on it)."""
    try:
        return any(_is_player_affordance(sa) for sa in obj.super_affordances())
    except Exception:
        simsight_log.error('objectnames: affordance check failed')
    return False


def _is_visible_inventory_item(obj: Any) -> bool:
    # A visible inventory item needs a readable name even when its
    # affordances are inventory-only. Hidden storage is not a UI item.
    component = getattr(obj, 'inventoryitem_component', None)
    return component is not None and not component.is_hidden and component.is_in_inventory()


def _should_stamp(obj: Any, force: bool = False, inventory: bool = False) -> bool:
    if obj is None:
        return False
    if not force and obj.id in _state.stamped:
        return False
    # Never override a tip the game already provides (gardening, crafted,
    # heirlooms, collectibles); those are richer than a bare name.
    if obj.is_sim or obj.hover_tip or obj.definition is None:
        return False
    if inventory:
        return _is_visible_inventory_item(obj)
    return _has_interactions(obj)


def stamp(obj: Any, force: bool = False, inventory: bool = False) -> bool:
    """Give one object a name tip; True when it got one."""
    if not _should_stamp(obj, force, inventory):
        return False
    try:
        name = LocalizationHelperTuning.get_object_name(obj)
        if name is None:
            return False
        if obj.get_component(component_types.TOOLTIP_COMPONENT) is not None:
            # External-field channel: merged into the component's lazy
            # first-hover rebuild only where it produced nothing itself.
            # Do NOT touch hover_tip here: if a style reaches the client
            # before the first-hover handshake, the engine believes the
            # tooltip already exists and never requests the rebuild, so EA
            # content (assigned-bed tips) never materializes. The style for
            # name-only objects is applied post-rebuild by the
            # update_object_tooltip wrap installed below.
            obj.update_tooltip_field(TooltipFields.recipe_name, name, should_update=True)
        else:
            obj.hover_tip = _STYLE
            obj.add_ui_metadata('recipe_name', name)
            obj.update_ui_metadata()
        _state.stamped.add(obj.id)
    except Exception:
        simsight_log.error('objectnames: stamp failed')
        return False
    return True


def _on_object_add(obj: Any) -> None:
    # Called by the object manager for every new object: must never raise.
    try:
        if _state.enabled:
            stamp(obj)
    except Exception:
        simsight_log.error('objectnames: object add')


def _stamp_manager(manager: Any, force: bool, inventory: bool) -> int:
    if manager is None:
        return 0
    return sum(1 for obj in tuple(manager.values()) if stamp(obj, force, inventory=inventory))


def stamp_all(force: bool = False) -> int:
    """Stamp everything on the lot.

    `force` re-applies to objects we stamped before whose tip has since been
    cleared (a tooltip component rebuild that came up empty disables the tip
    again if our external field was not yet in place).
    """
    manager = services.object_manager()
    if manager is None:
        return 0
    return _stamp_manager(manager, force, inventory=False) + stamp_inventory(force)


def stamp_inventory(force: bool = False) -> int:
    """Use the existing lazy tooltip path for visible inventory names too."""
    return _stamp_manager(services.inventory_manager(), force, inventory=True)


def enable(on: bool) -> int:
    """Turn names on (stamps the lot, returns how many) or off (returns 0)."""
    _state.enabled = on
    manager = services.object_manager()
    if manager is None:
        return 0
    if on:
        if not _state.registered:
            manager.register_callback(CallbackTypes.ON_OBJECT_ADD, _on_object_add)
            _state.registered = True
        return stamp_all()
    if _state.registered:
        manager.unregister_callback(CallbackTypes.ON_OBJECT_ADD, _on_object_add)
        _state.registered = False
    return 0


# ---------------------------------------------------------------- game hooks


def _install_inventory_hook() -> None:
    from objects.components import inventory_item

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def a11y_on_added_to_inventory(
            self: InventoryItemComponent, *args: Any, **kwargs: Any
        ) -> Any:
            result = original(self, *args, **kwargs)
            try:
                if _state.enabled:
                    stamp(self.owner, inventory=True)
            except Exception:
                simsight_log.error('objectnames: inventory name')
            return result

        return a11y_on_added_to_inventory

    simsight_patch.wrap(inventory_item.InventoryItemComponent, 'on_added_to_inventory', make)


def _tooltip_needs_our_style(component: TooltipComponent) -> bool:
    owner = component.owner
    return bool(
        _state.enabled
        and owner is not None
        and owner.id in _state.stamped
        and not owner.hover_tip
        and component.has_ui_metadata_handles()
    )


def _install_tooltip_rebuild_hook() -> None:
    """After EA's rebuild: if it produced handles (which includes our merged
    external name) but chose no style, give it ours. EA styles always win;
    a rebuild that disabled the tip (no handles) is left disabled."""
    from objects.components import tooltip_component

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def a11y_update_object_tooltip(self: TooltipComponent, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            try:
                if _tooltip_needs_our_style(self):
                    self.owner.hover_tip = _STYLE
                    self.owner.update_ui_metadata()
            except Exception:
                simsight_log.error('objectnames: rebuild hook')
            return result

        return a11y_update_object_tooltip

    simsight_patch.wrap(tooltip_component.TooltipComponent, 'update_object_tooltip', make)


def _install_zone_hook() -> None:
    import zone

    def make(original: simsight_patch.Function) -> simsight_patch.Function:
        def a11y_on_loading_screen_animation_finished(self: Zone, *args: Any, **kwargs: Any) -> Any:
            result = original(self, *args, **kwargs)
            try:
                if _state.enabled:
                    stamped = enable(True)
                    simsight_log.line(f'objectnames: stamped {stamped} objects at zone load')
            except Exception:
                simsight_log.error('objectnames: zone-load stamp')
            return result

        return a11y_on_loading_screen_animation_finished

    simsight_patch.wrap(zone.Zone, 'on_loading_screen_animation_finished', make)


def _install(install: Callable[[], None], label: str) -> None:
    try:
        install()
    except Exception:
        simsight_log.error(f'objectnames: {label} hook install')


_install(_install_inventory_hook, 'inventory')
_install(_install_tooltip_rebuild_hook, 'rebuild')
_install(_install_zone_hook, 'zone')


# ---------------------------------------------------------------- cheat commands


def _private(component: TooltipComponent, name: str) -> Any:
    """One of EA's private tooltip fields, for simsight.objdiag only: EA has
    no public API that shows them."""
    return getattr(component, name)


def _add_tooltip_test_lines(lines: list[str], obj: Any, component: TooltipComponent) -> None:
    try:
        from event_testing.resolver import SingleObjectResolver

        resolver = SingleObjectResolver(obj)
        lines.append(f'   tooltip_tests_pass={bool(component.tooltip_tests.run_tests(resolver))}')
        for i, tip in enumerate(_private(component, '_get_custom_tooltips')()):
            passed = bool(tip.object_tests) and bool(tip.object_tests.run_tests(resolver))
            lines.append(f'   custom[{i}] style={tip.tooltip_style!r} tests_pass={passed}')
    except Exception as ex:
        # A diagnostic: the failure is the answer, shown in the console (tuned
        # tests can raise anything).
        simsight_log.error('objectnames: objdiag tooltip tests')
        lines.append(f'   test eval failed: {ex!r}')


def _diag_lines(obj: Any) -> list[str]:
    """What simsight.objdiag says about one object."""
    head = (
        f'objdiag: {type(obj).__name__} id={obj.id} hover_tip={obj.hover_tip!r}'
        f' stamped={obj.id in _state.stamped} interactive={_has_interactions(obj)}'
    )
    lines = [head]
    component: TooltipComponent | None = obj.get_component(component_types.TOOLTIP_COMPONENT)
    if component is None:
        lines.append('   no tooltip component')
        return lines
    lines.append(
        f'   hovertip_requested={component.hovertip_requested}'
        f' handles={sorted(_private(component, "_ui_metadata_handles").keys())}'
        f' external={sorted(_private(component, "_external_field_to_data").keys())}'
    )
    _add_tooltip_test_lines(lines, obj, component)
    return lines


def _objdiag(match: str, output: Any) -> None:
    if not match:
        output('simsight: usage simsight.objdiag <substring of tuning name>')
        return
    manager = services.object_manager()
    if manager is None:
        output('simsight: no object manager')
        return
    count = 0
    for obj in tuple(manager.values()):
        if obj.is_sim or match.lower() not in type(obj).__name__.lower():
            continue
        count += 1
        for line in _diag_lines(obj):
            simsight_log.line(line)
            output(line)
        if count >= _DIAG_MAX_OBJECTS:
            break
    output(f'simsight: objdiag matched {count} object(s)')


@sims4.commands.Command(
    'simsight.objdiag', 'a11y.objdiag', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_objdiag(match: str = '', _connection: Any = None) -> bool:
    """simsight.objdiag <substring of tuning name>: log tooltip state of matching
    objects (e.g. `simsight.objdiag bedDouble`)."""
    try:
        _objdiag(match, sims4.commands.CheatOutput(_connection))
    except Exception:
        simsight_log.error('objectnames: objdiag command')
    return True


def _audit_hover_tips() -> tuple[int, int]:
    """(objects with a hover tip, objects): what the game currently believes
    carries a tip, which is what actually drives the tooltip appearing at all."""
    tipped = 0
    total = 0
    try:
        manager = services.object_manager()
        if manager is not None:
            for obj in tuple(manager.values()):
                if obj.is_sim:
                    continue
                total += 1
                if obj.hover_tip:
                    tipped += 1
    except Exception:
        simsight_log.error('objectnames: audit')
    return tipped, total


def _objectnames(mode: str, output: Any) -> None:
    if mode.lower() in ('redo', 'force', 'refresh'):
        # Re-apply over anything that lost its tip after our first pass.
        _state.stamped.clear()
        again = stamp_all(force=True)
        output(f'simsight: re-stamped {again} objects')
        _speak(f'Re-stamped {again} objects.')
        return
    if mode.lower() in ('off', 'false', '0'):
        enable(False)
        settings.set('object_names', False)
        output('simsight: object hover names OFF (restart or reload lot to clear existing tips)')
        _speak('Object hover names off.')
        return
    stamped = enable(True)
    settings.set('object_names', True)
    output(f'simsight: object hover names ON ({stamped} objects stamped)')
    tipped, total = _audit_hover_tips()
    output(f'simsight: {tipped}/{total} objects currently have a hover tip')
    simsight_log.line(f'objectnames: audit {tipped}/{total} objects have hover_tip')
    _speak(f'Object hover names on. {stamped} objects.')


@sims4.commands.Command(
    'simsight.objectnames', 'a11y.objectnames', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_objectnames(mode: str = 'on', _connection: Any = None) -> bool:
    try:
        _objectnames(mode, sims4.commands.CheatOutput(_connection))
    except Exception:
        simsight_log.error('objectnames: objectnames command')
    return True

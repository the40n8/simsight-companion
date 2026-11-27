"""Guided placement: choose a floor, then an area, and buy the product there."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log

from .area import LotArea, guided_areas
from .catalog import product, product_name
from .needs import NEEDS, requirement
from .placing import place
from .state import build_only, floor_name, household_id, in_build_mode, lot_zone_id, say_text

if TYPE_CHECKING:
    from world.lot import Lot
    from simsight_hub import Row

    from .area import Area


def guided_placement() -> bool:
    """Choose a floor, then choose an area and confirm the purchase."""
    import services
    import simsight_build as build
    import simsight_hub as hub
    import simsight_menu as menu

    if not build.controls_enabled():
        return build.refuse_disabled()
    if not build_only():
        return False
    item = product()
    if item is None:
        say_text(
            'Choose an object in the Build catalog first.',
            'Najpierw wybierz obiekt w katalogu trybu budowania.',
        )
        return False
    need = requirement(item['id'])
    if need in NEEDS:
        say_text(*NEEDS[need])
        return False
    lot = services.active_lot()
    if lot is None:
        return False
    owner = household_id()
    rows: list[Row] = [
        (
            menu.raw_text(floor_name(level)),
            None,
            lambda value=level: _choose_area(item, owner, value),
        )
        for level in range(int(lot.min_level), int(lot.max_level) + 1)
    ]
    if not rows:
        say_text('No lot floors available.', 'Brak dostępnych pięter.')
        return False
    return hub.show_window('guided placement', 'choose a floor to search', rows)


def _guided_current(item: dict[str, Any], owner: Any) -> bool:
    import simsight_build as build
    import simsight_keys as keys

    if (
        item is product()
        and owner == household_id()
        and in_build_mode()
        and build.controls_enabled()
        and not keys.status.editing_active
    ):
        return True
    say_text(
        'Placement selection expired. Open guided placement again.',
        'Wybór wygasł. Otwórz ponownie umieszczanie wspomagane.',
    )
    return False


def _area_labels(lot: Lot, areas: list[Area]) -> list[str]:
    """Each area's compass direction from the lot centre, with its room."""
    import simsight_scanner as scanner

    labels = []
    room_number = 0
    for center, area, room_name in areas:
        dx, dz = center.x - lot.center.x, center.z - lot.center.z
        label = (
            lang.text('Centre of the lot', 'Środek parceli')
            if area.index == 4
            else lang.t(scanner.COMPASS[scanner.bearing_index(dx, dz)])
        )
        if area.room_id is not None:
            room_number += 1
            label = '{}, {}'.format(
                room_name or lang.text('Room {}', 'Pokój {}').format(room_number), label
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
    surface = routing.SurfaceIdentifier(lot_zone_id(), level, routing.SurfaceType.SURFACETYPE_WORLD)
    try:
        definition = services.definition_manager().get(item['id'], get_fallback_definition_id=False)
        price = int(definition.price)
        areas = list(guided_areas(lot, level, surface))
    except Exception:  # EA's definition, terrain and room lookups
        simsight_log.error('place: could not sample the lot')
        areas = []
    if not areas:
        say_text('No areas found on that floor.', 'Nie znaleziono obszarów na tym piętrze.')
        return False

    def buy(area: LotArea) -> bool:
        return _guided_current(item, owner) and place(area.points[0], level, surface, area=area)

    labels = _area_labels(lot, areas)
    detail = lang.text(
        'Buy {} for {} simoleons if it fits.', 'Kup {} za {} simoleonów, jeśli się zmieści.'
    ).format(product_name(), price)
    rows: list[Row] = []
    for label, (center, area, _) in zip(labels, areas):
        shown = label
        if labels.count(label) > 1:
            shown += lang.text(', {} metres from centre', ', {} metrów od środka').format(
                round(math.sqrt(LotArea.distance(center, lot.center)), 1)
            )
        rows.append((menu.raw_text(shown), menu.raw_text(detail), lambda value=area: buy(value)))
    return hub.show_window('guided placement', 'choose an area to buy and place', rows)

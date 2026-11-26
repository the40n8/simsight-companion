"""Optional Build-grid orientation hints for dining and desk chairs.

This module only augments announcements. It never moves, rotates, validates,
buys, or otherwise changes an object or the baseline placement transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log

if TYPE_CHECKING:
    # (distance squared, object id, object, nearest covered tile)
    _Target = tuple[int, int, Any, tuple[int, int]]

# chair tag -> (chair label, table tag, table label)
_CHAIRS: dict[str, tuple[str, str, str]] = {
    'buycatssdiningchair': ('dining chair', 'buycatssdiningtable', 'dining table'),
    'buycatssdeskchair': ('desk chair', 'buycatssdesk', 'desk'),
}

# (facing - side) % 4 -> relation and its sentence; any other turn is side-on
_RELATIONS: dict[int, tuple[str, str, str]] = {
    0: (
        'toward',
        'Nearest {} is {}; placement faces toward it. ',
        'Najblizszy {} jest {}; ustawienie jest skierowane do niego. ',
    ),
    2: (
        'away',
        'Nearest {} is {}; placement faces away from it. ',
        'Najblizszy {} jest {}; ustawienie jest skierowane od niego. ',
    ),
}
_SIDE_ON = (
    'side-on',
    'Nearest {} is {}; placement is side-on to it. ',
    'Najblizszy {} jest {}; ustawienie jest bokiem do niego. ',
)


def _log(message: str) -> None:
    simsight_log.line('chair alignment: ' + message)


def _enabled() -> bool:
    try:
        import simsight_settings as settings

        return bool(settings.get('chair_alignment_hints', True))
    except (ImportError, AttributeError):
        return True


def _selected_kind() -> tuple[dict[str, Any], tuple[str, str, str]] | None:
    """The selected catalog product and its chair details, when it is a chair."""
    try:
        import services
        import simsight_chair_snap as chair_snap
        import simsight_place as place

        item = place.product()
        if item is None:
            return None
        definition = services.definition_manager().get(item['id'], get_fallback_definition_id=False)
        tags = chair_snap.tag_names(definition)
        for tag_name, details in _CHAIRS.items():
            if tag_name in tags:
                return item, details
    except (ImportError, AttributeError, KeyError, TypeError, ValueError) as ex:
        _log(f'selected chair classification failed: {ex!r}')
    return None


def _object_has_tag(obj: Any, wanted: str) -> bool:
    import simsight_chair_snap as chair_snap

    try:
        return wanted in chair_snap.tag_names(obj.definition)
    except AttributeError:  # no definition: not a table
        return False


def _nearest_target(frame: Any, i: int, j: int, level: int, wanted: str) -> _Target | None:
    import simsight_tiles as tiles

    candidates: list[_Target] = []
    seen: set[Any] = set()
    for di in range(-2, 3):
        for dj in range(-2, 3):
            ni, nj = i + di, j + dj
            if not frame.inside(ni, nj):
                continue
            for obj in tiles.objects_on(frame, ni, nj, level):
                obj_id = getattr(obj, 'id', None)
                if obj_id in seen or not _object_has_tag(obj, wanted):
                    continue
                seen.add(obj_id)
                covered = tiles.covered_tiles(frame, obj, level) or [(ni, nj)]
                nearest = min(covered, key=lambda cell: (cell[0] - i) ** 2 + (cell[1] - j) ** 2)
                distance = (nearest[0] - i) ** 2 + (nearest[1] - j) ** 2
                candidates.append((distance, obj_id or 0, obj, nearest))
    return min(candidates, key=lambda row: (row[0], row[1])) if candidates else None


def _direction(i: int, j: int, target: tuple[int, int]) -> int | None:
    """The side (0..3, as the grid's facing) of target from tile (i, j); None on it."""
    import simsight_chair_snap as chair_snap

    di, dj = target[0] - i, target[1] - j
    if di == 0 and dj == 0:
        return None
    return chair_snap.side_of(di, dj)


def _relation(facing: int, side: int | None) -> tuple[str, str, str]:
    """(relation, English, Polish) of a chair facing `facing` to a table on `side`."""
    if side is None:
        return 'same tile', 'Nearest {} is on this tile. ', 'Najblizszy {} jest na tym polu. '
    return _RELATIONS.get((facing - side) % 4, _SIDE_ON)


def _hint_parts(frame: Any) -> list[str]:
    import simsight_chair_snap as chair_snap
    import simsight_grid as grid
    import simsight_tiles as tiles

    selected = _selected_kind()
    if selected is None:
        return []
    item, details = selected
    if chair_snap.has_target(item, frame):
        return []
    chair_label, target_tag, target_label = details
    i, j = grid.location.tile
    target = _nearest_target(frame, i, j, grid.location.level, target_tag)
    if target is None:
        _log('product {} {}: no {} within two tiles'.format(item['id'], chair_label, target_label))
        return []
    _distance, obj_id, _obj, target_tile = target
    side = _direction(i, j, target_tile)
    facing = int(grid.location.facing) % 4
    relation, en, pl = _relation(facing, side)
    if side is None:
        result = lang.text(en, pl).format(target_label)
    else:
        result = lang.text(en, pl).format(target_label, tiles.direction_name(side))
    _log(
        'product {} {} target {} tile {} facing {} relation {}'.format(
            item['id'],
            chair_label,
            obj_id,
            target_tile,
            tiles.direction_name(facing),
            relation,
        )
    )
    return [result]


def fit_parts(frame: Any, with_facing: bool = True, objects: Any = None) -> list[Any]:
    """The grid's fit announcement, plus the chair hint when it is on."""
    parts = list(_original_fit_parts(frame, with_facing=with_facing, objects=objects))
    if _enabled():
        try:
            parts.extend(_hint_parts(frame))
        except Exception:
            simsight_log.error('chair alignment: announcement')
    return parts


def _original_fit_parts(frame: Any, with_facing: bool = True, objects: Any = None) -> Any:
    import simsight_grid as grid

    return grid.fit_parts_base(frame, with_facing=with_facing, objects=objects)

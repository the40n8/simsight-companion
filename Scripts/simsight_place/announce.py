"""What is said once an object is placed and paid for."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log

from .catalog import product_name
from .state import floor_name, last_placement, say, state, tile_key

if TYPE_CHECKING:
    from objects.game_object import GameObject

    from .state import Placement, TileKey


def _where(obj: Any, cursor_position: Any, level: int) -> str:
    import simsight_scanner as scanner

    try:
        entry = scanner.entry_for(obj, cursor_position, level, False, '', '')
        return scanner.place_part(entry)
    except Exception:  # a detail of the report: never stops it
        simsight_log.error('place: where it landed')
        return ''


def name_parts(obj: Any) -> Any:
    import simsight_scanner as scanner

    try:
        entry = scanner.entry_for(obj, obj.position, int(obj.level), False, '', '')
        return scanner.name_part(entry)
    except Exception:  # a detail of the report: never stops it
        simsight_log.error('place: object name')
        return lang.text('the object', 'obiekt')


def note_landed(obj: GameObject, level: int, key: TileKey) -> None:
    """Remember where it landed: the repeat guard covers that tile too."""
    try:
        last_placement['position'] = obj.position
        state.repeat_keys = (key, tile_key(obj.position, int(getattr(obj, 'level', level))))
    except Exception:  # paid: a detail of the report never stops it
        simsight_log.error('place: landed position')


def _placed_where(obj: GameObject, surface: Any, p: Placement) -> list[Any]:
    if surface is not None:
        return [lang.text(' placed on ', ' umieszczono na: '), name_parts(surface)]
    if p.area is not None:
        return [
            lang.text(' placed in the chosen area, ', ' umieszczono w wybranym obszarze, '),
            floor_name(p.level),
        ]
    where = _where(obj, p.position, p.level)
    if not where or where == lang.t('here'):
        return [lang.text(' placed at the cursor', ' umieszczono przy kursorze')]
    return [
        lang.text(' placed', ' umieszczono'),
        ', ',
        where,
        lang.text(' from the cursor', ' od kursora'),
    ]


def announce_placed(
    obj: GameObject, item: dict[str, Any], price: int, surface: Any, p: Placement
) -> None:
    note_landed(obj, p.level, p.key)
    parts = [item['name'] or product_name(), *_placed_where(obj, surface, p)]
    parts.append(lang.text(f', {price} simoleons.', f', {price} simoleonów.'))
    if _follow(obj, p.level):
        parts.append(lang.text(' The cursor moved to it.', ' Kursor przesunięto do niego.'))
    say(parts)


def _facing_part(f: Any, obj: GameObject) -> str | None:
    import simsight_tiles as tiles

    try:
        import sims4.math

        facing = f.facing_of(sims4.math.yaw_quaternion_to_angle(obj.orientation))
        return lang.text(
            f', facing {tiles.direction_name(facing)}',
            f', skierowany na {tiles.direction_name(facing)}',
        )
    except Exception:  # a detail of the report: never stops it
        simsight_log.error('place: facing')
        return None


def _covered_part(f: Any, obj: GameObject, level: int) -> str | None:
    import simsight_tiles as tiles

    covered = tiles.covered_tiles(f, obj, level)
    if len(covered) <= 1:
        return None
    base = f.tile_of(obj.position.x, obj.position.z)
    others = [tiles.relative(ci - base[0], cj - base[1]) for ci, cj in covered if (ci, cj) != base][
        :5
    ]
    return lang.text(
        ', covering {} tiles: this one and {}'.format(len(covered), '; '.join(others)),
        ', zajmuje pól: {}: to i {}'.format(len(covered), '; '.join(others)),
    )


def announce_exact(
    obj: GameObject, item: dict[str, Any], price: int, level: int, key: TileKey
) -> None:
    """The Build cursor placed it exactly: facing and the tiles it covers,
    counted from its own tile; the cursor moves to it."""
    import simsight_tiles as tiles

    note_landed(obj, level, key)
    parts = [item['name'] or product_name(), lang.text(' placed', ' umieszczono')]
    f = tiles.frame()
    if f is not None:
        details = (_facing_part(f, obj), _covered_part(f, obj, level))
        parts.extend(part for part in details if part is not None)
    parts.append(lang.text(f', {price} simoleons.', f', {price} simoleonów.'))
    if _follow(obj, level):
        parts.append(lang.text(' The cursor is on it.', ' Kursor jest na nim.'))
    say(parts)


def _follow(obj: GameObject, level: int) -> bool:
    """Put the grid cursor on the placed object, on its floor."""
    try:
        import simsight_grid as grid

        return bool(grid.focus_position(obj.position, int(getattr(obj, 'level', level))))
    except Exception:  # the grid cursor: a detail of the report
        simsight_log.error('place: cursor did not follow')
        return False

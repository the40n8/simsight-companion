# SimSight Plus - decorating helpers in the build helper (Alt+B).
# Decorate a room: choose a room, walls or floor, then a pattern from the catalog; it is chosen and
# painted over the whole room in one go. Sell things in a room: tick any number of objects and sell
# them for what they are worth now, the same way SimSight's own X key sells (remove + refund).
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import simsight_plus_build as build
import simsight_plus_core as core

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# part -> (catalog product type, label)
PARTS = {'walls': (7, 'Wall patterns, paint and wallpaper'), 'floor': (5, 'Floor patterns')}


def _say(text: str) -> None:
    core.post(core.speak, text)


def _row_name(obj: Any) -> Any:
    try:
        label = obj.catalog_name
        from protocolbuffers.Localization_pb2 import LocalizedString

        if not isinstance(label, LocalizedString):
            from sims4.localization import _create_localized_string

            label = _create_localized_string(int(getattr(label, 'hash', label)))
        return label
    except Exception:  # catalog names come in many shapes: a plain label still reads
        return 'Object'


# ---------------------------------------------------------------- decorate a room


def decorate_room() -> None:
    if not build.in_build_mode():
        core.speak('Go to Build mode first. Press F2, on a laptop Fn+F2.')
        return
    rows = build.room_rows()
    if not rows:
        core.speak('There are no rooms yet.')
        return

    def got_room(room: Any) -> None:
        prows = [
            ('Walls', 'Paint or wallpaper on every wall inside the room.', 'walls'),
            ('Floor', 'Tiles, wood, carpet and more over the whole floor.', 'floor'),
        ]
        core.choose(
            f'Decorate the {room.name}',
            'What do you want to change?',
            prows,
            lambda part: _list_patterns(room, part),
            tag='build',
        )

    core.choose('Decorate a room', 'Choose the room.', rows, got_room, tag='build')


def _list_patterns(room: Any, part: str) -> None:
    if build.busy[0]:
        core.speak('Still working on the last build step.')
        return
    (ptype, label) = PARTS[part]
    core.speak(f'Getting the {label.lower()}. Please wait.')

    def work() -> None:
        items = build.fetch_catalog(ptype, '', 'paint' + part)
        if not items:
            _say('The catalog did not answer. Try again in a moment.')
            return
        core.post(_show_patterns, room, part, items)

    threading.Thread(target=work, name='a11yplus_decor_list', daemon=True).start()


def _show_patterns(room: Any, part: str, items: Sequence[Any]) -> None:
    tiles = len(build.room_tiles(room))
    rows = []
    for i, (name, price, colors) in enumerate(items):
        desc = f'{price:,} simoleons a tile' if price else 'Free'
        if price and part == 'floor':
            desc += f', about {price * tiles:,} for this room'
        if colors > 1:
            desc += f', {colors} colors, the first color is used'
        rows.append((name, desc + '.', (i, name)))
    core.choose(
        '{} for the {}'.format('Walls' if part == 'walls' else 'Floor', room.name),
        '{} patterns. Enter paints the whole {} with it. You have {:,} simoleons.'.format(
            len(items), 'walls of the room' if part == 'walls' else 'floor', build.funds() or 0
        ),
        rows,
        lambda v: _paint(room, part, v[0], v[1]),
        tag='catalog',
    )


def _paint(room: Any, part: str, index: int, name: str) -> None:
    if build.busy[0]:
        core.speak('Still working on the last build step.')
        return
    build.busy[0] = True
    core.speak(f'Painting the {part} of the {room.name} with {name}. Please wait.')

    def work() -> None:
        try:
            build.catalog_state['silent_select'] = True
            try:
                build.select_item(index, name)
            finally:
                build.catalog_state['silent_select'] = False
            verify = build.on_main(
                _paint_check, f'Painted the {part} of the {room.name} with {name}'
            )
            build.set_floor(room.level)
            if part == 'walls':
                target = build.wall_point(room, 'north')
                ok = build.point_and_act(
                    [target],
                    room.level,
                    _shift_click,
                    'decorate walls',
                    height=0.15,
                    view=build.VIEW_FROM['north'],
                )
            else:
                ok = build.point_and_act(
                    [room.center],
                    room.level,
                    _shift_click,
                    'decorate floor',
                )
            if ok and verify is not None:
                time.sleep(0.5)
                core.post(verify)
        except Exception:
            core.log_exc('decorate')
            _say('Sorry, painting failed.')
        finally:
            build.busy[0] = False

    threading.Thread(target=work, name='simsight_plus_decor', daemon=True).start()


def _shift_click(points: Sequence[Any]) -> Any:
    """Shift+click paints the whole room (walls or floor) at once."""
    return build.click(points[0], shift=True)


def _paint_check(done_text: str) -> Callable[[], None]:
    """Like build.cost_check, but painting often costs nothing: the game trades in the old pattern
    at the same price. So no money change and no error from the game still means it was done."""
    before = build.funds()
    started = time.monotonic()

    def verify() -> None:
        after = build.funds()
        reason = build.last_error['text'] if build.last_error['at'] > started else ''
        if reason:
            core.speak(f'Not done. The game says: {reason}')
        elif before is None or after is None or after == before:
            core.speak(f'{done_text}. No extra cost: the old one was traded in.')
        elif after < before:
            core.speak(f'{done_text}. It cost {before - after:,} simoleons.')
        else:
            core.speak(f'{done_text}. You got {after - before:,} simoleons back for the old one.')

    return verify


# ---------------------------------------------------------------- sell things in a room


def sell_in_room() -> None:
    if not build.in_build_mode():
        core.speak('Go to Build mode first. Press F2, on a laptop Fn+F2.')
        return
    import simsight_plus_rooms as rooms

    all_rooms = rooms.scan_rooms()
    if not all_rooms:
        core.speak('There are no rooms yet.')
        return
    rows = [(r.name, f'{r.size_text()}, {len(r.objects)} things in it.', r) for r in all_rooms]
    core.choose('Sell things', 'Choose the room.', rows, _pick_things, tag='build')


_REFUSAL_TEXT = {
    'occupied': 'Something is on it or inside it, sell that first',
    'protected': 'the game does not allow selling it',
    'foreign': 'it belongs to another household',
    'hidden': 'it cannot be sold from here',
    'offlot': 'it is not on this lot',
}


def _pick_things(room: Any) -> None:
    import simsight_place as place

    rows = []
    for obj in room.objects:
        refusal = place.sell_refusal(obj)
        if refusal == 'sim':
            continue
        if refusal:
            desc = 'Cannot sell: {}.'.format(_REFUSAL_TEXT.get(refusal, 'not allowed'))
        else:
            desc = f'Sells for {place.object_value(obj):,} simoleons.'
        rows.append((_row_name(obj), desc, obj.id, False, obj))
    if not rows:
        core.speak(f'There is nothing to sell in the {room.name}.')
        return
    core.choose(
        f'Sell things in the {room.name}',
        f'{len(rows)} things. Check each one to sell with Space or Enter, then choose OK.',
        rows,
        lambda ids: _confirm(room, ids),
        tag='build',
        multi=len(rows),
    )


def _confirm(room: Any, ids: Sequence[int]) -> None:
    import services
    import simsight_place as place

    objs = [services.object_manager().get(i) for i in ids]
    objs = [o for o in objs if o is not None and not place.sell_refusal(o)]
    if not objs:
        core.speak('Nothing chosen that can be sold.')
        return
    total = sum(place.object_value(o) for o in objs)
    what = '1 thing' if len(objs) == 1 else f'{len(objs)} things'
    rows = [
        (f'Yes, sell {what} for {total:,} simoleons', None, True),
        ('No, keep everything', None, False),
    ]
    core.choose(
        'Sell?',
        f'Sell {what} from the {room.name} for {total:,} simoleons?',
        rows,
        lambda yes: _sell(room, [o.id for o in objs]) if yes else core.speak('Nothing sold.'),
        tag='build',
    )


def _sell(room: Any, ids: Sequence[int]) -> None:
    import services
    import simsight_place as place

    place.settle_owed()
    sold, money = 0, 0
    failed: list[int] = []
    before = build.funds() or 0
    for oid in ids:
        obj = services.object_manager().get(oid)
        if obj is None:
            continue
        if place.sell_refusal(obj):
            failed.append(oid)
            continue
        value = place.object_value(obj)
        if not place.destroy_object(obj):
            failed.append(oid)
            continue
        place.refund(value)
        if place.last_placement.get('obj_id') == oid:
            place.last_placement['obj_id'] = None
        sold += 1
        money += value
    got = (build.funds() or 0) - before
    text = 'Sold {} {} from the {}. You got {:,} simoleons back.'.format(
        sold, 'thing' if sold == 1 else 'things', room.name, max(got, 0)
    )
    if got < money:
        text += f' {money - max(got, 0):,} more will come back with your next build action.'
    if failed:
        text += f' {len(failed)} could not be sold: something may be on it or using it.'
    core.log(
        f'sell: {ids} in {room.name}: {sold} sold, {money} expected, {got} received, failed {failed}'
    )
    core.speak(text)

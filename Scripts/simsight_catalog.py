"""The Buy catalog as a tree of pickers, and catalog search (Build mode).

Ctrl+Alt+O opens the tree: Search the catalog, By room, By function. A room or
function lists the Buy panel's own subcategories (simsight_catalogtree, made
from EA's BuildBuyNavigation movie), and a subcategory lists its items with
price and colours. Enter on an item picks it up exactly as clicking it in the
catalog does, so the Build cursor (Ctrl+Alt+B) places it as usual. Escape goes
back up one level. Ctrl+Alt+I goes straight to search.

Every level is a native picker, so the picker owns the keyboard: Build mode's
own hotkeys never see the keys pressed in it. Item lists and selection use the
SimSight Plus catalog bridge in TooltipManager (A11yCatalogBrowse for lists
and searches, A11yPlusSelect to pick up), and its replies (a11yplus.flash).

Picker callbacks and the timeout run through simsight_plus_core.post, which
logs anything they raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote

import simsight_catalogtree as tree

if TYPE_CHECKING:
    from simsight_catalogtree import Group, Subcategory

    # (name, price in simoleons, number of colours), as simsight_plus_build parses it
    Item = tuple[str, int, int]

_TAG = 'catalogtree'
_TIMEOUT = 5.0
_SEARCH_PRODUCT_TYPE = 2  # search covers every Buy product
_HINT = (
    'Press Ctrl+Alt+B to place it with the Build cursor, or Ctrl+Alt+O to choose something else.'
)


def _core() -> Any:
    import simsight_plus_core as core

    return core


def _build() -> Any:
    import simsight_plus_build as build

    return build


def _label(key: int) -> Any:
    import simsight_menu as menu

    return menu.loc_string(key)


def _ready() -> bool:
    """In Build mode; otherwise says why not."""
    if not _build().in_build_mode():
        _core().speak('Open Build mode first with F2, then press Ctrl+Alt+O.')
        return False
    return True


def open_tree() -> bool:
    """The top of the tree: search, by room, by function."""
    if not _ready():
        return False
    rows = [
        (
            'Search the catalog',
            'Type a word, such as bed or fridge, and choose from what matches.',
            ('find',),
        ),
        (
            'By room',
            'Kitchen, bathroom, bedroom and other rooms, then what goes in them.',
            ('group', 'room'),
        ),
        (
            'By function',
            'Comfort, surfaces, plumbing, lighting and other kinds of object.',
            ('group', 'function'),
        ),
    ]

    def picked(value: tuple[str, ...]) -> None:
        if value[0] == 'find':
            find()
        else:
            _groups(value[1])

    return bool(
        _core().choose(
            'Catalog', 'Choose how to look, or search. Escape closes.', rows, picked, tag=_TAG
        )
    )


def _groups_of(kind: str) -> tuple[Group, ...]:
    return tree.ROOMS if kind == 'room' else tree.FUNCTIONS


def _groups(kind: str) -> None:
    rows = [(_label(label), None, i) for (i, (label, _subs)) in enumerate(_groups_of(kind))]
    _core().choose(
        'By room' if kind == 'room' else 'By function',
        'Choose one. Escape goes back.',
        rows,
        lambda i: _subcategories(kind, i),
        tag=_TAG,
        on_cancel=open_tree,
    )


def _subcategories(kind: str, index: int) -> None:
    (label, subs) = _groups_of(kind)[index]
    rows = [(_label(sub[0]), None, j) for (j, sub) in enumerate(subs)]
    _core().choose(
        _label(label),
        'Choose a kind of item. Escape goes back.',
        rows,
        lambda j: _browse(subs[j], back=lambda: _subcategories(kind, index)),
        tag=_TAG,
        on_cancel=lambda: _groups(kind),
    )


class _Arrived:
    """The Plus reply handler calls set() when a list has fully arrived."""

    def __init__(self, on_done: Callable[[], None]) -> None:
        self.on_done = on_done
        self.done = False

    def set(self) -> None:
        if not self.done:
            self.done = True
            _core().post(self.on_done)


def _request(
    product_type: int,
    tags: str,
    priority: str,
    search: str,
    on_done: Callable[[list[Item]], None],
) -> None:
    """Ask the bridge for a list; on_done(items) on the game thread."""
    build = _build()
    build.catalog_state['serial'] += 1
    serial = build.catalog_state['serial']
    arrived = _Arrived(lambda: on_done(list(build.catalog_state['items'])))
    build.catalog_state.update(items=[], total=0, name='', next='tree', quiet=arrived)
    build.send_ui(
        'A11y|plus|browse|{}|{}|{}|{}|{}'.format(
            product_type, tags, priority, serial, quote(search, safe='')
        )
    )

    def timeout() -> None:
        if not arrived.done and build.catalog_state['serial'] == serial:
            arrived.done = True
            _core().speak('The catalog did not answer. Open Build mode with F2 and try again.')

    import simsight_steps as steps

    steps.after(_TIMEOUT, _core().post, timeout, daemon=False)


def _browse(sub: Subcategory, back: Callable[[], object]) -> None:
    (label, product_type, tags, priority) = sub

    def arrived(items: list[Item]) -> None:
        if not items:
            _core().speak('There is nothing here in your game.')
            back()
            return
        _items(_label(label), items, back)

    _core().speak('Loading.')
    _request(product_type, tags, priority, '', arrived)


def _describe(price: int, colours: int) -> str:
    text = f'{price:,} simoleons'
    if colours > 1:
        text += f', {colours} colours'
    return text


def _items(title: Any, items: list[Item], back: Callable[[], object]) -> None:
    rows = [
        (name, _describe(price, colours), i) for (i, (name, price, colours)) in enumerate(items)
    ]
    _core().choose(
        title,
        f'{len(items)} items. Enter picks one up to place. Escape goes back.',
        rows,
        _pick,
        tag=_TAG,
        on_cancel=back,
    )


def _pick(index: int) -> None:
    build = _build()
    build.NEXT_HINT['tree'] = _HINT
    build.catalog_state['next'] = 'tree'
    build.catalog_state['chosen'] = index
    build.send_ui(f'A11y|plus|select|{index}')


def find() -> bool:
    """Ask for a word, then search the catalog for it."""
    if not _ready():
        return False

    def typed(text: str | None) -> None:
        text = (text or '').strip()
        if not text:
            open_tree()
            return
        search(text)

    return bool(
        _core().ask_text(
            'Search the catalog',
            'Type what you are looking for, for example bed, fridge or lamp.',
            '',
            typed,
        )
    )


def search(text: str) -> None:
    """Search the catalog for text and list what matches."""

    def arrived(items: list[Item]) -> None:
        if not items:
            _core().speak(f'Nothing found for {text}. Try another word.')
            find()
            return
        _items(f'Results for {text}', items, open_tree)

    _core().speak('Searching.')
    _request(_SEARCH_PRODUCT_TYPE, '', '', text, arrived)

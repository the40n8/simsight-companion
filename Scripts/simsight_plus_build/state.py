"""What the build helper shares between its parts and with other modules:
the busy flag, the chosen product, the last catalog list and the game's
last build error."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable

    Point = tuple[float, float]  # world (x, z), or screen (x, y)
    Box = tuple[int, int, int, int]  # (e0, n0, e1, n1) meters from the west and north edges
    Grid = list[list[Any]]  # grid[n][e]: room id, 'free', 'inside' or 'object'
    Row = tuple[Any, ...]  # a core.choose row
    Action = Callable[[list[Any]], object]  # gets the projected screen points


busy = [False]  # [True] while a pointing job runs (other modules read and set it)

_product: dict[str, Any] = {'id': None, 'name': ''}  # the chosen catalog item

# The last listed catalog category; other modules read 'chosen' and 'items'.
catalog_state: dict[str, Any] = {
    'serial': 0,
    'items': [],
    'total': 0,
    'name': '',
    'next': '',
    'event': None,
}

last_error: dict[str, Any] = {'text': '', 'at': 0.0}  # the game's last build error

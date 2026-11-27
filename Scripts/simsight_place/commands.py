"""The client's console commands: the catalog's product, leaving the
catalog, and the answer to a drop request."""

from __future__ import annotations

from typing import Any

import sims4.commands

from simsight_commands import real_annotations
import simsight_log

from .catalog import product, set_product
from .placing import acknowledged
from .state import say_text


@sims4.commands.Command(  # type: ignore[misc]  # EA's Command is untyped
    'simsight.buildproduct', 'a11y.buildproduct', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def buildproduct(encoded: str = '', _connection: Any = None) -> bool:
    """Client: the Build catalog selected a product ('<instance>|<name>') or
    dropped it ('0|'). Names arrive percent-encoded."""
    from urllib.parse import unquote

    try:
        text = str(encoded)
        if '|' not in text or len(text) > 800:
            return False
        instance, name = text.split('|', 1)
        # simsight_plus_build learns the product through on_product.
        return set_product(instance, unquote(name))
    except Exception:
        simsight_log.error('place: buildproduct')
        return False


def _catalog_left() -> bool:
    import simsight_grid as grid

    item = product()
    if grid.build_cursor():  # never raises: grid.active() reports its own errors
        name = item['name'] if item is not None and item['name'] else ''
        say_text(
            'Back to Build placement. {}'.format(
                name + ' chosen.' if name else 'No object chosen.'
            ),
            'Powrót do umieszczania. {}'.format(
                'Wybrano: ' + name + '.' if name else 'Nie wybrano obiektu.'
            ),
        )
        return True
    if item is not None and item['name']:
        say_text(
            'Left the Build catalog. {} chosen. Control Alt B places it. Tab returns to the catalog.'.format(
                item['name']
            ),
            'Opuszczono katalog budowania. Wybrano: {}. Control Alt B umieszcza. Tab wraca do katalogu.'.format(
                item['name']
            ),
        )
    else:
        say_text(
            'Left the Build catalog. Tab returns to the catalog.',
            'Opuszczono katalog budowania. Tab wraca do katalogu.',
        )
    return True


@sims4.commands.Command(  # type: ignore[misc]  # EA's Command is untyped
    'simsight.catalogleft', 'a11y.catalogleft', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def catalogleft(_state: str = '', _connection: Any = None) -> bool:
    """Client: Escape released the Build catalog's keys (TooltipManager
    A11yReleaseBuildCatalog). The chosen product is unchanged."""
    try:
        return _catalog_left()
    except Exception:
        simsight_log.error('place: catalogleft')
        return False


@sims4.commands.Command(  # type: ignore[misc]  # EA's Command is untyped
    'simsight.buildhand', 'a11y.buildhand', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def buildhand(encoded: str = '', _connection: Any = None) -> bool:
    """Client: '<serial>|ok' once EA's placement tool holds nothing (it put
    the product down, or held none), '<serial>|denied' when it could not."""
    try:
        text = str(encoded)
        if '|' not in text or len(text) > 40:
            return False
        serial, state = text.split('|', 1)
        if not serial.isdigit() or state not in ('ok', 'denied'):
            return False
        return acknowledged(int(serial), state == 'ok')
    except Exception:
        simsight_log.error('place: buildhand')
        return False

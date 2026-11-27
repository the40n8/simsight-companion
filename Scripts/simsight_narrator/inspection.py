"""Read-only diagnostics: inventory items and the UI tree."""

from __future__ import annotations

import threading
from itertools import islice
from typing import TYPE_CHECKING, Any

import sims4.commands

import simsight_log

from .base import _boundary, live_command
from .decode import decode_percent
from .state import _state

if TYPE_CHECKING:
    from sims.sim import Sim


def _log_inventory_item(obj: Any) -> None:
    try:
        definition = getattr(obj, 'definition', None)
        simsight_log.line(
            'Inventory inspect: id={} definition={} class={} catalog={} hover={} hidden={}'.format(
                obj.id,
                getattr(definition, 'id', None),
                type(obj).__name__,
                getattr(obj, 'catalog_name', None),
                getattr(obj, 'hover_tip', None),
                obj.is_hidden(),
            )
        )
    except (AttributeError, TypeError) as ex:  # not shaped like an inventory object
        simsight_log.line(f'Inventory inspect: item unavailable {ex!r}')


@live_command('simsight.inspect_inventory', 'a11y.inspect_inventory')
def a11y_inspect_inventory(_connection: Any = None) -> bool:
    """Log read-only item identities/tooltip flags; never request an action."""
    with _boundary('narrator: inspect_inventory'):
        import services

        output = sims4.commands.CheatOutput(_connection)
        sim: Sim | None = services.get_active_sim()
        inventory = getattr(sim, 'inventory_component', None)
        if inventory is None:
            output('No active Sim inventory.')
            return False
        count = 0
        # Inventory iteration also includes hidden storage. Log the visibility
        # flag, and bound this diagnostic independently of stack sizes.
        for obj in islice(iter(inventory), 100):
            count += 1
            _log_inventory_item(obj)
        output(f'Inventory snapshot: {count} entries (limit 100); see SimSight log.')
        return True
    return False


@live_command('simsight.inspect_ui', 'a11y.inspect_ui')
def a11y_inspect_ui(delay: float = 0, _connection: Any = None) -> bool:
    """Read-only snapshot, optionally delayed to inspect a console-blocking modal."""
    with _boundary('narrator: inspect_ui'):
        import simsight_keys as keys

        if _state.inspect_timer is not None:
            _state.inspect_timer.cancel()
            _state.inspect_timer = None
        delay = max(0.0, min(60.0, delay))
        if delay:
            # _send only queues work; the timer never calls game APIs directly.
            timer = threading.Timer(delay, keys.send_action, args=('A11yInspect',))
            timer.daemon = True
            timer.start()
            _state.inspect_timer = timer
        else:
            keys.send_action('A11yInspect')
        sims4.commands.CheatOutput(_connection)(
            f'Keyboard focus snapshot in {delay:.0f} seconds; see SimSight log.'
        )
        return True
    return False


@live_command('simsight.inspect_enc', 'a11y.inspect_enc')
def a11y_inspect_enc(text: str = '', _connection: Any = None) -> bool:
    with _boundary('narrator: inspect_enc'):
        for line in decode_percent(text)[:16000].splitlines():
            simsight_log.line('UI inspect: ' + line)
        return True
    return False

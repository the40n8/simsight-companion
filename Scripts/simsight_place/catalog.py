"""The product the Build catalog selected: the only definition ever created."""

from __future__ import annotations

from typing import Any, Callable

import simsight_lang as lang
import simsight_log

from .state import lot_zone_id, pending, state


def on_product(listener: Callable[[Any, Any], object]) -> None:
    """Call listener(instance_text, name) first whenever the catalog selects
    or drops a product (simsight_plus_build notes the tool's name). It replaces
    the previous listener and must not raise."""
    state.product_listener = listener


def set_product(instance_text: Any, name: Any) -> bool:
    """The native catalog selected (or dropped) a product."""
    if state.product_listener is not None:
        state.product_listener(instance_text, name)
    text = str(instance_text or '').strip()
    if not text.isdigit() or int(text) == 0:
        state.product = None
        pending.placement = None
        return True
    def_id = int(text)
    try:
        import services

        definition = services.definition_manager().get(def_id, get_fallback_definition_id=False)
    except Exception:  # EA loads the definition's tuning: any failure means no product
        simsight_log.error('place: definition lookup')
        simsight_log.line(f'place: definition lookup failed for {def_id}')
        definition = None
    if definition is None or getattr(definition, 'id', None) != def_id:
        simsight_log.line(f'place: catalog product {def_id} is not an object definition')
        state.product = None
        return False
    item = {'id': def_id, 'name': str(name or '').strip()[:200], 'zone': lot_zone_id()}
    state.product = item
    pending.placement = None
    simsight_log.line('place: product {} {!r}'.format(def_id, item['name']))
    return True


def product() -> dict[str, Any] | None:
    """The remembered product, if it still belongs to this zone."""
    if state.product is not None and state.product['zone'] != lot_zone_id():
        state.product = None
    return state.product


def forget() -> None:
    state.product = None
    state.repeat_keys = ()
    state.sell_id = None
    pending.placement = None


def product_name() -> str:
    item = product()
    if item and item['name']:
        return item['name']
    return lang.text('the object', 'obiekt')

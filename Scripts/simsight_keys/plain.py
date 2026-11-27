"""Which message a bare key sends now, given who owns the keys."""

from __future__ import annotations

import simsight_settings as settings

from .bindings import BINDINGS, BUILD_GRID_KEYS, NUMPAD_BINDINGS, SCANNER_KEYS
from .scannermode import _build_grid_routing, _scanner_routing
from .state import status


def _tab_message(shift: bool) -> str | None:
    if shift and not status.modal_active:
        # With no menu open Shift+Tab is the game's own first-person
        # camera switch; Flash lets it through too. Menus keep it.
        return None
    return 'A11yTabPrev' if shift else 'A11yTabNext'


def _owned_key_message(vk: int, shift: bool) -> str | None:
    """A bare key the Build cursor or scanner mode owns right now."""
    if vk in BUILD_GRID_KEYS and _build_grid_routing() and (not shift or vk == 0x52):
        return 'A11yBuildGrid:' + ('turnback' if shift else BUILD_GRID_KEYS[vk])
    if vk in SCANNER_KEYS and not shift and _scanner_routing():
        return SCANNER_KEYS[vk]
    return None


def _modal_key_message(vk: int, shift: bool) -> str | None:
    """A bare key while one of our UI owners has focus."""
    if vk == 0x1B:
        # Flash checks whether its native guard actually owned this press.
        # The world Escape may already have opened Options by this poll.
        return 'A11yEscape'
    if settings.get('numpad_navigation', False) and vk in NUMPAD_BINDINGS:
        return NUMPAD_BINDINGS[vk]
    if status.editing_active or shift:
        return None
    return next(
        (message for message, (_label, combos) in BINDINGS.items() if (vk, False) in combos),
        None,
    )


def plain_message(
    vk: int, ctrl: bool = False, alt: bool = False, shift: bool = False
) -> str | None:
    """One policy for native context, editing and optional numpad navigation."""
    if not status.enabled:
        return None
    if (
        vk == 0x4E
        and ctrl
        and shift
        and not alt
        and not status.modal_active
        and not status.editing_active
    ):
        return 'A11yNeeds'
    if ctrl or alt:
        return None
    if vk == 0x09 and (not status.editing_active or status.modal_active):
        return _tab_message(shift)
    owned = _owned_key_message(vk, shift)
    if owned is not None:
        return owned
    if not status.modal_active:
        return None
    return _modal_key_message(vk, shift)

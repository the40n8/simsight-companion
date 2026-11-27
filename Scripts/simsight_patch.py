"""Wrapping EA's functions: one way for the whole mod.

The mod listens to the game by replacing some of EA's methods with a
wrapper that calls the original. Every wrapper:

- takes `*args, **kwargs` and passes them on unchanged, so a game update
  that adds or moves a parameter (e.g. `immediate`) cannot break it;
- calls the original first or last as its job requires, and lets the
  original's result and errors through unchanged;
- never lets its own failure reach the game (the caller's hook body logs
  with simsight_log.error);
- chains with other modules' wrappers of the same function.

    simsight_patch.wrap(UiDialog, 'show_dialog', make_wrapper)  # make_wrapper(original) -> wrapper
"""

from __future__ import annotations

from typing import Any, Callable

import simsight_log

Function = Callable[..., Any]

_WRAPPED = '_simsight_wrapped'  # on a wrapper: the function it calls
_BY = '_simsight_by'  # on a wrapper: which `make` built it


def _maker(make: Callable[[Function], Function]) -> str:
    return f'{getattr(make, "__module__", "")}.{getattr(make, "__qualname__", repr(make))}'


def wrap(owner: Any, name: str, make: Callable[[Function], Function]) -> Function:
    """Replace `owner.name` by `make(current)` and return `current`, the
    function the wrapper must call.

    Wrappers chain: several modules may wrap the same EA function (the
    zone load, the tick, dialogs), and each new wrapper wraps whatever is
    installed, so every one runs. A `make` already in the chain (an
    install() run again, at each zone load) adds nothing.
    """
    current = getattr(owner, name)
    by = _maker(make)
    link: Any = current
    while hasattr(link, _WRAPPED):  # already in the chain: nothing to add
        if getattr(link, _BY, None) == by:
            found: Function = getattr(link, _WRAPPED)
            return found
        link = getattr(link, _WRAPPED)
    wrapper = make(current)
    setattr(wrapper, _WRAPPED, current)
    setattr(wrapper, _BY, by)
    setattr(owner, name, wrapper)
    simsight_log.line(f'patch: {getattr(owner, "__name__", owner)}.{name} ({by})')
    return current


def original(owner: Any, name: str) -> Function:
    """The game's own function behind every wrapper (or the function itself)."""
    found: Function = getattr(owner, name)
    while hasattr(found, _WRAPPED):
        found = getattr(found, _WRAPPED)
    return found


def unwrap(owner: Any, name: str) -> None:
    """Put the game's own function back (all wrappers removed)."""
    setattr(owner, name, original(owner, name))

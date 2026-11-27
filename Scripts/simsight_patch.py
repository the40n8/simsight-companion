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


def _below(link: Any) -> Any:
    """The function a wrapper of ours calls, or None for anything else.
    Read from the object's own __dict__: EA objects with a __getattr__
    would answer any attribute and loop forever."""
    own = getattr(link, '__dict__', None)
    return own.get(_WRAPPED) if isinstance(own, dict) else None


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
    below = _below(link)
    while below is not None:  # already in the chain: nothing to add
        if link.__dict__.get(_BY) == by:
            found: Function = below
            return found
        link, below = below, _below(below)
    wrapper = make(current)
    setattr(wrapper, _WRAPPED, current)
    setattr(wrapper, _BY, by)
    setattr(owner, name, wrapper)
    simsight_log.line(f'patch: {getattr(owner, "__name__", owner)}.{name} ({by})')
    return current


def original(owner: Any, name: str) -> Function:
    """The game's own function behind every wrapper (or the function itself)."""
    found: Function = getattr(owner, name)
    below = _below(found)
    while below is not None:
        found, below = below, _below(below)
    return found


def unwrap(owner: Any, name: str) -> None:
    """Put the game's own function back (all wrappers removed)."""
    setattr(owner, name, original(owner, name))

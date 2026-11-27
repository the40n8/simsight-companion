"""Wrapping EA's functions: one way for the whole mod.

The mod listens to the game by replacing some of EA's methods with a
wrapper that calls the original. Every wrapper:

- takes `*args, **kwargs` and passes them on unchanged, so a game update
  that adds or moves a parameter (e.g. `immediate`) cannot break it;
- calls the original first or last as its job requires, and lets the
  original's result and errors through unchanged;
- never lets its own failure reach the game (the caller's hook body logs
  with simsight_log.error).

    original = simsight_patch.wrap(UiDialog, 'show_dialog', lambda original: wrapper)
"""

from __future__ import annotations

from typing import Any, Callable

import simsight_log

Function = Callable[..., Any]

_MARK = '_simsight_original'


def wrap(owner: Any, name: str, make: Callable[[Function], Function]) -> Function:
    """Replace `owner.name` by `make(original)` once, and return the
    original. Wrapping twice (a module reloaded) keeps the first original."""
    current = getattr(owner, name)
    original: Function = getattr(current, _MARK, current)
    wrapper = make(original)
    setattr(wrapper, _MARK, original)
    setattr(owner, name, wrapper)
    simsight_log.line(f'patch: {getattr(owner, "__name__", owner)}.{name}')
    return original


def original(owner: Any, name: str) -> Function:
    """The game's own function behind a wrapper (or the function itself)."""
    current = getattr(owner, name)
    found: Function = getattr(current, _MARK, current)
    return found


def unwrap(owner: Any, name: str) -> None:
    """Put the game's own function back."""
    setattr(owner, name, original(owner, name))

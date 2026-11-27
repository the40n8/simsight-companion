"""Console commands for the Flash layer: registration and the boundary
that keeps a failure out of the game."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Callable, Iterator, TypeVar

import sims4.commands

from simsight_commands import real_annotations
import simsight_log

if TYPE_CHECKING:
    Output = Callable[[str], Any]

_F = TypeVar('_F', bound=Callable[..., Any])


def live_command(*names: str) -> Callable[[_F], _F]:
    """sims4.commands.Command(*names, Live) with the argument types EA's
    parser needs (see simsight_commands.real_annotations)."""
    register = sims4.commands.Command(*names, command_type=sims4.commands.CommandType.Live)

    def decorate(func: _F) -> _F:
        return register(real_annotations(func))

    return decorate


@contextlib.contextmanager
def _boundary(where: str) -> Iterator[None]:
    """A console command or callback: log a failure, never raise into the game."""
    try:
        yield
    except Exception:
        simsight_log.error(where)

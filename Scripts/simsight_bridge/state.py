"""What the companion bridge remembers between ticks, and small helpers
every part of it uses."""

from __future__ import annotations


import contextlib
from typing import TYPE_CHECKING, Any

import simsight_log


if TYPE_CHECKING:
    from collections.abc import Iterator

    from server.client import Client
    from sims.sim import Sim

    Json = dict[str, Any]
    # (ok, reason) or (ok, reason, extra fields for the result message)
    Answer = tuple[Any, ...]


class _State:
    """Everything the bridge remembers between ticks."""

    def __init__(self) -> None:
        self.proc: Any = None  # the bridge process (subprocess.Popen)
        self.voice: Json | None = None  # last voice settings, re-sent to a restarted bridge
        self.last_spawn = 0.0
        self.read_buf = b''
        self.last_state_json: str | None = None
        self.last_state_time = 0.0
        self.last_poll = 0.0
        self.seq = 0
        # Game strings asked for but not known yet (names still loading).
        self.loc_misses = 0
        self.loc_wanted: set[int] = set()
        self.loc_requested: set[int] = set()
        self.house_at = -1e9
        self.house_rooms: dict[str, Json] = {}
        self.problems_at = 0.0
        self.problems: list[Json] = []
        self.route_seen: dict[tuple[Any, str], float] = {}  # (sim id, action) -> time
        self.menus: dict[str, dict[str, tuple[Any, Any]]] = {}  # target -> option -> (aop, context)
        # Optional modules, set once at startup (None when unavailable).
        self.dialogs: Any = None  # simsight_dialogs
        self.mackeys: Any = None  # simsight_mackeys (Mac only)
        self.msvcrt: Any = None  # Windows only: reading the pipe without blocking
        self.ctypes: Any = None  # Windows only (PeekNamedPipe); never on the Mac


_state = _State()


def log(message: str) -> None:
    simsight_log.line('bridge: ' + message)


@contextlib.contextmanager
def _logged(where: str) -> Iterator[None]:
    """Run a block of game calls whose failure only leaves a value out:
    the failure is logged (once, then counted) and the caller goes on."""
    try:
        yield
    except Exception:
        simsight_log.error('bridge: ' + where)


def _distance(a: Any, b: Any) -> float:
    """Distance on the ground between two positions (x and z)."""
    return ((float(a.x) - float(b.x)) ** 2 + (float(a.z) - float(b.z)) ** 2) ** 0.5


def _on_active_lot(obj: Any, default: bool) -> bool:
    """obj.is_on_active_lot(), or `default` for objects that cannot tell."""
    on_lot = getattr(obj, 'is_on_active_lot', None)
    return default if on_lot is None else bool(on_lot())


def _in_game() -> bool:
    import services

    zone = services.current_zone()
    return (
        zone is not None
        and getattr(zone, 'is_zone_running', False)
        and services.active_household() is not None
    )


def _sim_and_client() -> tuple[Sim | None, Client | None]:
    import services

    sim = services.get_active_sim()
    client = services.client_manager().get_first_client()
    return sim, client

"""The game's tick: commands read, game strings asked for, keys and the
state sent, all on the simulation thread."""

from __future__ import annotations


import time
from typing import TYPE_CHECKING, Any

import simsight_log
import simsight_patch

from .commands import _handle
from .pipe import _read_chunk, alive, send, start
from .snapshot import _publish_state
from .state import _state, log
from .text import _request_localization


if TYPE_CHECKING:
    from simsight_patch import Function


STATE_INTERVAL = 1.0  # seconds between state snapshots (sent only if changed)
POLL_INTERVAL = 0.1  # seconds between command polls


# ---------------------------------------------------------------- tick


def _tick() -> None:
    """Runs on the game's tick (simulation thread), every frame."""
    if not alive() and not start():
        return
    now = time.monotonic()
    if now - _state.last_poll >= POLL_INTERVAL:
        _state.last_poll = now
        _read_commands()
        _request_localization()
        if _state.dialogs is not None:
            _state.dialogs.flush_notes()
    if _state.mackeys is not None:
        try:
            _state.mackeys.tick(send)
        except Exception:
            simsight_log.error('bridge: keys tick')
    if now - _state.last_state_time >= STATE_INTERVAL:
        _state.last_state_time = now
        _publish_state()


def _install_tick() -> None:
    import sims4.core_services as core

    def make(original: Function) -> Function:
        def on_tick(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            try:
                _tick()
            except Exception:
                simsight_log.error('bridge: tick')
            return result

        return on_tick

    simsight_patch.wrap(core, 'on_tick', make)


def _read_commands() -> None:
    try:
        chunk = _read_chunk()
    except BlockingIOError:
        return  # nothing waiting
    except OSError as ex:  # the bridge exited: alive() notices
        log(f'read failed: {ex!r}')
        return
    except Exception:
        simsight_log.error('bridge: read')
        return
    if not chunk:
        return
    _state.read_buf += chunk
    while b'\n' in _state.read_buf:
        line, _state.read_buf = _state.read_buf.split(b'\n', 1)
        if line.strip():
            _handle(line)

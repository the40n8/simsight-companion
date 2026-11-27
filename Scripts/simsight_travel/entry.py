"""The keyboard M entry: the readiness lease Flash reads, and its requests
(simsight.travel, simsight.travel_cancel), each answered exactly once."""

from __future__ import annotations

from typing import Any

import sims4.commands

from simsight_commands import real_annotations
import simsight_log
import simsight_pump as pump
import simsight_settings as settings

from .state import _Request, _say, _state, _ui, _zone_loaded, enabled, simplified
from .outcomes import _cancel
from .front import _away, push_travel, show_front


# Readiness lease for the keyboard M entry. Flash routes M to Python only
# while a recent lease says the pump is alive; the heartbeat runs from inside
# the pump drain, so a dead pump stops refreshing by construction. The lease
# value is a session epoch that only ever grows; Flash judges freshness by
# the age of the last heartbeat, not by the value.
_LEASE_INTERVAL = 2.0
_REQUEST_DEADLINE = 3.0
_REQUEST_HISTORY = 64


# ------------------------------------------------- keyboard M entry lease


def announce_setting() -> bool:
    return _ui('A11y|travellist|' + ('on' if settings.get('travel_list', True) else 'off'))


def apply_setting() -> bool:
    """Settings changed (menu or simsight.reload): an open list is cancelled
    natively when it was switched off (the same contract as a reply that
    arrives after the switch), every UI copy hears the value at once and
    the M lease starts a new epoch."""
    if not enabled() and _state.pending is not None:
        _cancel(_state.pending)
    announce_setting()
    return start_lease()


def _publish_lease() -> bool:
    if enabled() and _zone_loaded():
        return _ui(f'A11y|travelready|{_state.epoch}')
    # Flash drops its pending request on a zero lease, so nothing queued
    # here may survive to push later.
    _invalidate_requests('stale')
    return _ui('A11y|travelready|0')


def _refresh_lease(generation: int, force: bool = False) -> bool:
    """Pump-driven heartbeat: re-posts itself while the pump drains; sends at
    most one readiness message per interval. Exactly one chain is live: a
    callback queued before the latest start or stop ends without re-posting
    and leaves the live chain's state alone."""
    if generation != _state.lease_generation:
        return False
    import time

    now = time.monotonic()
    if force or now - _state.lease_sent >= _LEASE_INTERVAL:
        _publish_lease()
        _state.lease_sent = now
    _state.lease_running = pump.post(_refresh_lease, generation)
    return _state.lease_running


def start_lease() -> bool:
    """A new session epoch (zone load, pump start, setting change). Requests
    from an earlier epoch are refused from here on."""
    _state.epoch += 1
    _state.lease_generation += 1
    _invalidate_requests('stale')
    _state.lease_running = False
    return _refresh_lease(_state.lease_generation, force=True)


def stop_lease() -> None:
    """Pump stopped or zone unloading: publish no lease, keep the epoch."""
    _state.lease_generation += 1
    _state.lease_running = False
    _invalidate_requests('stale')
    _ui('A11y|travelready|0')


def _entry(key: tuple[int, int]) -> _Request | None:
    for entry in _state.requests:
        if entry.key == key:
            return entry
    return None


def _send_result(epoch: int, request: int, outcome: str, reason: str = '') -> None:
    text = f'A11y|travelresult|{epoch}|{request}|{outcome}'
    if reason:
        text += '|' + reason
    _ui(text)


def _invalidate_requests(reason: str) -> None:
    """Settle every queued request as refused and tell Flash, so nothing
    queued can push later and no pending request is left unanswered."""
    for entry in _state.requests:
        if entry.state == 'pending':
            entry.state = 'refused:' + reason
            epoch, request = entry.key
            _send_result(epoch, request, 'refused', reason)


def _remember(key: tuple[int, int], state: str, when: float) -> None:
    _state.requests.append(_Request(key, state, when))
    del _state.requests[:-_REQUEST_HISTORY]


def _result(request: int, outcome: str, reason: str = '', epoch: int | None = None) -> bool:
    """Explicit reply Flash waits for; the epoch lets it ignore replies to a
    request from an earlier session. The settled outcome is kept on the
    request entry so a lost reply can be answered again."""
    epoch = _state.epoch if epoch is None else epoch
    entry = _entry((epoch, request))
    if entry is not None and entry.state == 'pending':
        entry.state = outcome + (':' + reason if reason else '')
    _send_result(epoch, request, outcome, reason)
    return outcome == 'ok'


def _resend(entry: _Request) -> None:
    """Answer again from the settled state of an entry (lost reply, duplicate
    cancel, replayed request); nothing is queued or pushed."""
    epoch, request = entry.key
    state = entry.state
    if state == 'pending':
        return
    if state == 'cancelled':
        _send_result(epoch, request, 'refused', 'cancelled')
        return
    outcome, _, reason = state.partition(':')
    _send_result(epoch, request, outcome, reason)


def _ready_sim() -> tuple[Any, str]:
    """The active Sim when a keyboard entry may start, or (None, reason)
    after speaking why not."""
    import services

    if not enabled() or not _zone_loaded():
        _say('action unavailable')
        return None, 'refused'
    sim = services.get_active_sim()
    if sim is None:
        _say('load a household first')
        return None, 'no_sim'
    if _away(sim):
        _say('sim unavailable')
        return None, 'away'
    return sim, ''


def _start_entry(sim: Any, request: int, epoch: int) -> bool:
    if simplified():
        # The request is answered once our window is up; the choice pushes
        # EA's Travel on its own, like a second press of M would.
        if show_front(sim):
            simsight_log.line(
                f'travel: keyboard entry for sim {sim.sim_id} offers alone or with others (request {request})'
            )
            return _result(request, 'ok', '', epoch)
        _say('action unavailable')
        return _result(request, 'refused', 'error', epoch)
    outcome = push_travel(sim)
    if outcome == 'ok':
        simsight_log.line(f'travel: keyboard entry pushed for sim {sim.sim_id} (request {request})')
        return _result(request, 'ok', '', epoch)
    return _result(request, 'refused', outcome, epoch)


def open_travel(request: int = 0, epoch: int = 0) -> bool:
    """Keyboard entry: the tuned Sim menu Travel chain for the active Sim
    (companion picker, then this list). EA's own tests gate it: the save
    lock, the career and multi-unit event blacklists, venue rules. Runs on
    the pump; the request must still be current, uncancelled and younger
    than the deadline when it gets here. Every refusal is reported to Flash
    and spoken."""
    import time

    entry = _entry((epoch, request))
    if entry is None:
        return _result(request, 'refused', 'stale', epoch)
    if entry.state != 'pending':
        return False  # settled already (cancelled, stale or answered)
    if epoch != _state.epoch or time.monotonic() - entry.when > _REQUEST_DEADLINE:
        simsight_log.line(
            f'travel: keyboard entry request {request} expired (epoch {epoch} vs {_state.epoch})'
        )
        return _result(request, 'refused', 'stale', epoch)
    sim, reason = _ready_sim()
    if sim is None:
        return _result(request, 'refused', reason, epoch)
    return _start_entry(sim, request, epoch)


def _travel_ingress(lease: str, request: str) -> bool:
    import time

    try:
        epoch = int(lease)
        request_id = int(request)
    except (TypeError, ValueError):
        return _result(0, 'refused', 'error', 0)
    key = (epoch, request_id)
    entry = _entry(key)
    if entry is not None:
        if entry.state != 'pending':
            _resend(entry)  # cancelled tombstone or a lost reply
        return False  # still queued: only its eventual outcome may answer
    _remember(key, 'pending', time.monotonic())
    if epoch != _state.epoch or not _state.lease_running:
        return _result(request_id, 'refused', 'stale', epoch)
    if not enabled() or not _zone_loaded():
        return _result(request_id, 'refused', 'refused', epoch)
    if not pump.post(open_travel, request_id, epoch):
        _say('action unavailable')
        return _result(request_id, 'refused', 'no_pump', epoch)
    return True


@sims4.commands.Command(
    'simsight.travel', 'a11y.travel', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_travel(lease: str = '0', request: str = '0', _connection: Any = None) -> bool:
    """Flash posts simsight.travel <epoch> <request> after its native guards.
    Refusals here happen at ingress, before anything is queued."""
    try:
        return _travel_ingress(lease, request)
    except Exception:
        simsight_log.error('travel: simsight.travel')
        return False


def _travel_cancel(request: str, lease: str) -> bool:
    try:
        request_id = int(request)
        epoch = int(lease) if lease else _state.epoch
    except (TypeError, ValueError):
        return False
    import time

    key = (epoch, request_id)
    entry = _entry(key)
    if entry is None:
        # Cancel arrived before the request: leave a tombstone so the late
        # request is refused, and acknowledge now.
        _remember(key, 'cancelled', time.monotonic())
        _send_result(epoch, request_id, 'refused', 'cancelled')
        return True
    if entry.state == 'pending':
        entry.state = 'cancelled'
        simsight_log.line(f'travel: keyboard entry request {request_id} cancelled')
        _send_result(epoch, request_id, 'refused', 'cancelled')
        return True
    _resend(entry)  # duplicate cancel or a finished request: answer again
    return True


@sims4.commands.Command(
    'simsight.travel_cancel', 'a11y.travel_cancel', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_travel_cancel(request: str = '0', lease: str = '', _connection: Any = None) -> bool:
    """Flash invalidates one exact request (another M, Escape, zone change,
    its bounded timeout). Applied at ingress, never queued, so it always
    beats a push still waiting on a stalled pump; the acknowledgement is
    sent at once and Flash keeps the request pending until it arrives."""
    try:
        return _travel_cancel(request, lease)
    except Exception:
        simsight_log.error('travel: simsight.travel_cancel')
        return False

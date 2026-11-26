"""Which scanner objects the active Sim can use right now.

Mirrors how the native pie decides what to list (interactions/choices.py,
checked against the 1.128.90 bytecode because the decompile prints add_aop
with its test inverted): a passing aop is listed, a failing aop with a
tooltip is listed greyed, and a failing aop without one, a suppressed aop,
or an affordance that is not user-directed is not listed at all. Per object:

  usable   at least one listed row passes
  locked   every listed row is greyed (requirements the pie explains)
  empty    the pie would list nothing for this Sim
  pending  not checked yet for this Sim; shown, never hidden

Simulation thread only. A check is a generator that pauses after every
native test, so pump ticks share a small time budget even inside one
object. Nothing here builds a ChoiceMenu or touches the client's choices.
A verdict that keeps coming back the same is checked less often, and an
object whose checks are slow waits longer still, up to MAX_RECHECK_SECONDS.
"""

from __future__ import annotations

import contextlib
import sys
import time
from typing import TYPE_CHECKING, Any, NamedTuple

import sims4.commands

import simsight_log
import simsight_pump as pump
import simsight_settings as settings

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable

    # (state, why, failed) once a check is finished.
    Outcome = tuple[str, str, bool]
    # A check: pauses (yields None) after each native test, returns its outcome.
    Evaluation = Generator[None, None, Outcome]

PENDING = 'pending'
USABLE = 'usable'
LOCKED = 'locked'
EMPTY = 'empty'

RECHECK_SECONDS = 10.0  # first recheck; doubles while the verdict holds
MAX_RECHECK_SECONDS = 60.0
SLOW_STEP_MS = 16.0  # one native step at least this long is a hitch
BUDGET_SECONDS = 0.004  # per pump tick; at least one step always runs
MAX_STEPS = 400  # rows one object may cost before it stays listed
_REARM_SECONDS = 0.5  # the pump drops its oldest entry when full
_LOG_SECONDS = 30.0  # unchanged run summaries are logged this often
_VERDICT_LIMIT = 2000  # verdicts kept for objects out of the scan, oldest dropped


class _Verdict(NamedTuple):
    state: str
    checked_at: float
    why: str
    failed: bool
    streak: int  # how many checks in a row came back the same
    due: float  # monotonic time of the next check
    cost_ms: float  # slowest native step of the check


class _Check:
    """The check in progress, resumable across ticks."""

    __slots__ = ('at', 'cost_ms', 'gen', 'label', 'obj_id', 'phase', 'steps')

    def __init__(self, obj_id: int, label: str) -> None:
        self.obj_id = obj_id
        self.label = label
        self.gen: Evaluation | None = None
        self.steps = 0
        self.cost_ms = 0.0
        self.phase = 'setup'
        self.at = ''


class _Run:
    """Numbers for the run summary in the log."""

    def __init__(self) -> None:
        self.checked = 0
        self.max_tick_ms = 0.0
        self.max_step_ms = 0.0
        self.slow_steps = 0
        self.slowest = ''


class _State:
    def __init__(self) -> None:
        self.sim_id: Any = None
        self.token = 0  # only the most recently posted tick does work
        self.verdicts: dict[int, _Verdict] = {}
        self.queue: list[int] = []
        self.in_scan: set[int] = set()  # ids in the latest begin(); counts cover these only
        self.current: _Check | None = None
        self.revision = 0
        self.scheduled_at: float | None = None  # when the pending tick was posted, None if idle
        self.errors_logged: set[tuple[Any, int]] = set()
        self.last_tick_ms = 0.0
        self.run = _Run()
        self.logged_at: float | None = None
        self.logged_counts: str | None = None

    def busy(self) -> bool:
        return bool(self.queue) or self.current is not None


_state = _State()


def _shown(state: str) -> str:
    # What the scanner does with a verdict. Only a change here needs a rebuild.
    if state == EMPTY:
        return 'hidden'
    return 'tagged' if state == LOCKED else 'plain'


def _bump() -> None:
    _state.revision += 1


def _interval(streak: int, cost_ms: float) -> float:
    seconds = RECHECK_SECONDS * (2 ** min(max(streak, 1) - 1, 6))
    if cost_ms >= SLOW_STEP_MS:
        seconds *= 2
    return min(seconds, MAX_RECHECK_SECONDS)


def _close(gen: Evaluation | None) -> None:
    if gen is None:
        return
    # Closing runs the paused EA code's cleanup; its failure changes nothing here.
    with contextlib.suppress(Exception):
        gen.close()


def _drop_current() -> None:
    if _state.current is not None:
        _close(_state.current.gen)
    _state.current = None


def _cancel_work() -> None:
    del _state.queue[:]
    _drop_current()
    _state.token += 1
    _state.scheduled_at = None


def _forget_all() -> None:
    # A new Sim or zone: its first run summary is always logged.
    _state.verdicts.clear()
    _state.in_scan.clear()
    _cancel_work()
    _state.run = _Run()
    _state.logged_counts = None
    _bump()


def revision() -> int:
    return _state.revision


def _wanted() -> bool:
    return bool(settings.get('keyboard', True)) and bool(settings.get('scanner', True))


def _ui_busy() -> bool:
    # A dialog, the console or a scanner menu owns input. A native step
    # cannot be preempted, so the worker steps aside until the next scan.
    # Looked up, not imported: keys and scanner import this module.
    keys = sys.modules.get('simsight_keys')
    if keys is not None and (
        getattr(keys, '_modal_active', False) or getattr(keys, '_editing_active', False)
    ):
        return True
    scanner = sys.modules.get('simsight_scanner')
    return bool(scanner is not None and getattr(scanner, '_menu_open', False))


def begin(sim: Any, objects: Iterable[Any]) -> int:
    """Queue this scan's candidates that are due; keep every known verdict.
    Never raises: anything unchecked simply stays pending, and listed."""
    try:
        return _begin(sim, objects)
    except Exception:
        simsight_log.error('availability: begin')
        return _state.revision


def _sort_scan(objects: Iterable[Any], busy: int | None, now: float) -> tuple[set[int], list[int]]:
    """(ids in this scan, ids to check: never checked first, then most overdue)."""
    fresh = []
    due = []
    keep: set[int] = set()
    for obj in objects:
        obj_id = getattr(obj, 'id', None)
        if obj_id is None or obj_id in keep:
            continue
        keep.add(obj_id)
        if obj_id == busy:
            continue
        verdict = _state.verdicts.get(obj_id)
        if verdict is None:
            fresh.append(obj_id)
        elif now >= verdict.due:
            due.append((verdict.due, obj_id))
    due.sort()
    return keep, fresh + [obj_id for _, obj_id in due]


def _trim_verdicts(keep: set[int]) -> None:
    # Objects that left the scan keep their verdicts, so walking back in
    # range shows the last known state at once; only the oldest go.
    spare = [
        (verdict.checked_at, obj_id)
        for obj_id, verdict in _state.verdicts.items()
        if obj_id not in keep
    ]
    if len(spare) > _VERDICT_LIMIT:
        spare.sort()
        for _, obj_id in spare[: len(spare) - _VERDICT_LIMIT]:
            del _state.verdicts[obj_id]


def _begin(sim: Any, objects: Iterable[Any]) -> int:
    sim_id = getattr(sim, 'id', None)
    if sim_id != _state.sim_id:
        _forget_all()
        _state.sim_id = sim_id
    if sim_id is None:
        return _state.revision
    now = time.monotonic()
    busy = _state.current.obj_id if _state.current is not None else None
    keep, queue = _sort_scan(objects, busy, now)
    _trim_verdicts(keep)
    if busy is not None and busy not in keep:
        _drop_current()
    _state.in_scan.clear()
    _state.in_scan.update(keep)
    _state.queue[:] = queue
    _ensure_ticking(now)
    return _state.revision


def state(obj_or_id: Any) -> str:
    try:
        obj_id = obj_or_id if isinstance(obj_or_id, int) else obj_or_id.id
        if _state.busy():
            _ensure_ticking(time.monotonic())
        verdict = _state.verdicts.get(obj_id)
        return PENDING if verdict is None else verdict.state
    except Exception:
        simsight_log.error('availability: state')
        return PENDING


def invalidate(obj_ids: Iterable[int] | None = None) -> None:
    if obj_ids is None:
        _state.verdicts.clear()
        _drop_current()
    else:
        for obj_id in obj_ids:
            _state.verdicts.pop(obj_id, None)
            if _state.current is not None and _state.current.obj_id == obj_id:
                _drop_current()
    _bump()


def recheck(obj_ids: Iterable[int] | None = None) -> None:
    """Check again at the next begin(), keeping the last-known verdicts
    meanwhile, so an explicit rescan does not make hidden objects flicker."""
    for obj_id, verdict in list(_state.verdicts.items()):
        if obj_ids is None or obj_id in obj_ids:
            _state.verdicts[obj_id] = verdict._replace(due=0.0)


def pause() -> None:
    """Stop checking (the scanner went idle) but keep everything known, so
    the next scan shows the last filtered list at once and only rechecks
    what has come due."""
    _cancel_work()


def on_active_sim_changed(old_sim: Any = None, new_sim: Any = None) -> None:
    # Signature matches client.register_active_sim_changed.
    _forget_all()
    _state.sim_id = None


def reset() -> None:
    _forget_all()
    _state.sim_id = None
    _state.errors_logged.clear()


def _ensure_ticking(now: float) -> None:
    # A re-post supersedes the earlier tick, so a delayed tick and its
    # replacement can never both work in the same pump drain.
    if not _state.busy():
        return
    if _state.scheduled_at is not None and now - _state.scheduled_at < _REARM_SECONDS:
        return
    _state.token += 1
    if pump.post(_tick, _state.token):
        _state.scheduled_at = now


def _label(obj: Any) -> str:
    try:
        return str(getattr(obj.definition, 'name', '') or type(obj).__name__)[:40]
    except AttributeError:
        return '?'


def _tick(token: int | None = None) -> None:
    if token != _state.token:
        return
    _state.scheduled_at = None
    if not _state.busy():
        return
    if not _wanted():
        reset()
        return
    if _ui_busy():
        pause()
        return
    try:
        import services

        sim = services.get_active_sim()
        client = services.client_manager().get_first_client()
        manager = services.object_manager()
    except Exception as ex:
        simsight_log.line(f'availability: services unavailable {ex!r}')
        _cancel_work()
        return
    if sim is None or client is None or sim.id != _state.sim_id:
        # Verdicts belong to the Sim they were made for; the next scan's
        # begin() starts over for whoever is active now.
        _forget_all()
        return
    _work(sim, client, manager)


def _work(sim: Any, client: Any, manager: Any) -> None:
    """Run checks until this tick's time budget is spent."""
    run = _state.run
    started = time.perf_counter()
    deadline = started + BUDGET_SECONDS
    worked = False
    while _state.busy():
        # Every pass counts against the budget, a vanished object included.
        if worked and time.perf_counter() >= deadline:
            break
        worked = True
        check = _state.current or _start_next(sim, client, manager)
        if check is not None:
            _advance(check)
    _state.last_tick_ms = (time.perf_counter() - started) * 1000.0
    run.max_tick_ms = max(run.max_tick_ms, _state.last_tick_ms)
    if _state.busy():
        _ensure_ticking(time.monotonic())
    elif run.checked:
        _log_run()


def _start_next(sim: Any, client: Any, manager: Any) -> _Check | None:
    """Start checking the next queued object; None when it is gone."""
    obj_id = _state.queue.pop(0)
    try:
        obj = manager.get(obj_id)
    except Exception as ex:
        _store(obj_id, USABLE, repr(ex)[:80], True, 0.0)
        return None
    if obj is None:
        _state.verdicts.pop(obj_id, None)
        return None
    check = _Check(obj_id, _label(obj))
    check.gen = _evaluation(sim, client, obj, check)
    _state.current = check
    return check


def _advance(check: _Check) -> None:
    """One step of the current check, timed for the run summary."""
    run = _state.run
    step_started = time.perf_counter()
    outcome = _step(check)
    step_ms = (time.perf_counter() - step_started) * 1000.0
    check.cost_ms = max(check.cost_ms, step_ms)
    if step_ms >= SLOW_STEP_MS:
        run.slow_steps += 1
    if step_ms > run.max_step_ms:
        run.max_step_ms = step_ms
        run.slowest = f'{check.obj_id} {check.label} {check.phase} {check.at}'.strip()
    if outcome is not None:
        _state.current = None
        _store(check.obj_id, outcome[0], outcome[1], outcome[2], check.cost_ms)
        run.checked += 1


def _log_run() -> None:
    # Once per finished run at most, and an unchanged picture only every
    # _LOG_SECONDS; the numbers keep adding up in between.
    now = time.monotonic()
    counts = _counts_text()
    if (
        counts == _state.logged_counts
        and _state.logged_at is not None
        and now - _state.logged_at < _LOG_SECONDS
    ):
        return
    run = _state.run
    simsight_log.line(
        'availability: checked {} for sim {} ({}), slowest tick {:.1f} ms, '
        'slowest step {:.1f} ms, {} steps over {:.0f} ms{}'.format(
            run.checked,
            _state.sim_id,
            counts,
            run.max_tick_ms,
            run.max_step_ms,
            run.slow_steps,
            SLOW_STEP_MS,
            f', slowest at {run.slowest}' if run.slowest else '',
        )
    )
    _state.logged_at = now
    _state.logged_counts = counts
    _state.run = _Run()


class _LoggerProxy:
    """Stands in for objects.script_object.logger while a step runs.

    ScriptObject.potential_interactions catches its own exceptions and
    reports them with logger.exception, then simply stops yielding, which
    would read as an object with nothing to offer. The proxy records the
    report so the object stays listed, and keeps a background check from
    raising an error report the player never caused (a mouse click on the
    same object still reports it as before)."""

    def __init__(self, real: Any, watch: _SwallowWatch) -> None:
        self._real = real
        self._watch = watch

    def exception(self, message: Any = '', *args: Any, **kwargs: Any) -> None:
        self._watch.message = str(message)[:80]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _SwallowWatch:
    def __init__(self) -> None:
        self.message: str | None = None
        self._module: Any = None
        self._real: Any = None

    def __enter__(self) -> _SwallowWatch:
        try:
            import objects.script_object as module

            self._real = module.logger
            self._module = module
            module.logger = _LoggerProxy(self._real, self)
        except (ImportError, AttributeError):
            self._module = None  # no logger to watch: the step runs as it is
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._module is not None:
            self._module.logger = self._real


def _step(check: _Check) -> Outcome | None:
    """Advance one check by one native test; its outcome when finished."""
    check.steps += 1
    watch = _SwallowWatch()
    outcome = None
    try:
        with watch:
            next(check.gen)  # type: ignore[arg-type]  # set before the first step
    except StopIteration as done:
        outcome = done.value
    except Exception as ex:
        outcome = (USABLE, repr(ex)[:80], True)
    if watch.message is not None:
        outcome = (USABLE, 'swallowed: ' + watch.message, True)
    elif outcome is None and check.steps >= MAX_STEPS:
        outcome = (USABLE, f'over {MAX_STEPS} rows', True)
    if outcome is not None:
        _close(check.gen)
    return outcome


def _store(obj_id: int, verdict: str, why: str, failed: bool, cost_ms: float) -> None:
    old = _state.verdicts.get(obj_id)
    streak = 1
    if old is not None and old.state == verdict and bool(old.failed) == bool(failed):
        # The same outcome again, an error included: an object that always
        # fails (listed, fail-open) need not be retried every few seconds.
        streak = old.streak + 1
    now = time.monotonic()
    _state.verdicts[obj_id] = _Verdict(
        verdict, now, why, failed, streak, now + _interval(streak, cost_ms), cost_ms
    )
    if failed and (_state.sim_id, obj_id) not in _state.errors_logged:
        _state.errors_logged.add((_state.sim_id, obj_id))
        simsight_log.line(f'availability: check failed for {obj_id}, kept listed: {why}')
    if _shown(verdict) != _shown(PENDING if old is None else old.state):
        _bump()


def _name(affordance: Any) -> str:
    return getattr(affordance, '__name__', None) or str(affordance)


class _Probe(NamedTuple):
    """What every step of one object's check uses."""

    check: _Check
    choice_menu: Any  # interactions.choices.ChoiceMenu
    show_hidden: bool  # the cheat that lists hidden rows with their reason


class _Place(NamedTuple):
    """Where the pie would be opened: the picked object and what it forwards to."""

    sim: Any
    client: Any
    obj: Any
    lot_id: int
    level: int
    parameters: dict[str, Any]
    location: Any
    surface: Any
    targets: list[Any]


class _RowPass(NamedTuple):
    outcome: Outcome | None
    locked_by: Any  # the first row the pie greys out
    parents: list[tuple[Any, Any, Any]]  # (aop, result, context) listed only through mixers


def _int_or_zero(read: Callable[[], Any]) -> int:
    try:
        return int(read())
    except Exception:
        return 0  # the game's own default when the value is missing


def _place(sim: Any, client: Any, obj: Any) -> _Place:
    import services

    lot_id = _int_or_zero(lambda: services.active_lot().lot_id)
    level = _int_or_zero(lambda: obj.level)
    parameters = client.get_interaction_parameters() or {}
    location = obj.position
    surface = obj.routing_surface
    targets = [obj]
    targets.extend(
        target
        for target in obj.forwarded_pick_targets or ()
        if target is not None and target is not obj
    )
    return _Place(sim, client, obj, lot_id, level, parameters, location, surface, targets)


def _evaluation(sim: Any, client: Any, obj: Any, check: _Check) -> Evaluation:
    """Generator: pauses after each native test, returns (state, why, failed).
    check.phase and check.at name the work the latest step did."""
    from interactions import choices
    from server.pick_info import PickInfo, PickType

    probe = _Probe(
        check,
        choices.ChoiceMenu,
        bool(getattr(choices, '_show_interaction_failure_reason', False)),
    )
    place = _place(sim, client, obj)
    yield
    rows = yield from _row_pass(probe, place, PickInfo, PickType)
    if rows.outcome is not None:
        return rows.outcome
    outcome, locked_by = yield from _mixer_pass(probe, rows.parents, rows.locked_by)
    if outcome is not None:
        return outcome
    if locked_by is not None:
        return LOCKED, _name(locked_by), False
    check.phase = 'special'
    return _special(place)


def _pick_context(place: Any, target: Any, pick_info: Any, pick_type: Any) -> Any:
    # As _add_potential_object_aops: one pick and context per target, at
    # the picked object's location, surface and level, preferring it.
    pick = pick_info(
        pick_type=pick_type.PICK_OBJECT,
        target=target,
        location=place.location,
        routing_surface=place.surface,
        lot_id=place.lot_id,
        level=place.level,
    )
    context = place.client.create_interaction_context(place.sim, pick=pick)
    context.add_preferred_objects((place.obj,))
    return context


def _row_pass(
    probe: _Probe, place: _Place, pick_info: Any, pick_type: Any
) -> Generator[None, None, _RowPass]:
    """The pie's own rows for each target, one native test per step."""
    check = probe.check
    locked_by = None
    parents: list[tuple[Any, Any, Any]] = []
    for target in place.targets:
        check.phase = 'rows'
        context = _pick_context(place, target, pick_info, pick_type)
        for aop in target.potential_interactions(context, **place.parameters):
            affordance = aop.affordance
            check.at = _name(affordance)
            result = probe.choice_menu.is_valid_aop(aop, context, user_pick_target=target)
            if affordance.allow_user_directed is False:
                if probe.show_hidden and locked_by is None:
                    locked_by = affordance
                if result or result.tooltip:
                    parents.append((aop, result, context))
            elif result:
                return _RowPass((USABLE, _name(affordance), False), locked_by, parents)
            elif result.tooltip and locked_by is None:
                locked_by = affordance
            yield
        check.phase = 'rows end'
    return _RowPass(None, locked_by, parents)


def _mixer_pass(
    probe: _Probe, parents: list[tuple[Any, Any, Any]], locked_by: Any
) -> Generator[None, None, tuple[Outcome | None, Any]]:
    """Rows the pie lists through a hidden parent: (outcome, locked_by)."""
    # A greyed row's mixers inherit its failure, so they can add greyed
    # rows only; walk the passing parents first, they may add usable ones.
    parents.sort(key=lambda parent: not parent[1])
    for aop, result, context in parents:
        if not result and locked_by is not None:
            break
        outcome, locked_by = yield from _parent_mixers(probe, aop, result, context, locked_by)
        if outcome is not None:
            return outcome, locked_by
    return None, locked_by


def _parent_mixers(
    probe: _Probe, aop: Any, result: Any, context: Any, locked_by: Any
) -> Generator[None, None, tuple[Outcome | None, Any]]:
    check = probe.check
    parent_passed = bool(result)
    check.phase = 'mixers'
    check.at = _name(aop.affordance)
    sub_rows = aop.affordance.potential_pie_menu_sub_interactions_gen(
        aop.target, context, None, **aop.interaction_parameters
    )
    for mixer_aop, mixer_result in sub_rows or ():
        mixer = mixer_aop.affordance
        if mixer.allow_user_directed is False:
            if probe.show_hidden and locked_by is None:
                locked_by = mixer
        else:
            checked = probe.choice_menu.is_valid_aop(
                mixer_aop,
                context,
                result_override=mixer_result if parent_passed else result,
                do_test=False,
            )
            if checked:
                return (USABLE, _name(mixer), False), locked_by
            if getattr(checked, 'tooltip', None) and locked_by is None:
                locked_by = mixer
        yield
        if not parent_passed and locked_by is not None:
            break
    return None, locked_by


def _special(place: _Place) -> Outcome:
    # Rows this first cut does not probe: the pie adds terrain or ocean
    # points for surface providers, and fire rows when a nearby fire would
    # otherwise leave it empty. Keep those objects listed rather than guess.
    import services

    obj = place.obj
    if (
        getattr(obj, 'provided_routing_surface', None) is not None
        or getattr(obj, 'provides_terrain_interactions', False)
        or getattr(obj, 'provides_ocean_interactions', False)
    ):
        return USABLE, 'surface provider, not probed', False
    fire_service = services.get_fire_service()
    if fire_service is not None and fire_service.fire_is_active:
        surface = place.surface
        if fire_service.get_fires_in_potential_targets(
            [(target, surface) for target in place.targets]
        ):
            return USABLE, 'fire nearby, not probed', False
    return EMPTY, '', False


def _counts() -> dict[str, int]:
    counts = {USABLE: 0, LOCKED: 0, EMPTY: 0, 'errors': 0}
    for obj_id, verdict in _state.verdicts.items():
        if obj_id not in _state.in_scan:
            continue
        counts[verdict.state] += 1
        if verdict.failed:
            counts['errors'] += 1
    return counts


def _counts_text() -> str:
    counts = _counts()
    return 'usable {} locked {} empty {} errors {}'.format(
        counts[USABLE], counts[LOCKED], counts[EMPTY], counts['errors']
    )


def status() -> str:
    current = _state.current
    return 'availability: sim {} {} queued {} busy {} last tick {:.1f} ms revision {}'.format(
        _state.sim_id,
        _counts_text(),
        len(_state.queue),
        current.obj_id if current is not None else '-',
        _state.last_tick_ms,
        _state.revision,
    )


def _nearest(sim: Any, manager: Any) -> list[tuple[float, Any, _Verdict]]:
    """(squared distance, object, verdict) of every known object, nearest first."""
    rows = []
    for obj_id, verdict in _state.verdicts.items():
        obj = manager.get(obj_id)
        if obj is None or sim is None:
            continue
        dx = float(obj.position.x) - float(sim.position.x)
        dz = float(obj.position.z) - float(sim.position.z)
        rows.append((dx * dx + dz * dz, obj, verdict))
    rows.sort(key=lambda row: row[0])
    return rows


@sims4.commands.Command(
    'simsight.availability', 'a11y.availability', command_type=sims4.commands.CommandType.Live
)
def a11y_availability(_connection: Any = None) -> None:
    output = sims4.commands.CheatOutput(_connection)
    output(status())
    try:
        import services

        now = time.monotonic()
        rows = _nearest(services.get_active_sim(), services.object_manager())
        for distance, obj, verdict in rows[:10]:
            next_in = max(0.0, verdict.due - now)
            output(
                f'  {distance**0.5:.1f} m {_label(obj)} {verdict.state} {verdict.why} '
                f'({verdict.cost_ms:.1f} ms, next in {next_in:.0f} s)'
            )
        slow = sorted(_state.verdicts.items(), key=lambda item: -item[1].cost_ms)[:3]
        output(
            '  slowest: '
            + ', '.join(f'{obj_id} {verdict.cost_ms:.1f} ms' for obj_id, verdict in slow)
        )
    except Exception as ex:
        output(f'availability: listing failed {ex!r}')

"""Step-by-step build jobs: a worker thread on Windows, the game's tick on the Mac.

A job is a generator. It yields what it waits for and is sent the answer:

    value = yield steps.OnMain(fn, *args)   # fn(*args) on the simulation thread
    yield steps.Sleep(0.5)                  # half a second
    was_set = yield steps.WaitEvent(ev, 6)  # ev.set(), or 6 seconds at most

start(name, job) runs it. On Windows that is a daemon thread named `name`
that sleeps and waits exactly as the threaded workers always did: OnMain
posts to the Plus pump and waits up to 10 seconds for the answer. The Mac
game crashes when a module is imported for the first time off the
simulation thread, so there the job runs on the Plus pump itself: OnMain
runs at once, and a sleep or wait resumes on the first pump tick after it is
over (the pump ticks every 0.1 s). Same steps, same order, same waits.

run(job) runs a job to its end on the calling worker thread (Windows only):
the blocking helpers the mouse workers call (fetch_catalog, select_item) are
run() of their step versions.

after(seconds, post, fn, *args) calls post(fn, *args) once the time is up:
from a threading.Timer on Windows, from the Plus pump on the Mac.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import simsight_log
import simsight_plus_core as core

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import TypeVar, Union

    T = TypeVar('T')
    Step = Union['Sleep', 'OnMain', 'WaitEvent']
    Job = Generator[Step, Any, T]

IS_MAC = sys.platform == 'darwin'
ON_MAIN_SECONDS = 10.0  # how long a worker waits for the simulation thread


class Sleep:
    """Wait this many seconds."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds


class OnMain:
    """fn(*args) on the simulation thread; the job is sent its result (None if it raised)."""

    def __init__(self, fn: Callable[..., Any], *args: Any) -> None:
        self.fn = fn
        self.args = args


class WaitEvent:
    """Wait until event is set or timeout seconds pass; the job is sent whether it was set."""

    def __init__(self, event: threading.Event, timeout: float) -> None:
        self.event = event
        self.timeout = timeout


def _call(fn: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    """On the simulation thread: fn(*args), or None (logged) when it raises."""
    try:
        return fn(*args)
    except Exception:
        simsight_log.error(core.log_exc('on_main'))
        return None


# ---------------------------------------------------------------- Windows: worker threads


def on_main(fn: Callable[..., Any], *args: Any) -> Any:
    """Worker thread only: run fn on the game thread and wait for its result."""
    ev = threading.Event()
    box: list[Any] = []

    def go() -> None:
        box.append(_call(fn, args))
        ev.set()

    core.post(go)
    ev.wait(ON_MAIN_SECONDS)
    return box[0] if box else None


def _blocking(step: Step) -> Any:
    if isinstance(step, Sleep):
        time.sleep(step.seconds)
        return None
    if isinstance(step, OnMain):
        return on_main(step.fn, *step.args)
    return step.event.wait(step.timeout)


def run(job: Job[T]) -> T:
    """Worker thread only: run job to its end, sleeping and waiting on this thread."""
    answer: Any = None
    while True:
        try:
            step = job.send(answer)
        except StopIteration as stop:
            return stop.value
        answer = _blocking(step)


# ---------------------------------------------------------------- Mac: the Plus pump


class _Task:
    def __init__(self, name: str, job: Job[Any]) -> None:
        self.name = name
        self.job = job
        self.step: Step | None = None  # what it waits for; None: not started
        self.until = 0.0  # when that sleep or wait is over

    def ready(self, now: float) -> tuple[bool, Any]:
        """(whether the job can go on, what to send it)."""
        step = self.step
        if isinstance(step, WaitEvent):
            if step.event.is_set():
                return True, True
            return now >= self.until, False
        if isinstance(step, Sleep):
            return now >= self.until, None
        return True, None

    def advance(self, now: float) -> bool:
        """Run the job up to its next unfinished wait. False once it has ended."""
        go_on, answer = self.ready(now)
        if not go_on:
            return True
        while True:
            try:
                step = self.job.send(answer)
            except StopIteration:
                return False
            except Exception:
                simsight_log.error(f'steps: {self.name}')
                return False
            if isinstance(step, OnMain):
                answer = _call(step.fn, step.args)
                continue
            self.step = step
            self.until = now + (step.seconds if isinstance(step, Sleep) else step.timeout)
            go_on, answer = self.ready(now)
            if not go_on:
                return True


class _Pump:
    def __init__(self) -> None:
        self.tasks: list[_Task] = []
        self.ticking = False


_pump = _Pump()


def _tick() -> None:
    """A Plus pump ticker (simulation thread): move every waiting job on."""
    now = time.monotonic()
    for task in list(_pump.tasks):
        if not task.advance(now):
            _pump.tasks.remove(task)


def start(name: str, job: Job[Any]) -> None:
    """Run job: on its own thread on Windows, from the next pump tick on the Mac."""
    if not IS_MAC:
        threading.Thread(target=run, args=(job,), name=name, daemon=True).start()
        return
    if not _pump.ticking:
        _pump.ticking = True
        core.add_ticker(0.0, _tick)
    _pump.tasks.append(_Task(name, job))


def _later(
    seconds: float, post: Callable[..., Any], fn: Callable[..., Any], args: Any
) -> Job[None]:
    yield Sleep(seconds)
    post(fn, *args)


def after(
    seconds: float,
    post: Callable[..., Any],
    fn: Callable[..., Any],
    *args: Any,
    daemon: bool = True,
) -> None:
    """post(fn, *args) once seconds have passed (a timeout, a fallback)."""
    if IS_MAC:
        start('after ' + getattr(fn, '__name__', '?'), _later(seconds, post, fn, args))
        return
    timer = threading.Timer(seconds, lambda: post(fn, *args))
    timer.daemon = daemon
    timer.start()

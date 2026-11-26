"""SimSight.log: one log for the whole mod.

- line(): routine notes, written only when logging is on (mod menu, off by
  default).
- error(): something failed. Always written, logging on or off, with the
  traceback. The same place failing again (a tick handler can fail 30 times
  a second) is only counted, and the count is written now and then, so the
  log stays small and the first traceback stays readable.

Every module imports this one; it imports nothing of the mod or the game,
so it works even when the rest failed to load.
"""

from __future__ import annotations

import os
import time
import traceback

enabled = False  # set by simsight_speech from the logging setting

_REPEAT_EVERY = 100  # write "failed again" once per this many repeats
_failures: dict[str, int] = {}


def _mods_folder() -> str:
    # __file__ is inside the ts4script zip: <Mods>/SimSight/SimSight.ts4script/x.pyc.
    # The user folder name is localized: never hardcode it.
    path = os.path.normpath(__file__)
    lower = path.lower()
    marker = '.ts4script'
    if marker in lower:
        return os.path.dirname(path[: lower.index(marker) + len(marker)])
    return os.path.dirname(path)


PATH = os.path.join(_mods_folder(), 'SimSight.log')


def _write(text: str) -> None:
    try:
        with open(PATH, 'a', encoding='utf-8') as handle:
            handle.write('[{}] {}\n'.format(time.strftime('%H:%M:%S'), text))
    except OSError:
        pass  # no log is better than a mod that stops: nowhere else to report it


def line(message: str) -> None:
    """A routine note, when logging is on."""
    if enabled:
        _write(message)


def error(where: str, exc: BaseException | None = None) -> None:
    """Something failed in `where` (a short stable label, like 'phone: answer').

    Call it from an except block: the traceback of the exception being
    handled is written the first time; repeats are counted.
    """
    count = _failures.get(where, 0) + 1
    _failures[where] = count
    if count == 1:
        detail = (
            ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            if exc is not None
            else traceback.format_exc()
        )
        _write(f'ERROR {where}:\n{detail.rstrip()}')
    elif count % _REPEAT_EVERY == 0:
        _write(f'ERROR {where}: failed again ({count} times so far)')


def failures() -> dict[str, int]:
    """How often each place failed since the game started (for problem reports)."""
    return dict(_failures)

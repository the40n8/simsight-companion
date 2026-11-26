"""SimSight Plus, merged into SimSight (2026-10-08).

SimSight Plus 0.7.1 was a separate add-on (SimSight_Plus.ts4script) by
Michael Maslo, contributed through LeonianUniverse: Alt-key readouts (needs,
time and work, rooms, notifications), low-need, work and bill reminders, spoken game speed and
Build/Live mode, Create and edit Sims without Create a Sim, a brand-new
family moved onto a lot, and a Build mode helper that builds rooms and whole
houses with the game's own tools. Its modules are simsight_plus_*.py; its
catalog bridge now lives in TooltipManager_patched.as (A11yPlusHandle).

Each module is imported on its own so one failure cannot stop the others or
SimSight. Its log is SimSight_Plus.log next to the mod and in the
Windows user folder. Do not also install SimSight_Plus.ts4script: both
would poll the same Alt keys.
"""

from __future__ import annotations

import importlib

import simsight_log

MODULES = (
    'core',
    'menu',
    'needs',
    'notify',
    'time',
    'rooms',
    'sims',
    'family',
    'build',
    'house',
    'furnish',
    'decor',
    'draw',
)
loaded: list[str] = []
failed: list[str] = []


def _load_one(name: str) -> None:
    try:
        importlib.import_module('simsight_plus_' + name)
    except Exception:  # one broken feature must not stop the others
        failed.append(name)
        simsight_log.error(f'plus: {name} did not load')
    else:
        loaded.append(name)


for _module in MODULES:
    _load_one(_module)

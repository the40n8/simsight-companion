"""Load only local extensions not integrated into SimSight itself."""

from __future__ import annotations

import importlib
import sys

import simsight_log

# They move the real mouse through Windows APIs (ctypes); the Mac build
# leaves them out.
WINDOWS_ONLY = {'simsight_build_coordinates', 'simsight_build_helpers'}

# (module, whether its install() must be called), in load order. Scanner
# ranges, moving furniture, room labels, wall/surface placement and layer keys
# now belong to the native modules and key loop.
_EXTENSIONS: tuple[tuple[str, bool], ...] = (
    ('simsight_chair_alignment', False),
    ('simsight_roof_tools', False),
    ('simsight_house_planner', False),
    ('simsight_lot_grid', False),
    ('simsight_detached', False),
    ('simsight_build_coordinates', True),
    ('simsight_build_helpers', True),
    ('simsight_basement', True),
    ('simsight_house_workflow', True),
)


class _State:
    def __init__(self) -> None:
        self.installed = False


_state = _State()


def install() -> None:
    """Import every extension once; one that fails is logged and skipped."""
    if _state.installed:
        return
    _state.installed = True
    for name, explicit in _EXTENSIONS:
        if name in WINDOWS_ONLY and sys.platform != 'win32':
            continue
        try:
            module = importlib.import_module(name)
            if explicit:
                module.install()
        except Exception:
            simsight_log.error(f'extensions: local extension {name}')
    simsight_log.line('local extensions installed')

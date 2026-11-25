"""Load only local extensions not integrated into SimSight itself."""
import sys

import simsight_speech as speech

# They move the real mouse through Windows APIs (ctypes); the Mac build
# leaves them out.
WINDOWS_ONLY = {'simsight_build_coordinates', 'simsight_build_helpers'}

_installed = False

def install():
    global _installed
    if _installed:
        return
    _installed = True
    # Scanner ranges, moving furniture, room labels, wall/surface placement
    # and layer keys now belong to the native modules and key loop.
    import importlib
    for name, explicit in (
        ('simsight_chair_alignment', False),
        ('simsight_roof_tools', False),
        ('simsight_house_planner', False),
        ('simsight_lot_grid', False),
        ('simsight_detached', False),
        ('simsight_build_coordinates', True),
        ('simsight_build_helpers', True),
        ('simsight_basement', True),
        ('simsight_house_workflow', True),
    ):
        if name in WINDOWS_ONLY and sys.platform != 'win32':
            continue
        try:
            module = importlib.import_module(name)
            if explicit:
                module.install()
        except Exception as exc:
            speech.log_line('local extension {} failed: {!r}'.format(name, exc))
    speech.log_line('local extensions installed')


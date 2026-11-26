"""List every console command of the mod with its argument types, as EA's
console parser sees them (the annotations at registration time).

    <python3.7> -I tools/command_types.py <Scripts dir> [mac|windows]

Prints JSON {command name: {parameter: type name}}. Compare the output for
two trees to prove a change kept what the player can type: EA's parser
converts typed arguments only when the annotation is a class.
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mac', 'tests'))
from test_imports import WINDOWS_ONLY, GameFinder, GameModule  # noqa: E402

COMMANDS = {}


def _command(*names, **_options):
    def register(func):
        types = {}
        for param, annotation in getattr(func, '__annotations__', {}).items():
            if param != 'return':
                types[param] = (
                    annotation.__name__ if isinstance(annotation, type) else repr(annotation)
                )
        for name in names:
            COMMANDS[name] = types
        return func

    return register


def main():
    scripts = sys.argv[1]
    platform = sys.argv[2] if len(sys.argv) > 2 else 'mac'
    sys.path.insert(0, scripts)
    if platform == 'windows':
        sys.path.insert(1, os.path.join(os.path.dirname(scripts), 'windows', 'vendor'))
        sys.platform = 'win32'
    sys.meta_path.append(GameFinder())
    commands = GameModule('sims4.commands')
    commands.__path__ = []
    commands.Command = _command
    sys.modules['sims4.commands'] = commands
    setattr(importlib.import_module('sims4'), 'commands', commands)  # noqa: B010 - a fake module
    failed = []
    for name in sorted(n[:-3] for n in os.listdir(scripts) if n.endswith('.py')):
        if platform == 'mac' and name in WINDOWS_ONLY:
            continue
        try:
            importlib.import_module(name)
        except BaseException as exc:  # noqa: BLE001 - report every kind of failure
            failed.append(f'{name}: {exc!r}')
    print(json.dumps({'commands': COMMANDS, 'failed': failed}, indent=1, sort_keys=True))
    sys.stdout.flush()
    os._exit(0)  # skip atexit hooks of the loaded modules


if __name__ == '__main__':
    main()

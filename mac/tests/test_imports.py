"""Every mod module loads, with any game module replaced by a stand-in.

Catches what breaks a module before it runs at all: a misspelled name at
load, a wrong import, a syntax only newer Pythons accept. It does not check
EA's APIs (the stand-ins accept anything). Runs on a copy of Scripts/ in a
temporary folder, so nothing is written next to the sources, once as on the
Mac and once as on Windows.
"""

import importlib
import importlib.abc
import importlib.machinery
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
# Built for Windows only (mac/build.py): they need ctypes at load.
WINDOWS_ONLY = {
    'simsight_prism',
    'simsight_build_coordinates',
    'simsight_display_diagnostic',
    'simsight_speech_process',
}


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return stub(name)

    def __call__(cls, *args, **kwargs):
        if len(args) == 1 and not kwargs and callable(args[0]):
            return args[0]  # used as a decorator: keep what it decorates
        return super().__call__()

    def __iter__(cls):
        return iter(())

    def __getitem__(cls, key):
        return stub('item')

    def __or__(cls, other):
        return cls

    __ror__ = __and__ = __add__ = __radd__ = __sub__ = __mul__ = __or__

    def __int__(cls):
        return 0

    __index__ = __int__

    def __float__(cls):
        return 0.0

    def __lt__(cls, other):
        return False

    __le__ = __gt__ = __ge__ = __lt__

    def __hash__(cls):
        return id(cls)


class Stub(metaclass=_Meta):
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return stub(name)

    def __call__(self, *args, **kwargs):
        if len(args) == 1 and not kwargs and callable(args[0]):
            return args[0]
        return self

    def __iter__(self):
        return iter(())


def stub(name):
    return _Meta(name, (Stub,), {})


class GameModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        value = stub(name)
        setattr(self, name, value)
        return value


class GameFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Last on the path: anything the stdlib and Scripts/ do not have is the game's."""

    def find_spec(self, fullname, path, target=None):
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

    def create_module(self, spec):
        module = GameModule(spec.name)
        module.__path__ = []
        return module

    def exec_module(self, module):
        pass


def mod_names(scripts):
    """Every top-level module and package of the mod (packages: each submodule too)."""
    names = []
    for entry in sorted(os.listdir(scripts)):
        path = os.path.join(scripts, entry)
        if entry.endswith('.py'):
            names.append(entry[:-3])
        elif os.path.isfile(os.path.join(path, '__init__.py')):
            names.append(entry)
            names.extend(
                f'{entry}.{sub[:-3]}'
                for sub in sorted(os.listdir(path))
                if sub.endswith('.py') and sub != '__init__.py'
            )
    return names


def _load_all(scripts, platform):
    """Child process: import every module, print the ones that failed."""
    sys.path.insert(0, scripts)
    if platform == 'windows':
        sys.path.insert(1, os.path.join(ROOT, 'windows', 'vendor'))
        sys.platform = 'win32'
    sys.meta_path.append(GameFinder())
    names = mod_names(scripts)
    failed = 0
    for name in names:
        if platform == 'mac' and name.split('.')[0] in WINDOWS_ONLY:
            continue
        try:
            importlib.import_module(name)
        except BaseException as exc:
            failed += 1
            print(f'{name}: {type(exc).__name__}: {exc}')
    sys.stdout.flush()
    os._exit(1 if failed else 0)  # skip atexit hooks of the loaded modules


class LoadEveryModule(unittest.TestCase):
    def _check(self, platform):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, 'Scripts')
            shutil.copytree(
                os.path.join(ROOT, 'Scripts'), scripts, ignore=shutil.ignore_patterns('__pycache__')
            )
            done = subprocess.run(
                [sys.executable, '-I', '-B', os.path.abspath(__file__), scripts, platform],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=120,
                check=False,
                cwd=tmp,
            )
        self.assertEqual(
            done.returncode, 0, f'modules that do not load ({platform}):\n{done.stdout}'
        )

    def test_mac(self):
        self._check('mac')

    def test_windows(self):
        self._check('windows')


if __name__ == '__main__':
    if len(sys.argv) == 3:
        _load_all(sys.argv[1], sys.argv[2])
    unittest.main()

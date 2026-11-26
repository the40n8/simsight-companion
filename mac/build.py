"""Compile Scripts/ into SimSight.ts4script for the game on macOS or Windows.

Python 3.7 .pyc (PEP 552 header) in a stored zip, but it writes only to the given folder and never deploys.
Usage: <python3.7> mac/build.py <repo root> <out dir> [mac|windows]
"""

import marshal
import os
import struct
import sys
import zipfile
from importlib.util import MAGIC_NUMBER

# Windows-only modules, never shipped on macOS: the game imports every module
# at load, and these need ctypes, which the Mac game does not have. The
# Windows game has the _ctypes extension but not the ctypes package, so the
# Windows build adds windows/vendor/ctypes (from CPython 3.7, PSF licence).
SKIP_DIRS = {'__pycache__'}
MAC_SKIP_FILES = {
    'simsight_prism.py',
    # Windows screen/DPI measurement and the SAPI kernel job (ctypes at load);
    # only the Windows build helpers that move the real mouse import them.
    'simsight_build_coordinates.py',
    'simsight_display_diagnostic.py',
    'simsight_speech_process.py',
}


def main():
    if sys.version_info[:2] != (3, 7):
        sys.exit('mac/build.py must run under Python 3.7 (the game loads 3.7 bytecode only)')
    roots = [os.path.join(sys.argv[1], 'Scripts')]
    out = sys.argv[2]
    platform = sys.argv[3] if len(sys.argv) > 3 else 'mac'
    if platform not in ('mac', 'windows'):
        sys.exit('platform must be mac or windows')
    if platform == 'windows':
        roots.append(os.path.join(sys.argv[1], 'windows', 'vendor'))
    skip_files = MAC_SKIP_FILES if platform == 'mac' else set()
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, 'SimSight.ts4script')
    count = 0
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_STORED) as zf:
        for src in roots:
            for folder, subs, files in os.walk(src):
                subs[:] = sorted(d for d in subs if d not in SKIP_DIRS)
                for name in sorted(files):
                    if not name.endswith('.py') or name in skip_files:
                        continue
                    full = os.path.join(folder, name)
                    rel = os.path.relpath(full, src).replace(os.sep, '/')
                    with open(full, encoding='utf-8') as handle:
                        code = compile(handle.read(), rel, 'exec', optimize=2)
                    header = MAGIC_NUMBER + struct.pack('<III', 0, 0, 0)
                    zf.writestr(rel[:-3] + '.pyc', header + marshal.dumps(code))
                    count += 1
    print('built {} modules ({}) -> {}'.format(count, platform, target))


if __name__ == '__main__':
    main()

"""Prove that a change to Scripts/ does not change what the game runs.

Compiles every module of two trees the way mac/build.py does (Python 3.7,
optimize=2: no docstrings, no asserts) and compares the code objects,
ignoring only line numbers and file names. Identical bytecode means the
game behaves exactly the same; use it for formatting and pure cleanups.

    <python3.7> -I tools/samecode.py <old Scripts dir> <new Scripts dir>

Exit status 0 when every module is the same, 1 otherwise (lists them).
"""

import os
import sys
import types

IGNORED = {'co_firstlineno', 'co_lnotab', 'co_filename'}
FIELDS = [name for name in dir(types.CodeType) if name.startswith('co_') and name not in IGNORED]


def same(a, b):
    if isinstance(a, types.CodeType) and isinstance(b, types.CodeType):
        return all(same(getattr(a, f), getattr(b, f)) for f in FIELDS)
    if isinstance(a, (tuple, frozenset)) and type(a) is type(b):
        if isinstance(a, frozenset):
            return a == b
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def modules(root):
    found = {}
    for folder, subs, files in os.walk(root):
        subs[:] = [d for d in subs if d not in ('__pycache__', 'ctypes')]
        for name in files:
            if name.endswith('.py'):
                full = os.path.join(folder, name)
                found[os.path.relpath(full, root)] = full
    return found


def code_of(path):
    with open(path, encoding='utf-8') as handle:
        return compile(handle.read(), path, 'exec', optimize=2)


def main():
    if sys.version_info[:2] != (3, 7):
        sys.exit("run with the game's Python 3.7")
    old, new = modules(sys.argv[1]), modules(sys.argv[2])
    changed = sorted(set(old) ^ set(new))
    for rel in sorted(set(old) & set(new)):
        if not same(code_of(old[rel]), code_of(new[rel])):
            changed.append(rel)
    for rel in changed:
        print('changed:', rel)
    print('{} modules compared, {} changed'.format(len(set(old) & set(new)), len(changed)))
    sys.exit(1 if changed else 0)


if __name__ == '__main__':
    main()

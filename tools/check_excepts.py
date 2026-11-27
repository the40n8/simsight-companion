"""Our rule for catching errors, checked on Scripts/ (tools/check.sh).

A broad handler (`except:`, `except Exception`, `except BaseException`)
is allowed only where it reports the failure: its body calls
`simsight_log.error(...)` or re-raises. Everywhere else, catch the
exceptions you expect (AttributeError, KeyError, ...): a broad handler
that stays silent hides bugs, as the moodlet and career errors showed.

    python3 tools/check_excepts.py Scripts

Prints each offending handler and exits 1 when there is one.
"""

import ast
import os
import sys

BROAD = {'Exception', 'BaseException'}


def broad(handler):
    kind = handler.type
    if kind is None:
        return True
    names = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(isinstance(n, ast.Name) and n.id in BROAD for n in names)


def reports(handler):
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == 'error'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'simsight_log'
            ):
                return True
    return False


def problems(path):
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and broad(node) and not reports(node):
            yield node.lineno


def main():
    found = 0
    for folder, _subs, files in os.walk(sys.argv[1]):
        for name in sorted(files):
            if name.endswith('.py'):
                path = os.path.join(folder, name)
                for line in problems(path):
                    found += 1
                    print(
                        f'{path}:{line}: broad except that does not log (simsight_log.error) or re-raise'
                    )
    if found:
        print(f'{found} broad excepts to narrow or report')
    sys.exit(1 if found else 0)


if __name__ == '__main__':
    main()

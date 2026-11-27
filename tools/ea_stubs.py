"""Type stubs for EA's game code, generated from the installed game.

The Sims 4 ships its gameplay code as compiled Python 3.7 (core.zip,
simulation.zip). There is no official API documentation, so this reads
that bytecode and writes .pyi stubs that mypy checks our calls against:

- every class with its base classes, methods (with their real parameter
  names and which ones have defaults), class attributes, tuned attributes
  (INSTANCE_TUNABLES / FACTORY_TUNABLES keys) and attributes set on self;
- module functions and names.

Types are not in the bytecode, so values are Any, except where
tools/ea_overlay.py gives a return type that was checked by hand. What
this catches: a method or attribute that does not exist, a wrong number of
arguments, a misspelled keyword.

    <python3.7> -I tools/ea_stubs.py "<The Sims 4.app>" <out dir> [module ... | --used-by Scripts]

With module names (or --used-by: the modules the mod imports), only those
are written, plus what their classes inherit from. Without, every module.
tools/check.sh writes typings/ this way when the game is installed.
"""

import dis
import marshal
import os
import re
import sys
import types
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ea_overlay import RETURNS  # noqa: E402

CO_VARARGS, CO_VARKEYWORDS = 0x04, 0x08
FLEX = {'flexmethod', 'flexproperty'}
CLASSY = {'classmethod', 'classproperty', 'flexmethod'}
PROPS = {'property', 'classproperty', 'constproperty', 'flexproperty', 'cached_property'}
BUILTIN_BASES = {
    'object',
    'int',
    'float',
    'str',
    'dict',
    'list',
    'tuple',
    'set',
    'frozenset',
    'Exception',
    'BaseException',
    'RuntimeError',
    'ValueError',
    'KeyError',
    'TypeError',
}
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
KEYWORDS = {
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class',
    'continue', 'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global',
    'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise',
    'return', 'try', 'while', 'with', 'yield', 'print', 'exec',
}  # fmt: skip


def instructions(code):
    """The instructions of a code object, without EXTENDED_ARG prefixes
    (dis already folds them into the next instruction's argument)."""
    return [i for i in dis.get_instructions(code) if i.opname != 'EXTENDED_ARG']


def ok_name(name):
    return bool(IDENT.match(name)) and name not in KEYWORDS


class Func:
    def __init__(self, code, defaults, decorators):
        self.code = code
        self.defaults = defaults  # how many positional parameters have defaults
        self.decorators = decorators

    def params(self, skip):
        c = self.code
        names = list(c.co_varnames[: c.co_argcount])
        first_default = len(names) - self.defaults
        out = []
        for i, name in enumerate(names):
            if i < skip:
                continue
            out.append(f'{name}: Any = ...' if i >= first_default else f'{name}: Any')
        star = c.co_argcount + c.co_kwonlyargcount
        if c.co_flags & CO_VARARGS:
            out.append(f'*{c.co_varnames[star]}: Any')
        elif c.co_kwonlyargcount:
            out.append('*')
        # Keyword-only defaults are not counted: give them all one, so the
        # stub never asks for more than the game does.
        out.extend(
            f'{name}: Any = ...'
            for name in c.co_varnames[c.co_argcount : c.co_argcount + c.co_kwonlyargcount]
        )
        if c.co_flags & CO_VARKEYWORDS:
            extra = 1 if c.co_flags & CO_VARARGS else 0
            out.append(f'**{c.co_varnames[star + extra]}: Any')
        return [p for p in out if ok_name(p.split(':')[0].lstrip('*')) or p == '*']


class Klass:
    def __init__(self, name, bases):
        self.name = name
        self.bases = bases  # dotted names as written, or None when unknown
        self.funcs = {}
        self.attrs = set()


class Module:
    def __init__(self, name):
        self.name = name
        self.classes = {}
        self.funcs = {}
        self.names = set()
        self.imports = {}  # local name -> (module, attribute or None)


def dotted_before(instrs, end):
    """The dotted name loaded by the instructions just before index `end`
    (LOAD_NAME a, LOAD_ATTR b, ...), and where it starts."""
    parts: list = []
    i = end - 1
    while i >= 0 and instrs[i].opname == 'LOAD_ATTR':
        parts.insert(0, instrs[i].argval)
        i -= 1
    if i >= 0 and instrs[i].opname in ('LOAD_NAME', 'LOAD_GLOBAL'):
        parts.insert(0, instrs[i].argval)
        return '.'.join(parts), i
    return None, i


def class_bases(instrs, start, count):
    """The first `count` arguments after the class name constant, when each
    is a plain (dotted) name: the base classes. None otherwise."""
    bases, i = [], start
    for _ in range(count):
        if i >= len(instrs) or instrs[i].opname not in ('LOAD_NAME', 'LOAD_GLOBAL'):
            return None
        j = i + 1
        while j < len(instrs) and instrs[j].opname == 'LOAD_ATTR':
            j += 1
        name, _ = dotted_before(instrs, j)
        if name is None:
            return None
        bases.append(name)
        i = j
    return bases


def items_start(instrs, end, count):
    """Index where the `count` stack items pushed just before index `end`
    begin (walking back, adding each instruction's stack effect)."""
    depth, j = 0, end - 1
    while j >= 0:
        ins = instrs[j]
        try:
            depth += dis.stack_effect(
                ins.opcode, ins.arg if ins.opcode >= dis.HAVE_ARGUMENT else None
            )
        except ValueError:
            return None
        if depth == count:
            return j
        j -= 1
    return None


def function_parts(instrs, make):
    """(defaults count or None when unknown, start of everything this
    MAKE_FUNCTION uses) for the MAKE_FUNCTION at `make`. Before the code
    and name constants come, in order: defaults, keyword defaults,
    annotations, closure (one stack item each, by the flags)."""
    flags = instrs[make].arg or 0
    code_at = make - 2
    items = bin(flags & 0x0F).count('1')
    if not items:
        return 0, code_at
    start = items_start(instrs, code_at, items)
    if start is None:
        return None, code_at
    if not flags & 0x01:
        return 0, start
    # The defaults item ends where the next item (or the code constant) begins.
    after = code_at if items == 1 else items_start(instrs, code_at, items - 1)
    if after is None:
        return None, start
    last = after - 1
    ins = instrs[last]
    if ins.opname == 'LOAD_CONST' and isinstance(ins.argval, tuple):
        return len(ins.argval), start
    if ins.opname == 'BUILD_TUPLE':
        return ins.arg, start
    return None, start


def decorators_before(instrs, i):
    """Decorator names loaded just before index i."""
    names: list = []
    while i > 0:
        name, start = dotted_before(instrs, i)
        if name is None:
            break
        names.insert(0, name.split('.')[-1])
        i = start
    return names


def make_func(instrs, make):
    defaults, start = function_parts(instrs, make)
    code = instrs[make - 2].argval
    if defaults is None:  # not readable: every parameter may be left out
        defaults = code.co_argcount
    return Func(code, defaults, decorators_before(instrs, start))


def tunable_keys(instrs, i):
    """Keys of the dict stored at index i (INSTANCE_TUNABLES = {...}), when
    built with constant keys (BUILD_CONST_KEY_MAP), as EA's tuning is."""
    if i >= 2 and instrs[i - 1].opname == 'BUILD_CONST_KEY_MAP':
        keys = instrs[i - 2]
        if keys.opname == 'LOAD_CONST' and isinstance(keys.argval, tuple):
            return [k for k in keys.argval if isinstance(k, str)]
    return []


def _stores_on(code, load_op, me):
    """Attribute names stored on `me` (loaded with load_op) in code."""
    instrs = instructions(code)
    return {
        ins.argval
        for k, ins in enumerate(instrs)
        if ins.opname == 'STORE_ATTR'
        and k
        and instrs[k - 1].opname == load_op
        and instrs[k - 1].argval == me
    }


def self_attrs(code):
    """Attributes a method sets on its first argument (self.x = ...), also
    from functions nested inside it."""
    if not code.co_argcount:
        return set()
    me = code.co_varnames[0]
    out = _stores_on(code, 'LOAD_FAST', me)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            out |= _stores_on(const, 'LOAD_DEREF', me)
    return out


def function_at(instrs, k):
    """Index of the MAKE_FUNCTION whose result STORE_NAME at k stores
    (directly, or through decorators: CALL_FUNCTION 1 each), or None."""
    j = k - 1
    while j > 0 and instrs[j].opname == 'CALL_FUNCTION' and instrs[j].arg == 1:
        j -= 1
    if instrs[j].opname != 'MAKE_FUNCTION':
        return None
    return j if isinstance(instrs[j - 2].argval, types.CodeType) else None


def read_class(name, bases, code):
    klass = Klass(name, bases)
    instrs = instructions(code)
    for k, ins in enumerate(instrs):
        target = ins.argval
        if ins.opname != 'STORE_NAME' or target in (
            '__module__',
            '__qualname__',
            '__classcell__',
            '__doc__',
        ):
            continue
        make = function_at(instrs, k)
        if make is not None:
            klass.funcs[target] = make_func(instrs, make)
            klass.attrs |= self_attrs(instrs[make - 2].argval)
            continue
        prev = instrs[k - 1]
        if target in ('INSTANCE_TUNABLES', 'FACTORY_TUNABLES'):
            klass.attrs |= set(tunable_keys(instrs, k))
        if target == '__slots__' and prev.opname == 'LOAD_CONST' and isinstance(prev.argval, tuple):
            klass.attrs |= {slot for slot in prev.argval if isinstance(slot, str)}
        klass.attrs.add(target)
    return klass


def globals_set(code):
    """Module names functions assign with `global` (STORE_GLOBAL)."""
    out = set()
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            out |= {i.argval for i in instructions(const) if i.opname == 'STORE_GLOBAL'}
            out |= globals_set(const)
    return out


def read_import(mod, instrs, k, pending):
    """IMPORT_NAME / IMPORT_FROM at k: record what name it binds."""
    ins, after = instrs[k], instrs[k + 1] if k + 1 < len(instrs) else None
    if after is None or after.opname != 'STORE_NAME':
        return
    if ins.opname == 'IMPORT_NAME':
        level = instrs[k - 2].argval if k >= 2 else 0
        if not level:  # import a.b (as c): relative imports are not followed
            mod.imports[after.argval] = (ins.argval, None)
    elif pending is not None:
        mod.imports[after.argval] = (pending, ins.argval)


def read_class_statement(mod, instrs, k):
    """LOAD_BUILD_CLASS at k: LOAD_CONST code, LOAD_CONST name,
    MAKE_FUNCTION, LOAD_CONST name, bases..., CALL_FUNCTION(_KW), decorators,
    STORE_NAME."""
    if k + 2 >= len(instrs) or not isinstance(instrs[k + 1].argval, types.CodeType):
        return
    code, name = instrs[k + 1].argval, instrs[k + 2].argval
    call = k + 5
    while call < len(instrs) and not instrs[call].opname.startswith('CALL_FUNCTION'):
        call += 1
    if call >= len(instrs):
        return
    # CALL_FUNCTION n counts the function, the name and the bases; with
    # keywords (metaclass=...) CALL_FUNCTION_KW n counts them too.
    count = instrs[call].arg - 2
    if instrs[call].opname == 'CALL_FUNCTION_KW' and instrs[call - 1].opname == 'LOAD_CONST':
        count -= len(instrs[call - 1].argval)
    store = call + 1
    while store < len(instrs) and instrs[store].opname == 'CALL_FUNCTION':
        store += 1  # class decorators
    if store < len(instrs) and instrs[store].opname == 'STORE_NAME':
        mod.classes[instrs[store].argval] = read_class(
            name, class_bases(instrs, k + 5, count), code
        )


def read_module_name(mod, instrs, k):
    """STORE_NAME at k at module level: a function or another name."""
    target = instrs[k].argval
    if instrs[k - 1].opname in ('IMPORT_NAME', 'IMPORT_FROM') or target in mod.classes:
        return
    make = function_at(instrs, k)
    if make is not None:
        mod.funcs[target] = make_func(instrs, make)
    else:
        mod.names.add(target)


def read_module(name, code):
    mod = Module(name)
    mod.names |= globals_set(code)
    instrs = instructions(code)
    pending = None
    for k, ins in enumerate(instrs):
        if ins.opname == 'IMPORT_NAME':
            pending = ins.argval
        if ins.opname in ('IMPORT_NAME', 'IMPORT_FROM'):
            read_import(mod, instrs, k, pending)
        elif ins.opname == 'LOAD_BUILD_CLASS':
            read_class_statement(mod, instrs, k)
        elif ins.opname == 'STORE_NAME':
            read_module_name(mod, instrs, k)
    return mod


def load_all(game):
    """Every gameplay module, by dotted name."""
    contents = os.path.join(game, 'Contents') if game.endswith('.app') else game
    folder = os.path.join(contents, 'Data', 'Simulation', 'Gameplay')
    modules = {}
    for zip_name in ('core.zip', 'simulation.zip'):
        with zipfile.ZipFile(os.path.join(folder, zip_name)) as zf:
            for entry in zf.namelist():
                if not entry.endswith('.pyc'):
                    continue
                dotted = entry[:-4].replace('/', '.')
                if dotted.endswith('.__init__'):
                    dotted = dotted[: -len('.__init__')]
                try:
                    code = marshal.loads(zf.read(entry)[16:])
                except (ValueError, EOFError):
                    continue
                modules[dotted] = read_module(dotted, code)
    return modules


def resolve(mod, dotted, modules):
    """(module, class name) a dotted name in `mod` refers to, or None."""
    head, _, rest = dotted.partition('.')
    if not rest and head in mod.classes:
        return mod.name, head
    target = mod.imports.get(head)
    if target is None:
        return None
    module, attr = target
    if attr is not None and not rest:
        return (module, attr) if module in modules and attr in modules[module].classes else None
    path = module if attr is None else f'{module}.{attr}'
    full = f'{path}.{rest}' if rest else path
    owner, _, cls = full.rpartition('.')
    if owner in modules and cls in modules[owner].classes:
        return owner, cls
    return None


def needed(modules, roots):
    """Roots plus every module their classes inherit from."""
    out, todo = set(), [r for r in roots if r in modules]
    while todo:
        name = todo.pop()
        if name in out:
            continue
        out.add(name)
        mod = modules[name]
        for klass in mod.classes.values():
            for base in klass.bases or ():
                hit = resolve(mod, base, modules)
                if hit and hit[0] not in out:
                    todo.append(hit[0])
        for module, _attr in mod.imports.values():
            if module in roots and module not in out:
                todo.append(module)
    # parents of packages
    for name in list(out):
        parts = name.split('.')
        for i in range(1, len(parts)):
            parent = '.'.join(parts[:i])
            if parent in modules:
                out.add(parent)
    return out


def func_lines(name, func, indent, owner):
    decos = set(func.decorators)
    ret = RETURNS.get(f'{owner}.{name}', 'Any')
    if decos & (PROPS - {'property', 'cached_property'}):
        # classproperty and friends: read on the class too, a plain attribute
        return [f'{indent}{name}: {ret}']
    if decos & PROPS:
        return [f'{indent}@property', f'{indent}def {name}(self) -> {ret}: ...']
    skip = 0
    pre = []
    if indent and 'staticmethod' in decos:
        pre.append(f'{indent}@staticmethod')
    elif indent and decos & CLASSY:
        pre.append(f'{indent}@classmethod')
        skip = 2 if decos & FLEX else 1
    elif indent:
        skip = 1
    params = func.params(skip)
    first = (
        []
        if not indent
        else (['cls'] if pre and pre[0].endswith('classmethod') else ([] if pre else ['self']))
    )
    sig = ', '.join(first + params)
    return [*pre, f'{indent}def {name}({sig}) -> {ret}: ...']


def stub_imports(mod, modules, wanted):
    """Import lines for the names that refer to stubbed classes or modules."""
    lines, imported = [], set()
    for local, (module, attr) in sorted(mod.imports.items()):
        if not ok_name(local) or module not in wanted:
            continue
        if attr is not None and attr in modules[module].classes:
            lines.append(f'from {module} import {attr} as {local}')
            imported.add(local)
        elif attr is None and local == module.split('.')[0]:
            lines.append(f'import {module}')
            imported.add(local)
    return lines, imported


def stub_bases(mod, klass, modules, wanted, extra_imports):
    """The base list for a class stub; Any when a base is not known."""
    if klass.bases is None:
        return ['Any']
    bases = []
    for base in klass.bases:
        hit = resolve(mod, base, modules)
        if hit and hit[0] in wanted:
            if hit[0] == mod.name:
                bases.append(hit[1])
            else:
                bases.append(f'{hit[0]}.{hit[1]}')
                extra_imports.add(f'import {hit[0]}')
        elif base in BUILTIN_BASES:
            if base != 'object':
                bases.append(base)
        else:
            return ['Any']
    return list(dict.fromkeys(bases))


def stub_class(mod, name, klass, modules, wanted, extra_imports):
    bases = stub_bases(mod, klass, modules, wanted, extra_imports)
    body = [f'    {attr}: Any' for attr in sorted(klass.attrs - set(klass.funcs)) if ok_name(attr)]
    for fname, func in sorted(klass.funcs.items()):
        if ok_name(fname) or (fname.startswith('__') and fname.endswith('__')):
            body.extend(func_lines(fname, func, '    ', f'{mod.name}.{name}'))
    head = f'class {name}({", ".join(bases)}):' if bases else f'class {name}:'
    return ['', head, *(body or ['    ...'])]


def write_stub(mod, modules, wanted, out_dir):
    imports, imported = stub_imports(mod, modules, wanted)
    extra_imports: set = set()
    lines = [
        f'{name}: Any'
        for name in sorted(mod.names - set(mod.classes) - set(mod.funcs) - imported)
        if ok_name(name)
    ]
    lines += [
        f'{local}: Any'
        for local in sorted(set(mod.imports) - imported)
        if ok_name(local) and local not in mod.classes and local not in mod.funcs
    ]
    for name, func in sorted(mod.funcs.items()):
        if ok_name(name):
            lines.extend(func_lines(name, func, '', mod.name))
    for name, klass in sorted(mod.classes.items()):
        if ok_name(name):
            lines.extend(stub_class(mod, name, klass, modules, wanted, extra_imports))
    header = [
        '# Generated by tools/ea_stubs.py from the installed game. Do not edit.',
        '# mypy: ignore-errors',  # EA's own class layering is not ours to check
        'from typing import Any',
        *sorted(extra_imports),
        *imports,
        '',
    ]
    is_pkg = any(m.startswith(mod.name + '.') for m in modules)
    rel = mod.name.replace('.', os.sep)
    path = (
        os.path.join(out_dir, rel, '__init__.pyi')
        if is_pkg
        else os.path.join(out_dir, rel + '.pyi')
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(header + lines) + '\n')


def used_by(scripts):
    """Every module the mod's own code imports (anywhere, also inside functions)."""
    import ast

    found: set = set()
    for folder, _subs, files in os.walk(scripts):
        for name in files:
            if not name.endswith('.py'):
                continue
            with open(os.path.join(folder, name), encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    found.add(node.module)
                    found.update(f'{node.module}.{alias.name}' for alias in node.names)
    return found


def main():
    if sys.version_info[:2] != (3, 7):
        sys.exit("run with the game's Python 3.7 (it reads 3.7 bytecode)")
    game, out_dir = sys.argv[1], sys.argv[2]
    modules = load_all(game)
    roots = sys.argv[3:]
    if roots[:1] == ['--used-by']:
        roots = sorted(m for m in used_by(roots[1]) if m in modules)
    wanted = needed(modules, roots) if roots else set(modules)
    for name in sorted(wanted):
        write_stub(modules[name], modules, wanted, out_dir)
    print(f'{len(wanted)} modules written to {out_dir} (of {len(modules)} in the game)')


if __name__ == '__main__':
    main()

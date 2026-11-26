"""Console commands with typed arguments, in modules that use
`from __future__ import annotations`.

EA's console parser (sims4.commands.parse_args) converts what the player
types by looking at each parameter's annotation as a class (`arg_type is
int`, `issubclass(arg_type, CustomParam)`). The future import turns every
annotation into text ('int'), which the parser does not understand. Put
@real_annotations directly under @sims4.commands.Command:

    @sims4.commands.Command('simsight.example', command_type=...)
    @real_annotations
    def example(count: int = 1, _connection: Any = None) -> None: ...

Annotations that name a class become that class again; any other
annotation (Any, `X | None`, ...) is removed, so the parser treats that
parameter as plain text, exactly as an unannotated parameter.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

import simsight_log

F = TypeVar('F', bound=Callable[..., Any])


def real_annotations(func: F) -> F:
    resolved: dict[str, Any] = {}
    for name, annotation in func.__annotations__.items():
        if name == 'return':
            continue
        value = annotation
        if isinstance(annotation, str):
            try:
                value = eval(annotation, func.__globals__)  # noqa: S307 - our own annotation text
            except Exception:  # noqa: BLE001 - not a class at runtime: left as plain text
                continue
        if isinstance(value, type):
            resolved[name] = value
    func.__annotations__ = resolved
    simsight_log.line(f'command types: {func.__name__} {sorted(resolved)}')
    return func

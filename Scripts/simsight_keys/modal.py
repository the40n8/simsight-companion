"""Flash's owner reports: a modal owner or text field has the keys, and
whether that owner blocks the phone shortcut."""

from __future__ import annotations

from typing import Any

import sims4.commands

from simsight_commands import real_annotations
import simsight_log

from .state import _bit, _state, status
from .winapi import fence_opening_input


@sims4.commands.Command(
    'simsight.inputfence', 'a11y.inputfence', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_inputfence(
    payload: str = '',  # noqa: ARG001 - the console command's argument (see below)
    _connection: Any = None,
) -> bool:
    # Flash supplies an empty positional payload; the native command wrapper
    # also supplies _connection by keyword. Keep them separate. The startup
    # socket dispatcher calls this without either argument.
    fence_opening_input()
    return True


@sims4.commands.Command(
    'simsight.modalstate', 'a11y.modalstate', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_modalstate(state: str = '00', _connection: Any = None) -> bool:
    try:
        return apply_modal_state(state, 'modalstate')
    except Exception:
        simsight_log.error('keys: modal state')
        return False


def _modal_report(state: object, source: str) -> tuple[object, bool | None]:
    """The owner report as two digits (owner, editing), and the blocking bit
    when the report carries one."""
    if isinstance(state, int) and not isinstance(state, bool) and state in (0, 1, 10, 11):
        # sims4.commands converts only str arguments; a payload a caller
        # already turned into a number arrives as one.
        state = f'{state:02d}'
    if isinstance(state, str) and state in ('0', '1'):
        # Live evidence 2026-09-25: the native command path drops the
        # leading zero, so Flash's '00' and '01' arrive as '0' and '1'.
        state = '0' + state
    if source == 'context' and isinstance(state, str) and len(state) == 3 and state[2] in '01':
        # The context suffix carries the owner and its blocking bit as one
        # report. A three-digit direct payload is never accepted: the
        # native path drops leading zeros, so '010' would read as '10'.
        return state[:2], state[2] == '1'
    return state, None


def apply_modal_state(state: object, source: str) -> bool:
    """Flash's owner report. It arrives twice per change (simsight.modalstate and
    the simsight.context_enc suffix, from the same A11yModalFrame pass), so one
    lost post cannot leave keys routing chords to a closed owner. Applying
    the same state again changes nothing."""
    report, blocking = _modal_report(state, source)
    if not isinstance(report, str) or report not in ('00', '01', '10', '11'):
        simsight_log.line(
            f'keys: modal state rejected {type(report).__name__} {repr(report)[:24]} ({source})'
        )
        return False
    before = ('1' if status.modal_active else '0') + ('1' if status.editing_active else '0')
    if before != report:
        simsight_log.line(f'keys: modal {before}->{report} ({source})')
    if status.modal_active and report[0] == '0':
        _state.input_epoch += 1  # the owner closed: queued modal navigation is stale
    if (report[0] == '1' and not status.modal_active) or (
        status.editing_active and report[1] == '0'
    ):
        # New modal owner, or a text field just closed (console Enter):
        # retire keys already down or tapped before this surface change.
        fence_opening_input()
    owner_changed = (report[0] == '1') != status.modal_active
    status.modal_active = report[0] == '1'
    status.editing_active = report[1] == '1'
    if blocking is not None:
        _set_blocking(blocking, source)
    elif owner_changed:
        # An owner report without its bit: a stale permissive bit must not
        # outlive the owner it described. Unknown counts as blocking until
        # Flash reports the bit (simsight.blocking or the next context post).
        _set_blocking(None, source)
    if not status.modal_active:
        # A missed pie-menu Hide message must not route future world chords
        # to the whole HUD. Flash ownership is the authoritative context.
        import simsight_scanner as scanner

        scanner.a11y_menuclosed()
    return True


def _set_blocking(value: bool | None, source: str) -> None:
    if value != _state.blocking_explicit:
        simsight_log.line(
            f'keys: blocking {_blocking_text(_state.blocking_explicit)}->{_blocking_text(value)} ({source})'
        )
    _state.blocking_explicit = value


def _blocking_text(value: bool | None) -> str:
    return '?' if value is None else ('1' if value else '0')


def blocking_active() -> bool:
    """Whether the current owner blocks a recipient switch (the phone
    shortcut). The call alert, the phone panel, SimInfo panels and HUD
    browsing own the keys without blocking; dialogs, pickers, pie menus and
    dropdowns block. Until Flash has reported the bit for the current owner
    (older UI packages never do), any owner blocks."""
    if _state.blocking_explicit is None:
        return status.modal_active
    return _state.blocking_explicit


@sims4.commands.Command(
    'simsight.blocking', 'a11y.blocking', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_blocking(state: str = '1', _connection: Any = None) -> bool:
    """Flash: the blocking bit alone, posted on change after the owner
    report (context, modalstate, blocking) and again after a sender reset."""
    report = _bit(state)
    if report not in ('0', '1'):
        simsight_log.line(f'keys: blocking rejected {type(report).__name__} {repr(report)[:24]}')
        return False
    _set_blocking(report == '1', 'blocking')
    return True

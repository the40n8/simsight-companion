"""A Sim's name as the mod speaks and shows it.

Imports nothing of the mod or the game, so any module can use it.
"""

from __future__ import annotations

from typing import Any


def full_name(info: Any) -> str:
    """First and last name of a SimInfo, joined by a space, leaving out an
    empty part (or a missing one); '' when it has neither. The bridge and
    dialogs strip outer spaces from it, as they always did."""
    return ' '.join(
        str(part)
        for part in (getattr(info, 'first_name', ''), getattr(info, 'last_name', ''))
        if part
    )

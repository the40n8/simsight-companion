"""SimSight Plus main menu (Alt+A): every feature, its key, and settings.

Merged into SimSight 2026-10-08. The add-on's own action queue is not
carried over: Alt+Q opens SimSight's queue (Ctrl+Alt+Q), which does the same.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import simsight_log
import simsight_plus_core as core

if TYPE_CHECKING:
    from typing import Callable

    Feature = tuple[str, str, Callable[[], object]]


@core.hotkey(ord('Q'), 'action queue')
def open_queue() -> None:
    import simsight_hub as hub

    hub.action_queue()


def _features() -> list[Feature]:
    import simsight_plus_needs as needs
    import simsight_plus_notify as notify
    import simsight_plus_rooms as rooms
    import simsight_plus_sims as sims
    import simsight_plus_time as t

    feats: list[Feature] = [
        ('Needs', 'Alt+N. Reads all needs, lowest first.', needs.read_needs),
        (
            'Time, work and money',
            'Alt+T. Day, time, game speed, next work or school, money and bills.',
            t.read_time,
        ),
        (
            'Action queue',
            'Alt+Q. What your Sim is doing and has queued. Cancel actions.',
            open_queue,
        ),
        ('Rooms', 'Alt+R. The rooms of the house and what is in them.', rooms.open_rooms),
        ('Where am I', 'Alt+W. Which room your Sim is in.', rooms.where_am_i),
        ('Notifications', 'Alt+L. The last pop-up messages, newest first.', notify.open_history),
        (
            'Create and edit Sims',
            'Alt+C. Start a brand-new family, add a Sim, change name, traits, aspiration.',
            sims.open_menu,
        ),
    ]
    try:
        import simsight_plus_build as build
    except Exception:  # the menu works without the build helper
        simsight_log.error('plus menu: build helper')
    else:
        feats.append(
            (
                'Build helper',
                'Alt+B. Build rooms, walls, doors and windows from the keyboard.',
                build.open_menu,
            )
        )
    return feats


@core.hotkey(ord('A'), 'Access Plus menu')
def open_menu() -> None:
    rows: list[tuple[str, str, object]] = list(_features())
    rows.append(('Settings', 'Turn warnings and reminders on or off.', 'settings'))
    core.choose(
        f'SimSight Plus {core.VERSION}',
        'Every feature also has its own Alt key, shown in its description.',
        rows,
        _pick,
        tag='menu',
    )


def _pick(value: object) -> None:
    if value == 'settings':
        open_settings()
    elif callable(value):
        value()


def _on_off(on: bool) -> str:
    return 'on' if on else 'off'


def open_settings() -> None:
    rows = [
        (f'{name.capitalize()}: {_on_off(core.setting_on(name))}', 'Choose to switch it.', name)
        for name in core.DEFAULTS
    ]
    core.choose(
        'Access Plus settings',
        'Choose a setting to switch it on or off.',
        rows,
        _toggle,
        tag='menu',
    )


def _toggle(name: str) -> None:
    on = core.toggle_setting(name)
    core.speak(f'{name.capitalize()} {_on_off(on)}.')

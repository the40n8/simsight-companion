"""Known contacts through EA's relationship-portrait interaction path.

Contacts do not need instantiated Sims or a position on the loaded lot.
The native client requests, displays and resolves its own interaction menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sims4.commands
import simsight_lang as lang
from simsight_commands import real_annotations
import simsight_log
import simsight_names as names
import simsight_menu as menu
import simsight_speech as speech

if TYPE_CHECKING:
    from simsight_hub import Row


def _contacts(actor: Any) -> list[tuple[str, int]]:
    """(full name, sim id) of the actor's visible relationships, by name."""
    tracker = actor.sim_info.relationship_tracker
    seen = {actor.sim_info.sim_id}
    result = []
    for info in tracker.get_target_sim_infos():
        if info is None or info.sim_id in seen or tracker.is_hidden(info.sim_id):
            continue
        seen.add(info.sim_id)
        name = names.full_name(info)
        if name:
            result.append((name, info.sim_id))
    return sorted(result, key=lambda item: (item[0].casefold(), item[1]))


def open_contact(sim_id: int, actor_id: int) -> bool:
    import services
    import simsight_scanner as scanner
    from server.pick_info import PickType

    actor = services.get_active_sim()
    if actor is None or actor.sim_info.sim_id != actor_id:
        speech.speak(lang.t('contact changed'), explicit=True)
        return False
    names = {ident: name for name, ident in _contacts(actor)}
    if sim_id not in names:
        speech.speak(lang.t('sim unavailable'), explicit=True)
        return False
    # PICK_PORTRAIT is EA's relationship-panel path. It supplies the remote
    # target as picked_item_ids and retains all native availability tests.
    # A world PICK_SIM would wrongly require an instantiated target.
    pos = actor.position
    name = names[sim_id].replace('|', ' ')
    return scanner.send_ui(
        f'A11y|pick|{sim_id}|{int(PickType.PICK_PORTRAIT)}|{int(services.active_lot().lot_id)}|{name}|{float(pos.x):.3f}|{float(pos.y):.3f}|{float(pos.z):.3f}|{int(actor.level)}'
    )


def open_contacts() -> bool:
    import services
    import simsight_hub as hub

    actor = services.get_active_sim()
    if actor is None:
        speech.speak(lang.t('load a household first'), explicit=True)
        return False
    actor_id = actor.sim_info.sim_id
    rows: list[Row] = [
        (menu.raw_text(name), None, lambda ident=ident: open_contact(ident, actor_id))
        for name, ident in _contacts(actor)
    ]
    if not rows:
        speech.speak(lang.t('no contacts'), explicit=True)
        return False
    return hub.show_window('known contacts', 'contacts instructions', rows)


@sims4.commands.Command(
    'simsight.contacts', 'a11y.contacts', command_type=sims4.commands.CommandType.Live
)
@real_annotations
def a11y_contacts(_connection: Any = None) -> bool:
    try:
        return open_contacts()
    except Exception:
        simsight_log.error('contacts: open')
        return False

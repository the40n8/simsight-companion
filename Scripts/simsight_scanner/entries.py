"""One Sim or object as a scanner entry, and the words that describe it."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import simsight_lang as lang
import simsight_log

from .state import COMPASS, Entry, _subs_in
from .categories import _label_part

if TYPE_CHECKING:
    from sims.sim_info import SimInfo

    from .state import Part


def bearing_index(dx: float, dz: float) -> int:
    """Compass point index (0 north, clockwise) of an offset."""
    angle = math.degrees(math.atan2(dx, dz)) % 360.0
    return int(((angle + 22.5) % 360.0) // 45) % 8


def _level_of(obj: Any) -> int:
    try:
        return int(obj.level)
    except (AttributeError, TypeError, ValueError):
        return 0  # no floor level: count it as the ground floor


def _sim_name_parts(sim_info: SimInfo) -> tuple[str | None, int]:
    """Plain names when the game stores them as text; a loc key for the few
    special Sims that only carry a name key."""
    try:
        first = str(sim_info.first_name or '')
        last = str(sim_info.last_name or '')
    except (AttributeError, TypeError):
        first, last = '', ''
    joined = ' '.join(part for part in (first, last) if part).strip()
    if joined:
        return joined, 0
    for attr in ('full_name_key', 'first_name_key'):
        try:
            key = int(getattr(sim_info, attr, 0) or 0)
        except (TypeError, ValueError):
            key = 0
        if key:
            return None, key
    return '', 0


# Get to Work hospital patients (PatientSituationBase) by visit stage: the
# situation's current state class, as EA names them in
# situations.complex.patient_situation_base and hospital_patient_situation.
PATIENT_STAGES = {
    'ArrivingState': 'patient arriving',
    'WaitingState': 'patient waiting',
    '_AdmittedState': 'patient admitted',
    '_DiagnosedState': 'patient diagnosed',
    'TreatedState': 'patient treated',
}


def _patient_roles() -> dict[int, str]:
    """Sim id -> spoken role for every patient on the lot."""
    try:
        import services
        from situations.complex.patient_situation_base import PatientSituationBase

        manager = services.get_zone_situation_manager()
        situations = list(manager.values()) if manager is not None else []
    except Exception:
        simsight_log.error('scanner: patient situations')
        return {}
    roles = {}
    for situation in situations:
        try:
            if not isinstance(situation, PatientSituationBase):
                continue
            patient = situation.get_patient()
            if patient is None:
                continue
            state = type(getattr(situation, '_cur_state', None)).__name__
            roles[patient.id] = lang.t(PATIENT_STAGES.get(state, 'patient'))
        except Exception:
            simsight_log.error('scanner: patient role')
    return roles


def _first_name(sim_info: SimInfo) -> str:
    try:
        return str(sim_info.first_name or '')
    except (AttributeError, TypeError):
        return ''


def _object_name_parts(obj: Any) -> tuple[str | None, int]:
    """(player-given name, None) or (None, catalog name key)."""
    # A player-given name is plain text; otherwise catalog_name is the
    # string-table key itself (an int; EA's own GSI code does hex() on it).
    try:
        if obj.has_custom_name():
            custom = str(obj.custom_name or '') or None
            if custom is not None:
                return custom, 0
    except (AttributeError, TypeError):
        pass  # no custom-name support on this object: use the catalog name
    try:
        raw = obj.catalog_name
        return None, int(getattr(raw, 'hash', raw)) & 0xFFFFFFFF
    except (AttributeError, TypeError, ValueError):
        return None, 0


def entry_for(
    obj: Any, origin: Any, origin_level: int, is_sim: bool, category: str, group: str
) -> Entry:
    pos = obj.position
    dx = float(pos.x) - float(origin.x)
    dz = float(pos.z) - float(origin.z)
    distance = math.sqrt(dx * dx + dz * dz)
    level_delta = _level_of(obj) - origin_level
    if is_sim:
        name, key = _sim_name_parts(obj.sim_info)
        first = _first_name(obj.sim_info)
        return Entry(
            obj.id,
            True,
            name,
            key,
            distance,
            bearing_index(dx, dz),
            level_delta,
            category,
            group,
            first,
        )
    name, key = _object_name_parts(obj)
    return Entry(
        obj.id, False, name, key, distance, bearing_index(dx, dz), level_delta, category, group
    )


# --------------------------------------------------------------- describing


def name_part(entry: Entry) -> Part:
    if entry.name:
        return entry.name
    if entry.name_hash:
        return ('loc', entry.name_hash)
    return ''


def _floor_part(delta: int) -> str:
    """', one floor up' and the like; '' on the same floor."""
    if delta == 1:
        return ', ' + lang.t('one floor up')
    if delta == -1:
        return ', ' + lang.t('one floor down')
    if delta > 1:
        return ', {} {}'.format(delta, lang.t('floors up'))
    if delta < -1:
        return ', {} {}'.format(-delta, lang.t('floors down'))
    return ''


def place_part(entry: Entry) -> str:
    metres = round(entry.distance)
    if metres < 1:
        where = lang.t('here')
    else:
        where = '{} {} {}'.format(metres, lang.t('meters'), lang.t(COMPASS[entry.bearing]))
    return where + _floor_part(entry.level_delta)


def _entry_parts(entry: Entry, index: int, total: int) -> list[Part]:
    import simsight_cleanliness as cleanliness

    status = ', ' + lang.t('scanner unavailable') if entry.availability == 'locked' else ''
    if entry.role:
        status = ', ' + entry.role + status
    return [
        name_part(entry),
        cleanliness.suffix(entry) + status + ', ' + place_part(entry),
        '. {} {} {}.'.format(index + 1, lang.t('of'), total),
    ]


def _sub_label_part(entries: list[Entry], entry: Entry) -> Part | None:
    """The subcategory name, or None when the main category has a single
    subcategory that is the category itself (Sims, bare Misc)."""
    if len(_subs_in(entries)) <= 1 and _label_part(entry.category) == _label_part(entry.group):
        return None
    return _label_part(entry.category)

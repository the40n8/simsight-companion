"""Research log for the Get to Work doctor career (logging only).

Nothing here is spoken and nothing changes the game. With diagnostic logging
on (Ctrl+Alt+L), every cue the game sends about a sick Sim is written to
SimSight.log with the prefix "doctor:", so the accessible patient readout
can be built from what a sighted player is actually shown:

- the sickness and its symptoms when they are applied, with each symptom
  buff's presentation fields (vfx, cas_vfx, appearance_modifier, walkstyle,
  audio stings, voice pitch, pushed interactions, visible moodlet);
- SicknessTracker discoveries: symptom, sickness, exam, treatment, rule-out
  and diagnosis progress;
- thought balloons (BalloonRequest.distribute), headlines
  (Headline.send_headline_message) and visual effects (PlayEffect.start and
  start_one_shot) on a sick Sim;
- patient situation state changes (arriving, waiting, admitted, diagnosed,
  treated).

The log names hidden answers (the sickness, correct treatments) on purpose:
it is research data for the developers, never narration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import simsight_log

if TYPE_CHECKING:
    from collections.abc import Callable

    # Called after the wrapped method: (self or class, args, kwargs).
    After = Callable[[Any, tuple[Any, ...], dict[str, Any]], None]

# Buff fields that show a symptom to a sighted player.
_BUFF_FIELDS = (
    'visible',
    'vfx',
    'cas_vfx',
    'appearance_modifier',
    'walkstyle',
    'audio_sting_on_add',
    'voice_pitch_modifier',
    'mood_type',
)

# SicknessTracker methods logged, and how each is described.
_TRACKER_EVENTS = (
    ('discover_symptom', 'symptom discovered'),
    ('discover_sickness', 'sickness discovered'),
    ('track_examination', 'exam done'),
    ('track_treatment', 'treatment done'),
    ('rule_out_treatment', 'treatment ruled out'),
    ('record_last_progress', 'diagnosis progress'),
)


class _State:
    installed = False


_state = _State()


def _name(obj: Any) -> str:
    if obj is None:
        return 'None'
    return getattr(obj, '__name__', None) or type(obj).__name__


def _sim_info(target: Any) -> Any:
    if target is None:
        return None
    return getattr(target, 'sim_info', None) or (
        target if hasattr(target, 'sickness_tracker') else None
    )


def _sick(target: Any) -> bool:
    info = _sim_info(target)
    try:
        return (
            info is not None
            and info.sickness_tracker is not None
            and info.sickness_tracker.current_sickness is not None
        )
    except AttributeError:
        return False


def _who(target: Any) -> str:
    info = _sim_info(target)
    if info is None:
        return repr(target)
    return '{} {} ({})'.format(
        getattr(info, 'first_name', ''),
        getattr(info, 'last_name', ''),
        getattr(info, 'sim_id', '?'),
    )


def _log(text: str) -> None:
    simsight_log.line('doctor: ' + text)


def _short(value: Any) -> str:
    text = _name(value) if isinstance(value, type) else repr(value)
    return text if len(text) <= 160 else text[:157] + '...'


def _buff_fields(buff: Any) -> str:
    parts = []
    for field in _BUFF_FIELDS:
        value = getattr(buff, field, None)
        if value:
            parts.append(f'{field}={_short(value)}')
    interactions = getattr(buff, 'interactions', None)
    if interactions:
        try:
            names = [_name(a) for a in getattr(interactions, 'interaction_items', ()) or ()]
        except TypeError:  # not a list of interactions: show the tuning itself
            names = []
        parts.append(f'interactions={names or _short(interactions)}')
    return ', '.join(parts) or 'no presentation fields'


def _describe_symptom(symptom: Any) -> list[str]:
    lines = [
        f'    buff {_name(buff)}: {_buff_fields(buff)}'
        for buff in getattr(symptom, 'associated_buffs', ()) or ()
    ]
    lines.extend(
        f'    statistic {_name(stat)}'
        for stat in getattr(symptom, 'associated_statistics', ()) or ()
    )
    return lines


def _after_safely(where: str, after: After, owner: Any, args: Any, kwargs: Any) -> None:
    # The hooks only write the log: with logging off there is nothing to do.
    if not simsight_log.enabled:
        return
    try:
        after(owner, args, kwargs)
    except Exception:
        simsight_log.error(f'doctor: {where} log')


def _wrap_method(cls: Any, name: str, after: After) -> None:
    original = getattr(cls, name)
    where = f'{cls.__name__}.{name}'

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        _after_safely(where, after, self, args, kwargs)
        return result

    wrapper.__name__ = original.__name__
    setattr(cls, name, wrapper)


def _wrap_classmethod(cls: Any, name: str, after: After) -> None:
    original = getattr(cls, name).__func__
    where = f'{cls.__name__}.{name}'

    def wrapper(klass: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(klass, *args, **kwargs)
        _after_safely(where, after, klass, args, kwargs)
        return result

    wrapper.__name__ = original.__name__
    setattr(cls, name, classmethod(wrapper))


def _sickness_applied(klass: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    info = args[0] if args else kwargs.get('sim_info')
    _log(
        'sickness {} applied to {} (from_load={})'.format(
            _name(klass), _who(info), kwargs.get('from_load', args[1:2])
        )
    )
    for symptom in getattr(klass, 'symptoms', ()) or ():
        _log(f'  symptom {_name(symptom)}')
        for line in _describe_symptom(symptom):
            _log(line)
    for buff in getattr(klass, 'associated_buffs', ()) or ():
        _log(f'  sickness buff {_name(buff)}: {_buff_fields(buff)}')
    stat = getattr(klass, 'diagnosis_stat', None)
    _log('  diagnosis stat {} visible={}'.format(_name(stat), getattr(stat, 'visible', None)))
    try:
        order = [_name(s) for s in klass.get_ordered_symptoms()]
        _log(f'  discovery order {order}')
    except (AttributeError, TypeError):
        pass  # not every sickness tunes a discovery order
    _log(
        '  correct treatments {}'.format(
            [_name(a) for a in getattr(klass, 'correct_treatments', ()) or ()]
        )
    )


def _tracker_event(label: str) -> After:
    def after(tracker: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        value = args[0] if args else None
        shown = _short(value) if isinstance(value, (int, float)) else _name(value)
        _log('{} {}: {}'.format(_who(getattr(tracker, '_sim_info', None)), label, shown))

    return after


def _install_sickness() -> None:
    from sickness.sickness import Sickness
    from sickness.sickness_tracker import SicknessTracker

    _wrap_classmethod(Sickness, 'apply_to_sim_info', _sickness_applied)
    for method, label in _TRACKER_EVENTS:
        _wrap_method(SicknessTracker, method, _tracker_event(label))


def _balloon(request: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    sim = request._sim
    if _sick(sim):
        _log(
            'balloon on {}: icon={} overlay={} type={} category_icon={} icon_info={}'.format(
                _who(sim),
                _short(getattr(request, 'icon', None)),
                _short(getattr(request, 'overlay', None)),
                getattr(request, 'balloon_type', None),
                _short(getattr(request, 'category_icon', None)),
                _short(getattr(request, 'icon_info', None)),
            )
        )


def _headline(klass: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    info = args[0] if args else kwargs.get('sim_info')
    if _sick(info):
        _log(f'headline {_name(klass)} on {_who(info)}: value={args[1:2]}')


def _effect(label: str) -> After:
    def after(play: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        target = getattr(play, 'target', None)
        if _sick(target):
            _log(
                'vfx {} on {}: {} joint={}'.format(
                    label,
                    _who(target),
                    getattr(play, 'effect_name', '?'),
                    getattr(play, 'joint_name', None),
                )
            )

    return after


def _install_cues() -> None:
    from balloon.balloon_request import BalloonRequest
    from headlines.headline import Headline
    from vfx import PlayEffect

    _wrap_method(BalloonRequest, 'distribute', _balloon)
    _wrap_classmethod(Headline, 'send_headline_message', _headline)
    _wrap_method(PlayEffect, 'start', _effect('start'))
    _wrap_method(PlayEffect, 'start_one_shot', _effect('one shot'))


def _patient_changed(situation: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    state = args[0] if args else None
    patient = situation.get_patient() if hasattr(situation, 'get_patient') else None
    state_name = _name(type(state)) if state is not None else None
    _log(f'patient situation {_name(type(situation))} for {_who(patient)}: state {state_name}')


def _install_patients() -> None:
    from situations.complex.patient_situation_base import PatientSituationBase

    _wrap_method(PatientSituationBase, '_change_state', _patient_changed)


def install() -> bool:
    if _state.installed:
        return True
    _state.installed = True
    for part in (_install_sickness, _install_cues, _install_patients):
        _install_part(part)
    return True


def _install_part(part: Callable[[], None]) -> None:
    try:
        part()
    except Exception as ex:
        # A pack the player does not own: its classes are missing.
        simsight_log.line(f'doctor: {part.__name__} unavailable ({ex!r})')


try:
    install()
except Exception:
    simsight_log.error('doctor: install')

"""The travel list's request records, its one state object and the small
game lookups every part uses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


import simsight_lang as lang
import simsight_log
import simsight_settings as settings
import simsight_speech as speech

if TYPE_CHECKING:
    from collections.abc import Callable

    # A picker row: (localized name, localized description or None, tag).
    Row = tuple[Any, Any, Any]
    # A world entry: (world key, name, place count, localized label or None).
    World = tuple[int, str, int, Any]
    Step = Callable[..., Any]


TRAVEL, VACATION = 'travel', 'vacation'


class _Pending:
    """One travel request from EA, while its windows are open."""

    __slots__ = (
        'dialog',
        'dialog_msg',
        'fallback_used',
        'generation',
        'kwargs',
        'mode',
        'msg_type',
        'party',
        'places',
        'prices',
        'rented',
        'worlds',
    )

    def __init__(
        self,
        dialog: Any,
        msg_type: Any,
        dialog_msg: Any,
        kwargs: dict[str, Any],
        mode: str = TRAVEL,
    ) -> None:
        self.dialog = dialog
        self.msg_type = msg_type
        self.dialog_msg = dialog_msg
        self.kwargs = kwargs
        self.places: list[_Place] = []
        self.worlds: list[World] = []
        self.fallback_used = False
        self.mode = mode
        self.party: list[Any] = []  # sim infos travelling: the actor plus companions
        self.rented = False  # a vacation was created: no native fallback after this
        self.generation = 0  # price bridge token: replies for other requests are ignored
        self.prices: dict[int, int] = {}  # zone id -> per-day price the client reported


class _Place:
    """One destination row (or a building grouping unit rows)."""

    __slots__ = (
        'household',
        'household_id',
        'master',
        'name',
        'price',
        'region',
        'rental',
        'residential',
        'residents',
        'units',
        'venue',
        'visible',
        'world_key',
        'world_label',
        'world_name',
        'zone_id',
    )

    def __init__(self, zone_id: int, name: str) -> None:
        self.zone_id = zone_id
        self.name = name
        self.world_key = 0
        self.world_name = ''
        self.world_label: Any = None
        self.venue: Any = None
        self.household: Any = None
        self.household_id = 0
        self.master = zone_id
        self.units: list[_Place] = []
        self.residential: bool | None = None
        self.visible: bool | None = None
        self.region = ''
        self.residents: tuple[str, ...] = ()
        self.rental: bool | None = None
        self.price: int | None = None

    def copy(self) -> _Place:
        """The same row facts, without units."""
        other = _Place(self.zone_id, self.name)
        for slot in _Place.__slots__:
            if slot not in ('zone_id', 'name', 'units'):
                setattr(other, slot, getattr(self, slot))
        return other

    def label(self) -> str:
        """Occupied homes read family first; venues and empty lots lot first."""
        if self.residential is True and self.household:
            return f'{self.household}, {self.name}'
        return self.name


class _Request:
    """A keyboard entry request from Flash, kept to answer it exactly once."""

    __slots__ = ('key', 'state', 'when')

    def __init__(self, key: tuple[int, int], state: str, when: float) -> None:
        self.key = key  # (epoch, request id)
        self.state = state  # 'pending', 'cancelled' or '<outcome>[:<reason>]'
        self.when = when  # ingress time (time.monotonic)


class _State:
    """Everything this module remembers between calls."""

    def __init__(self) -> None:
        self.original_distribute: Callable[..., Any] | None = None
        self.original_sim_distribute: Callable[..., Any] | None = None
        self.pending: _Pending | None = None
        self.window_id: int | None = None
        self.last_rows: list[_Place] = []
        self.front_id: int | None = None  # the Go alone / Choose who comes window
        # Our pushed Travel interaction for one Go alone; held until EA shows
        # its picker, the interaction ends or a new Travel is pushed (a paused
        # game can hold it for as long as it likes).
        self.alone: Any = None
        self.alone_push: int | None = None  # the Sim id while our own Go alone push runs
        self.alone_claimed = False  # the picker was claimed inside that push
        self.price_generation = 0  # grows per vacation request; a reply must carry it
        self.epoch = 0
        self.lease_sent = 0.0
        self.lease_running = False
        self.lease_generation = 0  # bumped by every start and stop; retires queued heartbeats
        self.requests: list[_Request] = []  # newest last


_state = _State()


def _say(term: str) -> None:
    speech.speak(lang.t(term), explicit=True)


def enabled() -> bool:
    return bool(settings.get('keyboard', True)) and bool(settings.get('travel_list', True))


def simplified() -> bool:
    return bool(settings.get('simplified_menus', True))


def _funds() -> int | None:
    import services

    try:
        return int(services.active_household().funds.money)
    except AttributeError:
        return None  # no active household
    except Exception:
        simsight_log.error('travel: household funds')
        return None


def _zone_loaded() -> bool:
    try:
        import services

        return services.current_zone() is not None
    except Exception:
        simsight_log.error('travel: current zone')
        return False


def _service() -> Any:
    import services

    return services.ui_dialog_service()


def _dialog_active(dialog: Any) -> bool:
    """True while EA still holds this exact dialog as active."""
    try:
        service = _service()
        lookup = getattr(service, 'get_dialog', None)
        if lookup is not None:
            return lookup(dialog.dialog_id) is dialog
        return service._active_dialogs.get(dialog.dialog_id) is dialog
    except AttributeError:
        return False  # no dialog service (zone unloading)
    except Exception:
        simsight_log.error('travel: dialog lookup')
        return False


def _ui(message: str) -> bool:
    """Encoded UI message on the existing A11y| channel (Flash side: Codex)."""
    try:
        import distributor.ops
        import distributor.system

        distributor.system.Distributor.instance().add_op_with_no_owner(
            distributor.ops.SendUIMessage(message)
        )
        return True
    except Exception as ex:
        simsight_log.line(f'travel: ui message failed ({ex!r})')
        return False

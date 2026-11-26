"""Which scanner objects the active Sim can use: the checks run as
generators, one native test per step, spread over pump ticks."""

import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

POSTED = []
module(
    'sims4',
    commands=module(
        'sims4.commands',
        Command=lambda *a, **k: lambda f: f,
        CommandType=types.SimpleNamespace(Live=1),
    ),
)
module('simsight_pump', post=lambda fn, *args: POSTED.append((fn, args)) or True)
module('simsight_settings', get=lambda key, default=None: default)


class Result:
    def __init__(self, ok, tooltip=None):
        self.ok, self.tooltip = ok, tooltip

    def __bool__(self):
        return self.ok


class ChoiceMenu:
    @staticmethod
    def is_valid_aop(aop, context, **kwargs):
        return aop.result


class Context:
    def add_preferred_objects(self, objs):
        pass


class Client:
    def get_interaction_parameters(self):
        return {}

    def create_interaction_context(self, sim, pick=None):
        return Context()


def aop(name, result, user_directed=True):
    affordance = types.SimpleNamespace(__name__=name, allow_user_directed=user_directed)
    return types.SimpleNamespace(affordance=affordance, result=result)


class Obj:
    def __init__(self, obj_id, rows):
        self.id = obj_id
        self.rows = rows
        self.definition = types.SimpleNamespace(name=f'obj{obj_id}')
        self.forwarded_pick_targets = ()
        self.position = types.SimpleNamespace(x=0.0, z=0.0)
        self.routing_surface = None
        self.level = 0

    def potential_interactions(self, context, **kwargs):
        return iter(self.rows)


SIM = types.SimpleNamespace(id=1, position=types.SimpleNamespace(x=0.0, z=0.0))
OBJECTS = {}
module('interactions', choices=module('interactions.choices', ChoiceMenu=ChoiceMenu))
module('server')
module(
    'server.pick_info',
    PickInfo=lambda **kw: kw,
    PickType=types.SimpleNamespace(PICK_OBJECT=1),
)
module(
    'services',
    get_active_sim=lambda: SIM,
    client_manager=lambda: types.SimpleNamespace(get_first_client=Client),
    object_manager=lambda: types.SimpleNamespace(get=OBJECTS.get),
    active_lot=lambda: types.SimpleNamespace(lot_id=5),
    get_fire_service=lambda: None,
)

import simsight_availability as availability  # noqa: E402


def drain():
    """Run posted ticks like the pump; how many ran."""
    ticks = 0
    while POSTED:
        fn, args = POSTED.pop(0)
        fn(*args)
        ticks += 1
    return ticks


class Availability(unittest.TestCase):
    def setUp(self):
        del POSTED[:]
        OBJECTS.clear()
        availability.reset()
        self.budget = availability.BUDGET_SECONDS

    def tearDown(self):
        availability.BUDGET_SECONDS = self.budget

    def scan(self, *objs):
        for obj in objs:
            OBJECTS[obj.id] = obj
        availability.begin(SIM, objs)

    def test_verdicts(self):
        usable = Obj(10, [aop('Sit', Result(True))])
        locked = Obj(11, [aop('Cook', Result(False, 'Needs skill'))])
        empty = Obj(12, [aop('Hidden', Result(False))])
        self.scan(usable, locked, empty)
        self.assertEqual(availability.state(usable), availability.PENDING)
        drain()
        self.assertEqual(availability.state(10), availability.USABLE)
        self.assertEqual(availability.state(11), availability.LOCKED)
        self.assertEqual(availability.state(12), availability.EMPTY)

    def test_one_step_per_tick_without_budget(self):
        availability.BUDGET_SECONDS = -1.0  # every tick stops after its first step
        rows = [aop(f'Row{i}', Result(False)) for i in range(3)] + [aop('Sit', Result(True))]
        self.scan(Obj(20, rows))
        ticks = drain()
        # setup, three failing rows, then the passing one finishes the check
        self.assertEqual(ticks, 5)
        self.assertEqual(availability.state(20), availability.USABLE)

    def test_stale_tick_does_nothing(self):
        self.scan(Obj(30, [aop('Sit', Result(True))]))
        fn, args = POSTED.pop(0)
        availability.pause()
        fn(*args)
        self.assertEqual(availability.state(30), availability.PENDING)

    def test_failing_check_stays_listed(self):
        class Broken(Obj):
            def potential_interactions(self, context, **kwargs):
                raise RuntimeError('boom')

        self.scan(Broken(40, []))
        drain()
        self.assertEqual(availability.state(40), availability.USABLE)

    def test_same_verdict_rechecks_less_often(self):
        self.assertEqual(availability._interval(1, 0.0), availability.RECHECK_SECONDS)
        self.assertEqual(availability._interval(2, 0.0), 2 * availability.RECHECK_SECONDS)
        self.assertEqual(availability._interval(9, 0.0), availability.MAX_RECHECK_SECONDS)
        self.assertEqual(availability._interval(1, 20.0), 2 * availability.RECHECK_SECONDS)

    def test_recheck_keeps_the_verdict(self):
        self.scan(Obj(50, [aop('Sit', Result(True))]))
        drain()
        availability.recheck()
        self.assertEqual(availability.state(50), availability.USABLE)
        availability.begin(SIM, [OBJECTS[50]])
        self.assertTrue(POSTED)  # due again at once


if __name__ == '__main__':
    unittest.main()

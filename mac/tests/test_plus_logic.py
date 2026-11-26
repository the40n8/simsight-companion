"""SimSight Plus logic that needs no game: spoken times and spans, need
warning levels, floor and compass words, room names and their file,
lot prices, and the console commands' argument types."""

import inspect
import os
import tempfile
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

# Errors from hooks that cannot install without the game go to a scratch log.
simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

module(
    'simsight_plus_core',
    hotkey=lambda vk, description: lambda fn: fn,
    add_ticker=lambda seconds, fn: None,
    on_zone_started=lambda fn: fn,
    post=lambda fn, *args: None,
    log=lambda message: None,
    MODS_FOLDER=tempfile.mkdtemp(),
    IN_GAME=False,
)
module('simsight_speech', speak=lambda *a, **k: None)
commands = module(
    'sims4.commands',
    Command=lambda *a, **k: lambda fn: fn,
    CommandType=types.SimpleNamespace(Live=1),
)
module('sims4', commands=commands)

import simsight_plus_family as family  # noqa: E402
import simsight_plus_needs as needs  # noqa: E402
import simsight_plus_rooms as rooms  # noqa: E402
import simsight_plus_sims as sims  # noqa: E402
import simsight_plus_time as plus_time  # noqa: E402
import simsight_rooms as room_labels  # noqa: E402


class Clock:
    def __init__(self, day, hour, minute):
        self._day, self._hour, self._minute = day, hour, minute

    def day(self):
        return self._day

    def hour(self):
        return self._hour

    def minute(self):
        return self._minute


class TimeTest(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(plus_time.clock_text(Clock(0, 0, 0)), '12 AM')
        self.assertEqual(plus_time.clock_text(Clock(0, 9, 5)), '9:05 AM')
        self.assertEqual(plus_time.clock_text(Clock(0, 12, 30)), '12:30 PM')
        self.assertEqual(plus_time.clock_text(Clock(0, 23, 0)), '11 PM')

    def test_day(self):
        self.assertEqual(plus_time.day_text(Clock(0, 0, 0)), 'Sunday')
        self.assertEqual(plus_time.day_text(Clock(8, 0, 0)), 'Monday')

    def test_spans(self):
        self.assertEqual(plus_time.span_words(1), '1 minutes')
        self.assertEqual(plus_time.span_words(45), '45 minutes')
        self.assertEqual(plus_time.span_words(60), '1 hour')
        self.assertEqual(plus_time.span_words(125), '2 hours 5 minutes')
        self.assertEqual(plus_time.span_words(24 * 60), '1 day')
        self.assertEqual(plus_time.span_words(2 * 24 * 60 + 60 + 59), '2 days 1 hour')


class NeedsTest(unittest.TestCase):
    def test_scaled(self):
        self.assertEqual(needs.scaled(0, -100.0, 100.0), 50)
        self.assertEqual(needs.scaled(-150, -100.0, 100.0), 0)
        self.assertEqual(needs.scaled(150, -100.0, 100.0), 100)
        self.assertIsNone(needs.scaled(5, 10.0, 10.0))

    def test_words(self):
        self.assertEqual(
            [needs.word(p) for p in (10, 20, 50, 80)], ['critical', 'low', 'okay', 'good']
        )

    def test_levels_do_not_flicker(self):
        self.assertEqual(needs.next_level(10, None), 'critical')
        self.assertEqual(needs.next_level(17, 'critical'), 'critical')  # within 5 of critical
        self.assertEqual(needs.next_level(20, 'critical'), 'low')
        self.assertEqual(needs.next_level(25, None), 'low')
        self.assertEqual(needs.next_level(40, 'low'), 'low')  # not recovered yet
        self.assertEqual(needs.next_level(40, None), 'ok')
        self.assertEqual(needs.next_level(45, 'low'), 'ok')


class RoomsTest(unittest.TestCase):
    def test_floors(self):
        self.assertEqual(rooms.floor_name(0), 'ground floor')
        self.assertEqual(rooms.floor_name(-1), 'basement')
        self.assertEqual(rooms.floor_name(-2), 'basement level 2')
        self.assertEqual(rooms.floor_name(2), 'second floor up')
        self.assertEqual(rooms.floor_name(5), 'floor 5')

    def test_bearing(self):
        self.assertEqual(rooms.bearing(0, 1), 'north')
        self.assertEqual(rooms.bearing(1, 0), 'east')
        self.assertEqual(rooms.bearing(-1, -1), 'south west')

    def test_size(self):
        room = rooms.Room(1, 0)
        room.tiles = [(x + 0.5, z + 0.5) for x in range(3) for z in range(2)]
        self.assertEqual(room.size_text(), '3 by 2 meters')
        room.tiles.pop()
        self.assertEqual(room.size_text(), 'about 5 square meters')

    def test_names_file_lines(self):
        key = (7, 0, -3, 4, 10, 12)
        line = rooms.name_line(key, 'Den')
        self.assertEqual(line, '7\t0\t-3\t4\t10\t12\tDen\n')
        self.assertEqual(rooms.parse_name_line(line), (key, 'Den'))
        self.assertIsNone(rooms.parse_name_line('7\t0\tDen\n'))
        with self.assertRaises(ValueError):
            rooms.parse_name_line('a\t0\t0\t0\t0\t0\tDen\n')

    def test_names_from_furniture(self):
        tags = {1: {'BuyCatSS_Bed'}, 2: {'BuyCatSS_Bed'}, 3: {'Func_Fridge', 'BuyCatSS_Sofa'}}
        original = rooms.tag_names
        rooms.tag_names = lambda obj: tags[obj]
        try:
            made = []
            for rid, objs, n in ((1, [1], 4), (2, [2], 9), (3, [3], 6), (4, [], 2)):
                room = rooms.Room(rid, 0)
                room.objects, room.tiles = objs, [(0, 0)] * n
                made.append(room)
            original_given = rooms.given_name
            rooms.given_name = lambda room: 'Studio' if room.rid == 4 else None
            try:
                rooms._name_rooms(made)
            finally:
                rooms.given_name = original_given
        finally:
            rooms.tag_names = original
        # The bigger bedroom is number 1; the kitchen outweighs the sofa.
        self.assertEqual([r.name for r in made], ['Bedroom 2', 'Bedroom 1', 'Kitchen', 'Studio'])


class SimsAndFamilyTest(unittest.TestCase):
    def test_plural(self):
        self.assertEqual(sims.plural(1, 'Sim'), '1 Sim')
        self.assertEqual(sims.plural(3, 'trait'), '3 traits')

    def test_age_rows(self):
        self.assertEqual(len(sims.age_rows()), len(sims.AGE_CHOICES))
        self.assertEqual(
            [row[2] for row in sims.age_rows(family.GROWN_UPS)], ['YOUNGADULT', 'ADULT', 'ELDER']
        )

    def test_buying_price(self):
        self.assertEqual(family.buying_price(1000.0, 0.0, 0.1), 10000)
        self.assertEqual(family.buying_price(1234.0, 4.0, 0.1), 12300)
        self.assertIsNone(family.buying_price(0.0, 0.0, 0.1))
        self.assertIsNone(family.buying_price(100.0, 0.0, 0.0))

    def test_lot_label(self):
        lot = {'name': 'Elm', 'world': 'Willow Creek', 'empty': False, 'beds': 1, 'baths': 2}
        lot['price'] = 9000
        self.assertEqual(
            family._lot_label(lot),
            'Elm, Willow Creek. 1 bedroom, 2 bathrooms. about 9000 simoleons',
        )
        lot.update(empty=True, price=None)
        self.assertEqual(family._lot_label(lot), 'Elm, Willow Creek. price unknown')


class CommandArgumentsTest(unittest.TestCase):
    """The game's console converts each argument with its annotation: those
    must be real types, not the strings postponed annotations make."""

    def test_room_label(self):
        spec = inspect.getfullargspec(room_labels.roomlabel_command)
        self.assertEqual(spec.annotations, {'label': str})

    def test_lot_price(self):
        spec = inspect.getfullargspec(family._lotprice_enc(lambda *a, **k: None))
        self.assertEqual(spec.annotations, {'payload': str})

    def test_label_text(self):
        self.assertEqual(room_labels._clean('  my   den  '), 'my den')
        self.assertEqual(len(room_labels._clean('x' * 100)), 60)
        self.assertEqual(room_labels._clean(None), '')


if __name__ == '__main__':
    unittest.main()

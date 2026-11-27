"""Scanner logic that needs no game: compass bearings, catalog groups,
spoken text clean-up, distance words, dirt/dust terms, catalog prices and
console command argument types."""

import os
import tempfile
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands', Command=lambda *a, **k: lambda f: f, CommandType=types.SimpleNamespace(Live=1)
)
module('simsight_lang', t=lambda term: term)
module('simsight_pump', post=lambda fn, *a: True, status=lambda: 'ok')
module('simsight_settings', get=lambda name, fallback=None: fallback, brief=lambda: False)
module('simsight_speech', speak=lambda *a, **k: None)
module('simsight_availability', reset=lambda: None, pause=lambda: None)

import simsight_buycat as buycat  # noqa: E402
import simsight_catalog as catalog  # noqa: E402
import simsight_cleanliness as cleanliness  # noqa: E402
import simsight_scanner as scanner  # noqa: E402
from simsight_scanner import categories, loc  # noqa: E402


def entry(distance=0.0, bearing=0, level_delta=0):
    return scanner.Entry(1, False, 'Chair', 0, distance, bearing, level_delta, 'MISC', 'OBJECTS')


class Bearing(unittest.TestCase):
    def test_compass_points(self):
        self.assertEqual(scanner.bearing_index(0, 1), 0)  # north
        self.assertEqual(scanner.bearing_index(1, 1), 1)  # north east
        self.assertEqual(scanner.bearing_index(1, 0), 2)  # east
        self.assertEqual(scanner.bearing_index(0, -1), 4)  # south
        self.assertEqual(scanner.bearing_index(-1, 0), 6)  # west
        self.assertEqual(scanner.bearing_index(-1, 1), 7)  # north west


class Groups(unittest.TestCase):
    def test_flags_prefer_the_tag_family(self):
        # A bar reports Surfaces and Activities and Skills: EE tags mean the latter.
        flags = buycat.GROUP_FLAG['SURFACE'] | buycat.GROUP_FLAG['ENTERTAINMENT']
        self.assertEqual(categories._group_of(flags, 'BuyCatEE_Bar'), 'ENTERTAINMENT')

    def test_flags_fall_back_to_catalog_order(self):
        flags = buycat.GROUP_FLAG['LIGHTING'] | buycat.GROUP_FLAG['APPLIANCES']
        self.assertEqual(categories._group_of(flags, 'Func_Xmas'), 'APPLIANCES')

    def test_no_flags_build(self):
        self.assertEqual(categories._group_of(0, 'Build_Door'), 'BUILD')

    def test_no_flags_live_mode_tags(self):
        cases = {
            'BuyCatEE_PetToys': 'PETS',
            'BuyCatEE_MiscKids': 'KIDS',
            'BuyCatEE_Transportation': 'VEHICLE',
            'BuyCatEE_TVStand': 'SURFACE',
            'BuyCatEE_Computer': 'ELECTRONICS',
            'BuyCatEE_Bar': 'ENTERTAINMENT',
            'BuyCatSS_DiningChair': 'COMFORT',
            'BuyCatSS_Dresser': 'STORAGE',
            'BuyCatSS_Counter': 'SURFACE',
            'BuyCatPA_PetBowl': 'PETS',
            'BuyCatPA_Toilet': 'PLUMBING',
            'BuyCatPA_Refrigerator': 'APPLIANCES',
            'BuyCatLD_CeilingLight': 'LIGHTING',
            'BuyCatLD_Rug': 'DECOR',
            'Func_Xmas': 'MISC',
        }
        for sub, group in cases.items():
            with self.subTest(sub=sub):
                self.assertEqual(categories._group_of(0, sub), group)

    def test_subcategory_priority(self):
        names = {'Func_Xmas', 'BuyCatSS_Counter'}
        self.assertEqual(categories._sub_of(names), 'BuyCatSS_Counter')
        self.assertEqual(categories._sub_of(set()), 'MISC')


class Words(unittest.TestCase):
    def test_clean_text_drops_gaps_left_by_missing_names(self):
        self.assertEqual(loc._clean_text(['', ', here', '. 1 of 2.']), 'here. 1 of 2.')
        self.assertEqual(loc._clean_text(['Bed ', ', here']), 'Bed, here')

    def test_place(self):
        self.assertEqual(scanner.place_part(entry(0.4)), 'here')
        self.assertEqual(scanner.place_part(entry(3.2, 2)), '3 meters east')
        self.assertEqual(scanner.place_part(entry(0, 0, 1)), 'here, one floor up')
        self.assertEqual(scanner.place_part(entry(0, 0, -1)), 'here, one floor down')
        self.assertEqual(scanner.place_part(entry(0, 0, 2)), 'here, 2 floors up')
        self.assertEqual(scanner.place_part(entry(0, 0, -3)), 'here, 3 floors down')

    def test_dirt_terms(self):
        self.assertEqual(cleanliness.terms_for({}), [])
        # The visual DirtyState wins over the dirtiness commodity.
        self.assertEqual(cleanliness.terms_for({15129: 15130, 15128: 15133}), [])
        self.assertEqual(cleanliness.terms_for({15128: 15133}), ['scanner dirty'])
        self.assertEqual(
            cleanliness.terms_for({15129: 15131, 255045: 255050}),
            ['scanner dirty', 'scanner dusty'],
        )

    def test_catalog_price(self):
        self.assertEqual(catalog._describe(1250, 1), '1,250 simoleons')
        self.assertEqual(catalog._describe(80, 3), '80 simoleons, 3 colours')


class Commands(unittest.TestCase):
    def test_argument_types_are_real_types(self):
        # The game's parser calls each annotation to convert the argument.
        self.assertEqual(scanner.a11y_scan.__annotations__, {'action': str})
        self.assertEqual(scanner.a11y_loc_enc.__annotations__, {'text': str})

    def test_menu_flag_is_a_module_attribute(self):
        scanner.note_menu_open()
        self.assertTrue(scanner.menu_open())
        scanner.a11y_menuclosed()
        self.assertFalse(scanner.menu_open())


if __name__ == '__main__':
    unittest.main(verbosity=0)

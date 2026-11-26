"""Build coordinates (Windows): the UI stage mapped onto the game window, and
the UI's projection reply. Pure arithmetic; no window, no mouse."""

import os
import tempfile
import threading
import types
import unittest

import fakegame
from fakegame import module

fakegame.scripts_on_path()

sims4 = module('sims4')
sims4.commands = module(
    'sims4.commands',
    Command=lambda *names, **kw: lambda func: func,
    CommandType=types.SimpleNamespace(Live='live'),
)
module('simsight_speech', speak=lambda *a, **k: None)

import simsight_log  # noqa: E402

simsight_log.PATH = os.path.join(tempfile.mkdtemp(), 'SimSight.log')

import simsight_build_coordinates as coords  # noqa: E402


def geometry(w, h):
    return (1, 0, 0, w, h, 96, (0, 0, 1920, 1200), 0)


def viewport(mode='showAll', align='', width=1600.0, height=900.0, bounds=None):
    return {
        'zone': 7,
        'width': width,
        'height': height,
        'mode': mode,
        'align': align,
        'layout': (1600.0, 900.0),
        'bounds': bounds or (0.0, 0.0, 1600.0, 900.0),
    }


class Frame(unittest.TestCase):
    def test_same_aspect(self):
        f = coords.Frame(viewport(), geometry(1920, 1080))
        self.assertEqual((f.sx, f.sy, f.ox, f.oy), (1.2, 1.2, 0.0, 0.0))
        self.assertEqual(f.client(800, 450), (960, 540))
        self.assertEqual(f.zone, 7)

    def test_show_all_letterbox(self):
        f = coords.Frame(viewport(), geometry(1920, 1200))
        self.assertEqual(f.sx, 1.2)
        self.assertAlmostEqual(f.oy, 60.0)
        self.assertEqual(f.client(0, 0), (0, 60))
        self.assertEqual(coords.Frame(viewport(align='T'), geometry(1920, 1200)).oy, 0.0)
        self.assertAlmostEqual(coords.Frame(viewport(align='BR'), geometry(1920, 1200)).oy, 120.0)

    def test_no_border_crops(self):
        f = coords.Frame(viewport('noBorder'), geometry(1920, 1200))
        self.assertAlmostEqual(f.sx, 1200 / 900)
        self.assertAlmostEqual(f.ox, (1920 - 1600 * 1200 / 900) / 2)
        self.assertEqual(f.client(800, 450), (960, 600))
        with self.assertRaises(ValueError):
            f.client(10, 450)  # cropped off the left edge

    def test_exact_fit_and_offset(self):
        f = coords.Frame(viewport('exactFit'), geometry(1920, 1200))
        self.assertEqual(f.client(100, 100), (120, 133))
        self.assertEqual(f.client(100, 100, (10, 0)), (132, 133))

    def test_refusals(self):
        for bad in (
            viewport('fancy'),
            viewport(align='X'),
            viewport(width=50.0),
            viewport(width=float('nan')),
            viewport(bounds=(0.0, 0.0, 2000.0, 900.0)),
        ):
            with self.subTest(viewport=bad), self.assertRaises(ValueError):
                coords.Frame(bad, geometry(1920, 1080))
        f = coords.Frame(viewport(), geometry(1920, 1080))
        for point in ((-1, 0), (1600, 0), (0, 900), (float('inf'), 0)):
            with self.subTest(point=point), self.assertRaises(ValueError):
                f.client(*point)

    def test_clear_of_panels(self):
        f = coords.Frame(viewport(bounds=(0.0, 0.0, 800.0, 450.0)), geometry(1920, 1080))
        self.assertEqual(f.bounds, (0.0, 0.0, 800.0, 450.0))
        self.assertTrue(f.clear((400, 200)))
        self.assertFalse(f.clear((10, 200)))  # the left panel strip
        self.assertFalse(f.clear((900, 200)))  # outside the build bounds
        full = coords.Frame(viewport(), geometry(1920, 1080))
        self.assertTrue(full.clear((400, 600)))
        self.assertFalse(full.clear((400, 700)))  # below 70 per cent of the height


class Reply(unittest.TestCase):
    def setUp(self):
        self.job = {'event': threading.Event(), 'zone': 7, 'result': None}
        coords._pending.clear()
        coords._pending[5] = self.job

    def test_point(self):
        text = '5|7|point|10.5|20|1600|900|showAll||1600|900|0|0|1600|900'
        self.assertTrue(coords.receive(text))
        self.assertTrue(self.job['event'].is_set())
        self.assertEqual(
            self.job['result'],
            {
                'status': 'point',
                'zone': 7,
                'point': (10.5, 20.0),
                'width': 1600.0,
                'height': 900.0,
                'mode': 'showAll',
                'align': '',
                'layout': (1600.0, 900.0),
                'bounds': (0.0, 0.0, 1600.0, 900.0),
            },
        )

    def test_status_only(self):
        self.assertTrue(coords.receive('5|7|offscreen' + '|' * 12))
        self.assertEqual(self.job['result'], {'status': 'offscreen'})

    def test_refused(self):
        for text in (
            '5|8|point|1|2|1600|900|showAll||1600|900|0|0|1600|900',  # another zone
            '6|7|point|1|2|1600|900|showAll||1600|900|0|0|1600|900',  # unknown request
            '5|7|point|nan|2|1600|900|showAll||1600|900|0|0|1600|900',
            '5|7|point|x|2|1600|900|showAll||1600|900|0|0|1600|900',
            '5|7|point',
        ):
            with self.subTest(text=text):
                self.assertFalse(coords.receive(text))
        self.assertFalse(self.job['event'].is_set())
        self.assertTrue(
            coords.receive('5|7|viewport|nan|nan|1600|900|showAll||1600|900|0|0|1600|900')
        )


if __name__ == '__main__':
    unittest.main()

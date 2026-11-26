"""Startup transport: framing, the accepted lines, the bounded queue and the
key ownership Flash reports (no socket, no game)."""

import unittest

import fakegame

fakegame.scripts_on_path()

import simsight_log  # noqa: E402

simsight_log._write = lambda text: None

import simsight_startup as startup  # noqa: E402


class FramingTest(unittest.TestCase):
    def test_lines_across_chunks(self) -> None:
        reader = startup.FrameReader()
        self.assertEqual(reader.feed(b'HELLO|A11Y'), [])
        self.assertEqual(
            reader.feed(b'_STARTUP_V2\nPOST|a|b\nPO'), ['HELLO|A11Y_STARTUP_V2', 'POST|a|b']
        )
        self.assertEqual(reader.feed(b'ST|c|d\n'), ['POST|c|d'])

    def test_bad_framing(self) -> None:
        with self.assertRaises(ValueError):
            startup.FrameReader().feed(b'a\rb\n')
        with self.assertRaises(ValueError):
            startup.FrameReader().feed(b'x' * (startup.MAX_FRAME + 1))

    def test_hello(self) -> None:
        self.assertEqual(startup.parse_hello('HELLO|A11Y_STARTUP_V2'), '')
        self.assertEqual(startup.parse_hello('HELLO|A11Y_STARTUP_V2|ui 3'), 'ui 3')
        self.assertIsNone(startup.parse_hello('HELLO|OTHER'))

    def test_posts(self) -> None:
        self.assertEqual(
            startup.parse_post('POST|simsight.speak_enc|a|b'), ('simsight.speak_enc', 'a|b')
        )
        self.assertEqual(
            startup.parse_post('POST|simsight.modalstate|10'), ('simsight.modalstate', '10')
        )
        self.assertIsNone(startup.parse_post('POST|simsight.modalstate|2'))
        self.assertIsNone(startup.parse_post('POST|simsight.blocking|yes'))
        self.assertEqual(
            startup.parse_post('POST|simsight.inputfence|'), ('simsight.inputfence', '')
        )
        self.assertIsNone(startup.parse_post('POST|simsight.inputfence|x'))
        self.assertIsNone(startup.parse_post('POST|simsight.exec|x'))
        self.assertIsNone(startup.parse_post('GET|simsight.stop|'))


class SessionTest(unittest.TestCase):
    def setUp(self) -> None:
        startup._incoming.clear()
        startup._state.connected = False
        startup._state.flash_startup = False

    def test_greeting_first(self) -> None:
        session = startup._Session()
        self.assertFalse(session.take(b'POST|simsight.stop|\n'))
        self.assertFalse(session.greeted)

    def test_posts_queue_under_the_new_generation(self) -> None:
        session = startup._Session()
        generation = startup._state.generation
        self.assertTrue(session.take(b'HELLO|A11Y_STARTUP_V2\nPOST|simsight.stop|\n'))
        self.assertTrue(startup._state.connected)
        self.assertEqual(list(startup._incoming), [(generation + 1, 'simsight.stop', '')])
        self.assertFalse(session.take(b'junk\n'))

    def test_full_queue_keeps_state_channels(self) -> None:
        for _ in range(startup.MAX_QUEUE + 5):
            startup._enqueue(('simsight.speak_enc', 'x'))
        self.assertEqual(len(startup._incoming), startup.MAX_QUEUE)
        startup._enqueue(('simsight.blocking', '1'))
        self.assertEqual(len(startup._incoming), startup.MAX_QUEUE)
        self.assertEqual(startup._incoming[-1][1:], ('simsight.blocking', '1'))
        startup._enqueue(('simsight.speak_enc', 'dropped'))
        self.assertNotEqual(startup._incoming[-1][2], 'dropped')

    def test_flash_owns_keys_only_while_active_and_connected(self) -> None:
        startup.note_mark('startup-v11-main-active')
        self.assertFalse(startup.send_navigation('A11yArrowDown'))  # not connected
        startup._state.connected = True
        self.assertTrue(startup.send_navigation('A11yArrowDown'))
        self.assertFalse(startup.send_navigation('A11yInspect'))
        startup.note_mark('startup-v11-main-native')
        self.assertFalse(startup.send_navigation('A11yArrowDown'))
        startup.note_mark('something-else-active')  # not a mode mark: ignored
        self.assertFalse(startup.send_navigation('A11yArrowDown'))


class PortTest(unittest.TestCase):
    def test_port_follows_the_settings(self) -> None:
        import simsight_settings as settings

        saved = dict(settings._stored)
        try:
            settings._stored.update(keyboard=False, simplified_menus=True)
            self.assertEqual(startup.listen_port(), startup.PORT_SPEECH)
            settings._stored.update(keyboard=True, simplified_menus=False)
            self.assertEqual(startup.listen_port(), startup.PORT_KEYBOARD_FULL_MENUS)
            settings._stored.update(simplified_menus=True)
            self.assertEqual(startup.listen_port(), startup.PORT_KEYBOARD)
        finally:
            settings._stored.clear()
            settings._stored.update(saved)


if __name__ == '__main__':
    unittest.main()

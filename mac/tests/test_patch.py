"""simsight_patch: wrappers pass every argument through, once."""

import unittest

import fakegame

fakegame.scripts_on_path()

import simsight_patch as patch  # noqa: E402


class Dialog:
    def distribute(self, kind, msg, immediate=False):
        return (kind, msg, immediate)


class Wrap(unittest.TestCase):
    def setUp(self):
        patch.unwrap(Dialog, 'distribute')
        self.seen = []

    def make(self, original):
        def wrapper(*args, **kwargs):
            self.seen.append(args[1:])
            return original(*args, **kwargs)

        return wrapper

    def test_all_arguments_reach_the_original(self):
        patch.wrap(Dialog, 'distribute', self.make)
        self.assertEqual(Dialog().distribute(1, 2, True), (1, 2, True))  # positional immediate
        self.assertEqual(Dialog().distribute(1, 2, immediate=True), (1, 2, True))
        self.assertEqual(self.seen, [(1, 2, True), (1, 2)])

    def test_the_same_wrapper_twice_is_added_once(self):
        first = patch.wrap(Dialog, 'distribute', self.make)
        second = patch.wrap(Dialog, 'distribute', self.make)
        self.assertIs(first, second)
        self.assertEqual(Dialog().distribute(1, 2), (1, 2, False))
        self.assertEqual(len(self.seen), 1)  # not stacked

    def test_two_modules_wrapping_the_same_function_both_run(self):
        order = []

        def make_a(original):
            def a(*args, **kwargs):
                order.append('a')
                return original(*args, **kwargs)

            return a

        def make_b(original):
            def b(*args, **kwargs):
                order.append('b')
                return original(*args, **kwargs)

            return b

        game = Dialog.distribute
        patch.wrap(Dialog, 'distribute', make_a)
        patch.wrap(Dialog, 'distribute', make_b)
        patch.wrap(Dialog, 'distribute', make_a)  # A's install() again: not a third wrapper
        self.assertEqual(Dialog().distribute(1, 2, True), (1, 2, True))
        self.assertEqual(order, ['b', 'a'])  # the newest runs first, each once
        self.assertIs(patch.original(Dialog, 'distribute'), game)

    def test_unwrap(self):
        game = Dialog.distribute
        patch.wrap(Dialog, 'distribute', self.make)
        patch.unwrap(Dialog, 'distribute')
        self.assertIs(Dialog.distribute, game)


if __name__ == '__main__':
    unittest.main()

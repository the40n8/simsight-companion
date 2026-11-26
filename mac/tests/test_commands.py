"""Console command parameters keep real classes as annotations (EA's
parser compares them with `is int`), even with the future import."""

from __future__ import annotations

import unittest
from typing import Any

import fakegame

fakegame.scripts_on_path()

from simsight_commands import real_annotations  # noqa: E402


class Custom:
    pass


@real_annotations
def command(
    count: int = 1,
    flag: bool = False,
    who: Custom = None,
    other: Any = None,
    maybe: int | None = None,
) -> None:
    pass


class RealAnnotations(unittest.TestCase):
    def test_classes_come_back(self):
        self.assertIs(command.__annotations__['count'], int)
        self.assertIs(command.__annotations__['flag'], bool)
        self.assertIs(command.__annotations__['who'], Custom)

    def test_other_annotations_become_plain_text(self):
        for name in ('other', 'maybe', 'return'):
            self.assertNotIn(name, command.__annotations__)


if __name__ == '__main__':
    unittest.main()

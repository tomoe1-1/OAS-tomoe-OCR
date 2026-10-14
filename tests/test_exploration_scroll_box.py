"""The realm-raid scroll box must stay on the scroll digits, per real screens.

Exploration yielded the whole round to RealmRaid without fighting anything:
`activate_realm_raid` reads the scroll count on the very first frame of the
chapter list and bails out when it is >= threshold. The entrance page and the
chapter list have different top bars, so they need different boxes, and a
drifted box silently reads the wrong number.

Fixtures are named after what they actually show (`_22of30`, `_23of30`) so a
mismatch names itself. OCR runs for real against the real assets.
"""
import unittest
from pathlib import Path

import cv2
from numpy import fromfile, uint8

from tasks.Exploration.assets import ExplorationAssets

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
ENTRANCE = FIXTURES / 'exploration_entrance_scrolls_22of30.jpg'
CHAPTER_LIST = FIXTURES / 'exploration_chapterlist_scrolls_23of30.png'


def load_rgb(path):
    img = cv2.imdecode(fromfile(str(path), dtype=uint8), -1)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


class ScrollBoxFixtureTests(unittest.TestCase):
    def test_both_real_screens_are_present(self):
        for path in (ENTRANCE, CHAPTER_LIST):
            self.assertTrue(path.exists(), f'missing fixture: {path.name}')

    def test_entrance_box_reads_the_scroll_count_on_the_entrance_page(self):
        image = load_rgb(ENTRANCE)
        cu, _, total = ExplorationAssets.O_REALM_RAID_NUMBER1.ocr(image)
        # The screen shows 22/30. Reading 2 here is the observed regression:
        # it silently under-counts instead of failing.
        self.assertEqual((cu, total), (22, 30))

    def test_chapter_list_box_reads_the_scroll_count_on_the_chapter_list(self):
        image = load_rgb(CHAPTER_LIST)
        cu, _, total = ExplorationAssets.O_REALM_RAID_NUMBER.ocr(image)
        self.assertEqual((cu, total), (23, 30))

    def test_the_two_pages_do_not_share_one_box(self):
        # Sharing a box is the mistake that made the entrance page read 2.
        self.assertNotEqual(tuple(ExplorationAssets.O_REALM_RAID_NUMBER.roi),
                            tuple(ExplorationAssets.O_REALM_RAID_NUMBER1.roi))

    def test_each_box_stays_inside_the_screen(self):
        for name in ('O_REALM_RAID_NUMBER', 'O_REALM_RAID_NUMBER1'):
            x, y, w, h = getattr(ExplorationAssets, name).roi
            self.assertGreaterEqual(min(x, y), 0, name)
            self.assertLessEqual(x + w, 1280, name)
            self.assertLessEqual(y + h, 720, name)


if __name__ == '__main__':
    unittest.main()
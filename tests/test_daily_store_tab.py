"""Real gift-room pixels and displaced daily labels exercise screenshot-only navigation."""
from pathlib import Path
import unittest

import cv2
import numpy as np

from tasks.DailyTrifles.store_sign import (
    daily_tab_selected, find_daily_tab, gift_room_visible, daily_gift_claim_center,
)


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CENTER = (1208, 397)


def read_rgb(path):
    return cv2.cvtColor(cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8),
                                   cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


class DailyStoreTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = read_rgb(ROOT / 'tests/fixtures/store_gift_room.png')
        cls.honor = read_rgb(ROOT / 'tests/fixtures/honor_live_sold_out.png')
        cls.label = read_rgb(ROOT / 'tasks/DailyTrifles/store/store_daily.png')

    def shifted(self, center):
        frame = self.frame.copy()
        # Erase the old label, retaining the independent gift-room footer.
        frame[360:435, 1187:1229] = (132, 102, 79)
        self.put_label(frame, center)
        return frame

    def put_label(self, frame, center, *, selected=False, pink_paper=False):
        label = self.label.copy()
        if pink_paper:
            gray = cv2.cvtColor(label, cv2.COLOR_RGB2GRAY)
            label[gray >= 85] = (245, 195, 220)
        if selected:
            cv2.circle(frame, center, 39, (245, 195, 220), 6)
        height, width = label.shape[:2]
        x, y = center[0] - width // 2, center[1] - height // 2
        frame[y:y + height, x:x + width] = label

    def test_supplied_label_and_full_error_frame_have_portable_real_pixels(self):
        self.assertEqual(self.label.shape, (75, 42, 3))
        self.assertEqual(self.frame.shape, (720, 1280, 3))
        self.assertEqual(find_daily_tab(self.frame), ORIGINAL_CENTER)
        self.assertTrue(gift_room_visible(self.frame))
        # The screenshot has 限时 selected; the visible 日常 label is unselected.
        self.assertFalse(daily_tab_selected(self.frame))

    def test_actual_selected_daily_relocates_and_confirms_current_tab(self):
        frame = read_rgb(ROOT / 'tests/fixtures/store_daily_selected.png')
        self.assertEqual(find_daily_tab(frame), (1208, 228))
        self.assertTrue(gift_room_visible(frame))
        self.assertTrue(daily_tab_selected(frame))

    def test_real_daily_gift_title_and_claim_text_determine_the_button_center(self):
        frame = read_rgb(ROOT / 'tests/fixtures/store_daily_selected.png')
        self.assertEqual(daily_gift_claim_center(frame, (186, 191, 115, 83)), (242, 314))
        # Another product has a price, not this card's title and free claim.
        self.assertIsNone(daily_gift_claim_center(frame, (416, 191, 115, 83)))

    def test_selected_and_unselected_variants_share_one_ambiguity_check(self):
        frame = read_rgb(ROOT / 'tests/fixtures/store_daily_selected.png')
        self.put_label(frame, (800, 390))
        self.assertIsNone(find_daily_tab(frame))
        self.assertFalse(daily_tab_selected(frame))

    def test_label_found_at_actual_center_after_moving_in_both_axes(self):
        for center in ((1208, 220), (1110, 510), (900, 390)):
            with self.subTest(center=center):
                frame = self.shifted(center)
                self.assertEqual(find_daily_tab(frame), center)
                self.assertFalse(daily_tab_selected(frame))

    def test_two_complete_daily_labels_are_ambiguous_even_if_one_is_pink(self):
        frame = self.frame.copy()
        self.put_label(frame, (800, 250), selected=True)
        self.assertIsNone(find_daily_tab(frame))
        self.assertFalse(daily_tab_selected(frame))

    def test_missing_daily_label_sends_no_guessed_position(self):
        frame = self.frame.copy()
        frame[350:445, 1177:1239] = (132, 102, 79)
        self.assertIsNone(find_daily_tab(frame))
        self.assertFalse(daily_tab_selected(frame))
        self.assertTrue(gift_room_visible(frame))

    def test_partial_label_is_not_a_daily_target(self):
        frame = self.shifted((900, 390))
        frame[390:435, 879:921] = (132, 102, 79)
        self.assertIsNone(find_daily_tab(frame))

    def test_selected_daily_is_relocated_after_the_sidebar_recenters(self):
        frame = self.shifted((1208, 220))
        self.put_label(frame, (1208, 220), selected=True)
        self.assertEqual(find_daily_tab(frame), (1208, 220))
        self.assertTrue(daily_tab_selected(frame))

    def test_pink_at_the_old_position_cannot_confirm_the_relocated_label(self):
        frame = self.shifted((1208, 220))
        cv2.circle(frame, ORIGINAL_CENTER, 39, (245, 195, 220), 6)
        self.assertEqual(find_daily_tab(frame), (1208, 220))
        self.assertFalse(daily_tab_selected(frame))

    def test_pink_paper_background_preserves_recognition_of_the_upright_glyphs(self):
        frame = self.shifted((1208, 220))
        self.put_label(frame, (1208, 220), selected=True, pink_paper=True)
        self.assertEqual(find_daily_tab(frame), (1208, 220))
        self.assertTrue(daily_tab_selected(frame))

    def test_actual_other_shop_keeps_the_gift_icon_but_is_not_the_gift_room(self):
        self.assertEqual(self.honor.shape, (720, 1280, 3))
        self.assertFalse(gift_room_visible(self.honor))
        self.assertIsNone(find_daily_tab(self.honor))
        self.assertFalse(daily_tab_selected(self.honor))

    def test_daily_and_pink_on_another_shop_cannot_confirm_the_destination(self):
        frame = self.honor.copy()
        self.put_label(frame, (1208, 397), selected=True)
        self.assertEqual(find_daily_tab(frame), ORIGINAL_CENTER)
        self.assertFalse(gift_room_visible(frame))
        self.assertFalse(daily_tab_selected(frame))

    def test_gift_icon_without_the_selected_surrounding_glow_is_insufficient(self):
        frame = self.frame.copy()
        frame[635:700, 1097:1130] = (70, 50, 35)
        frame[635:700, 1198:1231] = (70, 50, 35)
        self.assertFalse(gift_room_visible(frame))

    def test_invalid_or_empty_images_do_not_match_or_verify(self):
        for image in (None, np.zeros(3), np.zeros((0, 1280, 3), np.uint8),
                      np.zeros((720, 0, 3), np.uint8),
                      np.zeros((720, 1280), np.uint8),
                      np.zeros((720, 1280, 4), np.uint8)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                self.assertIsNone(find_daily_tab(image))
                self.assertFalse(daily_tab_selected(image))
                self.assertFalse(gift_room_visible(image))

    def test_uniform_frames_are_not_targets_or_destinations(self):
        for value in (0, 127, 255):
            image = np.full((720, 1280, 3), value, np.uint8)
            self.assertIsNone(find_daily_tab(image))
            self.assertFalse(daily_tab_selected(image))
            self.assertFalse(gift_room_visible(image))


if __name__ == '__main__':
    unittest.main()

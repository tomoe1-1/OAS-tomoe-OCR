from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.visual_cards import locate_card, card_drag_start
from test_draft_field_drag import lineup


def image():
    return np.array(Image.open(Path(__file__).parent / 'fixtures/draft_portrait_reordered.png').convert('RGB'))


def text(value, area):
    x, y, w, h = area
    return SimpleNamespace(ocr_text=value, score=1,
                           box=np.array([[x,y], [x+w,y], [x+w,y+h], [x,y+h]]))


class DraftVisualCardsTests(unittest.TestCase):
    def test_live_selected_face_survives_cross_overlay_and_floating_frame_is_rejected(self):
        folder = Path(__file__).parent / 'fixtures'
        face = np.array(Image.open(folder / 'draft_geye_face.png').convert('RGB'))
        for file in ('draft_drag_before.png', 'draft_drag_settled.png'):
            frame = np.array(Image.open(folder / file).convert('RGB'))
            match = locate_card(frame, [face])
            self.assertIsNotNone(match)
            self.assertLess(abs(match['point'][0]-548), 15)
            self.assertGreater(match['score'], .8)
        floating = np.array(Image.open(folder / 'draft_drag_floating.png').convert('RGB'))
        self.assertIsNone(locate_card(floating, [face]))
    def test_same_portrait_is_found_after_bottom_row_reorders(self):
        frame = image()
        # 用户截图中，上方头像与底部卡图相同，炭治郎已经移动到第一张。
        carbon = frame[51:91, 307:356]
        rukia = frame[51:91, 379:428]
        carbon_match = locate_card(frame, [carbon])
        rukia_match = locate_card(frame, [rukia])
        self.assertIsNotNone(carbon_match)
        self.assertIsNotNone(rukia_match)
        self.assertLess(abs(carbon_match['point'][0] - 318), 15)
        self.assertLess(abs(rukia_match['point'][0] - 544), 15)

    def test_blank_portrait_is_rejected(self):
        self.assertIsNone(locate_card(image(), [np.zeros((40, 40, 3), np.uint8)]))

    def test_press_starts_in_card_body_below_cross_and_soul_icon(self):
        point = card_drag_start(image(), {'point': (539, 531)})
        self.assertEqual(point[0], 539)
        self.assertGreater(point[1], 580)
        self.assertLess(point[1], 640)

    def test_ambiguous_two_names_cannot_share_one_card(self):
        obj = ScriptTask.__new__(ScriptTask)
        obj.device = SimpleNamespace(image=image())
        portrait = obj.device.image[51:91, 379:428]
        obj._portrait_bank = {'a': [portrait], 'b': [portrait]}
        self.assertEqual(obj._card_locations(['a', 'b']), {'a': None, 'b': None})

    def test_full_screen_ocr_locates_confirm_away_from_old_coordinates(self):
        obj = ScriptTask.__new__(ScriptTask)
        obj.device = SimpleNamespace(image=image())
        obj._screen_results = Mock(return_value=[text('确定', (1030, 620, 95, 50))])
        self.assertTrue(obj._battle_ready())
        # 点击点取 OCR 框中心左移 35px 后收成的 50x50 —— 避免点文字中心落在按钮右缘。
        # OCR 框 (1030,620,95,50) -> 中心 (1042,645) -> area (1017,620,50,50)。
        self.assertNotEqual(obj.C_BATTLE_CONFIRM.roi_front, (1120, 565, 100, 100))
        self.assertEqual(obj.C_BATTLE_CONFIRM.roi_front, (1017, 620, 50, 50))

    def test_warning_containing_confirm_is_not_a_button(self):
        obj = ScriptTask.__new__(ScriptTask)
        obj.device = SimpleNamespace(image=image())
        obj._screen_results = Mock(return_value=[text('检测到长时间未确定', (510, 140, 280, 30))])
        self.assertFalse(obj._battle_ready())

    def test_full_screen_ocr_runs_once_per_screenshot(self):
        obj = ScriptTask.__new__(ScriptTask)
        obj.device = SimpleNamespace(image=image())
        obj.O_SCREEN = SimpleNamespace(detect_and_ocr=Mock(return_value=[text('确定', (1100, 560, 120, 70))]))
        self.assertTrue(obj._battle_ready())
        self.assertIn('确定', obj._screen_text())
        obj.O_SCREEN.detect_and_ocr.assert_called_once()
        obj.device.image = obj.device.image.copy()
        self.assertTrue(obj._battle_ready())
        self.assertEqual(obj.O_SCREEN.detect_and_ocr.call_count, 2)

    def test_moved_selected_card_is_validated_at_new_picture_location(self):
        obj = lineup()
        obj._selected_cross.side_effect = [(435, .9), (719, .94), (435, .9)]
        obj._card_locations.side_effect = [
            {'a': {'point': (780, 560), 'score': .9}, 'b': None, 'c': None, 'd': None, 'e': None},
            {'a': {'point': (719, 560)}}, {'a': {'point': (719, 560)}}]
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True) as drag, \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        drag.assert_called_once_with(obj.device, p1=(780, 605), p2=(250, 320))
        obj.click.assert_called_once()
        self.assertEqual(obj._battle_locked, ['a'])

    def test_missing_target_picture_does_not_fall_back_to_slot_index(self):
        obj = lineup()
        obj._card_locations.return_value = {'a': None, 'b': None, 'c': None, 'd': None, 'e': None}
        obj._card_locations.side_effect = None
        with patch('tasks.DraftDuel.script_task.drag_to_field') as drag:
            obj._select_battle_lineup_timed([])
        drag.assert_not_called()
        obj.click.assert_not_called()

    def test_preparation_ocr_is_not_misclassified_as_gold_victory(self):
        obj = ScriptTask.__new__(ScriptTask)
        obj.device = SimpleNamespace(image=image(), stuck_record_clear=Mock())
        obj.O_SCREEN = SimpleNamespace(detect_and_ocr=Mock(return_value=[text('确定', (1100, 560, 120, 70))]))
        obj.screenshot = Mock()
        obj._post_draft_lobby_role = Mock(side_effect=[None, 'finished', 'finished'])
        obj._victory_screen = Mock(return_value=True)
        obj._select_battle_lineup = Mock()
        obj.click = Mock()
        with patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._wait_battle_return_impl(20, [])
        obj._select_battle_lineup.assert_called_once()
        obj._victory_screen.assert_not_called()
        obj.click.assert_not_called()
        obj.O_SCREEN.detect_and_ocr.assert_called_once()


if __name__ == '__main__':
    unittest.main()

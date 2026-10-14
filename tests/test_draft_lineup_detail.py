"""阵容详情不得因 OCR 抖动重复弹出或采用切页中的名单。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.strategy import normalize_name


NAMES = ['阿修罗', '追月神', '鬼切', '黑童子', '灶门祢豆子']


def subject(frames=None):
    obj = ScriptTask.__new__(ScriptTask)
    obj.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8))
    obj.click = Mock(return_value=True)
    obj.screenshot = Mock()
    obj.O_DETAIL_TITLE = SimpleNamespace(ocr=Mock(return_value='阵容详情'))
    frames = frames or [NAMES, NAMES]
    obj.O_LINEUP_NAMES = [SimpleNamespace(ocr=Mock(side_effect=[frame[i] for frame in frames]))
                         for i in range(5)]
    obj._detail_name_boxes = Mock(side_effect=[[(name, SimpleNamespace(
        ocr_text=name, box=np.array([[280, 170], [340, 170], [340, 210], [280, 210]])))
        for name in frame] for frame in frames])
    obj.O_LINEUP_SPEEDS = [SimpleNamespace(ocr=Mock(return_value='181')) for _ in range(5)]
    obj.O_LINEUP_DEFENSES = [SimpleNamespace(ocr=Mock(return_value='')) for _ in range(5)]
    return obj


class DraftLineupDetailTests(unittest.TestCase):
    @patch('tasks.DraftDuel.script_task.time.sleep')
    def test_detail_retries_ocr_inside_one_open_panel(self, _):
        bad = NAMES[:4] + ['?']
        obj = subject([bad, NAMES, NAMES])
        self.assertEqual(obj._read_battle_lineup([])[0], NAMES)
        self.assertEqual(obj.click.call_count, 3)  # 打开、我的、关闭各一次
        self.assertEqual(obj._detail_name_boxes.call_count, 3)

    @patch('tasks.DraftDuel.script_task.time.sleep')
    def test_tab_transition_must_settle_before_accepting_names(self, _):
        other = ['鲸汐千姬', '福悦座敷童子', '情思日和坊', '纺愿缘结神', '泷']
        obj = subject([other, NAMES, NAMES])
        self.assertEqual(obj._read_battle_lineup([])[0], NAMES)

    @patch('tasks.DraftDuel.script_task.time.sleep')
    def test_confirmed_match_detail_is_reused_without_clicks(self, _):
        obj = subject()
        first = obj._read_battle_lineup([])
        obj.click.reset_mock()
        self.assertIs(obj._read_battle_lineup([]), first)
        obj.click.assert_not_called()

    @patch('tasks.DraftDuel.script_task.time.sleep')
    def test_unrecognized_name_cools_down_and_bounds_reopening(self, _):
        obj = subject([NAMES[:4] + ['?']] * 9)
        with patch('tasks.DraftDuel.script_task.time.monotonic', return_value=100):
            self.assertIsNone(obj._read_battle_lineup([]))
            obj.click.reset_mock()
            self.assertIsNone(obj._read_battle_lineup([]))
            obj.click.assert_not_called()
        for now in (110, 120):
            with patch('tasks.DraftDuel.script_task.time.monotonic', return_value=now):
                self.assertIsNone(obj._read_battle_lineup([]))
        obj.click.reset_mock()
        with patch('tasks.DraftDuel.script_task.time.monotonic', return_value=130):
            self.assertIsNone(obj._read_battle_lineup([]))
        obj.click.assert_not_called()
        self.assertFalse(hasattr(obj, '_match_lineup_detail'))

    @patch('tasks.DraftDuel.script_task.time.sleep')
    def test_blocked_open_does_not_click_more_cards(self, _):
        obj = subject()
        obj.click.return_value = False
        self.assertIsNone(obj._read_battle_lineup([]))
        self.assertEqual(obj.click.call_count, 1)

    def test_observed_nezuko_ocr_variant_resolves_only_full_name(self):
        self.assertEqual(normalize_name('灶门称豆子999999'), '灶门祢豆子')
        self.assertIsNone(normalize_name('称豆子'))


if __name__ == '__main__':
    unittest.main()

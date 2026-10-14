import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tasks.CollectiveMissions.script_task import ScriptTask
from tasks.CollectiveMissions.config import MC

PANEL_TITLE = '集体任务'


class CollectiveDailyLimitTests(unittest.TestCase):
    def subject(self, total=(20, 10, 30)):
        task = ScriptTask.__new__(ScriptTask)
        task.screenshot = Mock()
        # `is_finish` 先在 `_collective_panel_visible` 里停一帧标题面板；
        # 这里让面板始终可见，使桩落到「总次数 / 每日次数」两组 OCR 上。
        task.device = SimpleNamespace(image=_panel_image(), click_record_clear=Mock())
        task.O_CM_NUMBER = SimpleNamespace(ocr=Mock(return_value=total))
        return task

    def detect(self, texts, total=(20, 10, 30)):
        task = self.subject(total)
        panel, card = _rule(), _rule()
        card.detect_and_ocr.side_effect = [
            [SimpleNamespace(ocr_text=text)] for text in texts]

        # 源码里只有「捐赠卡每日次数」那条规则用 full rect 的 DigitCounter 语义，
        # 且 `is_finish` 只会按名字构造它；其余一概是面板标题规则。
        # 按名字路由（而非按调用次序）可以让桩与循环轮数解耦。
        def rule_factory(*args, **kwargs):
            if kwargs.get('name') == 'cm_submission_daily_count':
                return card
            return panel

        with patch('tasks.CollectiveMissions.script_task.RuleOcr',
                   side_effect=rule_factory):
            result = task.is_finish()
        return result, card

    def test_donation_ten_finishes_even_when_other_cards_total_twenty(self):
        result, rule = self.detect(['今日已完成10次', '今日已完成10次'])
        self.assertTrue(result)
        self.assertEqual(rule.detect_and_ocr.call_count, 2)

    def test_nine_donations_are_not_complete(self):
        self.assertFalse(self.detect(['今日已完成9次'])[0])

    def test_unreadable_donation_count_is_not_complete(self):
        self.assertFalse(self.detect([''])[0])

    def test_two_fresh_frames_must_agree_on_cap(self):
        self.assertFalse(self.detect(['今日已完成10次', '今日已完成9次'])[0])

    def test_thirty_overall_keeps_original_completion(self):
        task = self.subject((30, 0, 30))
        panel = _rule()
        with patch('tasks.CollectiveMissions.script_task.RuleOcr', return_value=panel):
            self.assertTrue(task.is_finish())
        # 两帧总次数都为 30/30 即完成，无需读捐赠卡的每日次数：
        # 除面板标题外不再构造其它 OCR（捐赠卡用 side_effect，未触发即无调用）。
        self.assertEqual(panel.detect_and_ocr.call_count, 2)

    def test_invisible_panel_is_not_complete(self):
        task = self.subject((30, 0, 30))
        blank = _rule('')
        with patch('tasks.CollectiveMissions.script_task.RuleOcr', return_value=blank):
            self.assertFalse(task.is_finish())

    def test_disabled_refresh_is_clicked_once_then_keeps_current_mission(self):
        task = self.subject()
        task.O_CM_2 = SimpleNamespace(ocr=Mock(return_value=MC.GR1.value))
        task.appear_then_click = Mock(return_value=True)
        task._ui_verify = Mock(return_value=False)
        self.assertFalse(task.select_and_update_cur_mission(MC.AW3))
        task.appear_then_click.assert_called_once()
        self.assertEqual(task.current_mission, MC.GR1)


def _panel_image():
    """`_collective_panel_visible` 只做 `image.ndim` 与 `shape[:2]`，给个形状即可。"""
    return _Shape()


class _Shape:
    ndim = 3

    @property
    def shape(self):
        return (720, 1280, 3)


def _rule(text=PANEL_TITLE):
    return SimpleNamespace(detect_and_ocr=Mock(
        return_value=[SimpleNamespace(ocr_text=text)]))


if __name__ == '__main__':
    unittest.main()

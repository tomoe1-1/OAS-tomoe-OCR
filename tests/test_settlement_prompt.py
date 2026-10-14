"""Settlement inputs must follow the visible prompt rather than a blank side."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tasks.Component.GeneralBattle.general_battle import GeneralBattle, BattleAction
from tasks.GameUi.default_pages import handle_battle_reward_page
from tasks.GameUi.page import page_battle_result, page_reward
from tasks.GameUi.settlement_prompt import settlement_continue_click


def reading(text, left=552, top=136, right=724, bottom=168):
    return SimpleNamespace(ocr_text=text, score=1.0,
                           box=np.array(((left, top), (right, top),
                                         (right, bottom), (left, bottom)), dtype=float))


class SettlementPromptTests(unittest.TestCase):
    def subject(self, member=False):
        task = GeneralBattle.__new__(GeneralBattle)
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                      click_record_clear=Mock())
        task._is_member_battle = Mock(return_value=member)
        task.appear = Mock(return_value=False)
        task.appear_then_click = Mock(return_value=False)
        task.click = Mock(return_value=True)
        task._verify_member_settlement = Mock()
        return task

    def recognize(self, task, results):
        reader = SimpleNamespace(detect_and_ocr=Mock(return_value=results))
        with patch('tasks.GameUi.settlement_prompt.RuleOcr', return_value=reader):
            return settlement_continue_click(task)

    def test_saved_reward_and_experience_prompt_boxes_click_their_actual_center(self):
        task = self.subject()
        for result, expected in ((reading('点击屏幕继续'), (638, 692)),
                                 (reading('点击空白区域继续', 548, 143, 729, 169), (638, 696))):
            with self.subTest(text=result.ocr_text):
                target = self.recognize(task, [reading('04:28', 198, 104, 264, 131), result])
                self.assertEqual(target.coord(), expected)
                self.assertEqual(target.burst_count, 1)

    def test_new_frame_uses_new_matching_position_and_whitespace_is_accepted(self):
        task = self.subject()
        first = self.recognize(task, [reading('点击 屏幕 继续')])
        second = self.recognize(task, [reading('点击屏幕继续', 600, 140, 800, 170)])
        self.assertEqual(first.coord(), (638, 692))
        self.assertEqual(second.coord(), (700, 695))

    def test_no_prompt_or_two_prompts_do_not_authorize_a_blind_input(self):
        task = self.subject()
        for results in ([], [reading('04:28')], [reading('屏幕'), reading('继续')],
                        [reading('点击屏幕继续'), reading('点击空白区域继续')]):
            with self.subTest(results=results):
                self.assertIsNone(self.recognize(task, results))

    def test_invalid_detected_box_does_not_produce_a_click(self):
        task = self.subject()
        for result in (reading('点击屏幕继续', -10, 136, 724, 168),
                       reading('点击屏幕继续', 552, 136, 1300, 168),
                       reading('点击屏幕继续', 552, 136, 724, 181),
                       reading('点击屏幕继续', 552, 136, 552, 168)):
            with self.subTest(box=result.box):
                self.assertIsNone(self.recognize(task, [result]))

    def test_result_and_reward_wait_without_input_or_verification_when_prompt_absent(self):
        for member in (False, True):
            for source, method in ((page_battle_result, '_handle_result'), (page_reward, '_handle_reward')):
                with self.subTest(member=member, source=source):
                    task = self.subject(member)
                    task._battle_settlement_click = Mock(return_value=None)
                    context = SimpleNamespace(last_page=source)
                    with patch('tasks.Component.GeneralBattle.general_battle.close_reward_details', return_value=False):
                        self.assertEqual(getattr(task, method)(context, SimpleNamespace()), BattleAction.CONTINUE)
                    task.click.assert_not_called()
                    task._verify_member_settlement.assert_not_called()

    def test_member_click_once_then_verify_and_failed_input_does_not_authorize_verification(self):
        task = self.subject(member=True)
        target = self.recognize(task, [reading('点击空白区域继续')])
        task._battle_settlement_click = Mock(return_value=target)
        context = SimpleNamespace(last_page=page_battle_result)
        task._handle_result(context, SimpleNamespace())
        task.click.assert_called_once_with(target, interval=0.8)
        task._verify_member_settlement.assert_called_once_with(context, page_battle_result, target)
        task.click.reset_mock()
        task._verify_member_settlement.reset_mock()
        task.click.return_value = False
        task._handle_result(context, SimpleNamespace())
        task.click.assert_called_once()
        task._verify_member_settlement.assert_not_called()

    def test_reward_confirmation_does_not_also_issue_continue_input(self):
        task = self.subject()
        task.appear_then_click = Mock(return_value=True)
        task._battle_settlement_click = Mock()
        with patch('tasks.Component.GeneralBattle.general_battle.close_reward_details', return_value=False):
            task._handle_reward(SimpleNamespace(last_page=page_reward), SimpleNamespace())
        task.appear_then_click.assert_called_once()
        task._battle_settlement_click.assert_not_called()
        task.click.assert_not_called()

    def test_default_reward_hook_waits_or_clicks_exactly_one_detected_target(self):
        task = self.subject()
        target = self.recognize(task, [reading('点击屏幕继续')])
        with patch('tasks.GameUi.default_pages.close_reward_details', return_value=False), \
                patch('tasks.GameUi.default_pages.settlement_continue_click', return_value=None):
            self.assertFalse(handle_battle_reward_page(task))
        task.click.assert_not_called()
        with patch('tasks.GameUi.default_pages.close_reward_details', return_value=False), \
                patch('tasks.GameUi.default_pages.settlement_continue_click', return_value=target):
            self.assertTrue(handle_battle_reward_page(task))
        task.click.assert_called_once_with(target, interval=0.8)


if __name__ == '__main__':
    unittest.main()

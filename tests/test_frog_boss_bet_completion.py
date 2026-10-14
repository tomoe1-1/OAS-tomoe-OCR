"""Regress modal priority, selected amount persistence, and single inputs."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import cv2
import numpy as np

from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.FrogBoss.script_task import ScriptTask
from tasks.FrogBoss.config import Strategy


def subject(initial='choices', stall=None, small=False):
    task = ScriptTask.__new__(ScriptTask)
    state = {'page': initial}
    visible = {
        'choices': (task.I_BET_LEFT, task.I_BET_RIGHT),
        'amount': (task.I_BET_SURE, task.I_GOLD_30),
        'selected': (task.I_BET_SURE, task.I_GOLD_30, task.I_GOLD_30_CHECK),
        # Underlying buttons remain detectable through the modal.
        'confirm': (task.I_BET_SURE, task.I_GOLD_30, task.I_UI_CONFIRM),
        'small': (task.I_BET_SURE, task.I_GOLD_30, task.I_UI_CONFIRM_SAMLL),
        'done': (task.I_BETTED,),
        'previous': (task.I_NEXT_COMPETITION, task.I_BET_FAILURE),
        'next-only': (task.I_NEXT_COMPETITION,),
    }
    task.appear = lambda rule: rule in visible[state['page']]
    task.screenshot = Mock()
    task._choose_bet = Mock(return_value=task.I_BET_RIGHT)
    destinations = {
        task.I_BET_RIGHT.name: 'amount',
        task.I_GOLD_30.name: 'selected',
        task.I_BET_SURE.name: 'small' if small else 'confirm',
        task.I_UI_CONFIRM.name: 'done',
        task.I_UI_CONFIRM_SAMLL.name: 'done',
        task.I_NEXT_COMPETITION.name: 'choices',
    }
    def click(x, y, control_name):
        if control_name != stall:
            state['page'] = destinations[control_name]
    task.device = SimpleNamespace(click=Mock(side_effect=click))
    def verify(predicate, controls):
        for _ in range(2):
            task.screenshot()
            if not predicate():
                raise TaskRecoveryFailed('No verified destination')
        return True
    task._ui_verify = Mock(side_effect=verify)
    task.enter = Mock()
    task.next_run = Mock()
    task.record_oas_result = Mock()
    return task


class FrogBossBetCompletionTests(unittest.TestCase):
    def names(self, task):
        return [call.kwargs['control_name'] for call in task.device.click.call_args_list]

    def test_existing_strategy_options_are_retained(self):
        self.assertEqual({item.value for item in Strategy}, {
            'frog_majority', 'frog_minority', 'frog_bilibili', 'frog_dashen',
            'frog_oas', 'frog_always_red', 'frog_always_blue',
        })

    def test_oas_strategy_still_executes_without_external_requests(self):
        task = subject()
        task._choose_bet = ScriptTask._choose_bet.__get__(task, ScriptTask)
        task.config = SimpleNamespace(model=SimpleNamespace(frog_boss=SimpleNamespace(
            frog_boss_config=SimpleNamespace(strategy_frog=Strategy.Oas))))
        task.O_LEFT_COUNT = SimpleNamespace(ocr=Mock(return_value=10))
        task.O_RIGHT_COUNT = SimpleNamespace(ocr=Mock(return_value=20))
        task.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        task.oas_history = Mock()
        task.oas_history.choose.return_value = {'side': 'LEFT'}
        with patch('tasks.FrogBoss.script_task.fetch_predictions', return_value=[]):
            self.assertIs(task._choose_bet(), task.I_BET_LEFT)
        task.oas_history.choose.assert_called_once()

    def test_each_bet_step_sends_one_input(self):
        task = subject()
        task.do_bet()
        self.assertEqual(self.names(task), [task.I_BET_RIGHT.name, task.I_GOLD_30.name,
                                          task.I_BET_SURE.name, task.I_UI_CONFIRM.name])
        self.assertGreaterEqual(task.screenshot.call_count, 8)

    def test_confirmation_takes_priority_over_background_buttons(self):
        task = subject('confirm')
        task.do_bet()
        self.assertEqual(self.names(task), [task.I_UI_CONFIRM.name])
        task._choose_bet.assert_not_called()

    def test_small_confirmation_takes_priority(self):
        task = subject('small')
        task.do_bet()
        self.assertEqual(self.names(task), [task.I_UI_CONFIRM_SAMLL.name])

    def test_scheduler_resumes_confirmation_without_navigating_away(self):
        task = subject('confirm')
        with self.assertRaises(TaskEnd):
            task.run()
        task.enter.assert_not_called()
        self.assertEqual(self.names(task), [task.I_UI_CONFIRM.name])
        task.next_run.assert_called_once()

    def test_scheduler_resumes_amount_selection_without_navigating_away(self):
        task = subject('selected')
        with self.assertRaises(TaskEnd):
            task.run()
        task.enter.assert_not_called()
        self.assertEqual(self.names(task), [task.I_BET_SURE.name, task.I_UI_CONFIRM.name])

    def test_previous_loss_enters_next_round_before_placing_bet(self):
        task = subject('previous')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(self.names(task), [task.I_NEXT_COMPETITION.name, task.I_BET_RIGHT.name,
            task.I_GOLD_30.name, task.I_BET_SURE.name, task.I_UI_CONFIRM.name])
        task.record_oas_result.assert_called_once()
        task.enter.assert_not_called()

    def test_next_round_button_works_without_old_result_heading(self):
        task = subject('next-only')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(self.names(task)[0], task.I_NEXT_COMPETITION.name)
        self.assertEqual(self.names(task).count(task.I_NEXT_COMPETITION.name), 1)

    def test_failed_next_round_click_blocks_bet_and_task_completion(self):
        task = subject('next-only', stall=ScriptTask.I_NEXT_COMPETITION.name)
        with self.assertRaises(TaskRecoveryFailed):
            task.run()
        self.assertEqual(self.names(task), [task.I_NEXT_COMPETITION.name])
        task.next_run.assert_not_called()

    def test_selected_amount_is_not_clicked_again(self):
        task = subject('selected')
        task.do_bet()
        self.assertEqual(self.names(task), [task.I_BET_SURE.name, task.I_UI_CONFIRM.name])

    def test_completed_bet_sends_no_input(self):
        task = subject('done')
        task.do_bet()
        task.device.click.assert_not_called()

    def test_unresponsive_submission_is_not_replayed(self):
        task = subject('selected', stall=ScriptTask.I_BET_SURE.name)
        with self.assertRaises(TaskRecoveryFailed):
            task.do_bet()
        self.assertEqual(self.names(task), [task.I_BET_SURE.name])

    def test_verified_result_declares_scheduler_success(self):
        task = ScriptTask.__new__(ScriptTask)
        task.config = SimpleNamespace(model=SimpleNamespace(frog_boss=SimpleNamespace(
            frog_boss_config=SimpleNamespace(before_end_frog=SimpleNamespace(hour=0, minute=15, second=0)))),
            _learning_task_outcomes={})
        task.set_next_run = Mock()
        task.next_run()
        self.assertTrue(task.config._learning_task_outcomes['frog_boss'])
        self.assertNotIn('success', task.set_next_run.call_args.kwargs)
        self.assertFalse(task.set_next_run.call_args.kwargs['server'])

    def test_current_screen_marker_distinguishes_selected_amount(self):
        fixture = Path(__file__).parent / 'fixtures' / 'frog_boss'
        rule = copy.deepcopy(ScriptTask.I_GOLD_30_CHECK)
        for name, expected in [('selected', True), ('unselected', False)]:
            with self.subTest(frame=name):
                crop = cv2.imdecode(np.fromfile(str(fixture / f'{name}.png'), dtype=np.uint8),
                                    cv2.IMREAD_COLOR)
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                image[559:644, 822:870] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                self.assertEqual(rule.template_match(image), expected)

    def test_next_round_is_located_across_the_full_screen(self):
        for x, y in ((75, 110), (775, 485), (1180, 630)):
            with self.subTest(position=(x, y)):
                rule = copy.deepcopy(ScriptTask.I_NEXT_COMPETITION)
                self.assertEqual(list(rule.roi_back), [0, 0, 1280, 720])
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                template = rule.image
                height, width = template.shape[:2]
                image[y:y + height, x:x + width] = template
                self.assertTrue(rule.template_match(image))
                self.assertEqual(rule.front_center(), (x + width / 2, y + height / 2))

    def test_next_round_click_uses_the_matched_center_once(self):
        task = subject('next-only')
        with patch.object(task.I_NEXT_COMPETITION, 'roi_front', [1180, 630, 42, 36]):
            task._advance_competition()
        task.device.click.assert_called_once_with(1201.0, 648.0,
                                                  control_name=task.I_NEXT_COMPETITION.name)

    def test_reward_tooltip_dismissed_before_submission(self):
        task = subject('selected')
        overlay = {'visible': True}
        original_appear = task.appear
        task.appear = lambda rule: (overlay['visible'] if rule in (task.I_BET_REWARD_HEADING, task.I_BET_REWARD_SILVER) else original_appear(rule))
        original_click = task.device.click.side_effect
        def click(x, y, control_name):
            if control_name == 'FROG_BET_REWARD_TOOLTIP_DISMISS':
                overlay['visible'] = False
            else:
                original_click(x, y, control_name)
        task.device.click.side_effect = click
        with patch.object(task.I_BET_REWARD_HEADING, 'roi_front', [607, 131, 174, 30]), patch.object(task.I_BET_REWARD_SILVER, 'roi_front', [553, 274, 50, 42]):
            task.do_bet()
        self.assertEqual(self.names(task), ['FROG_BET_REWARD_TOOLTIP_DISMISS', task.I_BET_SURE.name, task.I_UI_CONFIRM.name])

    def test_tooltip_that_does_not_close_prevents_submission(self):
        task = subject('selected')
        original_appear = task.appear
        task.appear = lambda rule: (True if rule in (task.I_BET_REWARD_HEADING, task.I_BET_REWARD_SILVER) else original_appear(rule))
        task.device.click.side_effect = None
        with patch.object(task.I_BET_REWARD_HEADING, 'roi_front', [607, 131, 174, 30]), patch.object(task.I_BET_REWARD_SILVER, 'roi_front', [553, 274, 50, 42]):
            with self.assertRaises(TaskRecoveryFailed):
                task.do_bet()
        self.assertEqual(self.names(task), ['FROG_BET_REWARD_TOOLTIP_DISMISS'])

    def test_reference_reward_tooltip_is_recognized(self):
        frame = cv2.imdecode(np.fromfile(str(Path(__file__).parent / 'fixtures' / 'frog_boss' / 'reward_tooltip.png'), np.uint8), 1)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = np.zeros((720,1280,3),dtype=np.uint8)
        image[100:100+frame.shape[0],500:500+frame.shape[1]]=frame
        task = ScriptTask.__new__(ScriptTask)
        task.appear = lambda rule: rule.template_match(image)
        self.assertTrue(task._bet_reward_tooltip_visible())
        image[274:316,553:603]=0
        self.assertFalse(task._bet_reward_tooltip_visible())

    def test_real_tooltip_screenshot_is_recognized(self):
        """真实弹窗截图必须能被识别。

        夹具是游戏里「选 30 万后弹出获胜可获得」的**整幅真实画面**（1280x720），
        不是合成图。早期实现把 roi_front 记成实测值、却漏改了银币行的几何校验，
        结果 visible() 恒为 False、弹窗永远收不掉、押注必然超时。
        这里用真实素材锁死「标题 + 银币」的相对几何（dx≈-117、dy≈+149）。
        """
        image = cv2.imdecode(np.fromfile(
            str(Path(__file__).parent / 'fixtures' / 'frog_boss' / 'reward_tooltip_real.png'), np.uint8), 1)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self.assertEqual(image.shape, (720, 1280, 3), '真实弹窗夹具应为整幅 1280x720 画面')
        task = ScriptTask.__new__(ScriptTask)
        task.appear = lambda rule: rule.template_match(image)
        self.assertTrue(task.I_BET_REWARD_HEADING.template_match(image), '标题「获胜可获得」应命中')
        self.assertTrue(task.I_BET_REWARD_SILVER.template_match(image), '竞猜银币应命中')
        self.assertTrue(task._bet_reward_tooltip_visible(),
                        '真实弹窗必须被识别为可见（否则收不掉、押注必超时）')
        # 银币模板在弹窗里有金币/银币/铜币三个同列命中，最高分落在中间那行，
        # 几何校验必须锁定「第二行」而不是任意一行。
        hx, hy = task.I_BET_REWARD_HEADING.front_center()
        sx, sy = task.I_BET_REWARD_SILVER.front_center()
        self.assertAlmostEqual(sx - hx, -117, delta=16)
        self.assertAlmostEqual(sy - hy, 149, delta=16)
        # 抹掉弹窗内容后应立刻判为不可见
        blanked = image.copy()
        blanked[130:230, 500:750] = 0
        task2 = ScriptTask.__new__(ScriptTask)
        task2.appear = lambda rule: rule.template_match(blanked)
        self.assertFalse(task2._bet_reward_tooltip_visible())

    def test_tooltip_dismissed_by_clicking_bet_sure(self):
        """关闭弹窗应点「竞猜」按钮，而不是面板空白处。

        大哥实测：选 30 万后先弹「获胜可获得」，点「竞猜」可关闭，
        再次点「竞猜」才是真正押注。旧实现点 (180,180) 空白处收不掉这个弹窗。
        """
        task = subject('selected')
        overlay = {'visible': True}
        clicks = []

        def click(x, y, control_name):
            clicks.append((int(x), int(y), control_name))
            if control_name == 'FROG_BET_REWARD_TOOLTIP_DISMISS':
                overlay['visible'] = False
        task.device.click.side_effect = click

        def appear(rule):
            if rule in (task.I_BET_REWARD_HEADING, task.I_BET_REWARD_SILVER):
                return overlay['visible']
            if rule == task.I_BET_SURE:
                return True
            return False
        task.appear = appear
        with patch.object(task.I_BET_REWARD_HEADING, 'roi_front', [571, 139, 174, 30]), \
             patch.object(task.I_BET_REWARD_SILVER, 'roi_front', [516, 288, 50, 42]), \
             patch.object(task.I_BET_SURE, 'roi_front', [1040, 430, 50, 48]):
            self.assertTrue(task._bet_reward_tooltip_visible())
            self.assertTrue(task._clear_bet_reward_tooltip())
        dismiss = [c for c in clicks if c[2] == 'FROG_BET_REWARD_TOOLTIP_DISMISS']
        self.assertTrue(dismiss, '应当通过点击关闭弹窗')
        self.assertEqual(dismiss[0][:2], (1065, 454), '关闭弹窗应点「竞猜」按钮中心，不是空白处')


if __name__ == '__main__':
    unittest.main()


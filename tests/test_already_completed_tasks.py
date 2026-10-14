import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.base_task import BaseTask
from tasks.CollectiveMissions.script_task import ScriptTask as Collective
from tasks.MemoryScrolls.script_task import ScriptTask as Scrolls
from tasks.Dokan.script_task import ScriptTask as Dokan, DokanFinishedError


class CompletedTasksTests(unittest.TestCase):
    def subject(self, cls=BaseTask):
        task = cls.__new__(cls)
        task.config = SimpleNamespace(_learning_task_outcomes={}, save=Mock())
        task._ui_verify = Mock(return_value=True)
        task.set_next_run = Mock()
        task.goto_page = Mock(side_effect=TaskRecoveryFailed('return navigation failed'))
        return task

    def test_verified_completion_declares_success_and_stops(self):
        task = self.subject()
        task.config._learning_task_outcomes['collective_missions'] = False
        with self.assertRaises(TaskEnd):
            task.finish_completed_task('CollectiveMissions', 'already done', verify=Mock())
        task.set_next_run.assert_called_once_with(task='CollectiveMissions', success=True)
        self.assertTrue(task.config._learning_task_outcomes['collective_missions'])
        task.goto_page.assert_not_called()

    def test_unverified_completion_keeps_failure(self):
        task = self.subject()
        task._ui_verify.side_effect = TaskRecoveryFailed('completion disappeared')
        with self.assertRaises(TaskRecoveryFailed):
            task.finish_completed_task('CollectiveMissions', 'already done', verify=Mock())
        task.set_next_run.assert_not_called()
        self.assertEqual(task.config._learning_task_outcomes, {})

    def test_schedule_error_is_not_converted_to_success(self):
        task = self.subject()
        with self.assertRaises(ValueError):
            task.finish_completed_task('Dokan', 'done', schedule=Mock(side_effect=ValueError()))
        self.assertEqual(task.config._learning_task_outcomes, {})

    def test_collective_completed_card_defers_cleanup_to_the_scheduler(self):
        task = self.subject(Collective)
        task.screenshot = Mock()
        task.appear = Mock(return_value=False)   # 无奖励浮层、无关闭叉，清理走最短路径
        task.device = SimpleNamespace(image=None, click_record_clear=Mock())
        task.get_task_reward = Mock()
        task.is_finish = Mock(return_value=True)
        # 集体任务是弹窗内任务：奖励浮层的关闭叉只有它自己的 recognizer 认得，
        # 别的任务来了会误点。所以收尾不能"顺带"做，也不能因为收尾失败就把
        # 整条进程打死 —— 那样别的任务永远等不到这个弹窗消失。
        # 新契约：业务成功立刻到调度器，收尾义务记成 cleanup_pending，
        # 由独立恢复去验证回主页（见 runtime_controller 的 switch-home）。
        task.goto_page.side_effect = [None, TaskRecoveryFailed('return failed')]
        empty_panel = SimpleNamespace(detect_and_ocr=Mock(return_value=[]))
        with patch('tasks.CollectiveMissions.script_task.RuleOcr',
                   return_value=empty_panel):
            with self.assertRaises(TaskEnd):
                task.run()
        # 只有进入集体任务页那一次导航；收尾不再由本任务兜着。
        self.assertEqual(task.goto_page.call_count, 1)
        # 核心事实：成功记账必须已落盘，且收尾义务明确交出去了。
        self.assertTrue(task.config._learning_task_outcomes['collective_missions'])
        self.assertEqual(task.config.task_runtime_outcome,
                         {'task': 'CollectiveMissions', 'status': 'success',
                          'cleanup_pending': True})

    def test_scroll_completed_preserves_auto_close_and_skips_return_click(self):
        task = self.subject(Scrolls)
        task.config.exploration = SimpleNamespace(scheduler=SimpleNamespace(enable=True))
        task.screenshot = Mock()
        task.appear = Mock(side_effect=lambda marker: marker in (task.I_UI_BACK_RED, task.I_MS_COMPLETE))
        task.ui_click_until_disappear = Mock(side_effect=TaskRecoveryFailed('return failed'))
        con = SimpleNamespace(notification_95=False, auto_close_exploration=True,
                              scroll_number=SimpleNamespace(name='ONE'))
        with self.assertRaises(TaskEnd):
            task.goto_scroll(con)
        self.assertFalse(task.config.exploration.scheduler.enable)
        task.config.save.assert_called_once()
        task.ui_click_until_disappear.assert_not_called()
        self.assertTrue(task.config._learning_task_outcomes['memory_scrolls'])

    def test_dokan_completed_ends_before_return_navigation(self):
        task = self.subject(Dokan)
        task.before_run = Mock()
        task.config.model = SimpleNamespace(dokan=SimpleNamespace(
            dokan_config=SimpleNamespace(monday_to_thursday=False),
            attack_count_config=SimpleNamespace(init_attack_count=Mock())))
        task.screenshot = Mock()
        task.get_current_page = Mock(return_value=__import__('tasks.Dokan.page', fromlist=['page_dokan']).page_dokan)
        task.run_on_dokan = Mock(side_effect=DokanFinishedError())
        task.next_run = Mock()
        task.goto_page.side_effect = [None, TaskRecoveryFailed('return failed')]
        with self.assertRaises(TaskEnd):
            task.run()
        task.next_run.assert_called_once_with(is_dokan_activated=True)
        self.assertEqual(task.goto_page.call_count, 1)
        self.assertTrue(task.config._learning_task_outcomes['dokan'])

    def test_dokan_unknown_count_preserves_original_finished_behavior(self):
        task = self.subject(Dokan)
        task.appear = Mock(return_value=True)
        task.config.dokan = SimpleNamespace(dokan_config=SimpleNamespace(try_start_dokan=True))
        task.found_dokan_cnt = 0
        task.update_remain_attack_count = Mock(return_value=-1)
        task.ensure_dokan_created = Mock(side_effect=TaskRecoveryFailed('unknown state'))
        with self.assertRaises(DokanFinishedError):
            task.run_on_dokan_map()
        task._ui_verify.assert_not_called()
        task.ensure_dokan_created.assert_not_called()


if __name__ == '__main__':
    unittest.main()

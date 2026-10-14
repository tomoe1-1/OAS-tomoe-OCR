"""Verified unavailable guild raids must not block the task scheduler."""
import ast
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from module.exception import TaskEnd, TaskRecoveryFailed

ROOT = Path(__file__).resolve().parents[1]


def load_subject():
    tree = ast.parse((ROOT / 'tasks/RyouToppa/script_task.py').read_text(encoding='utf-8'))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    methods = [n for n in original.body if isinstance(n, ast.FunctionDef)
               and n.name in ('run', 'plan_tomorrow_ryoutoppa', 'has_ticket')]
    subject = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    raid_assets = SimpleNamespace(I_REALM_RAID='realm', **{f'I_MEDAL_{i}': i for i in range(6)})
    namespace = dict(datetime=datetime, timedelta=timedelta, dt_time=dt_time,
                     TaskEnd=TaskEnd, logger=Mock(), ImageGrid=lambda images: images,
                     RealmRaidAssets=raid_assets, page_main='main', page_kekkai_toppa='guild',
                     page_shikigami_records='records')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[subject], type_ignores=[])),
                 'ryou_completion', 'exec'), namespace)
    return namespace['Subject']


class RyouToppaCompletionTests(unittest.TestCase):
    def make_task(self, state='open', ticket=False):
        task = load_subject()()
        con = SimpleNamespace(
            raid_config=SimpleNamespace(limit_time=SimpleNamespace(hour=0, minute=30, second=0),
                                        limit_count=50, ryou_access=False, next_ryoutoppa_time='07:00'),
            switch_soul_config=SimpleNamespace(enable=False, enable_switch_by_name=False),
            general_battle_config=SimpleNamespace(lock_team_enable=True))
        task.config = SimpleNamespace(ryou_toppa=con, _learning_task_outcomes={})
        for name in ('I_REAL_RAID_REFRESH', 'I_SUCCESS_PENETRATION', 'I_SELECT_RYOU_BUTTON',
                     'I_NO_SELECT_RYOU', 'I_RYOU_REWARD', 'I_RYOU_REWARD_90',
                     'I_TOPPA_UNLOCK_TEAM', 'I_TOPPA_LOCK_TEAM'):
            setattr(task, name, name)
        marker = dict(open=task.I_RYOU_REWARD, unopened=task.I_NO_SELECT_RYOU,
                      completed=task.I_SUCCESS_PENETRATION)[state]
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule == marker)
        task.appear_then_click = Mock(return_value=False)
        task.screenshot = Mock()
        task._ryou_raid_complete = Mock(return_value=state == 'completed')

        def verify(predicate, controls):
            for _ in range(2):
                task.screenshot()
                if not predicate():
                    raise TaskRecoveryFailed('completion state was not stable')
            return True

        task._ui_verify = Mock(side_effect=verify)
        task.goto_page = Mock()
        task.ui_click = Mock()
        task.device = SimpleNamespace(stuck_record_add=Mock())
        task._ensure_ryou_toppa_page = Mock()
        task.has_ticket = Mock(return_value=ticket)
        task.attack_area = Mock()
        task.current_count = 0
        task.start_time = datetime.now()
        task.custom_next_run = Mock()

        def schedule(**kwargs):
            task.config._learning_task_outcomes['ryou_toppa'] = kwargs['success']
        task.set_next_run = Mock(side_effect=schedule)
        return task

    def assert_scheduler_accepts(self, task):
        from script import Script
        owner = Script.__new__(Script)
        owner.config = task.config
        owner.last_task_runtime_outcome = None
        owner._capture_task_runtime_outcome = Mock()
        self.assertTrue(owner._handle_task_exception(TaskEnd(), 'RyouToppa'))

    def test_zero_chances_finishes_run_and_releases_scheduler(self):
        task = self.make_task()
        with self.assertRaises(TaskEnd):
            task.run()
        task.attack_area.assert_not_called()
        self.assertEqual(task.goto_page.call_args_list[-1].args, ('main',))
        task.set_next_run.assert_called_once_with(task='RyouToppa', finish=True, server=True, success=True)
        self.assert_scheduler_accepts(task)

    def test_unopened_raid_without_permission_is_a_normal_wait(self):
        task = self.make_task(state='unopened')
        with self.assertRaises(TaskEnd):
            task.run()
        task.attack_area.assert_not_called()
        self.assert_scheduler_accepts(task)

    def test_completed_raid_keeps_custom_daily_schedule(self):
        task = self.make_task(state='completed')
        with self.assertRaises(TaskEnd):
            task.run()
        task.custom_next_run.assert_called_once()
        task.set_next_run.assert_not_called()
        task._ui_verify.assert_called_once_with(task._ryou_raid_complete, [])
        self.assertEqual(task.screenshot.call_count, 3)
        task.has_ticket.assert_not_called()
        task.attack_area.assert_not_called()
        self.assertTrue(task.config._learning_task_outcomes['ryou_toppa'])
        self.assert_scheduler_accepts(task)

    def test_initial_completion_verification_failure_preserves_unfinished_run(self):
        task = self.make_task(state='completed')
        task._ui_verify.side_effect = TaskRecoveryFailed('completion state was not stable')
        with self.assertRaises(TaskRecoveryFailed):
            task.run()
        task.custom_next_run.assert_not_called()
        task.set_next_run.assert_not_called()
        task.attack_area.assert_not_called()
        self.assertNotIn('ryou_toppa', task.config._learning_task_outcomes)

    def test_guild_completing_mid_run_is_verified_before_daily_schedule(self):
        task = self.make_task(ticket=True)
        task._ryou_raid_complete.side_effect = [False, True, True, True]
        with self.assertRaises(TaskEnd):
            task.run()
        task._ui_verify.assert_called_once_with(task._ryou_raid_complete, [])
        task.custom_next_run.assert_called_once()
        task.has_ticket.assert_not_called()
        task.attack_area.assert_not_called()
        task.set_next_run.assert_not_called()
        self.assert_scheduler_accepts(task)

    def test_mid_run_completion_verification_failure_does_not_schedule_next_day(self):
        task = self.make_task(ticket=True)
        task._ryou_raid_complete.side_effect = [False, True]
        task._ui_verify.side_effect = TaskRecoveryFailed('completion state was not stable')
        with self.assertRaises(TaskRecoveryFailed):
            task.run()
        task.custom_next_run.assert_not_called()
        task.set_next_run.assert_not_called()
        task.attack_area.assert_not_called()
        self.assertNotIn('ryou_toppa', task.config._learning_task_outcomes)

    def test_count_limit_finishes_without_an_extra_attack(self):
        task = self.make_task(ticket=True)
        task.current_count = 50
        with self.assertRaises(TaskEnd):
            task.run()
        task.attack_area.assert_not_called()
        self.assert_scheduler_accepts(task)

    def test_time_limit_finishes_without_an_extra_attack(self):
        task = self.make_task(ticket=True)
        task.start_time = datetime.now() - timedelta(hours=1)
        with self.assertRaises(TaskEnd):
            task.run()
        task.attack_area.assert_not_called()
        self.assert_scheduler_accepts(task)

    def test_navigation_failure_is_not_declared_complete(self):
        task = self.make_task()
        task._ensure_ryou_toppa_page.side_effect = RuntimeError('destination not verified')
        with self.assertRaisesRegex(RuntimeError, 'destination not verified'):
            task.run()
        task.set_next_run.assert_not_called()
        self.assertNotIn('ryou_toppa', task.config._learning_task_outcomes)

    def test_return_navigation_failure_is_not_declared_complete(self):
        task = self.make_task()
        task.goto_page.side_effect = [None, RuntimeError('main not verified')]
        with self.assertRaisesRegex(RuntimeError, 'main not verified'):
            task.run()
        task.set_next_run.assert_not_called()
        self.assertNotIn('ryou_toppa', task.config._learning_task_outcomes)

    def ticket_subject(self, value, hour=19):
        task = load_subject()()
        task.screenshot = Mock()
        task.wait_until_appear = Mock()
        task.I_TOPPA_RECORD = 'record'
        task.O_NUMBER = SimpleNamespace(ocr=Mock(return_value=value))
        task.device = SimpleNamespace(image=object())
        clock = Mock()
        clock.now.return_value = SimpleNamespace(hour=hour)
        task.has_ticket.__func__.__globals__['datetime'] = clock
        return task

    def test_visible_zero_of_six_finishes_in_day_and_night(self):
        for hour in (4, 19, 21, 23):
            with self.subTest(hour=hour):
                task = self.ticket_subject((0, 6, 6), hour=hour)
                self.assertFalse(task.has_ticket())
                task.O_NUMBER.ocr.assert_called_once()

    def test_visible_two_of_six_remains_attackable(self):
        self.assertTrue(self.ticket_subject((2, 4, 6)).has_ticket())

    def test_empty_ocr_is_not_evidence_of_completion(self):
        self.assertTrue(self.ticket_subject((0, 0, 0)).has_ticket())


if __name__ == '__main__':
    unittest.main()

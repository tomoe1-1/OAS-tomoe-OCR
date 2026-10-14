import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.Component.GeneralRoom.cooldown import parse_team_cooldown
from tasks.Component.GeneralRoom.general_room import GeneralRoom


class TeamCooldownTests(unittest.TestCase):
    def test_durations_and_unrelated_counts(self):
        for text, value in [('3时23分', timedelta(hours=3, minutes=23)),
                            ('1小时2分钟', timedelta(hours=1, minutes=2)),
                            ('59分', timedelta(minutes=59)), ('30秒', timedelta(seconds=30)),
                            ('0时0分', timedelta())]:
            self.assertEqual(parse_team_cooldown(text), value)
        for text in ['', '金币妖怪', '2/6', '等级1-60', '3时99分', '25时']:
            self.assertIsNone(parse_team_cooldown(text))

    def subject(self):
        task = GeneralRoom.__new__(GeneralRoom)
        task.list_find = Mock(return_value=(250, 310))
        task.L_TEAM_LIST = SimpleNamespace(roi_back=(26, 106, 360, 549))
        task.device = SimpleNamespace(image=None)
        task.set_next_run = Mock()
        task.config = SimpleNamespace()
        return task

    def read(self, texts):
        task = self.subject()
        rule = SimpleNamespace(ocr=Mock(side_effect=texts))
        with patch('tasks.Component.GeneralRoom.general_room.RuleOcr', return_value=rule):
            return task.check_team_cooldown('金币妖怪')

    def test_two_frames_confirm_countdown_and_minute_rollover(self):
        self.assertEqual(self.read(['3时23分', '3时22分']), timedelta(hours=3, minutes=23))

    def test_no_countdown_allows_challenge(self):
        self.assertIsNone(self.read(['', '']))
        self.assertIsNone(self.read(['0时0分', '0时0分']))

    def test_disagreement_does_not_allow_challenge(self):
        for values in [['3时23分', ''], ['', '3时23分'], ['3时23分', '1时23分']]:
            with self.assertRaises(TaskRecoveryFailed):
                self.read(values)

    def test_missing_row_is_not_ready(self):
        task = self.subject()
        task.list_find.return_value = (0, 0)
        with self.assertRaises(TaskRecoveryFailed):
            task.check_team_cooldown('石距')

    def test_cooldown_sets_explicit_time_without_failure_interval_or_server_reset(self):
        task = self.subject()
        before = datetime.now()
        with self.assertRaises(TaskEnd):
            task.schedule_team_cooldown('GoldYoukai', timedelta(hours=3, minutes=23))
        args = task.set_next_run.call_args.kwargs
        self.assertFalse(args['server'])
        self.assertNotIn('success', args)
        self.assertGreaterEqual(args['target'], before + timedelta(hours=3, minutes=24, seconds=4))
        self.assertEqual(task.config.task_runtime_outcome['status'], 'cooldown_delayed')

    def test_cooldown_releases_current_checkpoint_without_reporting_failed_or_completed_battle(self):
        from script import Script
        owner = Script.__new__(Script)
        owner.config = SimpleNamespace(_learning_task_outcomes={}, task_runtime_outcome={
            'task': 'GoldYoukai', 'status': 'cooldown_delayed', 'wait_until': datetime.now() + timedelta(hours=3)})
        owner.last_task_runtime_outcome = None
        self.assertTrue(owner._handle_task_exception(TaskEnd(), 'GoldYoukai'))
        self.assertEqual(owner.last_task_runtime_outcome['status'], 'cooldown_delayed')


if __name__ == '__main__':
    unittest.main()

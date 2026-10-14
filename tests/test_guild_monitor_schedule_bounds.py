"""Offline regression for normal weekday deferral and invalid monitor days."""
import ast
from datetime import datetime, timedelta, time
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]


class Clock(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 10, 2, 12, 34, 56)  # Friday, weekday 5 in the configuration.


def subject(run_days, server_update=time(9)):
    source = ast.parse((ROOT / 'tasks/GuildActivityMonitor/script_task.py').read_text(encoding='utf-8'))
    owner = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'ScriptTask')
    node = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'check_run_days')
    namespace = dict(datetime=Clock, timedelta=timedelta, re=re, logger=Mock(),
                     TaskRecoveryFailed=TaskRecoveryFailed)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(ROOT / 'tasks/GuildActivityMonitor/script_task.py'), 'exec'), namespace)
    task = type('Subject', (), {'check_run_days': namespace['check_run_days']})()
    task.config = SimpleNamespace(guild_activity_monitor=SimpleNamespace(
        guild_activity_monitor_combat_time=SimpleNamespace(run_days=run_days),
        scheduler=SimpleNamespace(server_update=server_update)))
    task.set_next_run = Mock()
    return task


class GuildMonitorScheduleBoundsTests(unittest.TestCase):
    def test_only_today_is_scheduled_for_same_day_next_week(self):
        task = subject('5')
        self.assertTrue(task.check_run_days())
        task.set_next_run.assert_called_once_with(task='GuildActivityMonitor', success=None,
            finish=False, server=False, target=Clock.now() + timedelta(days=7))

    def test_only_today_respects_fixed_server_time_next_week(self):
        task = subject('5', time(3, 15))
        self.assertTrue(task.check_run_days())
        self.assertEqual(task.set_next_run.call_args.kwargs['target'], Clock(2026, 10, 9, 3, 15))

    def test_other_scheduled_weekday_is_normal_deferral(self):
        for days, expected in (('6', False), ('5,6', True)):
            with self.subTest(days=days):
                task = subject(days)
                self.assertIs(task.check_run_days(), expected)
                self.assertEqual(task.set_next_run.call_args.kwargs['target'],
                                 Clock.now() + timedelta(days=1))
                self.assertIsNone(task.set_next_run.call_args.kwargs['success'])

    def test_empty_or_invalid_days_raise_failure_instead_of_due_success_loop(self):
        for days in ('', '0,8', 'none'):
            with self.subTest(days=days):
                task = subject(days)
                with self.assertRaisesRegex(TaskRecoveryFailed, '运行日期配置无效'):
                    task.check_run_days()
                task.set_next_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

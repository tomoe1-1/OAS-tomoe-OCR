"""Exercise scheduler failures without starting a device or waiting in real time."""
from datetime import datetime, timedelta, time
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from module.config.config import Config, Function
from module.exception import TaskRecoveryFailed, TaskEnd
from module.script.runtime_controller import ScriptRuntimeDecision
from module.script.task_completion import TaskCompletionGate
from script import Script


class Clock(datetime):
    value = datetime(2026, 10, 2, 11)

    @classmethod
    def now(cls, tz=None):
        value = cls.value
        return cls(value.year, value.month, value.day, value.hour, value.minute, value.second)


def model(interval=3):
    scheduler = SimpleNamespace(enable=True, next_run=Clock.value - timedelta(days=1),
        priority=5, failure_interval=timedelta(hours=interval),
        success_interval=timedelta(days=1), server_update=time(4),
        delay_date=1, float_time=time(0))
    return SimpleNamespace(scheduler=scheduler, model_dump=lambda: dict(scheduler=vars(scheduler)))


class FailurePolicyTests(unittest.TestCase):
    def setUp(self):
        for method in ('remember', 'prepare', 'clear'):
            recovery_patch = patch('module.script.failed_task_recovery.' + method, return_value='normal')
            recovery_patch.start()
            self.addCleanup(recovery_patch.stop)
        Clock.value = datetime(2026, 10, 2, 11)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'retry.json'
        self.owner = Script.__new__(Script)
        self.owner.completion_gate = TaskCompletionGate(self.path)
        self.owner.completion_gate.retain('Orochi')
        self.cfg = SimpleNamespace(model=SimpleNamespace(orochi=model(), restart=model(5), running_task=''),
            reload=Mock(), save=Mock(), lock_config=threading.Lock(), update_scheduler=Mock(),
            _learning_task_outcomes={}, task_runtime_outcome=None, notifier=Mock(),
            global_game=SimpleNamespace(ocr=SimpleNamespace(save_ocr_log=False)),
            script=SimpleNamespace(device=SimpleNamespace(run_background_only=True),
                error=SimpleNamespace(handle_error=True, error_repeated=False)))
        self.cfg.get_next = lambda: Function('orochi', self.cfg.model.orochi.model_dump())
        self.cfg.model.restart.scheduler.next_run = Clock.value + timedelta(days=1)
        self.cfg.task_delay = lambda **kw: Config.task_delay(self.cfg, **kw)
        self.owner.config = self.cfg
        self.owner.last_task_runtime_outcome = None
        self.owner.config_name = 'test'
        self.owner.resource_precache_enable = False
        self.owner.is_first_task = False
        self.owner.state_queue = None
        self.owner.device = SimpleNamespace(stuck_record_clear=Mock(), click_record_clear=Mock(),
            sleep=Mock(), reset_task_recovery=Mock(), screenshot=Mock())
        self.owner.runtime = SimpleNamespace(prepare_task_execution=Mock(return_value=ScriptRuntimeDecision.READY))
        self.owner._antiban_wake_time = Mock(return_value=None)
        self.owner._handle_continuous_task_rest = Mock(return_value=False)
        self.owner._reset_continuous_task_rest = Mock()
        self.owner._finish_error_learning = Mock()
        self.owner.save_error_log = Mock()
        self.owner.exception_handler = Mock()
        for target in ('script.datetime', 'module.config.config.datetime', 'module.script.task_completion.datetime'):
            p = patch(target, Clock)
            p.start()
            self.addCleanup(p.stop)

    def test_failure_interval_is_from_end_and_ignores_daily_reset(self):
        Config.task_delay(self.cfg, 'Orochi', success=False, server=True,
                          start_time=Clock.value - timedelta(hours=8))
        self.assertEqual(self.cfg.model.orochi.scheduler.next_run, Clock.value + timedelta(hours=3))

    def test_failure_delay_is_written_once_per_attempt(self):
        first = self.owner._defer_failed_task('Orochi')
        Clock.value += timedelta(minutes=10)
        self.assertEqual(self.owner._defer_failed_task('Orochi'), first)
        self.cfg.save.assert_called_once()
        self.assertEqual(TaskCompletionGate(self.path).not_before, first)

    def test_new_attempt_can_extend_failure_delay(self):
        first = self.owner._defer_failed_task('Orochi')
        Clock.value = first
        self.owner._reset_task_runtime_outcome()
        self.assertEqual(self.owner._defer_failed_task('Orochi'), first + timedelta(hours=3))

    def test_backend_restart_preserves_delay_even_if_task_call_is_early(self):
        retry_at = self.owner._defer_failed_task('Orochi')
        self.cfg.model.orochi.scheduler.next_run = Clock.value
        gate = TaskCompletionGate(self.path)
        self.assertEqual(gate.select(self.cfg).next_run, retry_at)
        self.assertEqual(gate.task, 'Orochi')

    def test_restart_failure_delays_business_without_releasing_it(self):
        retry_at = self.owner._defer_failed_task('Restart')
        restored = TaskCompletionGate(self.path)
        self.assertEqual(restored.task, 'Orochi')
        self.assertEqual(restored.not_before, retry_at)
        self.assertEqual(restored.select(self.cfg).next_run, Clock.value + timedelta(hours=5))

    def test_goto_main_uses_its_own_short_retry_not_restart_interval(self):
        """GotoMain 用独立的短重试，不再借用 Restart 的计划失败间隔。

        2026-10-07 行为变更：本批把 GotoMain 的回主失败从「借用 Restart 的
        failure_interval（5 小时）」改成**独立 5 分钟**。理由是回主是导航恢复，
        不是计划任务的重试 —— 挂在 Restart 的 5 小时上意味着「点一下回主失败」
        会让整个实例空等 5 小时，这正是 10-06 斗技那次假性失败被放大的原因。

        守卫守的是新语义：GotoMain 写自己的 deadline、**不碰 Restart**，
        且不释放已完成业务任务的进度。
        """
        # 本文件用 Script.__new__ 绕过 __init__（真实实例在 script.py:65 才初始化
        # 该字典），所以这里用 getattr 取初值，与 _defer_failed_task 内部写法一致。
        restart_before = getattr(self.owner, '_task_failure_deadlines', {}).get('Restart')
        retry_at = self.owner._defer_failed_task('GotoMain')
        # 独立短重试：约 5 分钟，而非 Restart 那个 5 小时。
        self.assertLessEqual(retry_at, Clock.value + timedelta(minutes=6))
        self.assertGreaterEqual(retry_at, Clock.value + timedelta(minutes=4))
        # 写进 GotoMain 自己的槽位，不借用也不污染 Restart。
        self.assertEqual(self.owner._task_failure_deadlines['GotoMain'], retry_at)
        self.assertEqual(self.owner._task_failure_deadlines.get('Restart'), restart_before,
                         'GotoMain 的恢复不得写入 Restart 的 deadline')
        # 业务任务的进度不受影响。
        self.assertEqual(self.owner.completion_gate.task, 'Orochi')

    def test_server_maintenance_is_not_an_attempt_failure(self):
        target = Clock.value + timedelta(minutes=15)
        self.owner.last_task_runtime_outcome = dict(status='server_update_delayed', wait_until=target)
        self.assertEqual(self.owner._defer_failed_task('Orochi'), target)
        self.cfg.save.assert_not_called()
        self.assertIsNone(self.owner.completion_gate.not_before)

    def _loop(self, prep_fail=False):
        calls, waits = [], []
        def wait(next_run):
            waits.append(next_run)
            Clock.value = next_run
            return ScriptRuntimeDecision.READY
        self.owner.runtime.handle_wait_during_idle = wait
        count = 1 if prep_fail else 2
        def get_next():
            if len(calls) >= count:
                raise KeyboardInterrupt()
            return Script.get_next_task(self.owner)
        self.owner.get_next_task = get_next
        def run(command):
            calls.append((command, Clock.value))
            return True if prep_fail else len(calls) > 1
        self.owner.run = run
        self.owner.record_task_result = Mock()
        if prep_fail:
            self.owner.runtime.prepare_task_execution.side_effect = [ScriptRuntimeDecision.FAILED, ScriptRuntimeDecision.READY]
        with patch('script.logger'), patch('script.RuleScatter'), patch('script.del_cached_property'), \
                patch('module.script.auto_repair.resume_pending_repairs'):
            with self.assertRaises(KeyboardInterrupt):
                self.owner.loop()
        return calls, waits

    def test_actual_scheduler_waits_three_hours_before_same_task_retry(self):
        calls, waits = self._loop()
        self.assertEqual(calls, [('Orochi', datetime(2026, 10, 2, 11)),
                                 ('Orochi', datetime(2026, 10, 2, 14))])
        self.assertEqual(waits, [datetime(2026, 10, 2, 14)])
        self.owner.device.sleep.assert_not_called()
        self.assertEqual(self.owner.completion_gate.task, '')

    def test_failed_preparation_obeys_same_interval(self):
        calls, waits = self._loop(prep_fail=True)
        self.assertEqual(calls, [('Orochi', datetime(2026, 10, 2, 14))])
        self.assertEqual(waits, [datetime(2026, 10, 2, 14)])
        self.owner.record_task_result.assert_any_call('Orochi', False)

    def test_task_returning_without_taskend_is_failed_and_delayed(self):
        task = SimpleNamespace(run=Mock())
        with patch('script.load_module', return_value=SimpleNamespace(ScriptTask=lambda **kw: task)), \
             patch('script.set_ocr_logging_enabled'), patch('module.device.error_learning.create_session', return_value=None):
            self.assertFalse(self.owner.run('Orochi'))
        self.assertEqual(self.cfg.model.orochi.scheduler.next_run, Clock.value + timedelta(hours=3))

    def test_recovery_failure_and_explicit_taskend_failure_are_delayed(self):
        for error in (TaskRecoveryFailed('no progress'), TaskEnd('incomplete')):
            with self.subTest(error=type(error).__name__):
                def execute():
                    self.cfg._learning_task_outcomes['orochi'] = False
                    raise error
                task = SimpleNamespace(run=execute)
                with patch('script.load_module', return_value=SimpleNamespace(ScriptTask=lambda **kw: task)), \
                        patch('script.set_ocr_logging_enabled'), \
                        patch('module.device.error_learning.create_session', return_value=None), \
                        patch('module.script.auto_repair.queue_repair', return_value=False):
                    self.assertFalse(self.owner.run('Orochi'))
                self.assertEqual(self.cfg.model.orochi.scheduler.next_run, Clock.value + timedelta(hours=3))
                self.assertEqual(self.owner.completion_gate.task, 'Orochi')


if __name__ == '__main__':
    unittest.main()

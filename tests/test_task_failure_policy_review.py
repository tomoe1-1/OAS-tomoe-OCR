"""Independent regressions for real scheduling, checkpoint, and recovery paths.

Use the real Config.task_delay, Script.run/loop/get_next_task, and runtime
controller while replacing disk config I/O and game actions with test doubles.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from module.config.config import Config, Function
from module.exception import (
    GamePageUnknownError, RequestHumanTakeover, ScriptError, TaskEnd,
    TaskRecoveryFailed,
)
from module.script.runtime_controller import ScriptRuntimeController, ScriptRuntimeDecision
from module.script.task_completion import TaskCompletionGate
from script import Script


class FrozenDateTime(datetime):
    current = None

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            raise AssertionError('OAS scheduling uses local naive datetimes')
        return cls.current


def task_model(next_run, enabled=True, failure_hours=3):
    scheduler = SimpleNamespace(
        enable=enabled, next_run=next_run, priority=5,
        success_interval=timedelta(days=1),
        failure_interval=timedelta(hours=failure_hours),
        server_update=time(hour=3), delay_date=1, float_time=time(),
    )
    return SimpleNamespace(
        scheduler=scheduler,
        model_dump=lambda: {'scheduler': vars(scheduler).copy()},
    )


class TaskFailurePolicyReviewTests(unittest.TestCase):
    def setUp(self):
        for method in ('remember', 'prepare', 'clear', 'read_switch'):
            recovery_patch = patch('module.script.failed_task_recovery.' + method, return_value=None if method == 'read_switch' else 'normal')
            recovery_patch.start()
            self.addCleanup(recovery_patch.stop)
        self.start = FrozenDateTime(2026, 10, 2, 10, 0)
        FrozenDateTime.current = self.start
        for module in ('script', 'module.config.config',
                       'module.script.task_completion', 'module.script.runtime_controller'):
            patcher = patch(module + '.datetime', FrozenDateTime)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'retry.json'
        self.config = Config.__new__(Config)
        self.config.model = SimpleNamespace(
            wanted_quests=task_model(self.start - timedelta(minutes=1)),
            daily_trifles=task_model(self.start - timedelta(minutes=1)),
            restart=task_model(self.start + timedelta(days=1)),
            running_task='',
            script=SimpleNamespace(
                device=SimpleNamespace(run_background_only=True),
                error=SimpleNamespace(handle_error=True, error_repeated=False),
                optimization=SimpleNamespace(when_task_queue_empty='goto_main'),
            ),
            global_game=SimpleNamespace(ocr=SimpleNamespace(save_ocr_log=False)),
        )
        self.config.reload = Mock()
        self.config.save = Mock()
        self.config.lock_config = Lock()
        self.config.update_scheduler = Mock()
        self.config.notifier = Mock()
        self.config.task_call = Mock()
        self.config._learning_task_outcomes = {}
        self.config.task = Function('wanted_quests', self.config.model.wanted_quests.model_dump())
        self.config.get_next = Mock(return_value=self.config.task)
        self.owner = Script.__new__(Script)
        self.owner.config = self.config
        self.owner.config_name = 'failure-policy-review'
        self.owner.completion_gate = TaskCompletionGate(self.path)
        self.owner.completion_gate.retain('WantedQuests')
        self.owner.last_task_runtime_outcome = None
        self.owner._last_failure_scheduled = None
        self.owner._task_failure_deadlines = {}
        self.owner.failure_record = {}
        self.owner.is_first_task = False
        self.owner.resource_precache_enable = False
        self.owner.state_queue = None
        self.owner._emulator_down = False
        self.owner.device = Mock()
        self.owner.device.image = None
        self.owner.device.app_is_running.return_value = True
        self.owner.runtime = ScriptRuntimeController(self.owner)
        self.owner._antiban_wake_time = Mock(return_value=None)
        self.owner._handle_continuous_task_rest = Mock(return_value=False)
        self.owner._reset_continuous_task_rest = Mock()
        self.owner.wait_until = Mock(side_effect=self.advance)
        self.owner.save_error_log = Mock()
        self.owner.exception_handler = Mock()

    def advance(self, until):
        self.assertGreaterEqual(until, FrozenDateTime.current)
        FrozenDateTime.current = until
        return True

    @contextmanager
    def runtime_isolation(self, task):
        """Keep production scheduling but prevent game, OCR, logs, and repair I/O."""
        with ExitStack() as stack:
            stack.enter_context(patch('script.logger'))
            stack.enter_context(patch('script.RuleScatter'))
            stack.enter_context(patch('script.del_cached_property'))
            stack.enter_context(patch('script.set_ocr_logging_enabled'))
            stack.enter_context(patch('module.atom.click.RuleClick.reset_task_points'))
            stack.enter_context(patch('module.device.error_learning.create_session', return_value=None))
            stack.enter_context(patch('module.script.auto_repair.queue_repair', return_value=False))
            stack.enter_context(patch('module.script.auto_repair.queue_activity_scan', return_value=False))
            stack.enter_context(patch('module.script.auto_repair.resume_pending_repairs'))
            stack.enter_context(patch('script.load_module', return_value=SimpleNamespace(
                ScriptTask=Mock(return_value=task))))
            yield

    def wait_without_actions(self, until):
        self.owner.wait_until(until)
        return ScriptRuntimeDecision.READY

    def test_real_loop_failure_waits_three_hours_then_retries_exactly_once(self):
        attempts = []

        def run_task():
            attempts.append(FrozenDateTime.now())
            if len(attempts) == 1:
                return  # Real Script.run treats falling through as incomplete.
            self.config.task_delay(task='WantedQuests', success=True, server=False)
            raise TaskEnd()

        real_get_next = self.owner.get_next_task

        def get_next():
            if len(attempts) == 2:
                raise KeyboardInterrupt()
            return real_get_next()

        self.owner.get_next_task = get_next
        self.owner.runtime.prepare_task_execution = Mock(return_value=ScriptRuntimeDecision.READY)
        self.owner.runtime.handle_wait_during_idle = Mock(side_effect=self.wait_without_actions)
        task = SimpleNamespace(run=run_task)
        with self.runtime_isolation(task), patch.object(
                self.config, 'task_delay', wraps=self.config.task_delay) as task_delay:
            with self.assertRaises(KeyboardInterrupt):
                self.owner.loop()
        failure_writes = [c for c in task_delay.call_args_list if c.kwargs.get('success') is False]
        self.assertEqual(len(failure_writes), 1, 'run and loop must share one failure write')
        self.assertEqual(attempts, [self.start, self.start + timedelta(hours=3)])
        self.owner.wait_until.assert_called_once_with(self.start + timedelta(hours=3))
        self.assertEqual(self.owner.failure_record['WantedQuests'], 0)
        self.assertEqual(self.owner.completion_gate.task, '')
        self.assertFalse(self.path.exists())

    def test_real_prepare_exception_defers_before_business_run(self):
        prepare_times = []
        run_times = []

        def prepare(command):
            prepare_times.append(FrozenDateTime.now())
            if len(prepare_times) == 1:
                raise TaskRecoveryFailed('bounded navigation exhausted')
            return ScriptRuntimeDecision.READY

        def run_task():
            run_times.append(FrozenDateTime.now())
            self.config.task_delay(task='WantedQuests', success=True, server=False)
            raise TaskEnd()

        real_get_next = self.owner.get_next_task

        def get_next():
            if run_times:
                raise KeyboardInterrupt()
            return real_get_next()

        self.owner.get_next_task = get_next
        self.owner.runtime.prepare_task_execution = prepare
        self.owner.runtime.handle_wait_during_idle = Mock(side_effect=self.wait_without_actions)
        with self.runtime_isolation(SimpleNamespace(run=run_task)):
            with self.assertRaises(KeyboardInterrupt):
                self.owner.loop()
        self.assertEqual(prepare_times, [self.start, self.start + timedelta(hours=3)])
        self.assertEqual(run_times, [self.start + timedelta(hours=3)])
        self.config.task_call.assert_not_called()

    def test_real_config_failure_uses_end_time_and_ignores_daily_server_reset(self):
        self.config.task_delay(task='WantedQuests', success=False, server=True,
                               start_time=self.start - timedelta(hours=8))
        self.assertEqual(self.config.model.wanted_quests.scheduler.next_run,
                         self.start + timedelta(hours=3))
        self.config.save.assert_called_once()

    def test_repeated_defer_does_not_move_retry_deadline(self):
        retry = self.owner._defer_failed_task('WantedQuests')
        self.advance(self.start + timedelta(minutes=5))
        self.assertEqual(self.owner._defer_failed_task('WantedQuests'), retry)
        self.config.save.assert_called_once()
        self.assertEqual(TaskCompletionGate(self.path).not_before,
                         self.start + timedelta(hours=3))

    def test_failed_restart_preserves_and_delays_business_after_process_reload(self):
        self.owner._defer_failed_task('Restart')
        gate = TaskCompletionGate(self.path)
        self.assertEqual(gate.task, 'WantedQuests')
        self.assertEqual(gate.not_before, self.start + timedelta(hours=3))
        selected = gate.select(self.config)
        self.assertEqual(selected.command, 'WantedQuests')
        self.assertEqual(selected.next_run, self.start + timedelta(hours=3))
        gate.retain('Restart')
        gate.release('Restart')
        self.assertEqual(TaskCompletionGate(self.path).not_before,
                         self.start + timedelta(hours=3))
        self.advance(self.start + timedelta(hours=3))
        self.assertEqual(gate.select(self.config).command, 'Restart')

    def test_goto_main_without_scheduler_uses_its_own_short_retry_and_spares_restart(self):
        # GotoMain has no scheduler of its own. It used to borrow Restart's
        # 3-hour failure interval, which meant a failed home navigation blocked
        # the next scheduled Restart for hours. It now retries on its own short
        # 5-minute clock and leaves Restart's schedule untouched.
        # Note: this is the opposite of what the old name asserted
        # ("uses_restart_interval_and_both_deadlines"), and it is the same
        # contract asserted by
        # test_goto_main_failure_does_not_prevent_startup_recovery below.
        retry = self.owner._defer_failed_task('GotoMain')
        self.assertEqual(retry, self.start + timedelta(minutes=5))
        # Restart keeps its own next_run; GotoMain must not push it forward.
        self.assertEqual(self.config.model.restart.scheduler.next_run,
                         self.start + timedelta(days=1))
        self.assertEqual(self.owner._task_failure_deadlines['GotoMain'], retry)
        self.assertNotIn('Restart', self.owner._task_failure_deadlines)

    def test_idle_goto_main_does_not_repeat_after_interrupted_retry_wait(self):
        retry = self.owner._defer_failed_task('GotoMain')
        self.owner.run = Mock(return_value=True)
        self.owner.wait_until = Mock(return_value=False)  # Config reload interrupts waiting.
        first = self.owner.runtime._prepare_idle_goto_main()
        second = self.owner.runtime._prepare_idle_goto_main()
        self.assertEqual(first, ScriptRuntimeDecision.RESCHEDULE)
        self.assertEqual(second, ScriptRuntimeDecision.RESCHEDULE)
        self.owner.run.assert_not_called()
        self.assertEqual(self.owner.wait_until.call_args_list[0].args, (retry,))
        self.assertEqual(self.owner.wait_until.call_args_list[1].args, (retry,))

    def test_goto_main_failure_does_not_prevent_startup_recovery(self):
        retry = self.owner._defer_failed_task('GotoMain')
        self.owner.run = Mock(return_value=True)
        self.assertEqual(self.owner.runtime._run_restart_recovery('start stopped game'),
                         ScriptRuntimeDecision.RESCHEDULE)
        self.owner.run.assert_called_once_with('Restart')
        self.owner.wait_until.assert_not_called()
        self.assertEqual(self.owner._task_failure_deadlines['GotoMain'], retry)
        self.assertNotIn('Restart', self.owner._task_failure_deadlines)


    def test_repeated_home_failure_reports_human_instead_of_waiting_forever(self):
        """A home page that never appears is a human-takeover condition.

        GotoMain retries on its own short clock (5 minutes) instead of
        borrowing Restart's 3-hour failure interval. Three consecutive
        failures therefore arrive in ~10 minutes rather than ~9 hours, and
        the third one escalates to the human.

        This keeps the original intent of the old
        `..._still_wakes_for_normal_business_due_time` case: auxiliary
        navigation must not silently postpone an otherwise due business
        task. The escalation is the bounded form of that guarantee -- the
        operator is told, rather than the scheduler waiting indefinitely
        for a main page that is not going to appear.
        """
        future = self.start + timedelta(hours=1)
        self.config.model.wanted_quests.scheduler.next_run = future
        attempts = []

        def run_main():
            attempts.append(FrozenDateTime.now())
            raise TaskRecoveryFailed('main unavailable')

        failed_main = SimpleNamespace(run=Mock(side_effect=run_main))
        with self.runtime_isolation(failed_main):
            with self.assertRaises(SystemExit):
                self.owner.get_next_task()
        # Bounded: exactly three attempts on the 5-minute clock, then escalate.
        self.assertEqual(attempts, [self.start,
                                    self.start + timedelta(minutes=5),
                                    self.start + timedelta(minutes=10)])
        self.assertEqual(self.owner.failure_record, {'GotoMain': 3})
        # The due business task was never silently postponed: we stopped and
        # asked for a human long before its schedule could be missed.
        self.assertLess(FrozenDateTime.now(), future)

    def test_startup_failure_uses_short_retry_without_persisting_task_delay(self):
        self.owner.run = Mock(return_value=True)
        self.owner.device.app_is_running.return_value = False
        for attempt in (1, 2):
            self.assertEqual(self.owner.runtime._run_restart_recovery('recover app'),
                             ScriptRuntimeDecision.FAILED)
            retry = self.start + timedelta(seconds=5)
            self.assertEqual(self.owner._defer_failed_task('Restart'), retry)
            self.assertIsNone(TaskCompletionGate(self.path).not_before)
            self.assertEqual(TaskCompletionGate(self.path).recovery_not_before, {})
        self.config.save.assert_not_called()
        self.assertEqual(self.owner.run.call_count, 2)
        self.assertFalse(self.owner._startup_recovery_active)


    def test_real_restart_task_end_keeps_finite_startup_budget_without_day_wait(self):
        attempts = []
        def restart_claims_success():
            attempts.append(FrozenDateTime.now())
            self.config.task_delay(task='Restart', success=True, server=False)
            self.config.task_runtime_outcome = {'task': 'Restart', 'status': 'recovered'}
            raise TaskEnd()
        self.owner.device.app_is_running.return_value = False
        with self.runtime_isolation(SimpleNamespace(run=restart_claims_success)):
            for failures in (1, 2):
                self.assertEqual(self.owner.runtime._run_restart_recovery('recover app'),
                                 ScriptRuntimeDecision.FAILED)
                self.assertEqual(self.owner.runtime._restart_recovery_failures, failures)
                self.assertIsNone(TaskCompletionGate(self.path).not_before)
                self.advance(FrozenDateTime.now() + timedelta(seconds=5))
            with self.assertRaisesRegex(RequestHumanTakeover, 'failed 3 times'):
                self.owner.runtime._run_restart_recovery('recover app')
        self.assertEqual(attempts, [self.start + timedelta(seconds=n) for n in (0, 5, 10)])
        self.assertEqual(TaskCompletionGate(self.path).task, 'WantedQuests')
        self.assertIsNone(TaskCompletionGate(self.path).not_before)


    def test_real_restart_recovery_resets_failures_only_after_app_state_success(self):
        def restart_claims_success():
            self.config.task_delay(task='Restart', success=True, server=False)
            self.config.task_runtime_outcome = {'task': 'Restart', 'status': 'recovered'}
            raise TaskEnd()

        self.owner.runtime._restart_recovery_failures = 2
        retry_at = self.owner._defer_failed_task('Restart')
        self.advance(retry_at)
        self.owner.device.app_is_running.return_value = True
        with self.runtime_isolation(SimpleNamespace(run=restart_claims_success)):
            self.assertEqual(self.owner.runtime._run_restart_recovery('recover app'),
                             ScriptRuntimeDecision.RESCHEDULE)
        self.owner.device.app_is_running.assert_called_once()
        self.assertEqual(self.owner.runtime._restart_recovery_failures, 0)
        self.assertNotIn('Restart', self.owner._task_failure_deadlines)
        self.assertNotIn('Restart', TaskCompletionGate(self.path).recovery_not_before)

    def test_third_restart_prepare_failure_stops_without_delaying_business_checkpoint(self):
        self.owner.runtime._restart_recovery_failures = 2
        with self.assertRaises(RequestHumanTakeover):
            self.owner.runtime._restart_recovery_failed()
        gate = TaskCompletionGate(self.path)
        self.assertEqual(gate.task, 'WantedQuests')
        self.assertIsNone(gate.not_before)


    def test_server_update_delay_is_normal_wait_without_failure_interval(self):
        future = self.start + timedelta(hours=1)
        window_end = self.start + timedelta(hours=3)
        self.config.model.wanted_quests.scheduler.next_run = future

        def server_update(**kwargs):
            self.owner._set_task_runtime_outcome('GotoMain', 'server_update_delayed', window_end)
            return True

        self.owner._delay_tasks_for_server_update = Mock(side_effect=server_update)
        failing_page = SimpleNamespace(run=Mock(side_effect=GamePageUnknownError('server updating')))
        with self.runtime_isolation(failing_page):
            self.assertEqual(self.owner.get_next_task(), 'WantedQuests')
            self.assertEqual(self.owner.runtime.prepare_task_execution('WantedQuests'),
                             ScriptRuntimeDecision.READY)
        self.assertEqual(FrozenDateTime.now(), window_end)
        self.config.save.assert_not_called()
        self.assertEqual(self.owner.failure_record, {})
        self.assertEqual(self.owner._task_failure_deadlines, {})
        self.assertIsNone(self.owner.completion_gate.not_before)

    def test_fatal_task_exception_keeps_checkpoint_and_future_retry(self):
        for error in (ScriptError('invalid script'), RequestHumanTakeover('login needs user'),
                      ValueError('unexpected data')):
            with self.subTest(error=type(error).__name__):
                self.owner._reset_task_runtime_outcome()
                with patch('script.logger'), self.assertRaises(SystemExit):
                    self.owner._handle_task_exception(error, 'WantedQuests')
                gate = TaskCompletionGate(self.path)
                self.assertEqual(gate.task, 'WantedQuests')
                self.assertEqual(gate.not_before, self.start + timedelta(hours=3))
                self.assertEqual(gate.select(self.config).next_run,
                                 self.start + timedelta(hours=3))



    def test_stopped_game_starts_despite_day_wait_in_all_entry_paths(self):
        for path in ('idle', 'business', 'scheduled'):
            with self.subTest(path=path):
                retry = self.start + timedelta(days=1)
                self.owner._task_failure_deadlines = {'Restart': retry, 'GotoMain': retry}
                self.owner.completion_gate.defer_recovery('Restart', retry)
                self.owner.device.app_is_running.side_effect = [False, False, True] if path == 'idle' else [False, True]
                self.owner.run = Mock(return_value=True)
                self.owner.wait_until.reset_mock()
                if path == 'idle':
                    result = self.owner.runtime._prepare_idle_goto_main()
                else:
                    result = self.owner.runtime.prepare_task_execution('Restart' if path == 'scheduled' else 'WantedQuests')
                self.assertEqual(result, ScriptRuntimeDecision.RESCHEDULE)
                self.owner.run.assert_called_once_with('Restart')
                self.owner.wait_until.assert_not_called()
                self.assertNotIn('Restart', self.owner.completion_gate.recovery_not_before)
                self.owner.device.app_is_running.side_effect = None

    def test_real_failed_startup_does_not_write_gui_failure_interval(self):
        self.owner.device.app_is_running.return_value = False
        failed = SimpleNamespace(run=Mock(side_effect=TaskRecoveryFailed('login failed')))
        with self.runtime_isolation(failed):
            self.assertEqual(self.owner.runtime._run_restart_recovery('start game'), ScriptRuntimeDecision.FAILED)
        self.config.save.assert_not_called()
        self.assertEqual(self.owner.last_task_runtime_outcome['status'], 'startup_recovery_failed')
        self.assertEqual(self.owner._defer_failed_task('Restart'), self.start + timedelta(seconds=5))
        self.assertIsNone(TaskCompletionGate(self.path).not_before)

    def test_running_game_scheduled_restart_still_respects_task_wait(self):
        retry = self.owner._defer_failed_task('Restart')
        self.owner.device.app_is_running.return_value = True
        self.owner.run = Mock()
        self.assertEqual(self.owner.runtime.prepare_task_execution('Restart'), ScriptRuntimeDecision.RESCHEDULE)
        self.owner.wait_until.assert_called_once_with(retry)
        self.owner.run.assert_not_called()

    def test_startup_prepare_failure_never_defers_unstarted_business_task(self):
        attempts = []
        def prepare(command):
            attempts.append(FrozenDateTime.now())
            if len(attempts) == 3:
                raise KeyboardInterrupt()
            self.owner._set_task_runtime_outcome('Restart', 'startup_recovery_failed',
                                                FrozenDateTime.now() + timedelta(seconds=5))
            return ScriptRuntimeDecision.FAILED
        self.owner.runtime.prepare_task_execution = Mock(side_effect=prepare)
        self.owner.record_task_result = Mock()
        with self.runtime_isolation(SimpleNamespace(run=Mock())):
            with self.assertRaises(KeyboardInterrupt):
                self.owner.loop()
        self.assertEqual(attempts, [self.start + timedelta(seconds=n) for n in (0, 5, 10)])
        self.config.save.assert_not_called()
        self.owner.record_task_result.assert_not_called()
        self.assertIsNone(TaskCompletionGate(self.path).not_before)
        self.assertEqual(self.owner.completion_gate.task, 'WantedQuests')

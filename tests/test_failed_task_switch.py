import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from datetime import datetime,timedelta
from module.script.runtime_controller import ScriptRuntimeController,ScriptRuntimeDecision
import module.script.failed_task_recovery as recovery
from module.exception import TaskRecoveryFailed

class TaskSwitchTests(unittest.TestCase):
    def setUp(self):
        # record_task_result and _task_failure_deadlines are real Script
        # attributes that ScriptRuntimeController now touches on the home
        # verification path, so a SimpleNamespace stand-in must provide them.
        self.owner=SimpleNamespace(config_name='switch-test',config=SimpleNamespace(),device=Mock(),wait_until=Mock(),_defer_failed_task=Mock(return_value=datetime.now()+timedelta(hours=3)),_clear_recovery_failure=Mock(),run=Mock(),record_task_result=Mock(),_task_failure_deadlines={},completion_gate=Mock())
        self.runtime=ScriptRuntimeController(self.owner)
        self.runtime._ensure_emulator_running=Mock()
        self.runtime._wait_for_server_update_window=Mock(return_value=ScriptRuntimeDecision.READY)
    def test_changed_recovery_policy_can_make_one_fresh_home_attempt(self):
        retry=datetime.now()+timedelta(days=1)
        with patch.object(recovery,'read_switch',return_value={'task':'SoulsTidy','not_before':retry.isoformat()}),patch.object(recovery,'prepare_switch',return_value=True) as home:
            self.assertEqual(self.runtime.prepare_task_execution('FrogBoss'),ScriptRuntimeDecision.READY)
        home.assert_called_once();self.owner._defer_failed_task.assert_not_called()
    def test_other_task_requires_verified_home_before_ready(self):
        with patch.object(recovery,'read_switch',return_value={'task':'SoulsTidy'}),patch.object(recovery,'prepare_switch',return_value=True) as home:
            self.assertEqual(self.runtime.prepare_task_execution('FrogBoss'),ScriptRuntimeDecision.READY)
        home.assert_called_once();self.owner.run.assert_not_called()
        self.owner._defer_failed_task.assert_not_called()
    def test_home_failure_keeps_next_task_queued_not_failed(self):
        with patch.object(recovery,'read_switch',return_value={'task':'SoulsTidy'}),patch.object(recovery,'prepare_switch',side_effect=TaskRecoveryFailed('blocked')),patch.object(recovery,'write_switch') as save:
            self.assertEqual(self.runtime.prepare_task_execution('FrogBoss'),ScriptRuntimeDecision.RESCHEDULE)
        self.owner._defer_failed_task.assert_called_once_with('GotoMain')
        self.owner.run.assert_not_called();save.assert_called_once()
        self.owner.wait_until.assert_called_once()
    def test_same_failed_task_preserves_original_page_route(self):
        with patch.object(recovery,'read_switch',return_value={'task':'FrogBoss'}),patch.object(recovery,'prepare_switch') as home:
            self.assertEqual(self.runtime.prepare_task_execution('FrogBoss'),ScriptRuntimeDecision.READY)
        home.assert_not_called()
    def test_long_home_backoff_is_shortened_so_a_stuck_page_is_reported_early(self):
        # A 3-hour "come back to main later" backoff would keep a scheduler
        # silent for three hours on a page it can never leave. Home recovery
        # is therefore capped at five minutes: the retry still happens (no busy
        # loop, no repeated input), but a genuinely stuck page escalates to the
        # three-strike human takeover in ~10 minutes instead of ~9 hours.
        #
        # The old name asserted the opposite (backoff survives a restart
        # unchanged); that contract contradicted the bounded escalation above.
        retry=datetime.now()+timedelta(hours=3)
        with patch.object(recovery,'read_switch',return_value={'task':'SoulsTidy','not_before':retry.isoformat(),'policy':2}),patch.object(recovery,'prepare_switch') as home:
            self.assertEqual(self.runtime.prepare_task_execution('FrogBoss'),ScriptRuntimeDecision.RESCHEDULE)
        home.assert_not_called()
        self.owner.wait_until.assert_called_once()
        waited=self.owner.wait_until.call_args.args[0]
        self.assertLess(waited,retry)
        self.assertLessEqual(waited,datetime.now()+timedelta(minutes=5))
        # Only the home deadline is shortened; the failed task keeps its own.
        self.assertEqual(self.owner._task_failure_deadlines.get('GotoMain'),waited)
    def test_old_idle_goto_deadline_does_not_block_switch_home(self):
        self.owner._task_failure_deadlines={'GotoMain':datetime.now()+timedelta(days=1)}
        with patch.object(recovery,'read_switch',return_value={'task':'SoulsTidy'}),patch.object(recovery,'prepare_switch',return_value=True) as home:
            self.assertEqual(self.runtime.prepare_task_execution('FrogBoss'),ScriptRuntimeDecision.READY)
        home.assert_called_once()

if __name__=='__main__':unittest.main()

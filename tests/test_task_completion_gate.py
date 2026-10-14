"""Unfinished tasks survive scheduling and recovery instead of being skipped."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from module.config.config import Function
from module.exception import TaskEnd
from module.script.task_completion import TaskCompletionGate

def task_model(enable=True,due=False,next_run=None):
    scheduler=SimpleNamespace(enable=enable,next_run=(next_run if next_run is not None else
        datetime.now()+timedelta(days=-1 if due else 1)),priority=5)
    return SimpleNamespace(scheduler=scheduler,model_dump=lambda:dict(scheduler=vars(scheduler).copy()))

class GateTests(unittest.TestCase):
    def setUp(self):
        for method in ('remember', 'prepare', 'clear', 'read_switch'):
            recovery_patch = patch('module.script.failed_task_recovery.' + method, return_value=None if method == 'read_switch' else 'normal')
            recovery_patch.start();self.addCleanup(recovery_patch.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'unfinished.json';self.gate=TaskCompletionGate(self.path)
        self.config=SimpleNamespace(update_scheduler=Mock(),model=SimpleNamespace(wanted_quests=task_model(),daily_trifles=task_model()),
                                    get_next=Mock(return_value=Function('daily_trifles',task_model().model_dump())))

    def test_unfinished_task_keeps_its_future_time_before_another_due_task(self):
        self.gate.retain('WantedQuests')
        self.config.model.daily_trifles=task_model(due=True)
        self.assertEqual(self.gate.select(self.config).command,'WantedQuests')
        self.assertEqual(self.gate.select(self.config).next_run,
                         self.config.model.wanted_quests.scheduler.next_run)
        self.config.get_next.assert_not_called()
        self.assertGreater(self.config.model.wanted_quests.scheduler.next_run,datetime.now())

    def test_checkpoint_survives_process_restart(self):
        self.gate.retain('WantedQuests')
        self.assertEqual(TaskCompletionGate(self.path).select(self.config).command,'WantedQuests')

    def test_real_schedule_display_cache_is_initialized_on_retry(self):
        from module.config.config import Config
        from tasks.Script.config_optimization import ScheduleRule
        config=Config.__new__(Config)
        model=self.config.model
        model.running_task=''
        model.script=SimpleNamespace(optimization=SimpleNamespace(schedule_rule=ScheduleRule.FILTER))
        model.dict=lambda:dict(wanted_quests=model.wanted_quests.model_dump(),daily_trifles=model.daily_trifles.model_dump())
        config.model=model;config.scheduler_update_dt=None
        self.gate.retain('WantedQuests');self.gate.select(config)
        config.get_schedule_data()  # Previously raised datetime < None in the retry path.
        self.assertIsNotNone(config.scheduler_update_dt)

    def test_success_releases_the_next_task(self):
        self.gate.retain('WantedQuests');self.gate.release('WantedQuests')
        self.assertEqual(self.gate.select(self.config).command,'DailyTrifles')
        self.assertFalse(self.path.exists())

    def test_due_restart_cannot_bypass_the_business_task_schedule(self):
        self.gate.retain('WantedQuests');self.config.model.restart=task_model(due=True)
        self.assertEqual(self.gate.select(self.config).command,'WantedQuests')
        self.gate.release('Restart');self.gate.retain('Restart')
        self.assertEqual(self.gate.task,'WantedQuests')
        self.config.model.restart=task_model()
        self.assertEqual(self.gate.select(self.config).command,'WantedQuests')

    def test_due_unfinished_task_can_resume_without_changing_its_time(self):
        due=datetime.now()-timedelta(minutes=1)
        self.config.model.wanted_quests=task_model(next_run=due)
        self.gate.retain('WantedQuests')
        selected=self.gate.select(self.config)
        self.assertEqual(selected.command,'WantedQuests')
        self.assertEqual(selected.next_run,due)

    def test_queued_restart_can_run_once_the_business_task_is_due(self):
        due=datetime.now()-timedelta(minutes=1)
        self.config.model.wanted_quests=task_model(next_run=due)
        self.config.model.restart=task_model(next_run=due)
        self.gate.retain('WantedQuests')
        selected=self.gate.select(self.config)
        self.assertEqual(selected.command,'Restart')
        self.assertEqual(selected.next_run,due)
        self.gate.retain('Restart')
        self.gate.release('Restart')
        self.assertEqual(self.gate.task,'WantedQuests')
        self.config.model.restart=task_model()
        self.assertEqual(self.gate.select(self.config).command,'WantedQuests')

    def test_due_restart_still_waits_for_persisted_business_retry_time(self):
        self.config.model.wanted_quests=task_model(due=True)
        self.config.model.restart=task_model(due=True)
        retry=datetime.now()+timedelta(minutes=5)
        self.gate.defer('WantedQuests',retry)
        selected=self.gate.select(self.config)
        self.assertEqual(selected.command,'WantedQuests')
        self.assertEqual(selected.next_run,retry)
        self.gate.retain('Restart')
        self.gate.release('Restart')
        self.assertEqual(TaskCompletionGate(self.path).not_before,retry)

    def test_restart_future_time_is_never_forced_to_now(self):
        self.config.model.wanted_quests=task_model(due=True)
        self.config.model.restart=task_model()
        scheduled=self.config.model.restart.scheduler.next_run
        self.gate.retain('WantedQuests')
        self.assertEqual(self.gate.select(self.config).command,'WantedQuests')
        self.assertEqual(self.config.model.restart.scheduler.next_run,scheduled)

    def test_defer_overrides_a_stale_due_config_without_mutating_it(self):
        due=datetime.now()-timedelta(days=1)
        retry=datetime.now()+timedelta(minutes=5)
        self.config.model.wanted_quests=task_model(next_run=due)
        self.assertTrue(self.gate.defer('WantedQuests',retry))
        self.assertEqual(self.gate.select(self.config).next_run,retry)
        self.assertEqual(self.config.model.wanted_quests.scheduler.next_run,due)
        self.config.get_next.assert_not_called()

    def test_config_later_than_checkpoint_retry_is_respected(self):
        retry=datetime.now()+timedelta(minutes=5)
        configured=retry+timedelta(hours=1)
        self.config.model.wanted_quests=task_model(next_run=configured)
        self.gate.defer('WantedQuests',retry)
        self.assertEqual(self.gate.select(self.config).next_run,configured)

    def test_retry_deadline_survives_reload_retain_and_unrelated_restart_release(self):
        retry=datetime.now()+timedelta(minutes=5)
        self.config.model.wanted_quests=task_model(due=True)
        self.gate.defer('WantedQuests',retry)
        reloaded=TaskCompletionGate(self.path)
        reloaded.retain('WantedQuests')
        reloaded.retain('Restart')
        reloaded.release('Restart')
        self.assertEqual(reloaded.task,'WantedQuests')
        self.assertEqual(reloaded.not_before,retry)
        self.assertEqual(TaskCompletionGate(self.path).select(self.config).next_run,retry)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['not_before'],retry.isoformat())

    def test_defer_never_shortens_an_existing_retry_wait(self):
        retry=datetime.now()+timedelta(minutes=10)
        self.gate.defer('WantedQuests',retry)
        self.gate.defer('WantedQuests',retry-timedelta(minutes=5))
        self.assertEqual(self.gate.not_before,retry)
        self.gate.defer('WantedQuests',retry+timedelta(minutes=5))
        self.assertEqual(self.gate.not_before,retry+timedelta(minutes=5))

    def test_another_task_cannot_replace_or_delay_the_existing_checkpoint(self):
        retry=datetime.now()+timedelta(minutes=10)
        self.gate.defer('WantedQuests',retry)
        self.gate.retain('DailyTrifles')
        self.assertFalse(self.gate.defer('DailyTrifles',retry+timedelta(days=1)))
        reloaded=TaskCompletionGate(self.path)
        self.assertEqual(reloaded.task,'WantedQuests')
        self.assertEqual(reloaded.not_before,retry)

    def test_legacy_checkpoint_without_retry_time_respects_the_config_schedule(self):
        self.path.write_text(json.dumps({'task':'WantedQuests'}),encoding='utf-8')
        reloaded=TaskCompletionGate(self.path)
        self.assertIsNone(reloaded.not_before)
        self.assertEqual(reloaded.select(self.config).next_run,
                         self.config.model.wanted_quests.scheduler.next_run)

    def test_business_success_clears_retry_time_but_keeps_release_receipt(self):
        retry=datetime.now()+timedelta(minutes=10)
        self.gate.defer('GoldYoukai',retry)
        self.gate.release('GoldYoukai')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['task'],'')
        self.assertNotIn('not_before',data)
        self.assertIn('GoldYoukai',data['released'])
        reloaded=TaskCompletionGate(self.path)
        self.assertIsNone(reloaded.not_before)
        self.assertEqual(reloaded.released,self.gate.released)

    def test_disabling_unfinished_task_clears_retry_wait_and_returns_normal_scheduler(self):
        self.gate.defer('WantedQuests',datetime.now()+timedelta(minutes=10))
        self.config.model.wanted_quests.scheduler.enable=False
        selected=self.gate.select(self.config)
        self.assertEqual(selected.command,'DailyTrifles')
        self.assertEqual(selected.next_run,self.config.get_next.return_value.next_run)
        self.assertIsNone(self.gate.not_before)
        self.assertFalse(self.path.exists())

    def test_no_unfinished_task_uses_normal_future_scheduler_unchanged(self):
        expected=self.config.get_next.return_value
        self.assertIs(self.gate.select(self.config),expected)
        self.config.get_next.assert_called_once()
        self.config.update_scheduler.assert_not_called()

    def test_invalid_checkpoint_retry_clocks_fail_instead_of_running_early(self):
        for timestamp in ('not-a-date','2026-10-02T01:00:00+08:00',123):
            with self.subTest(timestamp=timestamp):
                self.path.write_text(json.dumps({'task':'WantedQuests','not_before':timestamp}),encoding='utf-8')
                with self.assertRaises(ValueError):
                    TaskCompletionGate(self.path)

    def test_user_disabling_task_releases_checkpoint(self):
        self.gate.retain('WantedQuests');self.config.model.wanted_quests.scheduler.enable=False
        self.assertEqual(self.gate.select(self.config).command,'DailyTrifles')

    def test_auxiliary_retry_ledger_survives_reload_without_business_checkpoint(self):
        retry=datetime.now()+timedelta(hours=3)
        self.gate.defer_recovery('GotoMain',retry)
        reloaded=TaskCompletionGate(self.path)
        self.assertEqual(reloaded.task,'')
        self.assertIsNone(reloaded.not_before)
        self.assertEqual(reloaded.recovery_not_before,{'GotoMain':retry})
        self.assertEqual(reloaded.select(self.config),self.config.get_next.return_value)

    def test_business_retain_defer_release_preserve_recovery_deadlines(self):
        retry=datetime.now()+timedelta(hours=3)
        self.gate.defer_recovery('GotoMain',retry)
        self.gate.defer_recovery('Restart',retry+timedelta(minutes=1))
        self.gate.retain('WantedQuests')
        self.gate.defer('WantedQuests',retry)
        self.gate.release('WantedQuests')
        reloaded=TaskCompletionGate(self.path)
        self.assertEqual(reloaded.task,'')
        self.assertIsNone(reloaded.not_before)
        self.assertEqual(reloaded.recovery_not_before,
                         {'GotoMain':retry,'Restart':retry+timedelta(minutes=1)})

    def test_defer_recovery_never_shortens_deadline_or_replaces_business(self):
        retry=datetime.now()+timedelta(hours=3)
        self.gate.defer('WantedQuests',retry+timedelta(hours=1))
        self.assertEqual(self.gate.defer_recovery('Restart',retry),retry)
        self.assertEqual(self.gate.defer_recovery('Restart',retry-timedelta(hours=1)),retry)
        reloaded=TaskCompletionGate(self.path)
        self.assertEqual(reloaded.task,'WantedQuests')
        self.assertEqual(reloaded.not_before,retry+timedelta(hours=1))
        self.assertEqual(reloaded.recovery_not_before,{'Restart':retry})

    def test_recovery_success_clears_only_its_own_deadline(self):
        retry=datetime.now()+timedelta(hours=3)
        self.gate.defer('WantedQuests',retry)
        self.gate.defer_recovery('GotoMain',retry)
        self.gate.defer_recovery('Restart',retry)
        self.assertTrue(self.gate.release_recovery('GotoMain'))
        reloaded=TaskCompletionGate(self.path)
        self.assertEqual(reloaded.task,'WantedQuests')
        self.assertEqual(reloaded.not_before,retry)
        self.assertEqual(reloaded.recovery_not_before,{'Restart':retry})
        self.assertFalse(reloaded.release_recovery('GotoMain'))

    def test_last_recovery_release_removes_empty_retry_file(self):
        self.gate.defer_recovery('GotoMain',datetime.now()+timedelta(hours=3))
        self.assertTrue(self.gate.release_recovery('GotoMain'))
        self.assertEqual(self.gate.recovery_not_before,{})
        self.assertFalse(self.path.exists())

    def test_recovery_release_preserves_existing_completion_receipt(self):
        self.gate.defer_recovery('Restart',datetime.now()+timedelta(hours=3))
        self.gate.retain('GoldYoukai')
        self.gate.release('GoldYoukai')
        self.gate.release_recovery('Restart')
        reloaded=TaskCompletionGate(self.path)
        self.assertIn('GoldYoukai',reloaded.released)
        self.assertEqual(reloaded.task,'')
        self.assertEqual(reloaded.recovery_not_before,{})

    def test_invalid_auxiliary_retry_ledger_fails_instead_of_running_early(self):
        for ledger in (None, [], {'WantedQuests':'2026-10-02T10:00:00'},
                       {'Restart':'not-a-date'}, {'GotoMain':123},
                       {'GotoMain':'2026-10-02T10:00:00+08:00'}):
            with self.subTest(ledger=ledger):
                self.path.write_text(json.dumps({'task':'','recovery_not_before':ledger}),encoding='utf-8')
                with self.assertRaises(ValueError):TaskCompletionGate(self.path)

    def test_invalid_recovery_arguments_leave_business_checkpoint_unchanged(self):
        self.gate.retain('WantedQuests')
        for name,clock in (('WantedQuests',datetime.now()),('Restart','bad-clock')):
            with self.subTest(name=name,clock=clock):
                with self.assertRaises(ValueError):self.gate.defer_recovery(name,clock)
        with self.assertRaises(ValueError):self.gate.release_recovery('WantedQuests')
        self.assertEqual(TaskCompletionGate(self.path).task,'WantedQuests')
        self.assertEqual(self.gate.recovery_not_before,{})

    def test_explicit_failure_taskend_is_not_success(self):
        from script import Script
        owner=Script.__new__(Script)
        owner.config=SimpleNamespace(_learning_task_outcomes={'wanted_quests':False})
        owner.last_task_runtime_outcome=None;owner._capture_task_runtime_outcome=Mock()
        self.assertFalse(owner._handle_task_exception(TaskEnd(),'WantedQuests'))
        self.assertEqual(owner.last_task_runtime_outcome['status'],'incomplete')

    def test_scheduler_retries_same_task_until_success_then_advances(self):
        from script import Script
        from module.script.runtime_controller import ScriptRuntimeDecision
        owner=Script.__new__(Script);owner.completion_gate=self.gate
        owner.config_name='test';owner.resource_precache_enable=False
        owner.config=SimpleNamespace(model=SimpleNamespace(running_task=''),script=SimpleNamespace(
            device=SimpleNamespace(run_background_only=True),error=SimpleNamespace(handle_error=True)))
        owner.device=SimpleNamespace(stuck_record_clear=Mock(),click_record_clear=Mock(),sleep=Mock())
        owner.runtime=SimpleNamespace(prepare_task_execution=Mock(return_value=ScriptRuntimeDecision.READY))
        owner.is_first_task=False;owner._active_date=datetime.now().date();owner._active_seconds_today=0
        owner.record_task_result=Mock();owner._defer_failed_task=Mock();calls=[];attempts=iter([False,False,True,True])
        def next_task():
            if len(calls)>=4:raise KeyboardInterrupt()
            return self.gate.task or ('DailyTrifles' if calls else 'WantedQuests')
        owner.get_next_task=next_task
        def run(command):calls.append(command);return next(attempts)
        owner.run=run
        with patch('script.del_cached_property'),patch('script.logger'),patch('script.RuleScatter'):
            with patch('module.script.auto_repair.resume_pending_repairs'):
                with self.assertRaises(KeyboardInterrupt):owner.loop()
        self.assertEqual(calls,['WantedQuests','WantedQuests','WantedQuests','DailyTrifles'])
        self.assertEqual(owner._defer_failed_task.call_count,2)

    def test_three_failures_stop_without_advancing_and_keep_checkpoint(self):
        from script import Script
        from module.script.runtime_controller import ScriptRuntimeDecision
        owner=Script.__new__(Script);owner.completion_gate=self.gate
        owner.config_name='test';owner.resource_precache_enable=False;owner.failure_record={}
        owner.config=SimpleNamespace(model=SimpleNamespace(running_task=''),notifier=Mock(),script=SimpleNamespace(
            device=SimpleNamespace(run_background_only=True),error=SimpleNamespace(handle_error=True,error_repeated=False)))
        owner.device=SimpleNamespace(stuck_record_clear=Mock(),click_record_clear=Mock(),sleep=Mock())
        owner.runtime=SimpleNamespace(prepare_task_execution=Mock(return_value=ScriptRuntimeDecision.READY))
        owner.is_first_task=False;owner._active_date=datetime.now().date();owner._active_seconds_today=0
        owner.get_next_task=lambda:self.gate.task or 'WantedQuests'
        owner._defer_failed_task=Mock()
        owner.run=Mock(return_value=False)
        with patch('script.del_cached_property'),patch('script.logger'),patch('script.RuleScatter'):
            with patch('module.script.auto_repair.resume_pending_repairs'):
                with self.assertRaises(SystemExit):owner.loop()
        self.assertEqual([c.args[0] for c in owner.run.call_args_list],['WantedQuests']*3)
        self.assertEqual(TaskCompletionGate(self.path).task,'WantedQuests')

if __name__=='__main__':unittest.main()

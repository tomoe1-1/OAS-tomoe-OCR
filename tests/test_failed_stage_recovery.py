import unittest,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import module.script.failed_task_recovery as recovery
from module.exception import TaskRecoveryFailed

class FailedStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.json'
        self.switch_path=Path(self.tmp.name)/'switch.json'
        switch_patch=patch.object(recovery,'switch_checkpoint',return_value=self.switch_path)
        switch_patch.start();self.addCleanup(switch_patch.stop)
        p=patch.object(recovery,'checkpoint',return_value=self.path);p.start();self.addCleanup(p.stop)
        self.ui=Mock();self.ui.goto_page.return_value=True;self.ui.match_page_once.return_value=False
        p=patch.object(recovery,'GameUi',return_value=self.ui);p.start();self.addCleanup(p.stop)
        import numpy as np
        self.device=SimpleNamespace(_failed_original_page='result',image=np.zeros((720,1280,3),np.uint8),screenshot=Mock())
        self.ui.device=self.device
    def pending(self,page='result'):
        self.path.write_text(json.dumps({'page':page}))
    def test_unknown_intermediate_after_back_still_uses_bounded_navigation(self):
        self.device.reset_task_recovery=Mock()
        self.device.recover_by_escape=Mock(side_effect=lambda *args:setattr(self.device,'_escape_generation',10))
        self.assertTrue(recovery.prepare_switch(None,self.device,'tomoe'))
        self.assertEqual(self.ui._seen_escape_generation,10)
        self.ui.goto_page.assert_called_once();self.ui._ui_verify.assert_called_once()
    def test_switch_uses_back_then_verifies_home_and_keeps_failed_page(self):
        self.pending();recovery.write_switch('tomoe',{'task':'Orochi'})
        self.device.reset_task_recovery=Mock();self.device.recover_by_escape=Mock()
        self.assertTrue(recovery.prepare_switch(None,self.device,'tomoe'))
        self.device.recover_by_escape.assert_called_once();self.ui.goto_page.assert_called_once()
        self.ui._ui_verify.assert_called_once();self.assertTrue(self.path.exists())
        self.assertIsNone(recovery.read_switch('tomoe'))
    def test_switch_failure_retains_marker_and_original_checkpoint(self):
        self.pending();recovery.write_switch('tomoe',{'task':'Orochi'})
        self.device.reset_task_recovery=Mock();self.device.recover_by_escape=Mock()
        self.ui.goto_page.return_value=False
        with self.assertRaises(TaskRecoveryFailed):recovery.prepare_switch(None,self.device,'tomoe')
        self.assertEqual(recovery.read_switch('tomoe')['task'],'Orochi')
        self.assertTrue(self.path.exists())
    def test_switch_already_home_needs_verification_without_back(self):
        self.device.reset_task_recovery=Mock();self.device.recover_by_escape=Mock()
        self.ui.match_page_once.return_value=True
        self.assertTrue(recovery.prepare_switch(None,self.device,'tomoe'))
        self.device.recover_by_escape.assert_not_called();self.ui.goto_page.assert_not_called()
        self.ui._ui_verify.assert_called_once()
    def test_last_failure_marker_survives_reload(self):
        recovery.write_switch('tomoe',{'task':'Orochi'})
        self.assertEqual(recovery.read_switch('tomoe')['task'],'Orochi')
    def test_home_receipt_prevents_old_page_migration(self):
        (self.switch_path.parent/'Orochi.page.json').write_text('{}')
        recovery.write_switch('tomoe',{'task':'','home_verified':True})
        self.assertIsNone(recovery.read_switch('tomoe'))
    def test_migrate_existing_last_failure(self):
        (self.switch_path.parent/'Orochi.page.json').write_text('{}')
        self.assertEqual(recovery.read_switch('tomoe')['task'],'Orochi')
    def test_all_tasks_at_main_restart_from_entry(self):
        for command in ('DailyTrifles','Orochi','Chess','WantedQuests','RyouToppa','ExperienceYoukai','GoldYoukai','Tako','Nian','FrogBoss'):
            with self.subTest(command=command):
                self.pending('main');self.ui.match_page_once.return_value=True
                with patch.object(recovery,'reset_from_main') as reset:
                    self.assertEqual(recovery.prepare(None,self.device,'tomoe',command),'main')
                reset.assert_called_once_with(None,command)
    def test_legacy_failure_without_page_restarts_at_main(self):
        config=SimpleNamespace(failed_task_store=SimpleNamespace(read=lambda:{'Orochi':{}}))
        self.ui.match_page_once.return_value=True
        self.assertEqual(recovery.prepare(config,self.device,'tomoe','Orochi'),'main')
    def test_escape_to_main_never_resumes_old_stage(self):
        self.escape_case('main',raises=True)
    def test_home_has_priority_over_original_main(self):
        self.pending('main');self.ui.match_page_once.return_value=True
        with patch.object(recovery,'page_key') as identify, patch.object(recovery,'reset_from_main') as reset:
            self.assertEqual(recovery.prepare(None,self.device,'tomoe','DailyTrifles'),'main')
        identify.assert_not_called();self.ui.goto_page.assert_not_called()
        reset.assert_called_once_with(None,'DailyTrifles')
    def test_failed_home_confirmation_does_not_reset(self):
        self.pending('main');self.ui.match_page_once.return_value=True
        self.ui._ui_verify.side_effect=TaskRecoveryFailed('unstable home')
        with patch.object(recovery,'reset_from_main') as reset,self.assertRaises(TaskRecoveryFailed):
            recovery.prepare(None,self.device,'tomoe','DailyTrifles')
        reset.assert_not_called();self.assertTrue(self.path.exists())
    def daily_config(self):
        from datetime import datetime
        names=('courtyard_affairs_dt','pickup_email_dt','guild_donate_dt','luck_msg_dt','store_sign_dt','summon_dt','sushi_dt')
        record=SimpleNamespace(**{name:datetime(2026,10,2,12) for name in names},guild_donate_finish=True)
        return SimpleNamespace(daily_trifles=SimpleNamespace(done_record=record),save=Mock())
    def test_reset_daily_checklist_preserves_spending_and_other_tasks(self):
        from datetime import datetime
        config=self.daily_config();config.other_task=object();other=config.other_task
        recovery.reset_from_main(config,'DailyTrifles')
        record=config.daily_trifles.done_record
        self.assertEqual(record.store_sign_dt,datetime(2000,1,1))
        self.assertFalse(record.guild_donate_finish)
        self.assertEqual(record.summon_dt,datetime(2026,10,2,12))
        self.assertEqual(record.sushi_dt,datetime(2026,10,2,12))
        self.assertIs(config.other_task,other);config.save.assert_called_once()
    def test_save_failure_restores_in_memory_progress(self):
        config=self.daily_config();before=vars(config.daily_trifles.done_record).copy()
        config.save.side_effect=OSError('write failed')
        with self.assertRaises(OSError):recovery.reset_from_main(config,'DailyTrifles')
        self.assertEqual(vars(config.daily_trifles.done_record),before)
    def test_other_task_cannot_reset_daily_progress(self):
        config=self.daily_config();before=vars(config.daily_trifles.done_record).copy()
        recovery.reset_from_main(config,'ExperienceYoukai')
        self.assertEqual(vars(config.daily_trifles.done_record),before);config.save.assert_not_called()
    def test_escape_changed_page_unwinds_old_stage(self):
        self.escape_case('room', raises=True)
    def test_escape_same_page_can_continue(self):
        self.escape_case('result', raises=False)
    def escape_case(self, key, raises):
        from tasks.Component.escape_recovery import handle_escape_recovery
        class FakeUi: pass
        ui=FakeUi();ui.device=Mock();ui.device._escape_generation=1
        ui.device._recovery_handling=False;ui.device._failed_original_page='result'
        ui.device._task_flow_guard=SimpleNamespace(command='ExperienceYoukai')
        ui.device._error_learning=None;ui.device.performance=None
        ui.interval_timer=Mock();ui.animates=Mock()
        ui.navigator=SimpleNamespace(current_page=None,last_enter_success_page_key=None,edge_penalties={})
        ui._detect_current_page=Mock(return_value=SimpleNamespace(key=key))
        with patch('tasks.GameUi.game_ui.GameUi',FakeUi), patch('tasks.Component.escape_recovery.game_exit_cancel_point',return_value=None), patch('tasks.Component.escape_recovery.battle_exit_confirm_point',return_value=None):
            if raises:
                with self.assertRaises(TaskRecoveryFailed): handle_escape_recovery(ui)
            else: handle_escape_recovery(ui)
        self.assertFalse(ui.device._recovery_handling)
    def test_same_page_two_frames_keeps_original(self):
        self.pending()
        with patch.object(recovery,'page_key',side_effect=['result','result']):
            self.assertEqual(recovery.prepare(None,self.device,'tomoe','ExperienceYoukai'),'original')
        self.ui.goto_page.assert_not_called();self.ui._ui_verify.assert_not_called()
    def test_changed_page_returns_home(self):
        self.pending()
        with patch.object(recovery,'page_key',return_value='room'):
            self.assertEqual(recovery.prepare(None,self.device,'tomoe','ExperienceYoukai'),'main')
        self.ui.goto_page.assert_called_once();self.ui._ui_verify.assert_called_once()
    def test_second_frame_changes_returns_home(self):
        self.pending()
        with patch.object(recovery,'page_key',side_effect=['result','room']):
            self.assertEqual(recovery.prepare(None,self.device,'tomoe','ExperienceYoukai'),'main')
        self.ui.goto_page.assert_called_once()
    def test_unknown_original_returns_home(self):
        self.pending(None)
        self.assertEqual(recovery.prepare(None,self.device,'tomoe','ExperienceYoukai'),'main')
        self.ui.goto_page.assert_called_once()
    def test_home_failure_keeps_checkpoint_and_raises(self):
        self.pending();self.ui.goto_page.return_value=False
        with patch.object(recovery,'page_key',return_value='room'),self.assertRaises(TaskRecoveryFailed):
            recovery.prepare(None,self.device,'tomoe','ExperienceYoukai')
        self.assertTrue(self.path.exists());self.ui._ui_verify.assert_not_called()
    def test_home_verification_failure_keeps_checkpoint(self):
        self.pending();self.ui._ui_verify.side_effect=TaskRecoveryFailed('not main')
        with patch.object(recovery,'page_key',return_value='room'),self.assertRaises(TaskRecoveryFailed):
            recovery.prepare(None,self.device,'tomoe','ExperienceYoukai')
        self.assertTrue(self.path.exists())
    def test_failed_home_cannot_replace_original(self):
        recovery.remember(None,self.device,'tomoe','ExperienceYoukai')
        self.device._failed_original_page='room'
        recovery.remember(None,self.device,'tomoe','ExperienceYoukai')
        self.assertEqual(json.loads(self.path.read_text())['page'],'result')
    def test_ordinary_task_has_no_navigation(self):
        self.assertEqual(recovery.prepare(None,self.device,'tomoe','Orochi'),'normal')
        self.ui.goto_page.assert_not_called()
    def test_completion_clears_only_current_task(self):
        self.pending();recovery.clear('tomoe','ExperienceYoukai');self.assertFalse(self.path.exists())
    def test_ambiguous_page_is_not_original(self):
        recovery.GameUi._prepare_page_rule_image_cache=Mock()
        self.ui.navigator.all_pages.return_value=[SimpleNamespace(key='main'),SimpleNamespace(key='result')]
        self.ui.match_page_once.return_value=True
        self.assertIsNone(recovery.page_key(None,self.device))

if __name__=='__main__':unittest.main()

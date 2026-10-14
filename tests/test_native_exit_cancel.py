"""Native exit recovery tests: real production methods, offline hardware boundary."""
from pathlib import Path
from types import SimpleNamespace as NS
import inspect
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from module.device import native_exit_cancel as native
from module.device import escape_popup as popup
from module.device.device import Device
from module.device.connection import Connection
from module.exception import TaskRecoveryFailed
from tasks.base_task import BaseTask

class Clock:
    def __init__(self):self.now=0
    def sleep(self,seconds):self.now+=seconds

FRAME=np.zeros((720,1280,3),np.uint8)
FRAME[239:481,312:968]=255
for name,x,y in [('android_exit_title.png',405,271),('android_exit_question.png',347,326),
                 ('android_exit_cancel.png',783,430),('android_exit_confirm.png',879,430)]:
    path=Path(popup.__file__).parent/'recovery_assets'/name
    image=cv2.cvtColor(cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),1),cv2.COLOR_BGR2RGB)
    h,w=image.shape[:2]
    FRAME[y:y+h,x:x+w]=image

class NativeDevice:
    def __init__(self,clock,finish=1):
        self.clock=clock;self.finish=finish;self.inputs=[];self.image=FRAME.copy();self.image_frame_id=1
        self._escape_generation=0;self._accepted_invitation_generation=0
        self.performance=NS(timeout=lambda value:value);self.stuck_record_clear=Mock()
        self._task_flow_guard=NS(check=Mock());self.hook=lambda:None;self.error=None;self.stale=False
    def sleep(self,value):self.clock.sleep(value)
    def screenshot(self):
        if not self.stale:self.image_frame_id+=1
        if len(self.inputs)>=self.finish:self.image=np.zeros_like(FRAME)
        self.hook()
    def adb_shell(self,cmd,*,timeout):
        self.inputs.append((cmd,timeout))
        if isinstance(self.error,Exception):raise self.error
        return self.error or ''

class NativeTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.clock.monotonic=lambda:self.clock.now
        self.p=patch.object(native,'monotonic',self.clock.monotonic);self.p.start()
    def tearDown(self):self.p.stop()
    def d(self,finish=1):return NativeDevice(self.clock,finish)
    def fail(self,d,**kwargs):
        with self.assertRaises(TaskRecoveryFailed):native.cancel_game_exit_dialog(d,**kwargs)
        self.assertFalse(getattr(d,'_native_exit_dialog_handling',False))
    def test_actual_sample_cancel(self):self.assertEqual(popup.game_exit_cancel_point(FRAME),(804,441))
    def test_titleless_white_modal_rejected(self):
        f=FRAME.copy();f[260:370,340:560]=255
        self.assertIsNone(popup.game_exit_cancel_point(f))
    def test_cancel_color_block_rejected(self):
        f=FRAME.copy();f[424:457,778:831]=(0,130,130)
        self.assertIsNone(popup.game_exit_cancel_point(f))
    def test_identity_visible_but_cancel_unavailable_blocks(self):
        d=self.d();d.image[424:457,778:831]=(0,130,130)
        self.assertTrue(popup.game_exit_dialog_present(d.image));self.fail(d);self.assertFalse(d.inputs)
    def test_actual_base_task_calls_verified_native_helper(self):
        d=self.d();task=BaseTask.__new__(BaseTask);task.device=d
        with patch('tasks.base_task.handle_escape_recovery') as recovery, patch('tasks.base_task.claim_gift_popup') as gift:
            task.screenshot()
        self.assertEqual(len(d.inputs),1);recovery.assert_called_once_with(task);gift.assert_called_once_with(task)
    def test_actual_base_task_blocks_followup_on_missing_cancel(self):
        d=self.d();d.finish=99;d.image[424:457,778:831]=(0,130,130)
        task=BaseTask.__new__(BaseTask);task.device=d
        with patch('tasks.base_task.handle_escape_recovery') as recovery, patch('tasks.base_task.claim_gift_popup') as gift:
            with self.assertRaises(TaskRecoveryFailed):task.screenshot()
        recovery.assert_not_called();gift.assert_not_called();self.assertFalse(d.inputs)
    def switch_subject(self,finish):
        from module.script import failed_task_recovery as recovery
        d=self.d(finish);d.reset_task_recovery=Mock();d.recover_by_escape=Mock()
        ui=Mock();ui.navigator.resolve_page.return_value=NS(key='main');ui.match_page_once.return_value=False
        ui.goto_page.return_value=True;ui.device=d
        return recovery,d,ui
    def test_actual_failed_switch_cancel_before_navigation(self):
        recovery,d,ui=self.switch_subject(1)
        def goto(*args,**kwargs):
            self.assertEqual(len(d.inputs),1);self.assertFalse(popup.game_exit_white_panel(d.image));return True
        ui.goto_page.side_effect=goto
        with patch.object(recovery,'GameUi',return_value=ui), patch.object(recovery,'write_switch') as save, patch('tasks.GameUi.recovery_exit.click_exit',return_value=False):
            self.assertTrue(recovery.prepare_switch(None,d,'offline'))
        d.recover_by_escape.assert_called_once();ui.goto_page.assert_called_once();ui._ui_verify.assert_called_once()
        save.assert_called_once_with('offline',{'task':'','home_verified':True})
    def test_actual_failed_switch_cancel_failure_no_home_receipt(self):
        recovery,d,ui=self.switch_subject(99)
        with patch.object(recovery,'GameUi',return_value=ui), patch.object(recovery,'write_switch') as save, patch('tasks.GameUi.recovery_exit.click_exit',return_value=False):
            with self.assertRaises(TaskRecoveryFailed):recovery.prepare_switch(None,d,'offline')
        self.assertEqual(len(d.inputs),2);ui.goto_page.assert_not_called();ui._ui_verify.assert_not_called();save.assert_not_called()
    def test_swapped_buttons_rejected(self):
        f=FRAME.copy();a=f[430:452,783:826].copy();b=f[430:452,879:922].copy()
        f[430:452,783:826]=b;f[430:452,879:922]=a
        self.assertIsNone(popup.game_exit_cancel_point(f))
    def test_first_input_success_two_fresh_frames(self):
        d=self.d();self.assertTrue(native.cancel_game_exit_dialog(d));self.assertEqual(len(d.inputs),1)
        self.assertEqual(d.inputs[0][0],['input','tap','804','441']);self.assertEqual(d.image_frame_id,5)
        d.stuck_record_clear.assert_called_once();self.assertIsNone(d._native_exit_cancel_receipt)
    def test_exact_same_modal_retry_success(self):
        d=self.d(2);self.assertTrue(native.cancel_game_exit_dialog(d));self.assertEqual(len(d.inputs),2)
    def test_persistent_exact_modal_two_and_no_refill(self):
        d=self.d(99);self.fail(d);self.assertEqual(len(d.inputs),2);self.fail(d);self.assertEqual(len(d.inputs),2)
        d.stuck_record_clear.assert_not_called()
    def test_stale_frame_before_input(self):
        d=self.d();d.stale=True;self.fail(d);self.assertFalse(d.inputs)
    def test_generation_change_before_input(self):
        d=self.d();d.hook=lambda:setattr(d,'_escape_generation',1);self.fail(d);self.assertFalse(d.inputs)
    def test_invitation_change_before_input(self):
        d=self.d();d.hook=lambda:setattr(d,'_accepted_invitation_generation',1);self.fail(d);self.assertFalse(d.inputs)
    def test_context_change_after_input_no_retry(self):
        d=self.d(99)
        def hook():
            if d.inputs:d.image[260:370,340:560]=255
        d.hook=hook;self.fail(d);self.assertEqual(len(d.inputs),1)
    def test_unknown_white_modal_no_false_success(self):
        d=self.d(99)
        def hook():
            if d.inputs:d.image[424:457,778:927]=255
        d.hook=hook;self.fail(d);self.assertEqual(len(d.inputs),1);d.stuck_record_clear.assert_not_called()
    def test_adb_error_no_replay(self):
        for error in ('error: offline',RuntimeError('offline')):
            d=self.d();d.error=error;self.fail(d);self.fail(d);self.assertEqual(len(d.inputs),1)
    def test_flow_budget_failure_no_input(self):
        d=self.d();d._task_flow_guard.check.side_effect=TaskRecoveryFailed('budget');self.fail(d);self.assertFalse(d.inputs)
    def test_parent_deadline_insufficient_verify_budget_no_input(self):
        d=self.d();self.fail(d,deadline=1);self.assertFalse(d.inputs)
    def test_capture_consumes_parent_deadline_no_input(self):
        d=self.d();d.hook=lambda:self.clock.sleep(5);self.fail(d,deadline=4);self.assertFalse(d.inputs)
    def test_absent_modal_no_input(self):
        d=self.d();d.image[:]=0;self.assertFalse(native.cancel_game_exit_dialog(d));self.assertFalse(d.inputs)
    def test_frame_cap_preserved_across_reentry(self):
        d=self.d(99);d.sleep=lambda _:None;self.fail(d);self.assertLessEqual(d.image_frame_id,65);self.fail(d);self.assertLessEqual(d.image_frame_id,65)
    def test_nested_scope_rejected(self):
        d=self.d();d._native_exit_dialog_handling=True
        with self.assertRaises(TaskRecoveryFailed):native.cancel_game_exit_dialog(d)
        self.assertTrue(d._native_exit_dialog_handling);self.assertFalse(d.inputs)
    def test_real_device_scopes_preserve_timers_and_block_nested_back(self):
        d=Device.__new__(Device);d.app_is_running=Mock();d._check_login_deadline=Mock();d._check_click_recovery=Mock()
        d._click_recovery_at=42
        for scope in ('_recovery_handling','_native_exit_dialog_handling'):
            setattr(d,scope,True);self.assertFalse(d.stuck_record_check())
            with self.assertRaises(TaskRecoveryFailed):d.recover_by_escape('test')
            setattr(d,scope,False)
        d.app_is_running.assert_not_called();d._check_login_deadline.assert_not_called();self.assertEqual(d._click_recovery_at,42)
    def test_actual_connection_timeout_signature(self):
        self.assertIn('timeout',inspect.signature(Connection.adb_shell).parameters)


if __name__=='__main__':unittest.main()

import unittest
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock,patch
from module.exception import TaskEnd,TaskRecoveryFailed
from tasks.Hunt.script_task import ScriptTask
class HuntAlreadyCompletedTests(unittest.TestCase):
 def subject(self,marker):
  t=ScriptTask.__new__(ScriptTask)
  t.screenshot=Mock(); t.appear=lambda r:r is marker
  t.appear_then_click=Mock(return_value=False)
  t.is_in_room=Mock(return_value=False)
  t.ui_click_until_disappear=Mock()
  t._ui_verify=Mock(return_value=True)
  t.set_next_run=Mock()
  t.tomorrow_kirin_day=False
  t.con_time=SimpleNamespace(kirin_time=SimpleNamespace(hour=19,minute=0,second=0),netherworld_time=SimpleNamespace(hour=19,minute=0,second=0))
  t.config=SimpleNamespace(_learning_task_outcomes={})
  return t
 def test_netheworld_completed_is_success_without_background_input(self):
  t=self.subject(ScriptTask.I_NW_DONE)
  with self.assertRaises(TaskEnd):t.netherworld()
  self.assertTrue(t.config._learning_task_outcomes['hunt'])
  t.appear_then_click.assert_not_called();t.ui_click_until_disappear.assert_not_called()
  self.assertFalse(t.set_next_run.call_args.kwargs['server'])
  self.assertEqual(t.set_next_run.call_args.kwargs['target'].hour,19)
 def test_kirin_completed_is_success_without_background_input(self):
  t=self.subject(ScriptTask.I_KIRIN_END)
  with self.assertRaises(TaskEnd):t.kirin()
  self.assertTrue(t.config._learning_task_outcomes['hunt'])
  t.appear_then_click.assert_not_called();t.ui_click_until_disappear.assert_not_called()
 def test_unverified_completed_marker_still_fails(self):
  t=self.subject(ScriptTask.I_NW_DONE)
  t._ui_verify.side_effect=TaskRecoveryFailed('marker vanished')
  with self.assertRaises(TaskRecoveryFailed):t.netherworld()
  t.set_next_run.assert_not_called()
  self.assertNotIn('hunt',t.config._learning_task_outcomes)
if __name__=='__main__':unittest.main()

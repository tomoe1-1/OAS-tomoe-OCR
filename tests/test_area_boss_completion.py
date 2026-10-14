"""A failed regional boss challenge cannot become a successful task end."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.AreaBoss.script_task import ScriptTask

def subject(results):
    obj=ScriptTask.__new__(ScriptTask)
    obj.check_can_run=Mock();obj.goto_page=Mock();obj.open_filter=Mock();obj.switch_to_famous=Mock()
    obj.set_next_run=Mock();obj.boss_fight=Mock(side_effect=results)
    obj._battle_is_available=Mock(return_value=True)
    obj.screenshot=Mock();obj.appear=Mock(return_value=True)
    obj.config=SimpleNamespace(area_boss=SimpleNamespace(
        general_battle=SimpleNamespace(lock_team_enable=True),
        boss=SimpleNamespace(boss_number=3,boss_reward=False,use_collect=False),
        switch_soul=SimpleNamespace(enable=False,enable_switch_by_name=False)))
    return obj

class AreaBossCompletionTests(unittest.TestCase):
    def test_first_failure_blocks_other_challenges_and_success(self):
        obj=subject([False,True,True])
        with self.assertRaises(TaskRecoveryFailed):obj.run()
        self.assertEqual(obj.boss_fight.call_count,1);obj.set_next_run.assert_not_called()

    def test_partial_completion_is_not_success(self):
        obj=subject([True,False,True])
        with self.assertRaises(TaskRecoveryFailed):obj.run()
        self.assertEqual(obj.boss_fight.call_count,2);obj.set_next_run.assert_not_called()

    def test_three_verified_challenges_finish_task(self):
        obj=subject([True,True,True])
        with self.assertRaises(TaskEnd):obj.run()
        self.assertEqual(obj.boss_fight.call_count,3)
        obj.set_next_run.assert_called_once_with(task='AreaBoss',success=True,finish=False)

if __name__=='__main__':unittest.main()

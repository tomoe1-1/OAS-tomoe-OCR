import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from module.exception import TaskEnd
from tasks.AreaBoss.script_task import ScriptTask


class AreaBossDisabledTargetTests(unittest.TestCase):
    def test_disabled_challenge_is_not_clicked(self):
        task = ScriptTask.__new__(ScriptTask)
        task._battle_is_available = Mock(return_value=False)
        task.click = Mock()

        self.assertFalse(task.open_boss_detail(task.I_BATTLE_3))
        task.click.assert_not_called()

    def test_configured_three_challenges_skip_one_grey_target(self):
        task = ScriptTask.__new__(ScriptTask)
        task.config = SimpleNamespace(area_boss=SimpleNamespace(
            boss=SimpleNamespace(boss_reward=False, use_collect=False, boss_number=3),
            general_battle=SimpleNamespace(lock_team_enable=False),
            switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False),
        ))
        task.check_can_run = Mock()
        task.goto_page = Mock()
        task.open_filter = Mock()
        task.screenshot = Mock()
        task.appear = Mock(side_effect=[True, False, False])
        task.switch_to_famous = Mock()
        def availability(battle):
            self.assertEqual(task.open_filter.call_count, (1, 2, 3)[task._battle_is_available.call_count - 1])
            return battle is not task.I_BATTLE_3
        task._battle_is_available = Mock(side_effect=availability)
        task.boss_fight = Mock(return_value=True)
        task.set_next_run = Mock()

        with self.assertRaises(TaskEnd):
            task.run()

        self.assertEqual(task.boss_fight.call_count, 2)
        self.assertEqual(task.open_filter.call_count, 3)
        task.set_next_run.assert_called_once_with(task='AreaBoss', success=True, finish=False)


if __name__ == '__main__':
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tasks.RealmRaid.script_task import ScriptTask


class RaidRewardOverlayTests(unittest.TestCase):
    def subject(self):
        task = ScriptTask.__new__(ScriptTask)
        task.screenshot = Mock()
        task.device = SimpleNamespace(image=None)
        task.click = Mock()
        task.appear = Mock(return_value=True)
        task._ui_verify = Mock(return_value=True)
        return task

    def test_bottom_continue_prompt_identifies_overlay(self):
        task = self.subject()
        for text, expected in [('点击屏幕继续', True), ('点击 屏幕 继续', True), ('', False), ('0/30', False)]:
            with patch('tasks.RealmRaid.script_task.RuleOcr', return_value=SimpleNamespace(ocr=Mock(return_value=text))):
                self.assertEqual(task.raid_reward_overlay_visible(), expected)

    def test_visible_overlay_is_closed_once_and_verified_before_underlying_controls(self):
        task = self.subject()
        task.raid_reward_overlay_visible = Mock(return_value=True)
        events = []
        task.click.side_effect = lambda *a, **k: events.append(('click', a[0].name))
        task._ui_verify.side_effect = lambda *a, **k: events.append(('verify', k['timeout'])) or True
        self.assertTrue(task.close_raid_reward_overlay())
        self.assertEqual(events, [('verify', 5), ('click', 'raid_reward_continue'), ('verify', 15)])
        task.click.assert_called_once()

    def test_absent_overlay_does_not_click(self):
        task = self.subject()
        task.raid_reward_overlay_visible = Mock(return_value=False)
        self.assertFalse(task.close_raid_reward_overlay())
        task.click.assert_not_called()

    def test_reward_claim_first_clears_overlay_then_claims_once_and_clears_new_overlay(self):
        task = self.subject()
        task.wait_until_appear = Mock()
        task.close_raid_reward_overlay = Mock(side_effect=[True, True])
        task.O_TEXT = SimpleNamespace(ocr=Mock(return_value='0/30'))
        task.raid_reward_overlay_visible = Mock(return_value=True)
        self.assertTrue(task.reward_detect_click())
        self.assertEqual(task.close_raid_reward_overlay.call_count, 2)
        task.click.assert_called_once_with(task.I_SOUL_RAID, interval=0)


if __name__ == '__main__':
    unittest.main()

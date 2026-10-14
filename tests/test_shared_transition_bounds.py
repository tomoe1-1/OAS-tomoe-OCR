"""Replay stuck recovery and battle UI transitions without device input."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import TaskRecoveryFailed
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBuff.general_buff import GeneralBuff
from tasks.Component.GeneralInvite.assets import GeneralInviteAssets
from tasks.GameUi.navigator import GameUi
from tasks.GameUi.page import page_battle_result, page_reward


ROOT = Path(__file__).resolve().parents[1]


def recovery_subject(multiplier=1):
    state = dict(now=0, screenshots=0)
    class FakeGameUi:
        pass
    task = FakeGameUi()
    task.device = SimpleNamespace(
        _escape_generation=1, image=object(), _recovery_handling=False,
        performance=SimpleNamespace(timeout=lambda value: value * multiplier),
        sleep=Mock(), click=Mock(return_value=True), press_escape=Mock(),
        stuck_record_clear=Mock(), click_record_clear=Mock())
    def screenshot():
        state['screenshots'] += 1
        state['now'] += 15
    task.device.screenshot = Mock(side_effect=screenshot)
    task.interval_timer, task.animates = {'stale': 1}, {'stale': 1}
    task.navigator = SimpleNamespace(current_page=None, last_enter_success_page_key=None,
                                     edge_penalties={})
    task._detect_current_page = Mock(return_value=None)
    game_ui = ModuleType('tasks.GameUi.game_ui')
    game_ui.GameUi = FakeGameUi
    with patch.dict(sys.modules, {'tasks.GameUi.game_ui': game_ui}):
        spec = importlib.util.spec_from_file_location('bounded_recovery_subject',
                                                     Path(__import__('tasks.Component.escape_recovery',fromlist=['x']).__file__))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.monotonic = lambda: state['now']
    module.game_exit_dialog_present = Mock(return_value=False)
    module.game_exit_cancel_point = Mock(return_value=None)
    module.battle_exit_confirm_point = Mock(return_value=None)
    return module, task, state, game_ui


class RecoveryDeadlineTests(unittest.TestCase):
    def invoke(self, module, task, game_ui):
        with patch.dict(sys.modules, {'tasks.GameUi.game_ui': game_ui}):
            module.handle_escape_recovery(task)

    def test_unknown_page_expires_and_releases_guard_without_another_esc(self):
        module, task, state, game_ui = recovery_subject()
        with self.assertRaises(TaskRecoveryFailed):
            self.invoke(module, task, game_ui)
        self.assertEqual(state['screenshots'], 4)
        self.assertFalse(task.device._recovery_handling)
        self.assertIsNone(task.navigator.current_page)
        task.device.press_escape.assert_not_called()
        task.device.click.assert_not_called()
        task.device.stuck_record_clear.assert_not_called()

    def test_slow_device_recovery_budget_is_scaled(self):
        module, task, state, game_ui = recovery_subject(multiplier=3)
        with self.assertRaises(TaskRecoveryFailed):
            self.invoke(module, task, game_ui)
        self.assertEqual(state['screenshots'], 12)

    def test_native_cancel_delegates_with_original_deadline(self):
        module, task, state, game_ui = recovery_subject()
        module.game_exit_dialog_present.return_value = True
        module.cancel_game_exit_dialog = Mock(side_effect=TaskRecoveryFailed('cancel not verified'))
        with self.assertRaises(TaskRecoveryFailed):
            self.invoke(module, task, game_ui)
        module.cancel_game_exit_dialog.assert_called_once_with(task.device, deadline=60)
        task.device.click.assert_not_called()
        task._detect_current_page.assert_not_called()
        self.assertEqual(state['screenshots'], 1)

    def test_persistent_battle_dialog_sends_only_one_confirmation(self):
        module, task, _, game_ui = recovery_subject()
        module.battle_exit_confirm_point.return_value = (190, 126)
        with self.assertRaises(TaskRecoveryFailed):
            self.invoke(module, task, game_ui)
        task.device.click.assert_called_once_with(190, 126, control_name='ESC_EXIT_BATTLE_CONFIRM')
        task.device.press_escape.assert_not_called()

    def test_blocked_cancel_is_not_replayed(self):
        module, task, _, game_ui = recovery_subject()
        module.game_exit_dialog_present.return_value = True
        module.cancel_game_exit_dialog = Mock(side_effect=TaskRecoveryFailed('cancel not verified'))
        task.device.click.return_value = False
        with self.assertRaises(TaskRecoveryFailed):
            self.invoke(module, task, game_ui)
        module.cancel_game_exit_dialog.assert_called_once()
        task.device.click.assert_not_called()
        self.assertFalse(task.device._recovery_handling)

    def test_recognized_page_restores_navigation_normally(self):
        module, task, _, game_ui = recovery_subject()
        page = SimpleNamespace(key='main')
        task._detect_current_page.return_value = page
        self.invoke(module, task, game_ui)
        self.assertIs(task.navigator.current_page, page)
        task.device.stuck_record_clear.assert_called_once()
        task.device.click_record_clear.assert_called_once()


class BattleTransitionTests(unittest.TestCase):
    def setUp(self):
        # Transition contract uses the shared confirmation helper now. Its
        # input/retry semantics have their own actual-Control regression suite.
        def confirm(task, match, controls):
            task.appear = Mock(side_effect=lambda target: target is task.I_EXIT_ENSURE)
            self.assertIsNotNone(match())
            return task.appear_then_click(controls[0])
        self.confirm_patch = patch('tasks.Component.GeneralBattle.general_battle.click_confirm_once', side_effect=confirm)
        self.confirm_patch.start()

    def tearDown(self):
        self.confirm_patch.stop()

    def subject(self):
        obj = GeneralBattle.__new__(GeneralBattle)
        obj.device = SimpleNamespace(performance=SimpleNamespace(timeout=lambda value: value))
        obj.screenshot = Mock()
        obj._ui_verify = Mock(return_value=True)
        obj.appear_then_click = Mock(return_value=True)
        obj.appear = Mock(side_effect=lambda target: target is obj.I_EXIT)
        return obj

    def test_exit_click_and_confirmation_each_send_once(self):
        obj = self.subject()
        with patch.object(GameUi, 'get_current_page', return_value=None):
            self.assertTrue(obj.exit_battle())
        self.assertEqual([call.args[0] for call in obj.appear_then_click.call_args_list],
                         [obj.I_EXIT, obj.I_EXIT_ENSURE])
        self.assertEqual(obj._ui_verify.call_count, 2)
        obj.screenshot.assert_called_once()

    def test_skip_first_reuses_the_current_frame(self):
        obj = self.subject()
        with patch.object(GameUi, 'get_current_page', return_value=None):
            obj.exit_battle(skip_first=True)
        obj.screenshot.assert_not_called()

    def test_exit_confirmation_can_resume_without_another_exit_click(self):
        obj = self.subject()
        obj.appear.side_effect = lambda target: target is obj.I_EXIT_ENSURE
        with patch.object(GameUi, 'get_current_page', return_value=None):
            obj.exit_battle()
        obj.appear_then_click.assert_called_once_with(obj.I_EXIT_ENSURE)

    def test_stuck_transition_raises_without_replaying_exit(self):
        obj = self.subject()
        obj._ui_verify.side_effect = TaskRecoveryFailed('no confirmation page')
        with patch.object(GameUi, 'get_current_page', return_value=None), \
                self.assertRaises(TaskRecoveryFailed):
            obj.exit_battle()
        obj.appear_then_click.assert_called_once_with(obj.I_EXIT)

    def test_natural_settlement_does_not_send_exit_confirmation(self):
        for destination in (page_battle_result, page_reward):
            with self.subTest(page=destination.key):
                obj = self.subject()
                with patch.object(GameUi, 'get_current_page', return_value=destination):
                    self.assertTrue(obj.exit_battle())
                obj.appear_then_click.assert_called_once_with(obj.I_EXIT)

    def test_unrecognized_battle_does_not_claim_an_exit(self):
        obj = self.subject()
        obj.appear.side_effect = None
        obj.appear.return_value = False
        self.assertFalse(obj.exit_battle())
        obj.appear_then_click.assert_not_called()
        obj._ui_verify.assert_not_called()

    def test_lock_switch_delegates_to_the_verified_single_click_helper(self):
        obj = self.subject()
        obj.ui_click = Mock()
        locked, unlocked = object(), object()
        obj.check_lock(True, locked, unlocked)
        obj.ui_click.assert_called_once_with(unlocked, locked, interval=1)

    def test_prepare_timeout_does_not_click_a_green_target(self):
        obj = self.subject()
        obj.wait_until_disappear = Mock(return_value=False)
        obj.device.click = Mock()
        with self.assertRaises(TaskRecoveryFailed):
            obj.green_mark_choose()
        obj.device.click.assert_not_called()
        obj.appear_then_click.assert_not_called()
        obj.wait_until_disappear.assert_called_once_with(obj.I_PREPARE_HIGHLIGHT, timeout=30)


class BuffTransitionTests(unittest.TestCase):
    def subject(self):
        obj = GeneralBuff.__new__(GeneralBuff)
        obj.device = SimpleNamespace(performance=SimpleNamespace(timeout=lambda value: value))
        obj.screenshot, obj.ui_click, obj._ui_verify = Mock(), Mock(), Mock(return_value=True)
        return obj

    def test_unreachable_buff_top_stops_after_three_swipes(self):
        obj = self.subject()
        obj.appear = Mock(side_effect=lambda target: target is obj.I_CLOUD)
        obj.swipe = Mock(return_value=True)
        with self.assertRaises(TaskRecoveryFailed):
            obj.open_buff()
        self.assertEqual(obj.swipe.call_count, 3)
        obj._ui_verify.assert_not_called()

    def test_buff_close_delegates_to_the_bounded_helper(self):
        obj = self.subject()
        obj.ui_click_until_smt_disappear = Mock()
        obj.close_buff()
        obj.ui_click_until_smt_disappear.assert_called_once_with(obj.I_BUFF_1, obj.I_CLOUD, interval=2)

    def test_reappearing_rejection_control_is_not_clicked_again(self):
        obj = self.subject()
        obj.appear = Mock(side_effect=lambda target: target is GeneralInviteAssets.I_I_REJECT_3)
        obj.appear_then_click = Mock(return_value=True)
        with self.assertRaises(TaskRecoveryFailed):
            obj.reject_invite()
        obj.appear_then_click.assert_called_once_with(GeneralInviteAssets.I_I_REJECT_3)


if __name__ == '__main__':
    unittest.main()

"""Navigation exits must recognize the supplied arrow and finish confirmation."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import TaskRecoveryFailed
from tasks.GameUi.action import conditional_action, sequence
from tasks.GameUi.navigator import GameUi
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.Exploration.assets import ExplorationAssets


ROOT = Path(__file__).resolve().parents[1]


class BoundedTimer:
    """Bound polling deterministically, without sleeping or controlling the game."""
    def __init__(self, limit):
        self.polls = 0

    def start(self):
        return self

    def reached(self):
        self.polls += 1
        return self.polls > 12


class Clock:
    """A monotonic stub that always advances, so verification loops terminate."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 0.2
        return self.now


def task():
    obj = GameUi.__new__(GameUi)
    # The exit handler now spends its budget proving the modal disappeared, so
    # the device stub must carry the timing and progress hooks that proof uses.
    obj.device = SimpleNamespace(_escape_generation=0,
                                 performance=SimpleNamespace(timeout=lambda seconds: seconds),
                                 confirm_control_progress=Mock())
    obj.navigator = SimpleNamespace(current_page=None, local_unknown_closers=[],
                                    unknown_close_history=[], add_penalty=Mock())
    obj.screenshot = Mock()
    obj.appear = Mock(return_value=False)
    obj.click = Mock(return_value=True)
    obj.maybe_screenshot = Mock()
    obj.get_current_page = Mock(return_value=object())
    return obj


class NavigationExitTests(unittest.TestCase):
    def test_supplied_arrow_matches_existing_template_inside_top_left_only(self):
        sample = cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/navigation_back_arrow.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR)
        sample = cv2.cvtColor(sample, cv2.COLOR_BGR2RGB)
        h, w = sample.shape[:2]
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[:h, :w] = sample
        rule = deepcopy(GlobalGameAssets.I_UI_BACK_YELLOW)
        self.assertTrue(rule.template_match(frame))
        self.assertLess(rule.roi_front[0] + rule.roi_front[2], 100)
        self.assertLess(rule.roi_front[1] + rule.roi_front[3], 100)
        frame[:] = 0
        frame[200:200+h, 500:500+w] = sample
        self.assertFalse(rule.template_match(frame))

    def test_exit_actions_include_composed_back_but_exclude_entrance(self):
        back = GlobalGameAssets.I_UI_BACK_YELLOW
        self.assertTrue(GameUi._is_navigation_back_action(back))
        self.assertTrue(GameUi._is_navigation_back_action(sequence(back)))
        self.assertTrue(GameUi._is_navigation_back_action([back]))
        self.assertTrue(GameUi._is_navigation_back_action(conditional_action(back, back)))
        self.assertFalse(GameUi._is_navigation_back_action(GameUiAssets.I_MAIN_GOTO_ACTIVITY))
        self.assertFalse(GameUi._is_navigation_back_action(GlobalGameAssets.I_UI_CONFIRM))
        self.assertIn(GlobalGameAssets.I_UI_BACK_CIRCLE, GameUi.DEFAULT_UNKNOWN_CLOSERS)

    def test_each_confirmation_requires_two_frames_then_clicks_recognized_button(self):
        for button in (GlobalGameAssets.I_UI_CONFIRM_SAMLL,
                       GlobalGameAssets.I_UI_CONFIRM, ExplorationAssets.I_E_EXIT_CONFIRM):
            with self.subTest(button=button.name):
                obj = task()
                state = {'input': False}

                def appear(candidate, state=state):
                    # The modal stays on screen until its confirmation input lands.
                    return candidate is button and not state['input']

                def click(*args, **kwargs):
                    state['input'] = True
                    return True

                obj.appear.side_effect = appear
                obj.click.side_effect = click
                with patch('tasks.base_task.monotonic', Clock()):
                    self.assertTrue(obj._handle_navigation_exit_confirmation())
                obj.click.assert_called_once_with(button, interval=1.5)
                # One frame proves the modal, two more prove it is gone.
                self.assertEqual(obj.screenshot.call_count, 3)
                obj.device.confirm_control_progress.assert_called_once_with([button])

    def test_confirmation_input_without_dismissal_is_not_accepted(self):
        obj = task()
        obj.appear.return_value = True
        with patch('tasks.base_task.monotonic', Clock()):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'UI next state not reached'):
                obj._handle_navigation_exit_confirmation()
        obj.click.assert_called_once()
        obj.device.confirm_control_progress.assert_not_called()

    def test_flickering_button_is_not_clicked(self):
        obj = task()
        obj.appear.side_effect = [True, False]
        self.assertTrue(obj._handle_navigation_exit_confirmation())
        obj.click.assert_not_called()

    def test_recovery_between_frames_aborts_confirmation_click(self):
        obj = task()
        obj.appear.return_value = True
        obj.screenshot.side_effect = lambda: setattr(obj.device, '_escape_generation', 1)
        self.assertTrue(obj._handle_navigation_exit_confirmation())
        obj.click.assert_not_called()

    def test_no_modal_means_no_click(self):
        obj = task()
        self.assertFalse(obj._handle_navigation_exit_confirmation())
        obj.click.assert_not_called()
        obj.screenshot.assert_not_called()

    def test_delayed_modal_is_confirmed_before_stable_destination(self):
        obj = task()
        states = iter(('waiting', 'modal', 'modal', 'destination', 'destination'))
        obj.frame = 'initial'
        obj.screenshot.side_effect = lambda: setattr(obj, 'frame', next(states, 'destination'))
        obj.appear.side_effect = lambda button: (
            obj.frame == 'modal' and button is GlobalGameAssets.I_UI_CONFIRM_SAMLL)
        obj.match_page_once = Mock(side_effect=lambda page: obj.frame == 'destination')
        destination = object()
        with patch('tasks.GameUi.navigator.Timer', BoundedTimer):
            self.assertTrue(obj._wait_for_destination(destination, handle_exit_confirmation=True))
        obj.click.assert_called_once_with(GlobalGameAssets.I_UI_CONFIRM_SAMLL, interval=1.5)
        self.assertIs(obj.navigator.current_page, destination)

    def test_blocked_confirmation_cannot_accept_underlying_destination(self):
        obj = task()
        obj.appear.side_effect = lambda button: button is GlobalGameAssets.I_UI_CONFIRM_SAMLL
        obj.click.return_value = False
        obj.confirm_page = Mock(return_value=True)
        with patch('tasks.GameUi.navigator.Timer', BoundedTimer):
            self.assertFalse(obj._wait_for_destination(object(), handle_exit_confirmation=True))
        obj.confirm_page.assert_not_called()
        self.assertIsNone(obj.navigator.current_page)

    def test_entrance_wait_does_not_auto_confirm(self):
        obj = task()
        obj.confirm_page = Mock(return_value=True)
        obj._handle_navigation_exit_confirmation = Mock(return_value=True)
        destination = object()
        with patch('tasks.GameUi.navigator.Timer', BoundedTimer):
            self.assertTrue(obj._wait_for_destination(destination))
        obj._handle_navigation_exit_confirmation.assert_not_called()
        obj.confirm_page.assert_called_once_with(destination, skip_first_screenshot=False)

    def test_exit_transition_handles_existing_modal_without_clicking_arrow_behind_it(self):
        obj = task()
        obj._handle_navigation_exit_confirmation = Mock(return_value=True)
        obj._execute_action = Mock()
        obj._wait_for_destination = Mock(return_value=True)
        obj._run_hooks = Mock()
        obj._mark_page_entered = Mock()
        source = SimpleNamespace(on_leave_success=[])
        destination = SimpleNamespace(on_enter_success=[])
        transition = SimpleNamespace(source=source, destination=destination,
            action=GlobalGameAssets.I_UI_BACK_YELLOW, on_leave_success=[], on_enter_success=[])
        with patch('tasks.GameUi.navigator.Timer', BoundedTimer):
            self.assertTrue(obj._execute_transition(transition))
        obj._execute_action.assert_not_called()
        obj._wait_for_destination.assert_called_once_with(destination, handle_exit_confirmation=True)

    def test_unknown_page_back_checks_confirmation_after_animation(self):
        obj = task()
        obj._execute_action = Mock(side_effect=lambda action, **kw:
                                   action is GlobalGameAssets.I_UI_BACK_YELLOW)
        obj._handle_navigation_exit_confirmation = Mock(return_value=True)
        with patch('tasks.GameUi.navigator.time.sleep'):
            self.assertTrue(obj.close_unknown_pages())
        obj._handle_navigation_exit_confirmation.assert_called_once()
        self.assertEqual(obj.navigator.unknown_close_history,
                         [GlobalGameAssets.I_UI_BACK_YELLOW.name])


    def unknown_task(self):
        obj = task()
        obj.get_current_page.return_value = None
        obj.device.image = np.zeros((720, 1280, 3), np.uint8)
        obj.device.recover_by_escape = Mock()
        obj._execute_action = Mock()
        obj._handle_navigation_exit_confirmation = Mock(return_value=False)
        return obj

    def test_unknown_page_uses_detected_exit_without_probing_closers(self):
        obj = self.unknown_task()
        with patch('tasks.GameUi.navigator.exit_point', return_value=(47, 40, 'back_arrow')), \
             patch('tasks.GameUi.navigator.click_exit', return_value=True) as click:
            self.assertTrue(obj.close_unknown_pages())
        click.assert_called_once_with(obj)
        obj._execute_action.assert_not_called()
        obj.device.recover_by_escape.assert_not_called()
        obj._handle_navigation_exit_confirmation.assert_called_once()
        self.assertIsNone(obj.navigator.current_page)

    def test_unknown_page_without_exit_sends_one_back_then_observes(self):
        obj = self.unknown_task()
        calls = Mock()
        calls.attach_mock(obj.device.recover_by_escape, 'back')
        calls.attach_mock(obj.screenshot, 'frame')
        calls.attach_mock(obj._handle_navigation_exit_confirmation, 'confirmation')
        with patch('tasks.GameUi.navigator.exit_point', return_value=None):
            self.assertTrue(obj.close_unknown_pages())
        self.assertEqual([c[0] for c in calls.mock_calls], ['back', 'frame', 'confirmation'])
        obj._execute_action.assert_not_called()

    def test_unchanged_matched_exit_does_not_chain_back_or_claim_progress(self):
        obj = self.unknown_task()
        with patch('tasks.GameUi.navigator.exit_point', return_value=(47, 40, 'back_arrow')), \
             patch('tasks.GameUi.navigator.click_exit', return_value=False):
            self.assertFalse(obj.close_unknown_pages())
        obj.device.recover_by_escape.assert_not_called()
        obj._handle_navigation_exit_confirmation.assert_not_called()
        self.assertEqual(obj.navigator.unknown_close_history, ['exit_unchanged'])

    def test_unknown_exit_cannot_repeat_beyond_navigation_recovery_budget(self):
        from module.exception import TaskRecoveryFailed
        from tasks.GameUi.navigator import _NavigationBudget
        obj = self.unknown_task()
        obj._navigation_budgets = (_NavigationBudget('main', 64, 180),)
        with patch('tasks.GameUi.navigator.exit_point', return_value=(47, 40, 'back_arrow')), \
             patch('tasks.GameUi.navigator.click_exit', return_value=False) as click:
            self.assertFalse(obj.close_unknown_pages())
            self.assertFalse(obj.close_unknown_pages())
            with self.assertRaisesRegex(TaskRecoveryFailed, 'recovery limit'):
                obj.close_unknown_pages()
        self.assertEqual(click.call_count, 2)

    def test_changed_page_guard_unwinds_instead_of_resuming_old_stage(self):
        from module.exception import TaskRecoveryFailed
        obj = self.unknown_task()
        obj.screenshot.side_effect = TaskRecoveryFailed('restart task from its entry')
        with patch('tasks.GameUi.navigator.exit_point', return_value=None):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'restart task'):
                obj.close_unknown_pages()
        obj.device.recover_by_escape.assert_called_once()
        obj._handle_navigation_exit_confirmation.assert_not_called()


if __name__ == '__main__':
    unittest.main()

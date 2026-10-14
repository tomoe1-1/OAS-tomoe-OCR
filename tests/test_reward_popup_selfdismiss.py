"""A reward popup that leaves on its own is a success, not a blocked input.

Drives the real `BaseTask.ui_get_reward` / `_dismiss_reward_once` entry points
against a virtual device. Only screenshots, elapsed time and control delivery
are simulated; `click_confirm_once` and its fence run for real.

Regression: Exploration claimed a paper-man reward, the game replaced the popup
with its own full-screen reward broadcast, and `ui_get_reward` raised
"Reward dismiss input was blocked" even though the reward had been granted.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from module.device import click_verification
from module.device.click_verification import after_click, before_click
from module.device.confirm_retry import click_confirm_once
from module.exception import TaskRecoveryFailed


class RuleClick:
    def __init__(self, name, coord=(640, 680)):
        self.name = name
        self._coord = coord

    def coord(self):
        return self._coord


class RuleImage(RuleClick):
    pass


class RuleGif(RuleClick):
    pass


class RuleOcr(RuleClick):
    pass


def scene_image(scene):
    return np.full((720, 1280, 3), (scene * 53) % 256, dtype=np.uint8)


class VirtualClock:
    """Keeps the fence's 8s deadlines and the 10s claim budget finite."""

    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        self.value += .2
        return self.value

    def sleep(self, seconds):
        self.value += seconds

    def time(self):
        return self.value


class RewardPopupGoneTest(unittest.TestCase):
    """`ui_get_reward` must survive the popup vanishing between two frames."""

    def build(self, *, popup_frames, blocked=()):
        """popup_frames: how many fresh captures still show the reward popup.

        The popup only exists after the source claim is clicked, and each fresh
        capture consumes one frame of its lifetime. `blocked` names the controls
        whose delivery is refused.
        """
        state = dict(frame=0, inputs=[], scene=0, observed=0,
                     claimable=True, claimed=False, popup=False,
                     blocked=set(blocked))
        device = SimpleNamespace(image=scene_image(0), image_frame_id=0,
                                 _escape_generation=0,
                                 performance=SimpleNamespace(timeout=lambda value: value))
        device.confirm_control_progress = mock.Mock()

        def observe():
            """One fresh capture ages the popup by a frame."""
            if state['popup']:
                if state['observed'] < popup_frames:
                    state['observed'] += 1
                else:
                    state['popup'] = False
                    state['scene'] += 1

        def frame():
            state['frame'] += 1
            observe()
            device.image = scene_image(state['scene'])
            device.image_frame_id = state['frame']

        def click(x, y, control_name=None, **kwargs):
            if control_name in state['blocked']:
                # A refused input is never delivered, so it is never recorded.
                return False
            before = before_click(device, x, y, control_name)
            state['inputs'].append((state['frame'], control_name))
            if control_name == 'I_BATTLE_REWARD':
                # The claim opens the popup, which changes the screen itself.
                state['claimed'] = True
                state['claimable'] = False
                state['popup'] = True
                state['observed'] = 0
            else:
                state['popup'] = False
            state['scene'] += 1
            after_click(device, x, y, control_name, before)
            return True

        device.screenshot = frame
        device.click = click

        task = SimpleNamespace(device=device)
        task.screenshot = frame

        reward = RuleImage('I_UI_REWARD')
        claim = RuleImage('I_BATTLE_REWARD')
        task.I_UI_REWARD = reward
        task.I_BATTLE_REWARD = claim
        task.C_UI_REWARD = RuleClick('ui_reward')

        def appear(target, interval=0, threshold=None, **kwargs):
            if target is reward:
                return state['popup']
            if target is claim:
                return state['claimable']
            return False

        task.appear = appear
        task._ui_action_once = lambda control, interval=1: (
            click(*control.coord(), control_name=control.name) is not False)

        def verify(predicate, verified_controls, timeout=12, fail=True):
            deadline = state['frame'] + 8
            stable = 0
            while state['frame'] < deadline:
                frame()
                if device._escape_generation:
                    raise TaskRecoveryFailed('UI state verification interrupted by recovery')
                stable = stable + 1 if predicate() else 0
                if stable >= 2:
                    device.confirm_control_progress(verified_controls)
                    return True
            if fail:
                raise TaskRecoveryFailed('UI next state not reached after one click')
            return False

        task._ui_verify = verify

        # Real entry points, bound to the virtual device.
        from tasks.base_task import BaseTask
        task._dismiss_reward_once = lambda: click_confirm_once(
            task, lambda: task.C_UI_REWARD if state['popup'] else None,
            [task.C_UI_REWARD, reward], dismiss_anywhere=True)
        task._ui_get_reward = BaseTask.ui_get_reward.__get__(task)
        return task, state

    def run_claim(self, **kwargs):
        task, state = self.build(**kwargs)
        clock = VirtualClock()
        with mock.patch.object(click_verification, 'time', clock), \
                mock.patch('tasks.base_task.monotonic', clock.monotonic):
            result = task._ui_get_reward(task.I_BATTLE_REWARD)
        return result, state

    def test_a_popup_that_leaves_on_its_own_still_counts_as_claimed(self):
        # Real timing: the claim input and the caller's own capture each age
        # the popup, then the game swaps in its reward broadcast before the
        # dismiss frame is taken.
        result, state = self.run_claim(popup_frames=3)
        self.assertTrue(result)
        # The source claim was clicked; no dismiss input was invented.
        self.assertEqual([name for _, name in state['inputs']], ['I_BATTLE_REWARD'])

    def test_the_reward_broadcast_page_is_not_read_as_a_blocked_input(self):
        # Drive the dismiss alone, after the popup has already been replaced.
        task, state = self.build(popup_frames=0)
        state['claimed'] = True
        state['claimable'] = False
        state['popup'] = True
        clock = VirtualClock()
        with mock.patch.object(click_verification, 'time', clock):
            self.assertTrue(task._dismiss_reward_once())
        self.assertEqual(state['inputs'], [])

    def test_a_blocked_dismiss_is_still_reported_as_a_failure(self):
        # The fix must not turn a refused input into a silent success. A
        # refused input is never delivered, so only the claim is recorded.
        task, state = self.build(popup_frames=99, blocked=('ui_reward',))
        clock = VirtualClock()
        with mock.patch.object(click_verification, 'time', clock), \
                mock.patch('tasks.base_task.monotonic', clock.monotonic):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'dismiss input was blocked'):
                task._ui_get_reward(task.I_BATTLE_REWARD)
        self.assertEqual([name for _, name in state['inputs']], ['I_BATTLE_REWARD'])

    def test_a_popup_kept_visible_is_dismissed_by_one_input(self):
        # The ordinary path is unchanged: claim, then a single dismiss tap.
        result, state = self.run_claim(popup_frames=99)
        self.assertTrue(result)
        self.assertEqual([name for _, name in state['inputs']],
                         ['I_BATTLE_REWARD', 'ui_reward'])


class ConfirmAbsentModalTests(unittest.TestCase):
    """`click_confirm_once` reports disappearance as the destination."""

    def test_absent_modal_costs_no_input_and_is_not_a_failure(self):
        device = SimpleNamespace(image=scene_image(0), image_frame_id=0,
                                 _escape_generation=0)
        device.confirm_control_progress = mock.Mock()
        device.click = mock.Mock(return_value=True)
        task = SimpleNamespace(device=device)
        task.screenshot = lambda: None
        task._ui_verify = lambda *a, **k: True
        target = RuleClick('CONTINUE')
        result = click_confirm_once(task, lambda: None, [target])
        self.assertTrue(result)
        device.click.assert_not_called()

    def test_a_refused_input_is_still_false(self):
        device = SimpleNamespace(image=scene_image(0), image_frame_id=0,
                                 _escape_generation=0)
        device.confirm_control_progress = mock.Mock()
        device.click = mock.Mock(return_value=False)
        task = SimpleNamespace(device=device)
        task.screenshot = lambda: None
        task._ui_verify = lambda *a, **k: True
        target = RuleClick('CONTINUE')
        result = click_confirm_once(task, lambda: target, [target])
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()

"""Audited confirmation controls may retry once; consuming actions may never.

The retry mechanism spans module/device/confirm_retry.py and the
before_click/after_click fence. Only screenshots, elapsed time and control
delivery are simulated; the recognition predicates and every guard inside the
fence run for real against real images.
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


def scene_image(scene):
    """A screen that is trivially comparable before and after an input."""
    return np.full((720, 1280, 3), (scene * 53) % 256, dtype=np.uint8)


class VirtualClock:
    """Keeps the fence's 8s deadlines finite without real sleeping."""

    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        self.value += .2
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class _FenceBlocker(TaskRecoveryFailed):
    pass


class ConfirmRetryTests(unittest.TestCase):
    def build(self, *, dismiss_works, retry_works=None, blocked=False):
        """One reward popup that only vanishes when the dismiss input lands."""
        state = dict(frame=0, inputs=[], scene=0, popup=True,
                     blocked=blocked, recovery_at=None)
        device = SimpleNamespace(image=scene_image(0), image_frame_id=0,
                                 _escape_generation=0,
                                 performance=SimpleNamespace(timeout=lambda value: value))
        device.confirm_control_progress = mock.Mock()

        def frame():
            state['frame'] += 1
            device.image = scene_image(state['scene'])
            device.image_frame_id = state['frame']
            if state['recovery_at'] == state['frame']:
                device._escape_generation += 1

        def deliver(control_name):
            state['inputs'].append((state['frame'], control_name))
            if state['blocked']:
                return False
            works = dismiss_works if len(state['inputs']) == 1 else retry_works
            if works:
                state['popup'] = False
                state['scene'] += 1
            return True

        def click(x, y, control_name=None, **kwargs):
            before = before_click(device, x, y, control_name)
            if deliver(control_name):
                after_click(device, x, y, control_name, before)
                return True
            return False

        device.screenshot = lambda: frame()
        device.click = click

        task = SimpleNamespace(device=device)
        task.screenshot = lambda: frame()

        def match():
            return RuleClick('CONTINUE') if state['popup'] else None

        controls = [RuleClick('CONTINUE'), RuleImage('REWARD')]

        def verify(predicate, verified_controls, timeout=12, fail=True):
            deadline = state['frame'] + 4
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
        return task, state, match, controls

    def run_retry(self, **kwargs):
        task, state, match, controls = self.build(**kwargs)
        with mock.patch.object(click_verification, 'time', VirtualClock()):
            result = click_confirm_once(task, match, controls, dismiss_anywhere=True)
        return result, state

    def test_a_dismiss_that_lands_is_never_retried(self):
        result, state = self.run_retry(dismiss_works=True)
        self.assertTrue(result)
        self.assertEqual([name for _, name in state['inputs']], ['CONTINUE'])

    def test_unchanged_confirmation_is_retried_exactly_once(self):
        result, state = self.run_retry(dismiss_works=False, retry_works=True)
        self.assertTrue(result)
        self.assertEqual([name for _, name in state['inputs']], ['CONTINUE', 'CONTINUE'])

    def test_a_retry_that_also_lands_nowhere_stops_instead_of_looping(self):
        task, state, match, controls = self.build(dismiss_works=False, retry_works=False)
        with mock.patch.object(click_verification, 'time', VirtualClock()):
            with self.assertRaises(TaskRecoveryFailed):
                click_confirm_once(task, match, controls, dismiss_anywhere=True)
        # Two inputs, never a third: the retry is a bounded permit, not a loop.
        self.assertEqual([name for _, name in state['inputs']], ['CONTINUE', 'CONTINUE'])

    def test_blocked_input_reports_failure_without_a_retry(self):
        result, state = self.run_retry(dismiss_works=True, blocked=True)
        self.assertFalse(result)
        self.assertEqual([name for _, name in state['inputs']], ['CONTINUE'])

    def test_an_absent_modal_costs_no_input(self):
        task, state, match, controls = self.build(dismiss_works=True)
        state['popup'] = False
        with mock.patch.object(click_verification, 'time', VirtualClock()):
            # Disappearance is the destination, so an already-vanished modal is
            # a completed dismiss. Only a refused input may report False.
            self.assertTrue(click_confirm_once(task, match, controls, dismiss_anywhere=True))
        self.assertEqual(state['inputs'], [])

    def test_an_exhausted_retry_may_not_be_replayed_on_re_entry(self):
        task, state, match, controls = self.build(dismiss_works=False, retry_works=True)
        with mock.patch.object(click_verification, 'time', VirtualClock()):
            click_confirm_once(task, match, controls, dismiss_anywhere=True)
        state['popup'] = True
        with mock.patch.object(click_verification, 'time', VirtualClock()):
            with self.assertRaises(TaskRecoveryFailed) as caught:
                click_confirm_once(task, match, controls, dismiss_anywhere=True)
        self.assertIn('already used', str(caught.exception))
        self.assertEqual([name for _, name in state['inputs']], ['CONTINUE', 'CONTINUE'])

    def test_recovery_during_the_wait_is_a_failure_not_a_retry(self):
        task, state, match, controls = self.build(dismiss_works=False, retry_works=True)
        state['recovery_at'] = state['frame'] + 2
        with mock.patch.object(click_verification, 'time', VirtualClock()):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'interrupted'):
                click_confirm_once(task, match, controls, dismiss_anywhere=True)

    def test_the_fence_refuses_a_retry_permit_that_no_longer_matches(self):
        task, state, match, controls = self.build(dismiss_works=False, retry_works=True)
        device = task.device
        device._verified_click = dict(name='CONTINUE', x=640, y=680,
                                      before=np.zeros((32, 48), np.uint8), changed=False)
        device._confirm_retry_permit = dict(receipt=device._verified_click,
                                            generation=0, used=False)
        with self.assertRaises(TaskRecoveryFailed):
            before_click(device, 900, 680, 'CONTINUE')
        self.assertFalse(device._confirm_retry_permit['used'])


class ConsumingActionTests(unittest.TestCase):
    """The retry may only be reached through an audited identity, never by name."""

    def test_retry_is_not_selected_for_a_generic_confirm(self):
        source = _read_source()
        body = source['ui_click_until_disappear']
        self.assertIn('is self.I_UI_BACK_RED', body)
        self.assertIn("I_SOU_SWITCH_SURE", body)
        # Selection is by identity, not by a substring of the control name.
        self.assertNotIn('.name ==', body)

    def test_reward_dismiss_does_not_fall_back_to_the_source_claim_button(self):
        source = _read_source()
        self.assertNotIn('appear_then_click(self.I_UI_REWARD', source['ui_reward_appear_click'])
        self.assertIn('_dismiss_reward_once', source['ui_reward_appear_click'])
        self.assertIn('_dismiss_reward_once', source['ui_get_reward'])


def _read_source():
    from pathlib import Path
    import ast
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'tasks/base_task.py').read_text(encoding='utf-8'))
    base = next(n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == 'BaseTask')
    return {n.name: ast.unparse(n) for n in base.body if isinstance(n, ast.FunctionDef)}


if __name__ == '__main__':
    unittest.main()
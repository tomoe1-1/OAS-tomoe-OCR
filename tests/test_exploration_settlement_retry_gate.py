"""The third exploration settlement input must not be spent on a false negative.

``after_click`` decides ``changed`` from two fresh frames (measured median wait
0.81s). The exploration reward page needs a median 2.65s, max 8.38s, to move on,
so that verdict declared clicks dead while the page had already advanced. These
guards drive the real ``after_click`` entry point and only replace the device,
so the wiring between the pixel verdict and the retry gate is exercised.
"""
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock

import numpy as np

from module.device.click_verification import (
    ClickResponseUnchanged, after_click, changed, signature,
)
from module.device.repeat_policy import exploration_retry_guard
from module.exception import TaskRecoveryFailed


class AdvancingClock:
    """A fake ``time`` whose monotonic() advances, so bounded loops terminate."""

    def __init__(self, step=0.2):
        self.now = 0.0
        self.step = step
        self.sleeps = []

    def monotonic(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def reward_page(width=1280, height=720):
    """A still settlement screen: nothing moves while it waits for input."""
    return np.full((height, width, 3), 40, dtype=np.uint8)


def next_page(width=1280, height=720):
    """A different scene, as after the continuation actually takes effect."""
    return np.full((height, width, 3), 200, dtype=np.uint8)


class SettlementRetryGateTests(unittest.TestCase):
    def subject(self, clock, *, probe_frames):
        """A device whose settlement page clears after ``probe_frames`` probes.

        The page recognizer, not the pixel patch, is what the probe consults, so
        the timeline counts *page recognitions*. ``after_click`` spends two of
        them on its own pixel verdict before the gate opens; ``probe_frames``
        therefore describes the remaining budget the slow page needs.
        """
        patcher = mock.patch('module.device.click_verification.time', clock)
        patcher.start()
        self.addCleanup(patcher.stop)

        seen = {'n': 0, 'shots': 0}

        def matches_result(_page):
            # Production asks twice per state (result page, then reward page).
            # Only the reward question decides "is this still settlement", so
            # the timeline advances on that one to keep the budget meaningful.
            from tasks.GameUi.page import page_reward as reward_page_ref
            if _page is reward_page_ref:
                seen['n'] += 1
                return seen['n'] <= probe_frames
            return False

        task = Mock()
        task.appear = Mock(return_value=False)
        task.match_page_once = Mock(side_effect=matches_result)
        task.I_OVER_GHOST = 'I_OVER_GHOST'
        task.I_GB_SKIN_CONFIRM = 'I_GB_SKIN_CONFIRM'

        guard = SimpleGuard(task)
        # A real attribute bag, not a Mock: production reads these with
        # getattr defaults and subscripts the permit, which a Mock would fake.
        device = SimpleNamespace(
            image=reward_page(), image_frame_id=0,
            _task_flow_guard=guard, _escape_generation=0,
            _accepted_invitation_generation=0, _task_flow_progress_generation=0,
            _handling_invitation=False, _verified_click=None,
            _confirm_retry_permit=None, _exploration_input_anchor=None,
        )
        # exploration_retry_guard only accepts a guard that points back at this
        # device, so the link has to be closed here rather than assumed.
        guard.device = device

        def screenshot():
            seen['shots'] += 1
            device.image_frame_id += 1
            # The local patch stays still no matter how far the page has moved:
            # that is exactly the false negative under test.
            device.image = reward_page()

        device.screenshot = screenshot
        return device, seen

    def receipt(self, device, seen, *, exploration_count):
        """Build the stored first receipt the third input is judged against.

        ``after_click`` reads the receipt that ``before_click`` left behind, so
        the anchor has to match too: production sets it from the same guard and
        generations, and a mismatch aborts before the gate is ever reached.
        """
        before = signature(device.image, 640, 360)
        device._verified_click = {
            'name': 'SETTLEMENT_RANDOM_CLICK', 'x': 640, 'y': 360,
            'before': before, 'changed': False,
            'confirm_retry_used': False, 'exploration_retry_used': True,
            'exploration_retry_count': exploration_count,
            'exploration_retry_pending': False,
            'exploration_guard': device._task_flow_guard,
            'generation': 0, 'invitation': 0, 'progress': 0,
            'exploration_context': None, 'exploration_settlement': (False, True),
        }
        device._exploration_input_anchor = (device._task_flow_guard, 0, 0)
        return device._verified_click

    def run_after(self, device, receipt):
        with mock.patch('tasks.GameUi.default_pages.reward_details_visible', return_value=False):
            return after_click(device, receipt['x'], receipt['y'],
                               receipt['name'], receipt['before'],
                               exploration_retry=True)

    def test_slow_page_keeps_the_third_input_from_being_wasted(self):
        # The page recognizer keeps reporting the reward page for several more
        # probes, exactly as the measured 2.65s median transition did, while the
        # local patch never moves. The click worked; the task must not be failed
        # and the caller must not be told to spend another input.
        clock = AdvancingClock()
        device, seen = self.subject(clock, probe_frames=6)
        receipt = self.receipt(device, seen, exploration_count=2)

        self.run_after(device, receipt)

        self.assertGreaterEqual(seen['shots'], 3,
                                'probe must look past the two-frame pixel window')
        self.assertFalse(device._verified_click['changed'],
                         'the local patch really was still')
        # The gate returned instead of raising, and the receipt the next caller
        # reads must not invite another settlement input.
        self.assertTrue(device._verified_click['exploration_retry_used'])
        self.assertEqual(device._verified_click['exploration_retry_count'], 2)

    def test_genuinely_stuck_page_still_spends_the_budget_and_raises(self):
        # The settlement page never leaves: the guard must still fire, otherwise
        # this fix would silently disable the retry fence entirely.
        clock = AdvancingClock()
        device, seen = self.subject(clock, probe_frames=10 ** 6)
        receipt = self.receipt(device, seen, exploration_count=2)

        with self.assertRaises(ClickResponseUnchanged) as caught:
            self.run_after(device, receipt)
        self.assertIn('unchanged after three inputs', str(caught.exception))
        self.assertGreater(seen['shots'], 2,
                           'must have polled for the real transition time')

    def test_first_and_second_input_keep_their_original_verdict(self):
        # The extra polling belongs to the last input only; earlier inputs must
        # not start waiting several seconds for a page they do not give up on.
        for count in (0, 1):
            with self.subTest(exploration_count=count):
                clock = AdvancingClock()
                device, seen = self.subject(clock, probe_frames=10 ** 6)
                receipt = self.receipt(device, seen, exploration_count=count)

                self.run_after(device, receipt)
                self.assertLessEqual(seen['shots'], 2)

    def test_non_settlement_control_is_never_delayed_by_the_page_probe(self):
        clock = AdvancingClock()
        device, seen = self.subject(clock, probe_frames=10 ** 6)
        receipt = self.receipt(device, seen, exploration_count=2)
        receipt['name'] = 'RES_FIRE'
        receipt['exploration_settlement'] = None

        self.run_after(device, receipt)
        self.assertLessEqual(seen['shots'], 2)

    def test_recovery_during_the_probe_refuses_to_judge(self):
        # An escape generation change mid-observation aborts verification before
        # the gate is reached; the click can no longer be attributed to this
        # input and must not be scored either way.
        clock = AdvancingClock()
        device, seen = self.subject(clock, probe_frames=10 ** 6)

        def screenshot():
            seen['shots'] += 1
            device.image_frame_id += 1
            device.image = reward_page()
            device._escape_generation = 1

        device.screenshot = screenshot
        receipt = self.receipt(device, seen, exploration_count=2)
        receipt['generation'] = 0

        with self.assertRaises(TaskRecoveryFailed) as caught:
            self.run_after(device, receipt)
        self.assertIn('interrupted by recovery', str(caught.exception))

    def test_probe_refuses_when_the_context_left_under_it(self):
        # Same rule as the recovery case, reached on the probe itself: the
        # generation recorded in the receipt no longer matches, so the probe
        # must decline to judge rather than read it as an advanced page.
        from module.device.click_verification import _exploration_settlement_left

        clock = AdvancingClock()
        device, seen = self.subject(clock, probe_frames=10 ** 6)
        receipt = self.receipt(device, seen, exploration_count=2)
        receipt['generation'] = 7

        with mock.patch('tasks.GameUi.default_pages.reward_details_visible', return_value=False):
            verdict = _exploration_settlement_left(device, 'SETTLEMENT_RANDOM_CLICK', receipt)
        self.assertIsNone(verdict,
                          'a moved generation must not be scored as an advanced page')

    def test_probe_reports_a_page_that_still_has_not_moved(self):
        # The other end of the contract: a page that really is still there is
        # reported as not-left, which is what keeps the gate armed.
        from module.device.click_verification import _exploration_settlement_left

        clock = AdvancingClock()
        device, seen = self.subject(clock, probe_frames=10 ** 6)
        receipt = self.receipt(device, seen, exploration_count=2)

        with mock.patch('tasks.GameUi.default_pages.reward_details_visible', return_value=False):
            verdict = _exploration_settlement_left(device, 'SETTLEMENT_RANDOM_CLICK', receipt)
        self.assertIs(verdict, False)


class SimpleGuard:
    def __init__(self, task):
        self.task = task
        self.command = 'Exploration'
        self.device = None
        self._entered = True


if __name__ == '__main__':
    unittest.main()

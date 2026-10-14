"""Exploration monster entry: a click that changed nothing may be re-sent once
under a strictly scoped permit, and it must never be re-sent blindly.

The real `fire` body is extracted from source so this guards the shipped
branch order, not a paraphrase of it.
"""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.device.repeat_policy import exploration_retry_guard
from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tasks/Exploration/base.py'
METHOD = 'fire'
# Names the extracted body calls; the namespace below must provide all of them.
NEEDED = ['appear_then_click', 'appear', 'run_general_battle']


def body_source():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == METHOD:
                    return ast.unparse(item)
    raise AssertionError(f'{METHOD} not found in {SOURCE}')


class BoundedTimer:
    """Each observation window ends after a fixed number of polls."""

    def __init__(self, *args, **kwargs):
        self.polls = 0

    def start(self):
        return self

    def reached(self):
        self.polls += 1
        return self.polls > 4


class Clock:
    """A monotonic stub that always advances."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 0.2
        return self.now


class Timeline:
    """Page recognizer stub: named pages plus a fixed observation sequence.

    A recognised page is returned once per call; the final entry repeats, so a
    stable state is reachable while an unstable one still runs out of budget.
    """

    def __init__(self, main, sequence):
        self.page_exp_main = main
        self.page_exp_exit = object()
        self.page_battle_prepare = object()
        self.page_battle = object()
        self.page_battle_result = object()
        self.page_reward = object()
        self._sequence = list(sequence)
        self.calls = 0

    def next(self):
        index = min(self.calls, len(self._sequence) - 1)
        self.calls += 1
        return self._sequence[index]


def device(receipt=None):
    class Device:
        _escape_generation = 0
        _accepted_invitation_generation = 0
        _task_flow_progress_generation = 0
        image_frame_id = 0

        def __init__(self):
            self.performance = SimpleNamespace(timeout=lambda seconds: seconds)
            self._verified_click = receipt
            self.observed = []
            self.progressed = None

        def click(self, x, y, control_name=None):
            self.observed.append((x, y, control_name))
            # Mirror the device layer: each re-spend carries the retry count
            # forward, so the task's own budget is what stops the loop.
            pending = self._verified_click
            if isinstance(pending, dict):
                count = pending.get('exploration_retry_count', 0)
                self._verified_click = dict(pending, exploration_retry_count=count + 1,
                                            exploration_retry_used=True)
            return True

        def confirm_control_progress(self, controls):
            self.progressed = list(controls)

    return Device()


def guard(command='Exploration', entered=True, handling_invitation=False):
    holder = SimpleNamespace(_entered=entered, command=command,
                             device=None, task=None)
    holder.handling_invitation = handling_invitation
    return holder


def build(pages, dev):
    """Run the shipped body against stubs; return the task object."""
    namespace = dict(device=dev, pages=pages, Timer=BoundedTimer,
                     logger=Mock(), time=SimpleNamespace(sleep=lambda _: None),
                     datetime=datetime,
                     TaskRecoveryFailed=TaskRecoveryFailed,
                     exploration_retry_guard=exploration_retry_guard)
    exec(compile(ast.parse(body_source()), '<fire>', 'exec'), namespace)

    obj = SimpleNamespace(device=dev, need_exit=True,
                          wait_start_time=datetime(2020, 1, 1),
                          _config=SimpleNamespace(general_battle_config='gb'),
                          _match_end=Mock())
    obj.get_current_page = Mock(side_effect=pages.next)
    for name in NEEDED:
        setattr(obj, name, Mock())

    def screenshot():
        dev.image_frame_id += 1

    obj.screenshot = screenshot
    obj.fire = lambda button: namespace[METHOD](obj, button)
    return obj


def receipt_for(guard_ref, **overrides):
    count = overrides.pop('count', 0)
    return dict(name=overrides.pop('name', 'MONSTER'),
                x=overrides.pop('x', 100), y=overrides.pop('y', 200), before='a',
                changed=overrides.pop('changed', False),
                confirm_retry_used=overrides.pop('used', False),
                exploration_retry_used=count > 0,
                exploration_retry_count=count,
                exploration_retry_pending=False,
                exploration_guard=overrides.pop('retry_guard', guard_ref),
                generation=overrides.pop('generation', 0),
                invitation=overrides.pop('invitation', 0),
                progress=overrides.pop('progress', 0),
                exploration_context=None, exploration_settlement=None)


BUTTON = SimpleNamespace(name='MONSTER', roi_front=(80, 180, 120, 60))


class FireHappyPathTests(unittest.TestCase):
    def setUp(self):
        self.main = object()
        self.guard_obj = guard()
        self.dev = device()
        self.dev._task_flow_guard = self.guard_obj
        self.guard_obj.device = self.dev

    def test_two_stable_battle_frames_hand_off_and_count_progress(self):
        # Build first so the timeline can name the real battle page objects.
        battle = object()
        pages = Timeline(self.main, [self.main, battle, battle])
        task = build(pages, self.dev)
        battle = pages.page_battle_prepare
        pages._sequence = [self.main, battle, battle]
        self.guard_obj.task = task
        task.appear_then_click.return_value = True
        self.assertTrue(task.fire(BUTTON))
        task.run_general_battle.assert_called_once_with('gb', exit_matcher=self.main)
        self.assertEqual(self.dev.progressed, [BUTTON])
        self.assertEqual(self.dev.observed, [])

    def test_exit_animation_cancels_the_pending_exit(self):
        exit_page = object()
        pages = Timeline(self.main, [self.main, self.main, exit_page])
        pages.page_exp_exit = exit_page
        task = build(pages, self.dev)
        self.guard_obj.task = task
        task.appear_then_click.return_value = True
        self.assertTrue(task.fire(BUTTON))
        self.assertFalse(task.need_exit)
        task.run_general_battle.assert_not_called()

    def test_source_click_that_never_lands_is_not_retried(self):
        pages = Timeline(self.main, [self.main, self.main])
        task = build(pages, self.dev)
        self.guard_obj.task = task
        task.appear_then_click.return_value = False
        self.assertFalse(task.fire(BUTTON))
        self.assertEqual(self.dev.observed, [])

    def test_a_single_battle_frame_never_counts_as_entry(self):
        battle = object()
        pages = Timeline(self.main, [self.main, battle, self.main])
        task = build(pages, self.dev)
        self.guard_obj.task = task
        task.appear_then_click.return_value = True
        task.appear.return_value = False
        self.dev._verified_click = None
        with self.assertRaisesRegex(TaskRecoveryFailed, 'did not enter a verified battle'):
            task.fire(BUTTON)
        task.run_general_battle.assert_not_called()


class FireRetryGateTests(unittest.TestCase):
    """Every one of these must stop the second input from being sent."""

    def setUp(self):
        self.main = object()
        self.guard_obj = guard()
        self.dev = device()
        self.dev._task_flow_guard = self.guard_obj
        self.guard_obj.device = self.dev

    def scenario(self, receipt, *, appear=True, button=BUTTON, guard_task=True):
        """Drive fire with a receipt that failed to change anything.

        Every refusal path ends the same way for the caller: the task fails
        instead of reporting the monster as entered.
        """
        self.dev._verified_click = receipt
        pages = Timeline(self.main, [self.main, self.main, self.main])
        task = build(pages, self.dev)
        self.guard_obj.task = task if guard_task else object()
        task.appear_then_click.return_value = True
        task.appear.return_value = appear
        self.dev.observed = []
        with self.assertRaises(TaskRecoveryFailed):
            task.fire(button)
        return task

    def valid_receipt(self, **overrides):
        return receipt_for(self.guard_obj, **overrides)

    def test_the_bounded_retry_budget_stops_at_three_inputs(self):
        """One source input plus at most two re-sends, then the task fails."""
        self.scenario(self.valid_receipt())
        self.assertEqual(self.dev.observed,
                         [(100, 200, 'MONSTER')] * 2)

    def test_no_permit_no_second_input(self):
        for case in ('no_guard', 'wrong_command', 'not_entered', 'other_device',
                     'handling_invitation', 'guard_task_mismatch'):
            with self.subTest(case=case):
                self.setUp()
                receipt = self.valid_receipt()
                if case == 'no_guard':
                    self.dev._task_flow_guard = None
                elif case == 'wrong_command':
                    self.guard_obj.command = 'Chess'
                elif case == 'not_entered':
                    self.guard_obj._entered = False
                elif case == 'other_device':
                    self.guard_obj.device = object()
                elif case == 'handling_invitation':
                    self.dev._handling_invitation = True
                self.scenario(receipt, guard_task=case != 'guard_task_mismatch')
                self.assertEqual(self.dev.observed, [], case)

    def test_receipt_must_still_be_the_pending_unchanged_click(self):
        cases = {}
        cases['screen_did_change'] = dict(changed=True)
        cases['other_control'] = dict(name='OTHER')
        cases['foreign_guard'] = dict(retry_guard=guard(command='Chess'))
        cases['stale_generation'] = dict(generation=7)
        cases['stale_invitation'] = dict(invitation=7)
        cases['stale_progress'] = dict(progress=7)
        cases['retries_exhausted'] = dict(count=2)
        cases['confirm_retry_used'] = dict(used=True)
        for case, override in cases.items():
            with self.subTest(case=case):
                self.setUp()
                self.scenario(self.valid_receipt(**override))
                self.assertEqual(self.dev.observed, [], case)

        for case in ('no_receipt', 'retry_pending', 'replaced_by_other_receipt'):
            with self.subTest(case=case):
                self.setUp()
                if case == 'no_receipt':
                    self.scenario(None)
                elif case == 'retry_pending':
                    receipt = self.valid_receipt()
                    receipt['exploration_retry_pending'] = True
                    self.scenario(receipt)
                else:
                    # The receipt object must still be the device's pending one.
                    receipt = self.valid_receipt()
                    self.dev._verified_click = receipt
                    pages = Timeline(self.main, [self.main] * 3)
                    task = build(pages, self.dev)
                    self.guard_obj.task = task
                    task.appear_then_click.return_value = True
                    task.appear.return_value = True
                    self.dev.observed = []
                    swap = {'done': False}

                    def screenshot():
                        self.dev.image_frame_id += 1
                        if not swap['done']:
                            swap['done'] = True
                            self.dev._verified_click = dict(receipt, x=110)

                    task.screenshot = screenshot
                    with self.assertRaises(TaskRecoveryFailed):
                        task.fire(BUTTON)
                self.assertEqual(self.dev.observed, [], case)

    def test_target_must_still_resolve_to_the_original_monster(self):
        self.scenario(self.valid_receipt(), appear=False)
        self.assertEqual(self.dev.observed, [])

    def test_a_moved_match_window_is_refused_rather_than_re_sent(self):
        drifted = SimpleNamespace(name='MONSTER', roi_front=(200, 300, 120, 60))
        self.scenario(self.valid_receipt(), button=drifted)
        self.assertEqual(self.dev.observed, [])

    def test_retry_point_outside_the_declared_window_is_refused(self):
        self.scenario(self.valid_receipt(x=900, y=900))
        self.assertEqual(self.dev.observed, [])

    def test_blocked_retry_input_is_an_error_not_a_silent_return(self):
        self.dev._verified_click = self.valid_receipt()
        pages = Timeline(self.main, [self.main] * 3)
        task = build(pages, self.dev)
        self.guard_obj.task = task
        task.appear_then_click.return_value = True
        task.appear.return_value = True
        self.dev.click = Mock(return_value=False)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'retry input was blocked'):
            task.fire(BUTTON)

    def test_recovery_mid_observation_aborts_before_any_retry(self):
        self.dev._verified_click = self.valid_receipt()
        pages = Timeline(self.main, [self.main] * 3)
        task = build(pages, self.dev)
        self.guard_obj.task = task
        task.appear_then_click.return_value = True
        task.appear.return_value = True
        self.dev.observed = []

        def screenshot():
            self.dev.image_frame_id += 1
            self.dev._escape_generation = 9

        task.screenshot = screenshot
        with self.assertRaisesRegex(TaskRecoveryFailed, 'interrupted by recovery'):
            task.fire(BUTTON)
        self.assertEqual(self.dev.observed, [])

    def test_a_stale_frame_never_counts_as_a_second_observation(self):
        self.dev._verified_click = self.valid_receipt()
        pages = Timeline(self.main, [self.main] * 3)
        task = build(pages, self.dev)
        self.guard_obj.task = task
        task.appear_then_click.return_value = True
        task.appear.return_value = True
        self.dev.observed = []
        task.screenshot = Mock()  # image_frame_id never advances
        with self.assertRaisesRegex(TaskRecoveryFailed, 'did not enter a verified battle'):
            task.fire(BUTTON)
        self.assertEqual(self.dev.observed, [])


if __name__ == '__main__':
    unittest.main()

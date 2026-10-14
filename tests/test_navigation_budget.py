"""Navigation must remain bounded despite page changes and recovery."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import TaskRecoveryFailed
from tasks.GameUi.navigator import GameUi, _NavigationBudget
from tasks.GameUi.page_definition import Page


class NeverTimer:
    """The progress timeout cannot rescue these deliberately endless flows."""
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return False


class Clock:
    def __init__(self):
        self.now = 100.0

    def advance(self, seconds):
        self.now += seconds


class ExpiredTimer(NeverTimer):
    def reached(self):
        return True


def page(name):
    return Page(lambda task: False, key=name, name=name, category='global', register=False)


def task():
    obj = GameUi.__new__(GameUi)
    obj.device = SimpleNamespace(_escape_generation=0, recover_by_escape=Mock())
    obj.navigator = SimpleNamespace(
        current_page=None, task_category='global', resolve_page=lambda target: target,
        add_page=lambda target: target, local_unknown_closers=[], unknown_close_history=[],
        last_enter_success_page_key=None, edge_penalties={}, add_penalty=Mock(return_value=1.0))
    obj._refresh_current_page = Mock(return_value=None)
    obj._run_enter_success_hooks_if_needed = Mock()
    obj._build_path = Mock(return_value=None)
    obj._execute_transition = Mock(return_value=False)
    obj.close_unknown_pages = Mock(return_value=True)
    obj.screenshot = Mock()
    obj.maybe_screenshot = Mock()
    obj.get_current_page = Mock(return_value=object())
    obj._run_hooks = Mock()
    obj._mark_page_entered = Mock()
    obj._log_navigation_timeout = Mock()
    return obj


class NavigationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.clock_patch = patch('tasks.GameUi.navigator.time.monotonic',
                                 side_effect=lambda: self.clock.now)
        self.timer_patch = patch('tasks.GameUi.navigator.Timer', NeverTimer)
        self.clock_patch.start()
        self.timer_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.timer_patch.stop)

    def test_default_call_has_nonrenewable_limits_and_cleans_up_after_success(self):
        obj = task()
        target = page('target')

        def navigate(*args, **kwargs):
            budget, = obj._navigation_budgets
            self.assertEqual(budget.max_steps, 64)
            self.assertEqual(budget.deadline, self.clock.now + 180)
            self.assertEqual(budget.max_recoveries, 2)
            self.assertEqual(budget.max_polls, 256)
            return True

        obj._goto_page = Mock(side_effect=navigate)
        self.assertTrue(obj.goto_page(target))
        obj._goto_page.assert_called_once_with(target, 0, True, 30, accepted_pages=())
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_default_unknown_page_closures_stop_even_when_clock_does_not_move(self):
        obj = task()
        with self.assertRaisesRegex(TaskRecoveryFailed, 'step limit'):
            obj.goto_page(page('target'))
        self.assertEqual(obj._refresh_current_page.call_count, 64)
        self.assertEqual(obj.close_unknown_pages.call_count, 64)
        obj.device.recover_by_escape.assert_not_called()
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_default_known_destination_finishes_without_recovery(self):
        obj = task()
        target = page('target')
        obj._refresh_current_page.return_value = target
        self.assertTrue(obj.goto_page(target))
        obj.close_unknown_pages.assert_not_called()
        obj.device.recover_by_escape.assert_not_called()
        self.assertIs(obj.navigator.current_page, target)
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_default_time_scales_for_slow_device_but_explicit_time_is_absolute(self):
        obj = task()
        obj.device.performance = SimpleNamespace(timeout=lambda seconds: seconds * 3)
        observed = []

        def navigate(*args, **kwargs):
            budget = obj._navigation_budgets[-1]
            observed.append((budget.max_steps, budget.deadline - self.clock.now))
            return True

        obj._goto_page = Mock(side_effect=navigate)
        obj.goto_page(page('target'))
        obj.goto_page(page('target'), max_steps=12, total_timeout=60)
        obj.goto_page(page('target'), max_steps=12)
        obj.goto_page(page('target'), total_timeout=60)
        self.assertEqual(observed, [(64, 540), (12, 60), (12, 540), (64, 60)])

    def test_default_absolute_deadline_survives_page_changes(self):
        obj = task()
        pages = [page('first'), page('second')]

        def refreshed(*args):
            self.clock.advance(40)
            return pages[(obj._refresh_current_page.call_count - 1) % 2]

        obj._refresh_current_page.side_effect = refreshed
        with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
            obj.goto_page(page('target'))
        self.assertEqual(obj._refresh_current_page.call_count, 5)
        self.assertEqual(obj.close_unknown_pages.call_count, 4)
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_default_stalled_navigation_never_dispatches_a_third_escape_recovery(self):
        obj = task()
        obj._log_navigation_timeout = GameUi._log_navigation_timeout.__get__(obj)
        obj._collect_page_check_results = Mock(return_value=[])
        obj.close_unknown_pages.return_value = False

        def recover(*args):
            obj.device._escape_generation += 1

        obj.device.recover_by_escape.side_effect = recover
        with patch('tasks.GameUi.navigator.Timer', ExpiredTimer):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'recovery limit'):
                obj.goto_page(page('target'))
        self.assertEqual(obj.device.recover_by_escape.call_count, 2)
        self.assertEqual(obj._refresh_current_page.call_count, 3)
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_default_transition_polling_stops_when_both_timers_and_clock_freeze(self):
        obj = task()
        source, target = page('source'), page('target')
        obj._refresh_current_page.return_value = source
        obj._build_path.return_value = [source.connect(target, object())]
        obj._execute_transition = GameUi._execute_transition.__get__(obj)
        obj._execute_action = Mock(return_value=False)
        obj._wait_for_destination = Mock()
        with self.assertRaisesRegex(TaskRecoveryFailed, 'polling limit'):
            obj.goto_page(target)
        self.assertEqual(obj._execute_action.call_count, 256)
        obj._wait_for_destination.assert_not_called()
        obj.device.recover_by_escape.assert_not_called()
        obj._run_hooks.assert_not_called()
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_default_destination_polling_stops_after_one_dispatched_action(self):
        obj = task()
        source, target = page('source'), page('target')
        obj._refresh_current_page.return_value = source
        obj._build_path.return_value = [source.connect(target, object())]
        obj._execute_transition = GameUi._execute_transition.__get__(obj)
        obj._execute_action = Mock(return_value=True)
        obj.confirm_page = Mock(return_value=False)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'polling limit'):
            obj.goto_page(target)
        obj._execute_action.assert_called_once()
        self.assertEqual(obj.confirm_page.call_count, 255)
        obj.device.recover_by_escape.assert_not_called()
        obj._mark_page_entered.assert_not_called()
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_unknown_page_closures_cannot_reset_the_step_limit(self):
        obj = task()
        with self.assertRaisesRegex(TaskRecoveryFailed, 'step limit'):
            obj.goto_page(page('target'), max_steps=3)
        self.assertEqual(obj._refresh_current_page.call_count, 3)
        self.assertEqual(obj.close_unknown_pages.call_count, 3)
        obj.device.recover_by_escape.assert_not_called()
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_changing_pages_and_failed_edges_share_one_step_count(self):
        obj = task()
        first, second, target = page('first'), page('second'), page('target')
        edges = [first.connect(target, object()), second.connect(target, object())]
        obj._refresh_current_page.side_effect = [first, second, first]
        obj._build_path.side_effect = [[edges[0]], [edges[1]]]
        with self.assertRaisesRegex(TaskRecoveryFailed, 'step limit'):
            obj.goto_page(target, max_steps=4)
        self.assertEqual(obj._refresh_current_page.call_count, 2)
        self.assertEqual(obj._execute_transition.call_count, 2)
        self.assertIsNone(obj.navigator.current_page)

    def test_steps_are_checked_between_edges_in_a_long_path(self):
        obj = task()
        source, middle, later, target = map(page, ('source', 'middle', 'later', 'target'))
        edges = [source.connect(middle, object()), middle.connect(later, object()),
                 later.connect(target, object())]
        obj._refresh_current_page.return_value = source
        obj._build_path.return_value = edges
        obj._execute_transition.return_value = True
        with self.assertRaisesRegex(TaskRecoveryFailed, 'step limit'):
            obj.goto_page(target, max_steps=3)
        self.assertEqual(obj._execute_transition.call_count, 2)

    def test_page_changes_do_not_reset_the_absolute_deadline(self):
        obj = task()
        pages = [page('first'), page('second')]

        def refreshed(*args):
            self.clock.advance(0.6)
            return pages[(obj._refresh_current_page.call_count - 1) % 2]

        obj._refresh_current_page.side_effect = refreshed
        with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
            obj.goto_page(page('target'), total_timeout=2)
        self.assertEqual(obj._refresh_current_page.call_count, 4)
        self.assertEqual(obj.close_unknown_pages.call_count, 3)
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_escape_recovery_does_not_reset_the_explicit_budget(self):
        obj = task()

        def refreshed(*args):
            obj.device._escape_generation += 1
            return None

        obj._refresh_current_page.side_effect = refreshed
        with self.assertRaisesRegex(TaskRecoveryFailed, 'step limit'):
            obj.goto_page(page('target'), max_steps=2)
        self.assertEqual(obj._refresh_current_page.call_count, 2)

    def test_entry_work_is_part_of_the_total_deadline(self):
        obj = task()

        def resolve(target):
            self.clock.advance(2)
            return target

        obj.navigator.resolve_page = resolve
        with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
            obj.goto_page(page('target'), total_timeout=1)
        obj._refresh_current_page.assert_not_called()
        obj.close_unknown_pages.assert_not_called()

    def test_unknown_closer_stops_before_a_second_action_after_deadline(self):
        obj = task()
        obj.close_unknown_pages = GameUi.close_unknown_pages.__get__(obj)
        obj.DEFAULT_UNKNOWN_CLOSERS = [object(), object()]
        obj._navigation_budgets = (_NavigationBudget('target', None, 1),)

        def action(*args, **kwargs):
            self.clock.advance(1.1)
            return False

        obj._execute_action = Mock(side_effect=action)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
            obj.close_unknown_pages()
        obj._execute_action.assert_called_once()
        self.assertEqual(obj.navigator.unknown_close_history, [])

    def test_transition_action_polling_obeys_the_deadline(self):
        obj = task()
        obj._execute_transition = GameUi._execute_transition.__get__(obj)
        obj._navigation_budgets = (_NavigationBudget('target', None, 1),)
        edge = page('source').connect(page('target'), object())

        def action(*args, **kwargs):
            self.clock.advance(0.6)
            return False

        obj._execute_action = Mock(side_effect=action)
        obj._wait_for_destination = Mock()
        with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
            obj._execute_transition(edge)
        self.assertEqual(obj._execute_action.call_count, 2)
        obj._wait_for_destination.assert_not_called()
        obj._run_hooks.assert_not_called()

    def test_late_destination_cannot_be_accepted_as_success(self):
        obj = task()
        obj._navigation_budgets = (_NavigationBudget('target', None, 1),)

        def confirm(*args, **kwargs):
            self.clock.advance(1.1)
            return True

        obj.confirm_page = Mock(side_effect=confirm)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
            obj._wait_for_destination(page('target'))
        self.assertIsNone(obj.navigator.current_page)

    def test_arrival_wait_is_capped_by_the_remaining_total_time(self):
        obj = task()
        target = page('target')
        obj._refresh_current_page.return_value = target
        with patch('tasks.GameUi.navigator.time.sleep', side_effect=self.clock.advance) as slept:
            with self.assertRaisesRegex(TaskRecoveryFailed, 'total timeout'):
                obj.goto_page(target, confirm_wait=10, total_timeout=1)
        slept.assert_called_once_with(1.0)
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_accepted_page_can_finish_within_the_budget(self):
        obj = task()
        accepted = page('accepted')
        obj._refresh_current_page.return_value = accepted
        self.assertTrue(obj.goto_page(page('target'), accepted_pages=(accepted,),
                                     max_steps=1, total_timeout=60))
        self.assertIs(obj.navigator.current_page, accepted)
        obj._execute_transition.assert_not_called()
        self.assertFalse(hasattr(obj, '_navigation_budgets'))

    def test_nested_navigation_cannot_escape_an_active_parent_limit(self):
        obj = task()
        parent = _NavigationBudget('parent', 2, None)
        obj._navigation_budgets = (parent,)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'step limit'):
            obj.goto_page(page('nested'))
        self.assertEqual(parent.steps, 2)
        self.assertEqual(obj._navigation_budgets, (parent,))

    def test_invalid_explicit_limits_are_rejected_before_navigation(self):
        for bounds in ({'max_steps': 0}, {'max_steps': True}, {'max_steps': 1.5},
                       {'total_timeout': 0}, {'total_timeout': float('inf')},
                       {'total_timeout': float('nan')}):
            with self.subTest(bounds=bounds):
                obj = task()
                with self.assertRaises(ValueError):
                    obj.goto_page(page('target'), **bounds)
                obj._refresh_current_page.assert_not_called()
                self.assertFalse(hasattr(obj, '_navigation_budgets'))


if __name__ == '__main__':
    unittest.main()

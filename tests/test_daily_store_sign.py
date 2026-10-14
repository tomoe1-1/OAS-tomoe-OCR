"""A daily store sign is recorded only after its reward closes on a verified page."""
import copy
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import TaskRecoveryFailed
from tasks.DailyTrifles.script_task import ScriptTask
from tasks.DailyTrifles.page import page_store_gift_room
from tasks.GameUi.page import page_main, page_mall_recommend


PREVIOUS_SIGN = datetime(2026, 9, 28, 8, 15)


def subject(done=False, selected=False, fault=None):
    task = ScriptTask.__new__(ScriptTask)
    state = dict(page='gift', selected=selected, frame=0, events=[], fault=fault,
                 tab=(1188, 466), selected_tab=(1212, 580), on_screenshot=None)
    trifles = SimpleNamespace(store_sign=True, buy_sushi_count=-1, one_summon=False,
                              courtyard_affairs=False, pickup_email=False, luck_msg=False)
    task.config = SimpleNamespace(daily_trifles=SimpleNamespace(
        trifles_config=trifles, done_record=SimpleNamespace(store_sign_dt=PREVIOUS_SIGN),
        today_is_done=Mock(return_value=done), guild_donate=SimpleNamespace(enable=False),
        scheduler=SimpleNamespace(next_run=datetime(2026, 10, 2, 12))))
    task.config.save = Mock(side_effect=lambda: state['events'].append(
        ('save', state['frame'], state['page'], task.config.daily_trifles.done_record.store_sign_dt)))
    task.plan_next_dt = Mock()
    task.run_buy_sushi = Mock()
    task.I_GIFT_SIGN = copy.deepcopy(ScriptTask.I_GIFT_SIGN)

    def screenshot():
        state['frame'] += 1
        if state['frame'] > 120:
            raise AssertionError('The sign flow did not stop at its bounds')
        callback = state['on_screenshot']
        if callback:
            callback(state)
        task.device.image[0, 0, 0] = state['frame']
        state['events'].append(('screenshot', state['frame'], state['page']))

    def navigate(page, **kwargs):
        state['events'].append(('navigate', state['frame'], page, kwargs))
        if page is page_store_gift_room:
            state['page'] = 'gift'
        elif page is page_main:
            state['page'] = 'main'
        elif page is page_mall_recommend:
            state['page'] = 'mall'

    def dispatch(kind, x=None, y=None):
        if task.config.daily_trifles.done_record.store_sign_dt != PREVIOUS_SIGN:
            raise AssertionError('The completion date was written before all sign input finished')
        state['events'].append(('input', state['frame'], kind, x, y))
        if kind == 'STORE_DAILY_TAB':
            if state['fault'] == 'tab_blocked':
                return False
            if state['fault'] != 'tab_stalled':
                state['selected'] = True
        elif kind == 'STORE_SIGN_CLAIM':
            if state['fault'] == 'claim_blocked':
                return False
            if state['fault'] != 'claim_stalled':
                state['page'] = 'reward'
        elif kind == 'continue':
            if state['fault'] == 'continue_blocked':
                return False
            if state['fault'] != 'close_stalled':
                state['page'] = 'unknown' if state['fault'] == 'close_unknown' else 'done'
                if state['fault'] == 'close_not_selected':
                    state['selected'] = False
        else:
            raise AssertionError('Unexpected sign control')
        return True

    def click_device(x, y, control_name):
        return dispatch(control_name, x, y)

    def click_rule(rule, **kwargs):
        if rule is not task.C_UI_REWARD:
            raise AssertionError('Only one reward continuation is allowed')
        return dispatch('continue')

    def appear(rule, **kwargs):
        state['events'].append(('appear', state['frame'], rule.name, state['page']))
        if rule is task.I_GIFT_SIGN:
            if state['fault'] == 'sign_missing':
                return False
            # The matcher moves its actionable region to the real sign marker.
            task.I_GIFT_SIGN.roi_front = (830, 210, 100, 80)
            return state['page'] == 'gift' and state['selected']
        if rule is task.I_UI_REWARD:
            return state['page'] == 'reward'
        return False

    task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
        click=Mock(side_effect=click_device), _escape_generation=0,
        performance=SimpleNamespace(timeout=lambda value: value),
        confirm_control_progress=Mock(side_effect=lambda controls:
            state['events'].append(('verified', state['frame'], tuple(controls)))))
    task.screenshot = Mock(side_effect=screenshot)
    task.goto_page = Mock(side_effect=navigate)
    task.appear = Mock(side_effect=appear)
    task.click = Mock(side_effect=click_rule)
    task.appear_then_click = Mock(side_effect=AssertionError('Sign input must not replay in a loop'))
    task.ui_get_reward = Mock(side_effect=AssertionError('Generic repeated reward input is forbidden'))
    return task, state


def simulation(task, state):
    stack = ExitStack()

    def find_tab(_):
        state['events'].append(('find_tab', state['frame'], state['page']))
        if state['fault'] in ('tab_missing', 'tab_ambiguous'):
            return None
        if state['page'] not in ('gift', 'done'):
            return None
        return state['selected_tab'] if state['selected'] else state['tab']

    stack.enter_context(patch('tasks.DailyTrifles.script_task.find_daily_tab', side_effect=find_tab))
    stack.enter_context(patch('tasks.DailyTrifles.script_task.daily_tab_selected',
        side_effect=lambda image: find_tab(image) is not None and state['selected']))
    stack.enter_context(patch('tasks.DailyTrifles.script_task.gift_room_visible',
        side_effect=lambda _: state['page'] in ('gift', 'done') and state['fault'] != 'room_missing'))
    stack.enter_context(patch('tasks.DailyTrifles.script_task.daily_gift_claim_center',
        side_effect=lambda *_: None if state['fault'] == 'claim_label_missing' else (880, 330)))
    stack.enter_context(patch('tasks.DailyTrifles.script_task.monotonic',
                             side_effect=lambda: state['frame'] * 0.1))
    return stack


class DailyStoreSignTests(unittest.TestCase):
    def input_names(self, state):
        return [event[2] for event in state['events'] if event[0] == 'input']

    def assert_not_completed(self, task):
        self.assertEqual(task.config.daily_trifles.done_record.store_sign_dt, PREVIOUS_SIGN)
        task.config.save.assert_not_called()

    def test_complete_sign_uses_each_input_once_and_saves_after_verified_reward_close(self):
        task, state = subject()
        with simulation(task, state):
            task.run_store_sign()
        self.assertEqual(self.input_names(state), ['STORE_DAILY_TAB', 'STORE_SIGN_CLAIM', 'continue'])
        task.device.click.assert_any_call(1188, 466, control_name='STORE_DAILY_TAB')
        task.device.click.assert_any_call(880, 330, control_name='STORE_SIGN_CLAIM')
        task.click.assert_called_once_with(task.C_UI_REWARD, interval=0.1)
        inputs = [event for event in state['events'] if event[0] == 'input']
        for before, after in zip(inputs, inputs[1:]):
            self.assertGreaterEqual(after[1] - before[1], 2)
        last_input = inputs[-1]
        save = next(event for event in state['events'] if event[0] == 'save')
        self.assertGreaterEqual(save[1] - last_input[1], 2)
        self.assertEqual(save[2], 'done')
        self.assertNotEqual(save[3], PREVIOUS_SIGN)
        self.assertEqual(state['events'][-2][0], 'verified')
        task.config.save.assert_called_once()

    def test_tab_position_is_refreshed_before_input_and_after_the_sidebar_moves(self):
        task, state = subject()

        def move(current):
            if not current['selected']:
                current['tab'] = (1205, 510) if current['frame'] == 1 else (1155, 435)

        state['on_screenshot'] = move
        with simulation(task, state):
            task.run_store_sign()
        task.device.click.assert_any_call(1155, 435, control_name='STORE_DAILY_TAB')
        tab = next(event for event in state['events'] if event[0] == 'input')
        reads = [event for event in state['events'] if event[0] == 'find_tab' and event[1] > tab[1]]
        self.assertGreaterEqual(len(reads), 2)
        self.assertEqual(self.input_names(state).count('STORE_DAILY_TAB'), 1)
        task.config.save.assert_called_once()

    def test_already_selected_daily_tab_needs_no_selection_input(self):
        task, state = subject(selected=True)
        with simulation(task, state):
            task.run_store_sign()
        self.assertEqual(self.input_names(state), ['STORE_SIGN_CLAIM', 'continue'])
        task.config.save.assert_called_once()

    def test_already_completed_today_skips_navigation_and_all_input(self):
        task, _ = subject(done=True)
        task.run_store_sign()
        task.goto_page.assert_not_called()
        task.screenshot.assert_not_called()
        task.device.click.assert_not_called()
        task.click.assert_not_called()
        self.assert_not_completed(task)

    def test_missing_or_ambiguous_tab_and_missing_room_never_send_input(self):
        for fault in ('tab_missing', 'tab_ambiguous', 'room_missing'):
            with self.subTest(fault=fault):
                task, state = subject(fault=fault)
                with simulation(task, state):
                    with self.assertRaises(TaskRecoveryFailed):
                        task.run_store_sign()
                task.device.click.assert_not_called()
                task.click.assert_not_called()
                self.assertLessEqual(task.screenshot.call_count, 24)
                self.assert_not_completed(task)

    def test_tab_disappearing_at_input_time_does_not_click_a_stale_position(self):
        task, state = subject()
        with simulation(task, state), \
                patch('tasks.DailyTrifles.script_task.find_daily_tab',
                      side_effect=[(1188, 466), (1188, 466), None]):
            with self.assertRaises(TaskRecoveryFailed):
                task.run_store_sign()
        task.device.click.assert_not_called()
        self.assert_not_completed(task)

    def test_tab_selection_failure_is_not_reclicked_or_marked_complete(self):
        task, state = subject(fault='tab_stalled')
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.run_store_sign()
        self.assertEqual(self.input_names(state), ['STORE_DAILY_TAB'])
        task.click.assert_not_called()
        self.assert_not_completed(task)

    def test_missing_sign_marker_does_not_send_claim_input(self):
        task, state = subject(selected=True, fault='sign_missing')
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.run_store_sign()
        task.device.click.assert_not_called()
        task.click.assert_not_called()
        self.assert_not_completed(task)

    def test_missing_daily_gift_title_or_claim_text_does_not_send_input(self):
        task, state = subject(selected=True, fault='claim_label_missing')
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.run_store_sign()
        task.device.click.assert_not_called()
        self.assert_not_completed(task)

    def test_claim_without_reward_overlay_is_not_retried_or_marked_complete(self):
        task, state = subject(fault='claim_stalled')
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.run_store_sign()
        self.assertEqual(self.input_names(state), ['STORE_DAILY_TAB', 'STORE_SIGN_CLAIM'])
        task.click.assert_not_called()
        self.assert_not_completed(task)

    def test_still_visible_reward_unknown_destination_or_unselected_tab_preserves_date(self):
        for fault in ('close_stalled', 'close_unknown', 'close_not_selected'):
            with self.subTest(fault=fault):
                task, state = subject(fault=fault)
                with simulation(task, state):
                    with self.assertRaises(TaskRecoveryFailed):
                        task.run_store_sign()
                self.assertEqual(self.input_names(state),
                                 ['STORE_DAILY_TAB', 'STORE_SIGN_CLAIM', 'continue'])
                task.click.assert_called_once()
                self.assert_not_completed(task)

    def test_blocked_inputs_preserve_date_and_stop_at_the_blocked_control(self):
        for fault, expected in (
            ('tab_blocked', ['STORE_DAILY_TAB']),
            ('claim_blocked', ['STORE_DAILY_TAB', 'STORE_SIGN_CLAIM']),
            ('continue_blocked', ['STORE_DAILY_TAB', 'STORE_SIGN_CLAIM', 'continue'])):
            with self.subTest(fault=fault):
                task, state = subject(fault=fault)
                with simulation(task, state):
                    with self.assertRaises(TaskRecoveryFailed):
                        task.run_store_sign()
                self.assertEqual(self.input_names(state), expected)
                self.assert_not_completed(task)

    def test_full_store_flow_has_all_three_navigation_bounds_and_sushi_remains_disabled(self):
        task, state = subject()
        with simulation(task, state):
            task.run_store()
        self.assertEqual([call.args[0] for call in task.goto_page.call_args_list],
                         [page_mall_recommend, page_store_gift_room, page_main])
        for call in task.goto_page.call_args_list:
            self.assertEqual(call.kwargs['max_steps'], 12)
            self.assertEqual(call.kwargs['total_timeout'], 60)
            self.assertIs(call.kwargs['skip_first_screenshot'], False)
        task.run_buy_sushi.assert_not_called()
        self.assertEqual(task.config.daily_trifles.trifles_config.buy_sushi_count, -1)
        task.config.save.assert_called_once()

    def test_store_already_done_skips_every_navigation(self):
        task, _ = subject(done=True)
        task.run_store()
        task.goto_page.assert_not_called()
        task.device.click.assert_not_called()
        task.run_buy_sushi.assert_not_called()

    def test_failed_store_sign_propagates_retryable_failure_instead_of_task_completion(self):
        task, state = subject(fault='claim_stalled')
        original_schedule = task.config.daily_trifles.scheduler.next_run
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed) as error:
                task.run()
        self.assertIsInstance(error.exception.__cause__, TaskRecoveryFailed)
        self.assertEqual(task.config.daily_trifles.done_record.store_sign_dt, PREVIOUS_SIGN)
        task.config.save.assert_called()
        task.plan_next_dt.assert_not_called()
        self.assertTrue(task.config.daily_trifles.trifles_config.store_sign)
        self.assertEqual(task.config.daily_trifles.scheduler.next_run, original_schedule)
        self.assertEqual(self.input_names(state), ['STORE_DAILY_TAB', 'STORE_SIGN_CLAIM'])

    def test_later_non_store_failure_preserves_earlier_daily_date(self):
        task, _ = subject()
        con = task.config.daily_trifles.trifles_config
        con.one_summon = con.courtyard_affairs = True
        completed = datetime(2026, 10, 2, 8)
        task.run_one_summon = Mock(side_effect=lambda: setattr(
            task.config.daily_trifles.done_record, 'summon_dt', completed))
        task.run_courtyard_affairs = Mock(side_effect=TaskRecoveryFailed('navigation limit'))
        saved = []
        task.config.save.side_effect = lambda: saved.append(
            dict(vars(task.config.daily_trifles.done_record)))
        with self.assertRaises(TaskRecoveryFailed):
            task.run()
        self.assertEqual(saved[-1]['summon_dt'], completed)
        self.assertEqual(saved[-1]['store_sign_dt'], PREVIOUS_SIGN)
        task.plan_next_dt.assert_not_called()

    def test_bounded_navigation_failure_remains_retryable(self):
        task, _ = subject()
        task.goto_page.side_effect = TaskRecoveryFailed('navigation limit reached')
        task.run_store_sign = Mock()
        with self.assertRaises(TaskRecoveryFailed) as error:
            task.run_store()
        self.assertIsInstance(error.exception.__cause__, TaskRecoveryFailed)
        task.run_store_sign.assert_not_called()
        task.goto_page.assert_called_once()
        self.assertEqual(task.config.daily_trifles.done_record.store_sign_dt, PREVIOUS_SIGN)
        task.config.save.assert_called_once()

    def test_failed_sign_saves_other_completed_daily_progress_without_forging_sign_date(self):
        task, state = subject(fault='tab_missing')
        prior_summon = datetime(2026, 10, 2, 8)
        prior_mail = datetime(2026, 10, 2, 8, 1)
        task.config.daily_trifles.done_record.summon_dt = prior_summon
        task.config.daily_trifles.done_record.pickup_email_dt = prior_mail
        saved = []
        task.config.save.side_effect = lambda: saved.append(
            dict(vars(task.config.daily_trifles.done_record)))
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.run_store()
        self.assertEqual(saved, [dict(store_sign_dt=PREVIOUS_SIGN,
                                    summon_dt=prior_summon, pickup_email_dt=prior_mail)])
        task.device.click.assert_not_called()
        task.plan_next_dt.assert_not_called()

    def test_return_navigation_failure_stops_runner_after_preserving_claimed_date(self):
        task, _ = subject()
        claimed = datetime(2026, 10, 2, 8)

        def complete_sign():
            task.config.daily_trifles.done_record.store_sign_dt = claimed

        task.run_store_sign = Mock(side_effect=complete_sign)
        task.goto_page.side_effect = [None, TaskRecoveryFailed('main did not verify')]
        with self.assertRaises(TaskRecoveryFailed):
            task.run_store()
        self.assertEqual(task.config.daily_trifles.done_record.store_sign_dt, claimed)
        task.run_store_sign.assert_called_once()
        task.config.save.assert_called_once()


class DailyStoreWaitTests(unittest.TestCase):
    def test_frozen_clock_still_stops_after_24_fresh_frames(self):
        task, _ = subject()
        predicate = Mock(return_value=False)
        with patch('tasks.DailyTrifles.script_task.monotonic', return_value=0):
            with self.assertRaises(TaskRecoveryFailed):
                task._store_wait(predicate, 'test destination')
        self.assertEqual(task.screenshot.call_count, 24)
        self.assertEqual(predicate.call_count, 24)
        task.device.confirm_control_progress.assert_not_called()

    def test_time_limit_also_stops_before_frame_limit(self):
        task, _ = subject()
        clock = iter(range(100))
        with patch('tasks.DailyTrifles.script_task.monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(TaskRecoveryFailed):
                task._store_wait(lambda: False, 'test destination')
        self.assertGreater(task.screenshot.call_count, 0)
        self.assertLess(task.screenshot.call_count, 24)
        task.device.confirm_control_progress.assert_not_called()

    def test_only_two_consecutive_positive_frames_confirm_progress(self):
        task, _ = subject()
        predicate = Mock(side_effect=[True, False, True, True])
        with patch('tasks.DailyTrifles.script_task.monotonic', return_value=0):
            self.assertTrue(task._store_wait(predicate, 'test destination', ['test_control']))
        self.assertEqual(task.screenshot.call_count, 4)
        task.device.confirm_control_progress.assert_called_once_with(['test_control'])

    def test_escape_recovery_interrupts_verification_before_accepting_a_match(self):
        task, state = subject()
        state['on_screenshot'] = lambda _: setattr(task.device, '_escape_generation', 1)
        predicate = Mock(return_value=True)
        with patch('tasks.DailyTrifles.script_task.monotonic', return_value=0):
            with self.assertRaises(TaskRecoveryFailed):
                task._store_wait(predicate, 'test destination')
        self.assertEqual(task.screenshot.call_count, 1)
        predicate.assert_not_called()
        task.device.confirm_control_progress.assert_not_called()
        task.config.save.assert_not_called()


if __name__ == '__main__':
    unittest.main()

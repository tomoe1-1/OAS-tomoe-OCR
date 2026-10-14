"""A reward overlay must close before any visible FrogBoss background control."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.atom.click import RuleClick
from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.FrogBoss.script_task import ScriptTask


PROMPT = RuleClick(roi_front=(639, 693, 1, 1), roi_back=(639, 693, 1, 1),
                   name='SETTLEMENT_CONTINUE')


class ExpiredTimer:
    def __init__(self, *args):
        pass

    def start(self):
        return self

    def reached(self):
        return True


def subject(initial='overlay-next', stalled=False, prompt_delay=0):
    task = ScriptTask.__new__(ScriptTask)
    state = {'page': initial, 'delay': prompt_delay}
    visible = {
        'next': (task.I_NEXT_COMPETITION,),
        'choices': (task.I_BET_LEFT, task.I_BET_RIGHT),
        'amount': (task.I_BET_SURE, task.I_GOLD_30),
        'selected': (task.I_BET_SURE, task.I_GOLD_30, task.I_GOLD_30_CHECK),
        'confirm': (task.I_BET_SURE, task.I_GOLD_30, task.I_UI_CONFIRM),
        'done': (task.I_BETTED,),
        'rest': (task.I_FROG_BOSS_REST,),
        'win': (task.I_BET_SUCCESS, task.I_BET_SUCCESS_BOX),
        'lose': (task.I_BET_FAILURE, task.I_NEXT_COMPETITION),
        'unknown': (),
    }

    def appear(rule):
        page = state['page']
        if page.startswith('overlay-'):
            return rule is task.I_REWARD or rule in visible[page[8:]]
        return rule in visible[page]

    def continuation(_):
        if not state['page'].startswith('overlay-'):
            return None
        if state['delay']:
            state['delay'] -= 1
            return None
        return PROMPT

    transitions = {task.I_NEXT_COMPETITION.name: 'choices', task.I_BET_RIGHT.name: 'amount',
                   task.I_GOLD_30.name: 'selected', task.I_BET_SURE.name: 'confirm',
                   task.I_UI_CONFIRM.name: 'done'}

    def click(x, y, control_name):
        if control_name == PROMPT.name:
            if not stalled:
                state['page'] = state['page'][8:]
        else:
            if state['page'].startswith('overlay-'):
                raise AssertionError('Background input before reward overlay dismissal')
            state['page'] = transitions[control_name]

    task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                  click=Mock(side_effect=click))
    task.appear = appear
    task.screenshot = Mock()
    task.enter = Mock()
    task._choose_bet = Mock(return_value=task.I_BET_RIGHT)
    task.record_oas_result = Mock()
    task.detect = Mock(return_value=True)
    task.next_run = Mock()

    def verify(predicate, controls):
        for _ in range(2):
            task.screenshot()
            if not predicate():
                raise TaskRecoveryFailed('No verified destination')
        return True

    task._ui_verify = Mock(side_effect=verify)
    return task, state, continuation


class FrogBossRewardOverlayTests(unittest.TestCase):
    def names(self, task):
        return [call.kwargs['control_name'] for call in task.device.click.call_args_list]

    def test_existing_overlay_on_reentry_closes_before_next_round(self):
        task, _, continuation = subject()
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            with self.assertRaises(TaskEnd):
                task.run()
        self.assertEqual(self.names(task)[:2], [PROMPT.name, task.I_NEXT_COMPETITION.name])
        self.assertEqual(self.names(task).count(PROMPT.name), 1)
        task.device.click.assert_any_call(639, 693, control_name=PROMPT.name)
        task.enter.assert_not_called()
        task.next_run.assert_called_once()

    def test_reward_precedes_betted_rest_and_betting_choices(self):
        for background in ('done', 'rest', 'choices'):
            with self.subTest(background=background):
                task, _, continuation = subject('overlay-' + background)
                with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
                    with self.assertRaises(TaskEnd):
                        task.run()
                self.assertEqual(self.names(task)[0], PROMPT.name)
                task.next_run.assert_called_once()

    def test_win_inner_loop_clears_new_reward_before_its_background_next_button(self):
        task, state, continuation = subject('win')
        opened = []

        def open_box(rule, interval):
            if rule is task.I_BET_SUCCESS_BOX and state['page'] == 'win':
                opened.append(rule)
                state['page'] = 'overlay-next'
                return True
            return False

        task.appear_then_click = Mock(side_effect=open_box)
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            with self.assertRaises(TaskEnd):
                task.run()
        self.assertEqual(self.names(task)[:2], [PROMPT.name, task.I_NEXT_COMPETITION.name])
        self.assertEqual(opened, [task.I_BET_SUCCESS_BOX])
        self.assertTrue(all(call.args == (task.I_BET_SUCCESS_BOX,)
                            for call in task.appear_then_click.call_args_list))

    def test_background_input_guard_rechecks_the_new_frame(self):
        task, _, continuation = subject()
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            task._advance_competition()
        self.assertEqual(self.names(task), [PROMPT.name, task.I_NEXT_COMPETITION.name])
        self.assertGreaterEqual(task.screenshot.call_count, 5)

    def test_direct_bet_checks_overlay_before_background_already_betted(self):
        task, _, continuation = subject('overlay-done')
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            task.do_bet()
        self.assertEqual(self.names(task), [PROMPT.name])
        task._choose_bet.assert_not_called()

    def test_prompt_animation_waits_with_fresh_frames_without_background_input(self):
        task, _, continuation = subject(prompt_delay=2)
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            self.assertTrue(task._clear_frog_reward_overlay())
        self.assertEqual(self.names(task), [PROMPT.name])
        self.assertEqual(task.screenshot.call_count, 4)

    def test_missing_or_ambiguous_prompt_cannot_fall_back_to_next_or_random_input(self):
        task, _, _ = subject()
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', return_value=None), \
                patch('tasks.FrogBoss.script_task.Timer', ExpiredTimer):
            with self.assertRaises(TaskRecoveryFailed):
                task.run()
        task.device.click.assert_not_called()
        task.next_run.assert_not_called()

    def test_no_overlay_sends_no_reward_input(self):
        task, _, continuation = subject('next')
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            self.assertFalse(task._clear_frog_reward_overlay())
        task.device.click.assert_not_called()
        task._ui_verify.assert_not_called()

    def test_still_visible_overlay_is_not_reclicked_or_completed(self):
        task, _, continuation = subject(stalled=True)
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            with self.assertRaises(TaskRecoveryFailed):
                task.run()
        self.assertEqual(self.names(task), [PROMPT.name])
        task.next_run.assert_not_called()

    def test_prompt_ocr_loss_does_not_verify_a_still_visible_reward_bag(self):
        task, _, continuation = subject(stalled=True)
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', return_value=None):
            self.assertFalse(task._frog_reward_overlay_closed())
        task.device.click.assert_not_called()

    def test_after_click_ocr_loss_with_bag_still_visible_preserves_unfinished_task(self):
        task, _, _ = subject(stalled=True)
        reads = iter([PROMPT, None, None])
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=lambda _: next(reads)):
            with self.assertRaises(TaskRecoveryFailed):
                task._clear_frog_reward_overlay()
        self.assertEqual(self.names(task), [PROMPT.name])
        task.next_run.assert_not_called()

    def test_absent_bag_without_a_verified_frog_destination_cannot_complete(self):
        task, _, continuation = subject('overlay-unknown')
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', side_effect=continuation):
            with self.assertRaises(TaskRecoveryFailed):
                task._clear_frog_reward_overlay()
        self.assertEqual(self.names(task), [PROMPT.name])
        task.next_run.assert_not_called()

    def test_continue_text_still_visible_after_bag_disappears_is_not_closed(self):
        task, _, _ = subject('next')
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', return_value=PROMPT):
            self.assertFalse(task._frog_reward_overlay_closed())

    def test_real_bag_template_blocks_next_marker_even_when_ocr_reads_nothing(self):
        task, _, _ = subject('next')
        task.I_REWARD = copy.deepcopy(ScriptTask.I_REWARD)
        task.I_NEXT_COMPETITION = copy.deepcopy(ScriptTask.I_NEXT_COMPETITION)
        for rule in (task.I_REWARD, task.I_NEXT_COMPETITION):
            x, y, width, height = rule.roi_front
            task.device.image[y:y + height, x:x + width] = rule.image
        task.appear = lambda rule: (rule in (task.I_REWARD, task.I_NEXT_COMPETITION)
                                    and rule.template_match(task.device.image))
        with patch('tasks.FrogBoss.script_task.settlement_continue_click', return_value=None):
            self.assertFalse(task._frog_reward_overlay_closed())
            x, y, width, height = task.I_REWARD.roi_back
            task.device.image[y:y + height, x:x + width] = 0
            self.assertTrue(task._frog_reward_overlay_closed())


if __name__ == '__main__':
    unittest.main()

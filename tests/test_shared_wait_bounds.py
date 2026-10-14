"""Shared waits and popup helpers must fail finitely without claiming progress.

Extract production methods without importing Config, device drivers or image RPC.
Only screenshots, elapsed time and control delivery are simulated.
"""
import ast
from datetime import datetime
from enum import Enum
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
from unittest.mock import Mock

import numpy as np

from module.device import click_verification
from module.device.click_verification import after_click, before_click
from module.device.confirm_retry import click_confirm_once
from module.exception import ScriptError, TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]


def screen_image(scene):
    """A screen the real click fence can compare before and after an input."""
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


class NamedRule:
    def __init__(self, name):
        self.name = name

    def coord(self):
        return 640, 680


class RuleImage(NamedRule):
    pass


class RuleOcr(NamedRule):
    pass


class RuleGif(RuleImage):
    pass


class RuleClick(NamedRule):
    pass


class RuleAnimate(RuleImage):
    pass


class FriendInvitation(str, Enum):
    ACCEPT = 'accept'
    REJECT = 'reject'
    ONLY_JADE = 'only_jade'
    JADE_AND_FOOD = 'jade_and_food'
    IGNORE = 'ignore'


def subject(step=1, multiplier=1):
    state = dict(frame=0, now=0.0, events=[], visible=set(), callback=None,
                 blocked=False, keep_popup=False, click_error=None, scene=0)

    class FrameTimer:
        def __init__(self, limit, count=0):
            self.limit = limit
            self.count = count
            self.started = state['now']

        def start(self):
            self.started = state['now']
            return self

        def reset(self):
            return self.start()

        def reached(self):
            return state['now'] - self.started >= self.limit

    methods = {'_default_wait_timeout', 'wait_until_appear', 'wait_until_disappear',
               'wait_animate_stable', 'ui_get_reward', 'ui_reward_appear_click',
               '_dismiss_reward_once',
               '_ui_action_once', '_ui_verify', '_burst',
               '_invitation_identity', '_invitation_identity_changed'}
    source = ast.parse((ROOT / 'tasks/base_task.py').read_text(encoding='utf-8'))
    original = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'BaseTask')
    extracted = ast.ClassDef(name='Subject', bases=[], keywords=[], decorator_list=[],
                             body=[n for n in original.body
                                   if isinstance(n, ast.FunctionDef) and n.name in methods])
    namespace = dict(Timer=FrameTimer, monotonic=lambda: state['now'], isfinite=isfinite,
                     datetime=datetime, logger=Mock(), RuleImage=RuleImage, RuleOcr=RuleOcr,
                     RuleGif=RuleGif, RuleClick=RuleClick, RuleAnimate=RuleAnimate,
                     TaskRecoveryFailed=TaskRecoveryFailed, ScriptError=ScriptError,
                     FriendInvitation=FriendInvitation,
                     click_confirm_once=click_confirm_once)
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
                                            names=[ast.alias(name='annotations')], level=0),
                              extracted], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'shared_base_task', 'exec'), namespace)
    obj = namespace['Subject']()
    obj.config = SimpleNamespace(global_game=SimpleNamespace(
        battle=SimpleNamespace(battle_timeout=300),
        emergency=SimpleNamespace(friend_invitation=FriendInvitation.ACCEPT)))
    obj.interval_timer = {}
    obj.I_UI_REWARD, obj.C_UI_REWARD = RuleImage('REWARD'), RuleClick('CONTINUE')
    for name in ('I_G_ACCEPT', 'I_G_REJECT', 'I_G_IGNORE', 'I_G_JADE',
                 'I_G_CAT_FOOD', 'I_G_DOG_FOOD'):
        setattr(obj, name, RuleImage(name))
    obj.device = SimpleNamespace(image=screen_image(0), image_frame_id=0,
                                 _escape_generation=0,
                                 performance=SimpleNamespace(timeout=lambda value: value * multiplier),
                                 detect_record={'BATTLE_GUARD'})

    def screenshot(kind='screenshot'):
        state['frame'] += 1
        state['now'] += step
        if state['frame'] > 2000:
            raise AssertionError('Shared helper did not terminate')
        obj.device.image = screen_image(state['scene'])
        obj.device.image_frame_id = state['frame']
        state['events'].append((kind, state['frame']))
        if state['callback']:
            state['callback'](obj, state)

    def dispatch(control, **kwargs):
        state['events'].append(('input', state['frame'], control.name))
        obj.device.detect_record = {'POPUP_INPUT'}
        if state['click_error']:
            raise state['click_error']
        if state['blocked']:
            return False
        if not state['keep_popup']:
            # Both the legacy reward image and the audited dismiss control close it.
            if control is obj.I_UI_REWARD or control is obj.C_UI_REWARD:
                state['visible'].discard('REWARD')
                state['scene'] += 1
            elif control.name.startswith('I_G_'):
                state['visible'].difference_update({'I_G_ACCEPT', 'I_G_REJECT', 'I_G_IGNORE'})
                state['scene'] += 1
        return True

    def appear_then_click(control, **kwargs):
        if control.name not in state['visible']:
            return False
        return dispatch(control, **kwargs)

    known_controls = {rule.name: rule for rule in
                      [obj.I_UI_REWARD, obj.C_UI_REWARD,
                       *(getattr(obj, name) for name in ('I_G_ACCEPT', 'I_G_REJECT', 'I_G_IGNORE'))]}

    def device_click(x, y, control_name=None, **kwargs):
        """The confirmation retry drives the real click fence, not a shortcut."""
        control = known_controls.get(control_name) or RuleClick(control_name)
        before = before_click(obj.device, x, y, control_name)
        if dispatch(control, **kwargs):
            after_click(obj.device, x, y, control_name, before)
            return True
        return False

    obj.screenshot = Mock(side_effect=screenshot)
    obj.device.screenshot = Mock(side_effect=lambda: screenshot('device_screenshot'))
    obj.device.click = device_click
    obj.appear = Mock(side_effect=lambda control, **kwargs: control.name in state['visible'])
    obj.ocr_appear = Mock(side_effect=lambda control, **kwargs: control.name in state['visible'])
    obj.appear_then_click = Mock(side_effect=appear_then_click)
    obj.ocr_appear_click = Mock(side_effect=appear_then_click)
    obj.click = Mock(side_effect=dispatch)
    obj.device.confirm_control_progress = Mock(side_effect=lambda controls:
        state['events'].append(('verified', state['frame'], tuple(rule.name for rule in controls))))
    obj.set_next_run = Mock(side_effect=lambda **kwargs:
        state['events'].append(('scheduled', state['frame'], kwargs['task'])))
    obj.clock = VirtualClock()
    return obj, state


def input_events(state, name=None):
    return [event for event in state['events']
            if event[0] == 'input' and (name is None or event[2] == name)]


class SharedWaitBoundsTests(unittest.TestCase):
    def setUp(self):
        # The confirmation fence has real 8s deadlines; keep them virtual.
        self.clock = VirtualClock()
        patcher = mock.patch.object(click_verification, 'time', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_wait_uses_longest_valid_battle_budget(self):
        obj, _ = subject()
        obj.config.global_game.battle.battle_timeout = 900
        obj._battle_context = SimpleNamespace(battle_timer=SimpleNamespace(limit=1200))
        self.assertEqual(obj._default_wait_timeout(), 1200)
        obj._battle_context.battle_timer.limit = 500
        self.assertEqual(obj._default_wait_timeout(), 900)
        obj.config.global_game.battle.battle_timeout = 300
        self.assertEqual(obj._default_wait_timeout(), 500)

    def test_invalid_battle_budgets_cannot_make_default_wait_unbounded(self):
        for value in (None, -1, 0, float('nan'), float('inf'), 'invalid'):
            with self.subTest(value=value):
                obj, _ = subject()
                obj.config.global_game.battle.battle_timeout = value
                obj._battle_context = SimpleNamespace(battle_timer=SimpleNamespace(limit=value))
                self.assertEqual(obj._default_wait_timeout(), 420)

    def test_implicit_appear_disappear_and_animation_timeouts_raise_retained_failure(self):
        for kind in ('appear', 'disappear', 'animate'):
            with self.subTest(kind=kind):
                obj, state = subject(step=60)
                target = RuleAnimate('TARGET') if kind == 'animate' else RuleImage('TARGET')
                if kind == 'disappear':
                    state['visible'].add('TARGET')
                if kind == 'animate':
                    target.stable = Mock(return_value=False)
                method = {'appear': obj.wait_until_appear, 'disappear': obj.wait_until_disappear,
                          'animate': obj.wait_animate_stable}[kind]
                with self.assertRaises(TaskRecoveryFailed):
                    method(target)
                self.assertEqual(state['frame'], 7)
                self.assertEqual(input_events(state), [])
                obj.device.confirm_control_progress.assert_not_called()

    def test_explicit_appear_disappear_and_animation_timeouts_remain_false(self):
        for kind in ('appear', 'disappear', 'animate'):
            with self.subTest(kind=kind):
                obj, state = subject()
                target = RuleAnimate('TARGET') if kind == 'animate' else RuleImage('TARGET')
                if kind == 'disappear':
                    state['visible'].add('TARGET')
                if kind == 'animate':
                    target.stable = Mock(return_value=False)
                if kind == 'appear':
                    result = obj.wait_until_appear(target, wait_time=3)
                elif kind == 'disappear':
                    result = obj.wait_until_disappear(target, timeout=3)
                else:
                    result = obj.wait_animate_stable(target, timeout=3)
                self.assertFalse(result)
                self.assertEqual(state['frame'], 3)
                self.assertEqual(input_events(state), [])

    def test_long_battle_can_finish_after_default_floor_without_failure(self):
        for kind in ('appear', 'disappear', 'animate'):
            with self.subTest(kind=kind):
                obj, state = subject(step=100)
                obj.config.global_game.battle.battle_timeout = 800
                target = RuleAnimate('TARGET') if kind == 'animate' else RuleImage('TARGET')
                if kind == 'disappear':
                    state['visible'].add('TARGET')

                def update(task, current):
                    if current['frame'] == 6:
                        if kind == 'disappear':
                            current['visible'].discard('TARGET')
                        else:
                            current['visible'].add('TARGET')

                state['callback'] = update
                if kind == 'animate':
                    target.stable = Mock(side_effect=lambda *args, **kwargs: state['frame'] >= 6)
                method = {'appear': obj.wait_until_appear, 'disappear': obj.wait_until_disappear,
                          'animate': obj.wait_animate_stable}[kind]
                self.assertTrue(method(target))
                self.assertEqual(state['now'], 600)

    def test_low_spec_wait_budget_is_scaled_once(self):
        obj, state = subject(step=60, multiplier=3)
        with self.assertRaises(TaskRecoveryFailed):
            obj.wait_until_appear(RuleImage('TARGET'))
        self.assertEqual(state['now'], 1260)

    def test_existing_frame_can_satisfy_explicit_skip_without_extra_capture(self):
        obj, state = subject()
        state['visible'].add('TARGET')
        self.assertTrue(obj.wait_until_appear(RuleImage('TARGET'), skip_first_screenshot=True))
        obj.screenshot.assert_not_called()

    def test_ocr_wait_keeps_existing_success_contract(self):
        obj, state = subject()
        state['visible'].add('OCR')
        self.assertTrue(obj.wait_until_appear(RuleOcr('OCR')))
        obj.ocr_appear.assert_called_once()

    def test_reward_timeout_after_one_source_input_is_a_retained_failure(self):
        obj, state = subject()
        state['visible'].add('SOURCE')
        with self.assertRaises(TaskRecoveryFailed):
            obj.ui_get_reward(RuleImage('SOURCE'))
        self.assertEqual(input_events(state, 'SOURCE'), [('input', 1, 'SOURCE')])
        self.assertEqual(state['now'], 10)
        obj.device.confirm_control_progress.assert_not_called()

    def test_absent_reward_and_absent_source_never_claim_success_or_dispatch(self):
        obj, state = subject()
        self.assertFalse(obj.ui_get_reward(RuleImage('SOURCE')))
        self.assertEqual(input_events(state), [])
        obj.device.confirm_control_progress.assert_not_called()

    def test_delayed_reward_is_closed_once_then_verified_in_two_new_frames(self):
        obj, state = subject()
        state['visible'].add('SOURCE')

        def update(task, current):
            if current['frame'] == 4:
                current['visible'].add('REWARD')

        state['callback'] = update
        self.assertTrue(obj.ui_get_reward(RuleImage('SOURCE')))
        # The already displayed reward is closed through its audited dismiss
        # control; the source claim button is never re-clicked.
        self.assertEqual([event[2] for event in input_events(state)], ['SOURCE', 'CONTINUE'])
        verified = next(event for event in state['events'] if event[0] == 'verified')
        self.assertEqual(verified[1], 9)
        # Both audited controls back the reward, not the source claim button.
        self.assertEqual(verified[2], ('CONTINUE', 'REWARD'))

    def test_existing_reward_does_not_click_source(self):
        obj, state = subject()
        state['visible'].update({'SOURCE', 'REWARD'})
        self.assertTrue(obj.ui_get_reward(RuleImage('SOURCE')))
        self.assertEqual([event[2] for event in input_events(state)], ['CONTINUE'])
        self.assertEqual(state['frame'], 8)

    def test_stuck_reward_raises_failure_after_one_dismissal_and_one_retry(self):
        obj, state = subject()
        state['visible'].add('REWARD')
        state['keep_popup'] = True
        with self.assertRaises(TaskRecoveryFailed):
            obj.ui_get_reward(RuleImage('SOURCE'))
        # One dismissal plus the single bounded retry, then a retained failure.
        self.assertEqual([event[2] for event in input_events(state)], ['CONTINUE', 'CONTINUE'])
        obj.device.confirm_control_progress.assert_not_called()

    def test_transient_reward_disappearance_does_not_satisfy_two_frame_confirmation(self):
        obj, state = subject()
        state['visible'].add('REWARD')

        def update(task, current):
            if current['frame'] == 4:
                current['visible'].add('REWARD')
            elif current['frame'] == 5:
                current['visible'].discard('REWARD')

        state['callback'] = update
        self.assertTrue(obj.ui_get_reward(RuleImage('SOURCE')))
        self.assertEqual([event[2] for event in input_events(state)], ['CONTINUE'])
        self.assertEqual(state['frame'], 8)
        # Dismiss confirmation and disappearance confirmation are separate stages.
        self.assertEqual(obj.device.confirm_control_progress.call_count, 2)

    def test_recovery_interrupting_reward_confirmation_is_failure(self):
        obj, state = subject()
        state['visible'].add('REWARD')

        def update(task, current):
            if current['frame'] == 3:
                task.device._escape_generation += 1

        state['callback'] = update
        with self.assertRaisesRegex(TaskRecoveryFailed, 'interrupted'):
            obj.ui_get_reward(RuleImage('SOURCE'))
        # Recovery is re-checked before the input, so nothing is dispatched.
        self.assertEqual([event[2] for event in input_events(state)], ['CONTINUE'])
        obj.device.confirm_control_progress.assert_not_called()

    def test_invitation_accept_uses_direct_captures_one_click_then_two_fresh_frames(self):
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        guard = obj.device.detect_record
        self.assertTrue(obj._burst())
        obj.screenshot.assert_not_called()
        self.assertEqual(input_events(state), [('input', 1, 'I_G_ACCEPT')])
        self.assertEqual(obj.device.screenshot.call_count, 3)
        self.assertEqual(obj.device.detect_record, guard)
        verified, scheduled = state['events'][-2:]
        self.assertEqual(verified, ('verified', 3, ('I_G_ACCEPT',)))
        self.assertEqual(scheduled, ('scheduled', 3, 'WantedQuests'))

    def test_invitation_replaced_without_hiding_accept_counts_as_closed(self):
        import numpy as np
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        state['keep_popup'] = True

        def device_screenshot():
            state['frame'] += 1
            state['now'] += 1
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            if state['frame'] >= 3:
                frame[240:276, 578:772] = 255
            obj.device.image = frame
            obj.device.image_frame_id = state['frame']
            state['events'].append(('device_screenshot', state['frame']))
            if state['callback']:
                state['callback'](obj, state)

        obj.device.screenshot = Mock(side_effect=device_screenshot)
        obj.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertTrue(obj._burst())
        self.assertEqual(input_events(state), [('input', 1, 'I_G_ACCEPT')])
        obj.set_next_run.assert_called_once()
        self.assertEqual(obj.set_next_run.call_args.kwargs['task'], 'WantedQuests')
        self.assertIs(obj.set_next_run.call_args.kwargs['server'], False)
        self.assertEqual(obj.device._accepted_invitation_generation, 1)

    def test_rejected_or_ignored_invitation_does_not_schedule_wanted_quests(self):
        for choice, expected in ((FriendInvitation.REJECT, 'I_G_REJECT'),
                                 (FriendInvitation.IGNORE, 'I_G_IGNORE'),
                                 (FriendInvitation.ONLY_JADE, 'I_G_IGNORE'),
                                 (FriendInvitation.JADE_AND_FOOD, 'I_G_IGNORE')):
            with self.subTest(choice=choice):
                obj, state = subject()
                obj.config.global_game.emergency.friend_invitation = choice
                state['visible'].update({'I_G_ACCEPT', 'I_G_REJECT', 'I_G_IGNORE'})
                guard = obj.device.detect_record
                self.assertTrue(obj._burst())
                self.assertEqual(input_events(state), [('input', 1, expected)])
                self.assertEqual(obj.device.detect_record, guard)
                obj.set_next_run.assert_not_called()

    def test_invitation_needs_both_original_and_selected_buttons_to_disappear(self):
        obj, state = subject()
        obj.config.global_game.emergency.friend_invitation = FriendInvitation.REJECT
        state['visible'].update({'I_G_ACCEPT', 'I_G_REJECT'})
        state['keep_popup'] = True
        guard = obj.device.detect_record

        def update(task, current):
            if current['frame'] >= 2:
                current['visible'].discard('I_G_ACCEPT')

        state['callback'] = update
        with self.assertRaises(TaskRecoveryFailed):
            obj._burst()
        self.assertEqual(input_events(state), [('input', 1, 'I_G_REJECT')])
        self.assertEqual(obj.device.detect_record, guard)
        obj.device.confirm_control_progress.assert_not_called()
        obj.set_next_run.assert_not_called()

    def test_stuck_accepted_invitation_preserves_guard_and_does_not_schedule(self):
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        state['keep_popup'] = True
        guard = obj.device.detect_record
        with self.assertRaises(TaskRecoveryFailed):
            obj._burst()
        self.assertEqual(input_events(state), [('input', 1, 'I_G_ACCEPT')])
        self.assertEqual(state['now'], 12)
        self.assertEqual(obj.device.detect_record, guard)
        obj.set_next_run.assert_not_called()

    def test_invitation_click_exception_always_restores_battle_guard(self):
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        state['click_error'] = TaskRecoveryFailed('dispatch failed')
        guard = obj.device.detect_record
        with self.assertRaisesRegex(TaskRecoveryFailed, 'dispatch failed'):
            obj._burst()
        self.assertEqual(obj.device.detect_record, guard)
        obj.set_next_run.assert_not_called()

    def test_invitation_that_vanishes_before_dispatch_has_no_input_or_followup(self):
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        state['callback'] = lambda task, current: current['visible'].clear()
        self.assertTrue(obj._burst())
        self.assertEqual(input_events(state), [])
        self.assertEqual(state['frame'], 2)
        obj.device.confirm_control_progress.assert_not_called()
        obj.set_next_run.assert_not_called()

    def test_no_invitation_does_not_capture_or_modify_detect_record(self):
        obj, state = subject()
        guard = obj.device.detect_record
        self.assertFalse(obj._burst())
        obj.device.screenshot.assert_not_called()
        self.assertIs(obj.device.detect_record, guard)
        self.assertEqual(state['events'], [])

    def test_blocked_source_or_reward_dismissal_stops_after_one_attempt(self):
        for visible, expected in (({'SOURCE'}, 'SOURCE'), ({'REWARD'}, 'CONTINUE')):
            with self.subTest(visible=visible):
                obj, state = subject()
                state['visible'] = visible
                state['blocked'] = True
                with self.assertRaisesRegex(TaskRecoveryFailed, 'blocked'):
                    obj.ui_get_reward(RuleImage('SOURCE'))
                self.assertEqual([event[2] for event in input_events(state)], [expected])
                obj.device.confirm_control_progress.assert_not_called()

    def test_fixed_reward_source_is_clicked_once_without_image_recognition(self):
        obj, state = subject()
        with self.assertRaises(TaskRecoveryFailed):
            obj.ui_get_reward(RuleClick('SOURCE'))
        obj.click.assert_called_once()
        self.assertEqual(input_events(state), [('input', 1, 'SOURCE')])

    def test_blocked_invitation_stops_after_one_attempt_and_restores_guard(self):
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        state['blocked'] = True
        guard = set(obj.device.detect_record)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'blocked'):
            obj._burst()
        self.assertEqual(input_events(state), [('input', 1, 'I_G_ACCEPT')])
        self.assertEqual(obj.device.detect_record, guard)
        obj.set_next_run.assert_not_called()

    def test_recovery_during_invitation_verification_preserves_guard_and_fails(self):
        obj, state = subject()
        state['visible'].add('I_G_ACCEPT')
        guard = set(obj.device.detect_record)

        def update(task, current):
            if current['frame'] == 2:
                task.device._escape_generation += 1

        state['callback'] = update
        with self.assertRaisesRegex(TaskRecoveryFailed, 'interrupted'):
            obj._burst()
        self.assertEqual(input_events(state), [('input', 1, 'I_G_ACCEPT')])
        self.assertEqual(obj.device.detect_record, guard)
        obj.device.confirm_control_progress.assert_not_called()
        obj.set_next_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

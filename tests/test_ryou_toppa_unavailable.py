"""Unavailable guild targets must release their popup before another target is chosen."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.RealmRaid.assets import RealmRaidAssets
from tasks.RyouToppa import page_recognition
from tasks.RyouToppa.script_task import ScriptTask, TOPPA_CLICK_FAILURE_LIMIT, area_map


class ScreenshotTimer:
    """One second passes per simulated fresh frame; no game or wall-clock waits."""

    def __init__(self, state, limit):
        self.state = state
        self.limit = limit
        self.started_at = None

    def start(self):
        self.started_at = self.state['frame']
        return self

    def reached(self):
        return self.state['frame'] - self.started_at >= self.limit


def subject(notice=True, open_popup=True, close_to='card', notice_text='该结界已被攻打'):
    task = ScriptTask.__new__(ScriptTask)
    state = dict(page='card', frame=0, events=[], fire_at=None,
                 notice=notice, notice_text=notice_text,
                 open_popup=open_popup, close_to=close_to,
                 on_screenshot=None)
    task.config = SimpleNamespace(ryou_toppa=SimpleNamespace(
        raid_config=SimpleNamespace(random_delay=False),
        general_battle_config=object()))

    def screenshot():
        state['frame'] += 1
        if state['frame'] > 100:
            raise AssertionError('The unavailable-target flow did not terminate')
        callback = state['on_screenshot']
        if callback:
            callback(state)
        state['events'].append(('screenshot', state['frame'], state['page']))

    def dispatch(name):
        state['events'].append((name, state['frame']))
        if name == 'card':
            if state['page'] != 'card':
                raise AssertionError('Another target was clicked with the old popup still open')
            if state['open_popup']:
                state['page'] = 'popup'
        elif name == 'fire':
            if state['fire_at'] is not None:
                raise AssertionError('The same unavailable target was challenged twice')
            state['fire_at'] = state['frame']
            state['page'] = 'blocked'
        elif name == 'safe':
            if state['page'] not in ('popup', 'blocked'):
                raise AssertionError('A blank-screen dismissal was sent without a visible popup')
            state['page'] = state['close_to']
        return True

    def click_rule(rule, **kwargs):
        if rule is RealmRaidAssets.I_FIRE:
            return dispatch('fire')
        if rule is task.C_SAFE_AREA:
            return dispatch('safe')
        return dispatch('card')

    def click_device(x, y, control_name):
        if control_name != task.C_SAFE_AREA.name:
            raise AssertionError('Unexpected direct device control')
        left, top, width, height = task.C_SAFE_AREA.roi_front
        if not (left <= x < left + width and top <= y < top + height):
            raise AssertionError('Dismissal point must remain inside the safe area')
        return dispatch('safe')

    task.device = SimpleNamespace(
        image=np.zeros((720, 1280, 3), dtype=np.uint8),
        click=Mock(side_effect=click_device), click_record_clear=Mock(),
        performance=SimpleNamespace(timeout=lambda value: value),
        _escape_generation=0,
        confirm_control_progress=Mock(side_effect=lambda controls:
            state['events'].append(('verified', state['frame'], tuple(controls)))))
    task.screenshot = Mock(side_effect=screenshot)
    task.appear = Mock(side_effect=lambda rule, **kwargs:
                       rule is RealmRaidAssets.I_FIRE and state['page'] in ('popup', 'blocked'))
    task.appear_then_click = Mock(side_effect=click_rule)
    task.click = Mock(side_effect=click_rule)
    task._ensure_ryou_toppa_page = Mock()
    task.check_area = Mock(return_value=True)
    task.is_in_battle = Mock(side_effect=lambda *_: state['page'] == 'battle')
    task.run_general_battle = Mock(return_value=True)
    task.set_next_run = Mock()
    task.custom_next_run = Mock()
    return task, state


def simulation(task, state):
    stack = ExitStack()
    stack.enter_context(patch('tasks.RyouToppa.script_task.Timer',
                             side_effect=lambda limit: ScreenshotTimer(state, limit)))
    stack.enter_context(patch('tasks.RyouToppa.script_task.time.sleep'))
    stack.enter_context(patch('tasks.base_task.monotonic', side_effect=lambda: state['frame']))
    stack.enter_context(patch('tasks.RyouToppa.script_task.is_ryou_toppa_page',
                             side_effect=lambda _: state['page'] in ('card', 'popup', 'blocked')))
    stack.enter_context(patch('tasks.RyouToppa.script_task.is_ryou_toppa_context', return_value=False))
    ocr = Mock()
    ocr.detect_and_ocr.side_effect = lambda _: (
        [SimpleNamespace(ocr_text=state['notice_text'])]
        if state['notice'] and state['page'] == 'blocked' else [])
    stack.enter_context(patch('tasks.RyouToppa.script_task.RuleOcr', return_value=ocr))
    return stack


class RyouToppaUnavailableTests(unittest.TestCase):
    def inputs(self, state):
        return [event[0] for event in state['events'] if event[0] in ('card', 'fire', 'safe')]

    def assert_no_task_completion(self, task):
        task.set_next_run.assert_not_called()
        task.custom_next_run.assert_not_called()

    def test_explicit_notice_closes_popup_after_one_challenge_and_two_new_frames(self):
        task, state = subject()
        with simulation(task, state):
            self.assertFalse(task.attack_area(0))
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        safe = next(event for event in state['events'] if event[0] == 'safe')
        verified = next(event for event in state['events'] if event[0] == 'verified')
        fresh = [event for event in state['events']
                 if event[0] == 'screenshot' and safe[1] < event[1] <= verified[1]]
        self.assertEqual(len(fresh), 2)
        self.assertTrue(all(event[2] == 'card' for event in fresh))
        self.assertLess(safe[1] - state['fire_at'], 5)
        task.device.confirm_control_progress.assert_called_once_with([task.C_SAFE_AREA])
        task.run_general_battle.assert_not_called()
        self.assert_no_task_completion(task)

    def test_timeout_without_notice_still_challenges_once_then_closes(self):
        task, state = subject(notice=False)
        with simulation(task, state):
            self.assertFalse(task.attack_area(0))
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        safe = next(event for event in state['events'] if event[0] == 'safe')
        self.assertGreaterEqual(safe[1] - state['fire_at'], 5)
        self.assertEqual(state['page'], 'card')
        self.assert_no_task_completion(task)

    def test_defeated_target_notices_also_close_after_a_single_challenge(self):
        for text in ('该结界已被攻破', '该结界已攻破'):
            with self.subTest(text=text):
                task, state = subject(notice_text=text)
                with simulation(task, state):
                    self.assertFalse(task.attack_area(0))
                self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
                task.run_general_battle.assert_not_called()
                self.assert_no_task_completion(task)

    def test_missing_target_popup_waits_are_bounded_and_send_no_blind_dismissal(self):
        task, state = subject(notice=False, open_popup=False)
        with simulation(task, state):
            self.assertFalse(task.attack_area(0))
        self.assertEqual(self.inputs(state), ['card'] * TOPPA_CLICK_FAILURE_LIMIT)
        task.device.click.assert_not_called()
        task.run_general_battle.assert_not_called()
        task.device.confirm_control_progress.assert_called_once_with([])
        self.assert_no_task_completion(task)

    def test_a_still_visible_popup_fails_without_a_second_dismissal(self):
        task, state = subject(close_to='blocked')
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.attack_area(0)
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        task.device.confirm_control_progress.assert_not_called()
        task.run_general_battle.assert_not_called()
        self.assert_no_task_completion(task)

    def test_popup_disappearance_on_unknown_page_does_not_verify_a_skip(self):
        task, state = subject(close_to='unknown')
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.attack_area(0)
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_a_single_transient_raid_frame_is_insufficient_to_verify_a_skip(self):
        task, state = subject()

        def transition(current):
            safe = next((event for event in current['events'] if event[0] == 'safe'), None)
            if safe is not None and current['frame'] - safe[1] >= 2:
                current['page'] = 'unknown'

        state['on_screenshot'] = transition
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.attack_area(0)
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_residual_unavailable_notice_prevents_confirming_popup_disappearance(self):
        task, state = subject()
        rule = Mock()
        rule.detect_and_ocr.side_effect = lambda _: (
            [SimpleNamespace(ocr_text='该结界已被攻打')]
            if state['fire_at'] is not None else [])
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule):
            with self.assertRaises(TaskRecoveryFailed):
                task.attack_area(0)
        self.assertEqual(state['page'], 'card')
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_no_popup_and_no_raid_page_cannot_be_treated_as_skipped(self):
        task, state = subject()
        state['page'] = 'unknown'
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task._skip_unavailable_area()
        task.device.click.assert_not_called()
        task.run_general_battle.assert_not_called()
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_blocked_dismissal_fails_without_waiting_or_reclicking(self):
        """关闭被阻止时必须立刻失败：只点一次、不重试、不重截。

        关闭前会做一次竞态复核（战斗可能此刻才开始），因此截图次数可以 >1；
        真正要守住的是「只点一次」和「立刻抛错」。
        """
        task, state = subject()
        state['page'] = 'blocked'
        task.device.click.side_effect = None
        task.device.click.return_value = False
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task._skip_unavailable_area()
        task.device.click.assert_called_once()
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_race_recheck_prevents_closing_popup_when_battle_starts(self):
        """竞态复核：关浮窗前若战斗已开始，必须交给战斗控制器而不是关窗。

        10-06 13:55:47 实测：判定「未进入战斗」后仅 50ms 战斗就开始了。
        旧实现直接关浮窗，与战斗启动相撞导致脚本崩溃重启。
        """
        task, state = subject()
        state['page'] = 'blocked'
        # 第一次复核仍在浮窗页，第二次复核时战斗已开始
        seq = iter(['blocked', 'blocked', 'battle'])
        state['on_screenshot'] = lambda current: current.update(page=next(seq, 'battle'))
        with simulation(task, state):
            self.assertTrue(task._skip_unavailable_area())
        task.device.click.assert_not_called()
        task.run_general_battle.assert_called_once_with(
            config=task.config.ryou_toppa.general_battle_config)
        self.assert_no_task_completion(task)

    def test_battle_starting_before_dismissal_is_handed_to_battle_controller(self):
        task, state = subject()
        state['page'] = 'blocked'
        state['on_screenshot'] = lambda current: current.update(page='battle')
        with simulation(task, state):
            self.assertTrue(task._skip_unavailable_area())
        task.device.click.assert_not_called()
        task.run_general_battle.assert_called_once_with(
            config=task.config.ryou_toppa.general_battle_config)
        self.assert_no_task_completion(task)

    def test_battle_starting_during_close_verification_is_not_discarded(self):
        task, state = subject(close_to='battle')
        with simulation(task, state):
            self.assertTrue(task.attack_area(0))
        self.assertEqual(self.inputs(state), ['card', 'fire', 'safe'])
        task.device.confirm_control_progress.assert_called_once_with([task.C_SAFE_AREA])
        task.run_general_battle.assert_called_once_with(
            config=task.config.ryou_toppa.general_battle_config)
        self.assert_no_task_completion(task)

    def test_normal_battle_can_enter_before_timeout_without_dismissal(self):
        task, state = subject(notice=False)

        def transition(current):
            if current['fire_at'] is not None and current['frame'] - current['fire_at'] == 2:
                current['page'] = 'battle'

        state['on_screenshot'] = transition
        with simulation(task, state):
            self.assertTrue(task.attack_area(0))
        self.assertEqual(self.inputs(state), ['card', 'fire'])
        task.device.click.assert_not_called()
        task.run_general_battle.assert_called_once()
        self.assert_no_task_completion(task)

    def test_exact_notice_accepts_only_whitespace_and_terminal_punctuation(self):
        task, _ = subject()
        for text in ('该结界已被攻打', ' 该 结界 已被 攻打！ ', '该结界已被攻打。',
                     '！该结界已被攻打!', '该结界已被攻破', '该结界已攻破！'):
            with self.subTest(text=text):
                rule = Mock()
                rule.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=text)]
                with patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule) as factory:
                    self.assertTrue(task._area_already_attacked())
                self.assertEqual(factory.call_args.kwargs['roi'], (0, 0, 1280, 720))
                self.assertEqual(factory.call_args.kwargs['area'], (0, 0, 1280, 720))
                self.assertEqual(factory.call_args.kwargs['mode'], 'Full')
                self.assertEqual(rule.score, 0.8)

    def test_unrelated_or_partial_notice_cannot_skip_early(self):
        task, _ = subject()
        for text in ('已被攻打', '该结界未被攻打', '该结界已被攻打过吗',
                     '已攻破', '该结界未攻破', '该结界已被攻打，请等待', ''):
            with self.subTest(text=text):
                rule = Mock()
                rule.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=text)]
                with patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule):
                    self.assertFalse(task._area_already_attacked())

    def test_invalid_images_do_not_create_an_ocr_rule_or_send_input(self):
        task, _ = subject()
        for image in (None, np.zeros((0, 1280, 3)), np.zeros((720, 0, 3)), np.zeros(3)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                task.device.image = image
                with patch('tasks.RyouToppa.script_task.RuleOcr') as factory:
                    self.assertFalse(task._area_already_attacked())
                factory.assert_not_called()
        task.device.click.assert_not_called()


class RyouToppaDefeatedAreaTests(unittest.TestCase):
    def task(self):
        task, state = subject(notice=False)
        task.check_area = ScriptTask.check_area.__get__(task, ScriptTask)
        task.plan_tomorrow_ryoutoppa = Mock()
        return task, state

    def assert_no_task_completion(self, task):
        task.plan_tomorrow_ryoutoppa.assert_not_called()
        task.set_next_run.assert_not_called()
        task.custom_next_run.assert_not_called()

    def test_each_defeated_card_template_skips_only_its_target_without_scheduling(self):
        for index, area in enumerate(area_map):
            for template in area['finished_sign']:
                with self.subTest(index=index, template=template.file):
                    task, state = self.task()
                    task.appear = Mock(side_effect=lambda rule, **kwargs: rule is template)
                    with simulation(task, state):
                        self.assertFalse(task.attack_area(index))
                    task.click.assert_not_called()
                    task.device.click.assert_not_called()
                    task.run_general_battle.assert_not_called()
                    self.assertEqual(task.screenshot.call_count, 3)
                    task.device.confirm_control_progress.assert_called_once_with([])
                    self.assert_no_task_completion(task)

    def test_defeated_card_ocr_skips_without_challenge_or_next_day_schedule(self):
        task, state = self.task()
        task.appear = Mock(return_value=False)
        rule = Mock()
        rule.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=' 已 攻破！ ')]
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule) as factory:
            self.assertFalse(task.attack_area(3))
        roi = area_map[3]['finished_sign'][0].roi_back
        self.assertTrue(factory.call_count >= 3)
        for call in factory.call_args_list:
            self.assertEqual(call.kwargs['roi'], roi)
            self.assertEqual(call.kwargs['area'], roi)
            self.assertEqual(call.kwargs['mode'], 'Full')
        self.assertEqual(rule.score, 0.8)
        task.click.assert_not_called()
        task.device.click.assert_not_called()
        task.device.confirm_control_progress.assert_called_once_with([])
        self.assert_no_task_completion(task)


    def test_other_cards_defeated_ocr_cannot_mark_the_current_card_finished(self):
        for index in range(len(area_map)):
            with self.subTest(index=index):
                task, state = self.task()
                task.appear = Mock(return_value=False)
                current_roi = area_map[index]['finished_sign'][0].roi_back

                def card_ocr(**kwargs):
                    rule = Mock()
                    # Every other card has the status. The selected card does not.
                    rule.detect_and_ocr.return_value = ([] if kwargs['roi'] == current_roi
                        else [SimpleNamespace(ocr_text='已攻破')])
                    return rule

                with simulation(task, state), \
                        patch('tasks.RyouToppa.script_task.RuleOcr', side_effect=card_ocr) as factory:
                    self.assertTrue(task.check_area(index))
                self.assertTrue(factory.call_count >= 1)
                self.assertTrue(all(call.kwargs['roi'] == current_roi
                                    for call in factory.call_args_list))
                task.device.confirm_control_progress.assert_not_called()
                task.device.click.assert_not_called()
                self.assert_no_task_completion(task)

    def test_another_cards_finished_template_does_not_skip_current_target(self):
        task, state = self.task()
        other = area_map[1]['finished_sign']
        task.appear = Mock(side_effect=lambda rule, **kwargs: any(rule is mark for mark in other))
        with simulation(task, state):
            self.assertTrue(task.check_area(0))
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_partial_or_unrelated_card_text_does_not_skip(self):
        task, state = self.task()
        task.appear = Mock(return_value=False)
        for text in ('未攻破', '已攻破吗', '该结界已被攻破', '已攻破其他结界', ''):
            with self.subTest(text=text):
                rule = Mock()
                rule.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=text)]
                with simulation(task, state), \
                        patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule):
                    self.assertTrue(task.check_area(0))
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)

    def test_transient_defeated_card_template_is_not_accepted_as_verified(self):
        task, state = self.task()
        mark = area_map[0]['finished_sign'][0]
        task.appear = Mock(side_effect=lambda rule, **kwargs:
                           rule is mark and state['frame'] == 1)
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.check_area(0)
        task.device.confirm_control_progress.assert_not_called()
        task.device.click.assert_not_called()
        self.assert_no_task_completion(task)

    def test_defeated_card_without_raid_page_cannot_be_verified(self):
        task, state = self.task()
        state['page'] = 'unknown'
        mark = area_map[0]['finished_sign'][0]
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is mark)
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.check_area(0)
        task.device.confirm_control_progress.assert_not_called()
        task.device.click.assert_not_called()
        self.assert_no_task_completion(task)

    def test_whole_guild_completion_is_verified_before_scheduling_next_day(self):
        task, state = self.task()
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_SUCCESS_PENETRATION)
        with simulation(task, state):
            with self.assertRaises(TaskEnd):
                task.check_area(0)
        self.assertEqual(task.screenshot.call_count, 3)
        task.device.confirm_control_progress.assert_called_once_with([])
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        task.click.assert_not_called()
        task.device.click.assert_not_called()
        task.run_general_battle.assert_not_called()

    def test_transient_whole_guild_completion_preserves_unfinished_task(self):
        task, state = self.task()
        task.appear = Mock(side_effect=lambda rule, **kwargs:
                           rule is task.I_SUCCESS_PENETRATION and state['frame'] == 1)
        with simulation(task, state):
            with self.assertRaises(TaskRecoveryFailed):
                task.check_area(0)
        task.device.confirm_control_progress.assert_not_called()
        self.assert_no_task_completion(task)


class RyouToppaWholeRaidCompletionTests(unittest.TestCase):
    def task(self):
        task, state = subject(notice=False)
        task.check_area = ScriptTask.check_area.__get__(task, ScriptTask)
        task._ensure_ryou_toppa_page = ScriptTask._ensure_ryou_toppa_page.__get__(task, ScriptTask)
        task.plan_tomorrow_ryoutoppa = Mock()
        task.get_current_page = Mock()
        task.goto_page = Mock()
        task.appear = Mock(return_value=False)
        return task, state

    def rule(self, text):
        rule = Mock()
        rule.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=text)]
        return rule

    def test_existing_global_completion_template_remains_supported(self):
        task, _ = self.task()
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_SUCCESS_PENETRATION)
        with patch('tasks.RyouToppa.script_task.is_ryou_toppa_context') as context, \
                patch('tasks.RyouToppa.script_task.RuleOcr') as ocr:
            self.assertTrue(task._ryou_raid_complete())
        context.assert_not_called()
        ocr.assert_not_called()

    def test_completed_percentage_or_status_text_uses_only_global_progress_region(self):
        task, _ = self.task()
        for text in ('100%', '100.0%', '100.00%', ' 100 ％ ', '已攻破', '攻破完成',
                     '阴阳寮已攻破！'):
            with self.subTest(text=text):
                rule = self.rule(text)
                with patch('tasks.RyouToppa.script_task.is_ryou_toppa_context', return_value=True), \
                        patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule) as factory:
                    self.assertTrue(task._ryou_raid_complete())
                factory.assert_called_once()
                self.assertEqual(factory.call_args.kwargs['roi'], (130, 365, 250, 65))
                self.assertEqual(factory.call_args.kwargs['area'], (130, 365, 250, 65))
                self.assertEqual(factory.call_args.kwargs['mode'], 'Full')
                self.assertEqual(rule.score, 0.8)

    def test_incomplete_or_ambiguous_global_text_is_not_completion(self):
        task, _ = self.task()
        for text in ('99%', '99.99%', '100.01%', '100', '未攻破', '攻破完成了吗',
                     '已攻破一个结界', '该结界已被攻破', '攻破阴阳寮', ''):
            with self.subTest(text=text):
                with patch('tasks.RyouToppa.script_task.is_ryou_toppa_context', return_value=True), \
                        patch('tasks.RyouToppa.script_task.RuleOcr', return_value=self.rule(text)):
                    self.assertFalse(task._ryou_raid_complete())

    def test_global_text_cannot_complete_personal_or_unselected_guild_page(self):
        for tab, highlight, record in (('个人', True, True), ('阴阳寮', False, True),
                                        ('阴阳寮', True, False)):
            with self.subTest(tab=tab, highlight=highlight, record=record):
                task, _ = self.task()
                if highlight:
                    task.device.image[375:486, 1192:1208] = 220
                task.appear = Mock(side_effect=lambda rule, **kwargs:
                    rule is task.I_TOPPA_RECORD and record)
                with patch.object(page_recognition.GUILD_TAB, 'ocr', return_value=tab), \
                        patch('tasks.RyouToppa.script_task.RuleOcr',
                              return_value=self.rule('攻破完成')) as ocr:
                    self.assertFalse(task._ryou_raid_complete())
                ocr.assert_not_called()

    def test_individual_card_status_does_not_complete_the_entire_guild(self):
        task, _ = self.task()
        global_roi = (130, 365, 250, 65)
        card_roi = area_map[0]['finished_sign'][0].roi_back

        def region_rule(**kwargs):
            rule = Mock()
            rule.detect_and_ocr.return_value = ([SimpleNamespace(ocr_text='已攻破')]
                if kwargs['roi'] == card_roi else [])
            return rule

        with patch('tasks.RyouToppa.script_task.is_ryou_toppa_context', return_value=True), \
                patch('tasks.RyouToppa.script_task.RuleOcr', side_effect=region_rule) as factory:
            self.assertFalse(task._ryou_raid_complete())
            self.assertTrue(task._area_finished(0))
        self.assertEqual([call.kwargs['roi'] for call in factory.call_args_list],
                         [global_roi, card_roi])
        task.plan_tomorrow_ryoutoppa.assert_not_called()

    def test_completed_status_without_percentage_is_a_valid_guild_navigation_destination(self):
        task, _ = self.task()
        task.device.image[375:486, 1192:1208] = 220
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_TOPPA_RECORD)
        with patch.object(page_recognition.GUILD_TAB, 'ocr', return_value='阴阳寮'), \
                patch.object(page_recognition.BREAKTHROUGH_PROGRESS, 'ocr', return_value='攻破完成'), \
                patch('tasks.RyouToppa.script_task.RuleOcr', return_value=self.rule('攻破完成')):
            self.assertTrue(page_recognition.is_ryou_toppa_context(task))
            self.assertFalse(page_recognition.is_ryou_toppa_page(task))
            task._ensure_ryou_toppa_page()
        task.goto_page.assert_not_called()
        task.get_current_page.assert_not_called()
        task.device.click.assert_not_called()
        task.plan_tomorrow_ryoutoppa.assert_not_called()

    def test_global_completed_ocr_is_verified_in_two_frames_before_daily_schedule(self):
        task, state = self.task()
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context', return_value=True), \
                patch('tasks.RyouToppa.script_task.RuleOcr', return_value=self.rule('攻破完成')):
            with self.assertRaises(TaskEnd):
                task.check_area(0)
        self.assertEqual(task.screenshot.call_count, 3)
        task.device.confirm_control_progress.assert_called_once_with([])
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        task.device.click.assert_not_called()

    def test_transient_completed_global_text_cannot_end_the_task(self):
        task, state = self.task()
        rule = Mock()
        rule.detect_and_ocr.side_effect = lambda _: ([SimpleNamespace(ocr_text='攻破完成')]
            if state['frame'] == 1 else [SimpleNamespace(ocr_text='99%')])
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context', return_value=True), \
                patch('tasks.RyouToppa.script_task.RuleOcr', return_value=rule):
            with self.assertRaises(TaskRecoveryFailed):
                task.check_area(0)
        task.plan_tomorrow_ryoutoppa.assert_not_called()
        task.device.confirm_control_progress.assert_not_called()
        task.device.click.assert_not_called()

    def test_popup_closing_to_completed_global_page_is_verified_without_extra_challenge(self):
        task, state = self.task()
        state['page'] = 'blocked'
        state['close_to'] = 'complete'
        task.appear = Mock(side_effect=lambda rule, **kwargs:
                           rule is RealmRaidAssets.I_FIRE and state['page'] == 'blocked')

        def region_rule(**kwargs):
            rule = Mock()
            rule.detect_and_ocr.side_effect = lambda _: ([SimpleNamespace(ocr_text='攻破完成')]
                if kwargs['roi'] == (130, 365, 250, 65) and state['page'] == 'complete' else [])
            return rule

        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context',
                      side_effect=lambda _: state['page'] == 'complete'), \
                patch('tasks.RyouToppa.script_task.RuleOcr', side_effect=region_rule):
            with self.assertRaises(TaskEnd):
                task._skip_unavailable_area()
        self.assertEqual([event[0] for event in state['events'] if event[0] in ('card', 'fire', 'safe')],
                         ['safe'])
        self.assertEqual(task.device.click.call_count, 1)
        self.assertEqual(task.device.confirm_control_progress.call_count, 2)
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        task.run_general_battle.assert_not_called()

    def completion_rules(self, state):
        def region_rule(**kwargs):
            rule = Mock()
            rule.detect_and_ocr.side_effect = lambda _: ([SimpleNamespace(ocr_text='攻破完成')]
                if kwargs['roi'] == (130, 365, 250, 65) and state['page'] == 'complete' else [])
            return rule
        return region_rule

    def test_global_completion_during_single_card_verification_ends_before_next_card(self):
        task, state = self.task()
        mark = area_map[7]['finished_sign'][0]
        task.appear = Mock(side_effect=lambda rule, **kwargs:
                           rule is mark and state['page'] == 'card')

        def transition(current):
            # The navigation check is the first frame, the card check the second.
            if current['frame'] >= 3:
                current['page'] = 'complete'

        state['on_screenshot'] = transition
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context',
                      side_effect=lambda _: state['page'] == 'complete'), \
                patch('tasks.RyouToppa.script_task.RuleOcr',
                      side_effect=self.completion_rules(state)):
            with self.assertRaises(TaskEnd):
                task.attack_area(7)
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        self.assertEqual(task.device.confirm_control_progress.call_count, 2)
        task.click.assert_not_called()
        task.device.click.assert_not_called()
        task.run_general_battle.assert_not_called()

    def test_global_completion_during_random_delay_sends_no_card_or_challenge_input(self):
        task, state = self.task()
        task.config.ryou_toppa.raid_config.random_delay = True
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context',
                      side_effect=lambda _: state['page'] == 'complete'), \
                patch('tasks.RyouToppa.script_task.RuleOcr',
                      side_effect=self.completion_rules(state)), \
                patch('tasks.RyouToppa.script_task.time.sleep',
                      side_effect=lambda _: state.update(page='complete')):
            with self.assertRaises(TaskEnd):
                task.attack_area(0)
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        task.device.confirm_control_progress.assert_called_once_with([])
        task.click.assert_not_called()
        task.appear_then_click.assert_not_called()
        task.device.click.assert_not_called()

    def test_global_completion_while_waiting_for_target_popup_sends_no_challenge(self):
        task, state = self.task()

        def transition(current):
            if current['page'] == 'popup':
                current['page'] = 'complete'

        state['on_screenshot'] = transition
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context',
                      side_effect=lambda _: state['page'] == 'complete'), \
                patch('tasks.RyouToppa.script_task.RuleOcr',
                      side_effect=self.completion_rules(state)):
            with self.assertRaises(TaskEnd):
                task.attack_area(0)
        self.assertEqual([event[0] for event in state['events'] if event[0] in ('card', 'fire', 'safe')],
                         ['card'])
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        task.device.confirm_control_progress.assert_called_once_with([])
        task.appear_then_click.assert_not_called()
        task.device.click.assert_not_called()

    def test_global_completion_on_third_navigation_check_sends_no_target_input(self):
        task, state = self.task()
        checks = {'count': 0}

        def ensure_page():
            checks['count'] += 1
            task.screenshot()
            if checks['count'] == 3:
                state['page'] = 'complete'

        task._ensure_ryou_toppa_page = Mock(side_effect=ensure_page)
        # The first card control was not actionable, so no device input was sent.
        # The next navigation screenshot exposes completion before its card input.
        task.click = Mock(return_value=False)
        with simulation(task, state), \
                patch('tasks.RyouToppa.script_task.is_ryou_toppa_context',
                      side_effect=lambda _: state['page'] == 'complete'), \
                patch('tasks.RyouToppa.script_task.RuleOcr',
                      side_effect=self.completion_rules(state)):
            with self.assertRaises(TaskEnd):
                task.attack_area(0)
        self.assertEqual(checks['count'], 3)
        task.click.assert_called_once()
        self.assertFalse(any(event[0] in ('card', 'fire', 'safe') for event in state['events']))
        task.appear_then_click.assert_not_called()
        task.device.click.assert_not_called()
        task.device.confirm_control_progress.assert_called_once_with([])
        task.plan_tomorrow_ryoutoppa.assert_called_once()
        fresh = [event for event in state['events'] if event[0] == 'screenshot'][-2:]
        self.assertEqual([event[2] for event in fresh], ['complete', 'complete'])

if __name__ == '__main__':
    unittest.main()

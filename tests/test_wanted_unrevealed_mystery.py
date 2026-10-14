"""Only a positively unrevealed mystery card may finish this run as skipped."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from module.exception import TaskEnd, TaskRecoveryFailed
from tasks.WantedQuests.config import WQType, WQInfo
from tasks.WantedQuests.script_task import ScriptTask


def text(value, x, y, width=80, height=25):
    return SimpleNamespace(ocr_text=value, score=1.0,
                           box=np.array(((x, y), (x + width, y),
                                         (x + width, y + height), (x, y + height))))


def mystery(clue='石墙', current=0, total=14, shift=0):
    # Actual saved OCR panel coordinates translated by completion ROI (100, 60).
    return [text('神秘妖怪', 449 + shift, 161, 38, 117),
            text('线索：' + clue, 519 + shift, 426, 118, 27),
            text(f'{current}/{total}', 546 + shift, 448, 62, 29)]


class UnrevealedMysteryTests(unittest.TestCase):
    def subject(self):
        task = ScriptTask.__new__(ScriptTask)
        task.config = SimpleNamespace()
        task.device = SimpleNamespace(image=None)
        task.wq_skipped_names = set()
        task.screenshot = Mock()
        task.goto_page = Mock()
        task.ui_click = Mock()
        task.ui_click_until_disappear = Mock()
        task._completed_invitation_regions = Mock(return_value=[])
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_TRACE_DISABLE)
        return task

    def verify(self, task, frames):
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=frames))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader):
            return task.verify_quest_panel_complete()

    def test_actual_unrevealed_card_is_skipped_after_two_fresh_frames(self):
        task = self.subject()
        self.verify(task, [mystery(), mystery()])
        self.assertEqual(task.screenshot.call_count, 3)
        self.assertEqual(task.wq_unrevealed_skips, {('石墙', 14)})
        self.assertEqual(task.config.task_runtime_outcome['status'], 'skipped_unrevealed_mystery')
        self.assertIn('线索:石墙', task.config.task_runtime_outcome['quests'][0])

    def test_regular_neighbor_remains_unfinished(self):
        task = self.subject()
        frame = mystery() + [text('击败1个青蛙瓷器', 195, 426, 160), text('0/1', 259, 448, 50)]
        with self.assertRaises(TaskRecoveryFailed):
            self.verify(task, [frame])
        self.assertFalse(hasattr(task.config, 'task_runtime_outcome'))

    def test_skip_only_masks_the_matching_counter_region(self):
        frame = mystery() + [text('0/2', 840, 448, 50)]
        ignored = ScriptTask._unrevealed_mystery_regions(frame)
        unfinished, _, _ = ScriptTask._panel_unfinished_quests(frame, ignored.values())
        self.assertEqual(unfinished, ['0/2'])

    def test_revealed_target_is_never_skipped_even_with_stale_title_and_clue(self):
        for label in ('自己击败涂壁', '好友击败涂壁', '击败14个涂壁'):
            with self.subTest(label=label):
                frame = mystery() + [text(label, 519, 390, 150)]
                self.assertEqual(ScriptTask._unrevealed_mystery_regions(frame), {})
                task = self.subject()
                with self.assertRaises(TaskRecoveryFailed):
                    self.verify(task, [frame])

    def test_missing_title_clue_box_or_counter_is_not_completion(self):
        frame = mystery()
        cases = [frame[1:], frame[:1] + frame[2:], frame[:2],
                 [SimpleNamespace(ocr_text=item.ocr_text) for item in frame],
                 mystery(clue='?'), mystery(current=14)]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(ScriptTask._unrevealed_mystery_regions(value), {})

    def test_identical_mystery_cards_are_ambiguous_and_preserved(self):
        frame = mystery() + mystery(shift=300)
        self.assertEqual(ScriptTask._unrevealed_mystery_regions(frame), {})
        task = self.subject()
        with self.assertRaises(TaskRecoveryFailed):
            self.verify(task, [frame])

    def test_cross_card_clue_and_counter_cannot_form_a_mystery_identity(self):
        frame = mystery()
        frame[1] = text('线索：石墙', 850, 426, 118)
        self.assertEqual(ScriptTask._unrevealed_mystery_regions(frame), {})

    def test_changed_clue_or_location_requires_another_fresh_confirmation(self):
        for second in (mystery(clue='石壁'), mystery(shift=30)):
            with self.subTest(second=second):
                task = self.subject()
                self.verify(task, [mystery(), second, second])
                self.assertEqual(task.screenshot.call_count, 4)

    def test_later_reveal_cannot_inherit_an_earlier_mystery_skip(self):
        task = self.subject()
        self.verify(task, [mystery(), mystery()])
        revealed = [text('自己击败涂壁', 519, 390, 150), text('0/14', 546, 448, 62)]
        with self.assertRaises(TaskRecoveryFailed):
            self.verify(task, [revealed])

    def test_counter_disappearing_after_one_positive_frame_is_not_completion(self):
        task = self.subject()
        missing = mystery()[:2]
        with self.assertRaises(TaskRecoveryFailed):
            self.verify(task, [mystery(), missing, missing, missing])
        self.assertFalse(hasattr(task.config, 'task_runtime_outcome'))
        self.assertEqual(task.screenshot.call_count, 5)

    def test_visible_mystery_with_missing_clue_or_counter_never_completes(self):
        for frame in (mystery()[:2], mystery()[:1],
                      [SimpleNamespace(ocr_text='神秘妖怪')]):
            with self.subTest(frame=frame):
                task = self.subject()
                with self.assertRaises(TaskRecoveryFailed):
                    self.verify(task, [frame] * 4)
                self.assertFalse(hasattr(task.config, 'task_runtime_outcome'))

    def test_first_goto_absence_does_not_cancel_a_usable_later_route(self):
        task = self.subject()
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_GOTO_2)
        task.O_WQ_MONSTER_TYPE = SimpleNamespace(detect_text=Mock(return_value='涂壁'))
        task.unwanted_boss_name_list = []
        route = WQInfo(WQType.EXPLORE, '第二章', 1, task.I_GOTO_2, 1)
        task.get_ordered_wq_infos = Mock(return_value=[route])
        task.get_need_exec_wq = Mock(return_value=route)
        task.explore = Mock()
        task.challenge = Mock()
        task.secret = Mock()
        task.wq_executed_set = set()
        task.execute_mission(1)
        task.explore.assert_called_once_with(task.I_GOTO_2, 1)
        task.ui_click.assert_not_called()

    def test_template_misses_without_mystery_evidence_do_not_complete_panel(self):
        task = self.subject()
        task.appear = Mock(return_value=False)
        task.execute_mission(14)
        regular = [text('自己击败涂壁', 519, 390, 150), text('0/14', 546, 448, 62)]
        with self.assertRaises(TaskRecoveryFailed):
            self.verify(task, [regular])
        self.assertFalse(hasattr(task.config, 'task_runtime_outcome'))

    def test_failed_route_filter_and_already_executed_work_remain_unfinished(self):
        task = self.subject()
        task.wq_skipped_names = {'高层秘闻', '已经执行'}
        with self.assertRaises(TaskRecoveryFailed):
            self.verify(task, [[text('0/1', 259, 448, 50)]])

    def run_subject(self, remained):
        task = self.subject()
        task.config.model = SimpleNamespace(wanted_quests=SimpleNamespace(
            wanted_quests_config=SimpleNamespace(unwanted_boss_names='')))
        task.get_config = Mock(return_value=SimpleNamespace(cooperation_only=False))
        task.switch_mission_souls = Mock()
        task.pre_work = Mock(return_value=True)
        task.is_wq_remained = Mock(side_effect=remained)
        task.find_wq = Mock(return_value=(0, 0, 14, [30, 180, 50, 25]))
        task.O_WQ_TEXT_ALL = SimpleNamespace(area=None)
        task.open_wq_info = Mock(return_value=True)
        task.execute_mission = Mock(return_value=False)
        task.next_run = Mock()
        task.swipe = Mock()
        task.wq_unrevealed_skips = {('old', 2)}
        return task

    def test_black_untraced_run_reaches_panel_check_once_and_normal_schedule(self):
        # A completed/untraced black card no longer ends the scan by itself; the
        # loop keeps swiping until the bounded scroll budget is exhausted.
        task = self.run_subject([True] + [False] * 4)
        task.find_wq = Mock(side_effect=[(0, 0, 14, [30, 180, 50, 25])]
                                + [(-1, -1, 1, [30, 180, 50, 25])] * 4)
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=[mystery(), mystery()]))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader), \
                patch('tasks.WantedQuests.script_task.sleep'):
            with self.assertRaises(TaskEnd):
                task.run()
        task.open_wq_info.assert_called_once()
        task.execute_mission.assert_called_once_with(14)
        task.next_run.assert_called_once()
        task.swipe.assert_called()
        self.assertEqual(task.wq_unrevealed_skips, {('石墙', 14)})

    def test_still_visible_black_counter_has_a_bounded_run_and_normal_schedule(self):
        task = self.run_subject([True] * ScriptTask.WQ_LIST_MAX_SCROLLS)
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=[mystery(), mystery()]))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader), \
                patch('tasks.WantedQuests.script_task.sleep'):
            with self.assertRaises(TaskEnd):
                task.run()
        self.assertEqual(task.open_wq_info.call_count, ScriptTask.WQ_LIST_MAX_SCROLLS)
        self.assertEqual(task.execute_mission.call_count, ScriptTask.WQ_LIST_MAX_SCROLLS)
        task.next_run.assert_called_once()
        task.swipe.assert_not_called()

    def test_unusable_black_does_not_prevent_processing_the_next_regular_quest(self):
        task = self.run_subject([True, True] + [False] * 5)
        task.find_wq = Mock(side_effect=[(0, 1, 14, [30, 180, 50, 25]),
                                        (0, 1, 1, [30, 180, 50, 25])]
                                + [(-1, -1, 1, [30, 180, 50, 25])] * 5)
        task.execute_mission = Mock(side_effect=[False, None])
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=[mystery(), mystery()]))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader), \
                patch('tasks.WantedQuests.script_task.sleep'):
            with self.assertRaises(TaskEnd):
                task.run()
        self.assertEqual(task.execute_mission.call_args_list[0].args, (14,))
        self.assertEqual(task.execute_mission.call_args_list[1].args, (1,))
        task.next_run.assert_called_once()

    def test_bounded_unusable_routes_preserve_another_unfinished_regular_card(self):
        task = self.run_subject([True] * ScriptTask.WQ_LIST_MAX_SCROLLS)
        frame = mystery() + [text('击败1个青蛙瓷器', 195, 426, 160), text('0/1', 259, 448, 50)]
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=[frame]))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader), \
                patch('tasks.WantedQuests.script_task.sleep'):
            with self.assertRaises(TaskRecoveryFailed):
                task.run()
        self.assertEqual(task.open_wq_info.call_count, ScriptTask.WQ_LIST_MAX_SCROLLS)
        task.next_run.assert_not_called()

    def test_bounded_missing_templates_on_regular_card_cannot_end_successfully(self):
        task = self.run_subject([True] * ScriptTask.WQ_LIST_MAX_SCROLLS)
        frame = [text('自己击败涂壁', 519, 390, 150), text('0/14', 546, 448, 62)]
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=[frame]))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader), \
                patch('tasks.WantedQuests.script_task.sleep'):
            with self.assertRaises(TaskRecoveryFailed):
                task.run()
        self.assertEqual(task.execute_mission.call_count, ScriptTask.WQ_LIST_MAX_SCROLLS)
        task.next_run.assert_not_called()

    def test_failed_detail_open_is_bounded_and_preserves_unfinished_regular_work(self):
        task = self.run_subject([True] * ScriptTask.WQ_LIST_MAX_SCROLLS)
        task.open_wq_info = Mock(return_value=False)
        frame = [text('自己击败涂壁', 519, 390, 150), text('0/14', 546, 448, 62)]
        reader = SimpleNamespace(detect_and_ocr=Mock(side_effect=[frame]))
        with patch('tasks.WantedQuests.script_task.RuleOcr', return_value=reader), \
                patch('tasks.WantedQuests.script_task.sleep'):
            with self.assertRaises(TaskRecoveryFailed):
                task.run()
        self.assertEqual(task.open_wq_info.call_count, ScriptTask.WQ_LIST_MAX_SCROLLS)
        self.assertEqual(task.swipe.call_count, ScriptTask.WQ_LIST_MAX_SCROLLS - 1)
        task.execute_mission.assert_not_called()
        task.next_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

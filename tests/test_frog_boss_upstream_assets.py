"""Keep upstream FrogBoss templates usable without losing local input safety."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from module.atom.image import RuleImage
from module.exception import TaskRecoveryFailed
from tasks.FrogBoss.assets import FrogBossAssets
from tasks.FrogBoss.script_task import ScriptTask


ASSET_DIR = Path(__file__).resolve().parents[1] / 'tasks' / 'FrogBoss' / 'fb'


def roi(value):
    return tuple(map(int, value.split(',')))


def frame_with(rule, position=None):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y = position if position is not None else rule.roi_front[:2]
    height, width = rule.image.shape[:2]
    frame[y:y + height, x:x + width] = rule.image
    return frame


class FrogBossUpstreamAssetTests(unittest.TestCase):
    def test_image_catalogue_matches_generated_rules_and_original_png_dimensions(self):
        rows = []
        for filename in ('image.json', 'image2.json'):
            rows.extend(json.loads((ASSET_DIR / filename).read_text(encoding='utf-8')))
        by_name = {row['itemName']: row for row in rows}
        self.assertEqual(len(by_name), len(rows), 'Duplicate asset identifiers')
        # These recording controls must survive the upstream addition as a set.
        self.assertTrue({'frog_log', 'frog_log_check', 'frog_log_close',
                         'frog_last_win', 'frog_last_lose',
                         'frog_last_select_red', 'frog_last_select_blue'} <= by_name.keys())
        image_rules = {name[2:].lower(): rule for name, rule in vars(FrogBossAssets).items()
                       if isinstance(rule, RuleImage)}
        self.assertEqual(set(image_rules), set(by_name))
        for name, row in by_name.items():
            with self.subTest(asset=name):
                rule = copy.deepcopy(image_rules[name])
                expected_front, expected_back = roi(row['roiFront']), roi(row['roiBack'])
                self.assertEqual(tuple(rule.roi_front), expected_front)
                self.assertEqual(tuple(rule.roi_back), expected_back)
                self.assertEqual(Path(rule.file).name, row['imageName'])
                self.assertEqual(rule.method, row['method'])
                self.assertEqual(rule.threshold, row['threshold'])
                # The generator omits the JSON's explicit Default profile.
                expected_profile = row.get('profile')
                self.assertEqual(rule.profile, None if expected_profile in ('', 'Default')
                                 else expected_profile)
                pixels = cv2.imdecode(np.fromfile(str(ASSET_DIR / row['imageName']),
                                                dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(pixels, 'Missing or unreadable PNG')
                # Check before RuleImage.load_image can silently repair roi_front.
                self.assertEqual(pixels.shape[:2], (expected_front[3], expected_front[2]))
                bx, by, bw, bh = expected_back
                fx, fy, fw, fh = expected_front
                self.assertTrue(0 <= bx <= fx and 0 <= by <= fy)
                self.assertTrue(fx + fw <= bx + bw <= 1280)
                self.assertTrue(fy + fh <= by + bh <= 720)
                np.testing.assert_array_equal(rule.image, cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB))
                self.assertEqual(tuple(rule.roi_front), expected_front)

    def test_last_round_time_ocr_and_existing_counts_have_matching_metadata(self):
        rows = json.loads((ASSET_DIR / 'ocr.json').read_text(encoding='utf-8'))
        self.assertIn('frog_last_time', {row['itemName'] for row in rows})
        for row in rows:
            with self.subTest(asset=row['itemName']):
                rule = getattr(FrogBossAssets, 'O_' + row['itemName'].upper())
                self.assertEqual(tuple(rule.roi), roi(row['roiFront']))
                self.assertEqual(tuple(rule.area), roi(row['roiBack']))
                self.assertEqual(rule.mode.name, row['mode'].upper())
                self.assertEqual(rule.method.get_val(), row['method'].upper())
                self.assertEqual(rule.keyword, row['keyword'])

    def assert_distinct_markers(self, first, second):
        for source, opposite in ((first, second), (second, first)):
            with self.subTest(marker=source.name):
                image = frame_with(copy.deepcopy(source))
                self.assertTrue(copy.deepcopy(source).template_match(image))
                self.assertFalse(copy.deepcopy(opposite).template_match(image),
                                 'Opposite history marker must not record the wrong result')
                self.assertFalse(copy.deepcopy(source).template_match(np.zeros_like(image)))

    def test_recorded_win_and_loss_do_not_match_each_other(self):
        self.assert_distinct_markers(FrogBossAssets.I_FROG_LAST_WIN,
                                     FrogBossAssets.I_FROG_LAST_LOSE)

    def test_recorded_red_and_blue_choices_do_not_match_each_other(self):
        self.assert_distinct_markers(FrogBossAssets.I_FROG_LAST_SELECT_RED,
                                     FrogBossAssets.I_FROG_LAST_SELECT_BLUE)

    def test_selected_300k_border_and_reward_coins_keep_separate_semantics(self):
        selected = copy.deepcopy(FrogBossAssets.I_GOLD_30_CHECK)
        reward = copy.deepcopy(FrogBossAssets.I_GOLD_30_REWARD_COINS)
        self.assertEqual(tuple(selected.roi_front), (832, 569, 28, 65))
        self.assertEqual(tuple(selected.roi_back), (822, 559, 48, 85))
        self.assertEqual(selected.threshold, 0.88)
        self.assertEqual(tuple(reward.roi_front), (513, 196, 58, 53))
        self.assertEqual(tuple(reward.roi_back), (495, 195, 104, 111))
        self.assertEqual(reward.threshold, 0.8)
        self.assertNotEqual(selected.file, reward.file)
        self.assert_distinct_markers(selected, reward)

        fixtures = Path(__file__).parent / 'fixtures' / 'frog_boss'
        for filename, expected in (('selected.png', True), ('unselected.png', False)):
            with self.subTest(amount_border=filename):
                pixels = cv2.imdecode(np.fromfile(str(fixtures / filename), dtype=np.uint8),
                                      cv2.IMREAD_COLOR)
                self.assertIsNotNone(pixels)
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                x, y, width, height = selected.roi_back
                self.assertEqual(pixels.shape[:2], (height, width))
                frame[y:y + height, x:x + width] = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
                self.assertEqual(selected.template_match(frame), expected)
                self.assertFalse(reward.template_match(frame),
                                 'Selected amount must never be treated as reward coins')

    def next_round_subject(self, position=None):
        task = ScriptTask.__new__(ScriptTask)
        task.I_NEXT_COMPETITION = copy.deepcopy(FrogBossAssets.I_NEXT_COMPETITION)
        task.I_BET_LEFT = copy.deepcopy(FrogBossAssets.I_BET_LEFT)
        task.I_BET_RIGHT = copy.deepcopy(FrogBossAssets.I_BET_RIGHT)
        template = task.I_NEXT_COMPETITION.image
        task.device = SimpleNamespace(image=(frame_with(task.I_NEXT_COMPETITION, position)
                                             if position is not None else
                                             np.zeros((720, 1280, 3), dtype=np.uint8)))
        matched_rules = (task.I_NEXT_COMPETITION, task.I_BET_LEFT, task.I_BET_RIGHT)
        task.appear = lambda rule: (rule in matched_rules and rule.template_match(task.device.image))
        task.screenshot = Mock()
        task._clear_frog_reward_overlay = Mock(return_value=False)

        def clicked(*args, **kwargs):
            task.device.image = frame_with(task.I_BET_LEFT)
            right = task.I_BET_RIGHT
            x, y = right.roi_front[:2]
            height, width = right.image.shape[:2]
            task.device.image[y:y + height, x:x + width] = right.image

        task.device.click = Mock(side_effect=clicked)

        def verify(predicate, controls):
            self.assertEqual(controls, [task.I_NEXT_COMPETITION])
            for _ in range(2):
                task.screenshot()
                if not predicate():
                    raise TaskRecoveryFailed('No verified next-round destination')
            return True

        task._ui_verify = Mock(side_effect=verify)
        return task, template.shape[:2]

    def test_displaced_next_template_drives_one_click_at_actual_center(self):
        # Both positions are outside the narrow annotation from upstream.
        for x, y in ((70, 110), (1190, 640)):
            with self.subTest(position=(x, y)):
                task, (height, width) = self.next_round_subject((x, y))
                self.assertEqual(tuple(task.I_NEXT_COMPETITION.roi_back), (0, 0, 1280, 720))
                task._advance_competition()
                task.device.click.assert_called_once_with(
                    x + width // 2, y + height // 2,
                    control_name=task.I_NEXT_COMPETITION.name)
                self.assertEqual(task.screenshot.call_count, 3)
                task._ui_verify.assert_called_once()

    def test_absent_next_template_cannot_send_a_stale_annotation_click(self):
        task, _ = self.next_round_subject()
        with self.assertRaises(TaskRecoveryFailed):
            task._advance_competition()
        task.device.click.assert_not_called()
        task._ui_verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()

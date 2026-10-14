"""Soul preset rows must be located, not assumed; the group marker is one witness.

Only screenshots, elapsed time and control delivery are simulated. The row
lookup, the highlight test and the two-frame confirmation are the production
methods, extracted from source, so a change in their judgement is observable.

The rules under test decide whether a slot press lands on the intended row;
an ambiguous read must fail rather than guess.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]

GOLD = (222, 176, 84)
PLAIN = (60, 66, 78)
METHODS = ('_soul_generation', '_soul_guard', '_soul_capture', '_soul_wait',
           '_soul_row', '_soul_group_highlight', '_soul_selected_group')


def extract(namespace):
    """Pull the real methods out of SwitchSoul so nothing is re-implemented."""
    source = ast.parse(
        (ROOT / 'tasks/Component/SwitchSoul/switch_soul.py').read_text(encoding='utf-8'))
    cls = next(n for n in source.body
               if isinstance(n, ast.ClassDef) and n.name == 'SwitchSoul')
    body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__',
                              names=[ast.alias(name='annotations')], level=0), *body],
        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'switch_soul', 'exec'), namespace)
    return namespace


def screen(pixels=None, fill=PLAIN, shape=(720, 1280, 3)):
    image = np.zeros(shape, dtype=np.uint8)
    if len(shape) == 3:
        image[:, :] = fill
    for (x, y), color in (pixels or {}).items():
        image[y, x] = color
    return image


def group_roi(group):
    """Mirrors C_SOU_GROUP_n in tasks/Component/SwitchSoul/assets.py."""
    return 1086, 85 + 84 * (group - 1), 158, 47


def highlighted_group_image(group, others=()):
    """Paint only the sampled marker patches that belong to each group."""
    pixels = {}
    for index in (group,) + tuple(others):
        _, y, _, height = group_roi(index)
        center = int(y + height / 2)
        for left in (1098, 1225):
            for dy in range(-8, 8):
                for dx in range(8):
                    pixels[(left + dx, center + dy)] = GOLD
    return screen(pixels)


def box_result(text, score, box):
    return SimpleNamespace(ocr_text=text, score=score, box=box)


class SoulSubject:
    """A SwitchSoul carrying the extracted methods over a simulated device."""

    def __init__(self, image=None, timeout=lambda value: value):
        # A fresh namespace per instance: the extracted code closes over the
        # clock, so a shared one would carry an earlier instance's elapsed time
        # into this one's deadline.
        self.now = 0.0
        self.frames = 0
        namespace = extract(dict(TaskRecoveryFailed=TaskRecoveryFailed,
                                 monotonic=lambda: self.now,
                                 sleep=lambda seconds: None,
                                 logger=mock.Mock(), time=mock.Mock(),
                                 np=np, cv2=mock.Mock()))
        # Bind per instance, never on the class: a class attribute is rebound by
        # every construction, leaving the earliest closure pointing at an
        # object this test no longer owns.
        self.__class__ = type('BoundSwitchSoul', (SoulSubject,),
                              {name: namespace[name] for name in METHODS})
        self._image = image if image is not None else screen()
        self.device = SimpleNamespace(
            image=self._image, image_frame_id=0, _escape_generation=0,
            _accepted_invitation_generation=0,
            performance=SimpleNamespace(timeout=timeout))
        for index in range(1, 8):
            setattr(self, f'C_SOU_GROUP_{index}', SimpleNamespace(roi_front=group_roi(index)))
        self._soul_preset_visible_ocr = self.preset_visible

    @property
    def image(self):
        return self._image

    @image.setter
    def image(self, value):
        self._image = value
        self.device.image = value

    def advance(self, bump_generation=False):
        """One real screenshot: a new frame id, and optionally a new session.

        Time advances by a realistic capture cost, not by a whole second;
        `_soul_wait` re-checks the deadline after the second witness, so an
        inflated step would push the clock past it and hide that branch.
        """
        self.now += .1
        self.frames += 1
        self.device.image_frame_id = self.frames
        if bump_generation:
            self.device._escape_generation += 1

    def screenshot(self):
        # A real screenshot always yields a new frame id; otherwise the
        # staleness guard under test could never fail for the right reason.
        self.advance()

    def preset_visible(self):
        return True

    def preset_hidden(self):
        return False


class RowLookupTests(unittest.TestCase):
    """A row is confirmed only when exactly one confident in-roi box agrees."""

    def setUp(self):
        self.task = SoulSubject()
        self.rule = SimpleNamespace(roi=(0, 0, 200, 700))

    def locate(self, name, results):
        """Feed the production lookup through the OCR rule it will actually call."""
        self.rule.detect_and_ocr = lambda image: results
        return self.task._soul_row(self.rule, name)

    def test_one_matching_in_roi_box_yields_that_point(self):
        hit = box_result('soul-a', .9, [[10, 20], [110, 20], [110, 50], [10, 50]])
        other = box_result('soul-b', .9, [[10, 300], [110, 300], [110, 330], [10, 330]])
        self.assertEqual(self.locate('soul-a', [hit, other]), (60, 35))

    def test_two_matching_boxes_are_ambiguous_and_refuse_to_guess(self):
        box = [[10, 20], [110, 20], [110, 50], [10, 50]]
        duplicated = [box_result('dup', .9, box), box_result('dup', .9, box)]
        self.assertIsNone(self.locate('dup', duplicated))

    def test_low_score_and_out_of_roi_boxes_are_not_witnesses(self):
        blurry = box_result('blurry', .5, [[10, 20], [110, 20], [110, 50], [10, 50]])
        outside = box_result('outside', .95, [[10, 800], [110, 800], [110, 830], [10, 830]])
        self.assertIsNone(self.locate('blurry', [blurry]))
        self.assertIsNone(self.locate('outside', [outside]))

    def test_a_degenerate_box_is_not_a_position(self):
        flat = box_result('flat', .9, [[10, 20], [10, 20], [10, 20]])
        self.assertIsNone(self.locate('flat', [flat]))

    def test_a_malformed_box_length_is_ignored_rather_than_crashing(self):
        broken = box_result('broken', .9, [[10, 20], [110, 20]])
        self.assertIsNone(self.locate('broken', [broken]))


class GroupHighlightTests(unittest.TestCase):
    """The marker is one witness for one group, not a panel-wide state signal."""

    def test_only_the_painted_group_reports_highlighted(self):
        task = SoulSubject(highlighted_group_image(3))
        self.assertEqual([i for i in range(1, 8) if task._soul_group_highlight(i)], [3])

    def test_two_painted_groups_are_ambiguous_and_select_nothing(self):
        task = SoulSubject(highlighted_group_image(2, others=(5,)))
        self.assertFalse(task._soul_selected_group(2))

    def test_an_unpainted_panel_selects_nothing(self):
        task = SoulSubject(screen())
        self.assertFalse(task._soul_selected_group(4))

    def test_the_painted_group_is_confirmed_when_it_is_the_only_one(self):
        task = SoulSubject(highlighted_group_image(6))
        self.assertTrue(task._soul_selected_group(6))

    def test_a_hidden_preset_page_never_reports_a_selected_group(self):
        task = SoulSubject(highlighted_group_image(3))
        task._soul_preset_visible_ocr = task.preset_hidden
        self.assertFalse(task._soul_selected_group(3))

    def test_a_foreign_shape_is_not_scored_as_a_panel(self):
        task = SoulSubject(screen(shape=(720, 1280)))
        self.assertFalse(task._soul_group_highlight(1))

    def test_the_witness_is_the_declared_patch_not_the_whole_group(self):
        """Gold only inside the group but off the marker must not count."""
        _, y, _, height = group_roi(1)
        center = int(y + height / 2)
        pixels = {(1090 + i, center + j): GOLD
                  for i in range(20) for j in range(-8, 8)}
        task = SoulSubject(screen(pixels))
        self.assertFalse(task._soul_group_highlight(1))


class FrameAndRecoveryTests(unittest.TestCase):
    """A stale frame or an interrupted session must not confirm anything."""

    def test_a_repeated_frame_id_cannot_satisfy_the_capture(self):
        task = SoulSubject()
        task.screenshot = lambda: None  # a screenshot that yields no new frame
        with self.assertRaises(TaskRecoveryFailed):
            task._soul_capture((0, 0))

    def test_a_fresh_frame_lets_the_capture_pass_the_guard(self):
        task = SoulSubject()
        task._soul_capture((0, 0))
        self.assertEqual(task.device.image_frame_id, 1)

    def test_invitation_arriving_mid_operation_is_a_failure(self):
        task = SoulSubject()
        task.device._accepted_invitation_generation = 1
        with self.assertRaises(TaskRecoveryFailed):
            task._soul_guard((0, 0))

    def test_recovery_bumps_the_session_generation_and_is_caught(self):
        task = SoulSubject()
        task.device._escape_generation = 3
        with self.assertRaises(TaskRecoveryFailed):
            task._soul_guard((0, 0))

    def test_a_single_witness_is_not_enough(self):
        """The predicate may already hold, yet one frame must not confirm."""
        task = SoulSubject()
        seen = []

        def always_true():
            seen.append(task.device.image_frame_id)
            return True

        task._soul_wait(always_true, (0, 0), timeout=12)
        # Exactly two captures: a second witness is demanded even though the
        # first frame already satisfied the predicate.
        self.assertEqual(len(seen), 2)
        self.assertEqual(task.frames, 2)
        self.assertEqual(seen, [1, 2])

    def test_the_two_witnesses_must_be_consecutive(self):
        """A break in the middle resets the count instead of accumulating."""
        task = SoulSubject()
        # True, True would settle on frame 2; the leading False means the pair
        # only completes on frame 3.
        answers = iter((False, True, True, True))
        seen = []

        def flaky():
            seen.append(task.device.image_frame_id)
            return next(answers)

        task._soul_wait(flaky, (0, 0), timeout=12)
        self.assertEqual(len(seen), 3)
        self.assertEqual(task.frames, 3)

    def test_a_predicate_that_lags_one_call_still_needs_a_third_frame(self):
        """Guards against reading `stable` as the number of captures."""
        task = SoulSubject()
        seen = []

        def second_call_only():
            seen.append(task.device.image_frame_id)
            return len(seen) >= 2

        task._soul_wait(second_call_only, (0, 0), timeout=12)
        self.assertEqual(len(seen), 3)

    def test_a_wait_that_never_holds_fails_instead_of_spinning(self):
        task = SoulSubject()
        with self.assertRaises(TaskRecoveryFailed):
            task._soul_wait(lambda: False, (0, 0), timeout=4)


class HyakkiTurnImageTests(unittest.TestCase):
    """The relaxed lock rule must stay a positive witness, not an absence."""

    @staticmethod
    def probe():
        from tasks.Chess.runtime.hyakki_draft import ChessHyakkiDraftMixin
        return ChessHyakkiDraftMixin()

    def test_a_flat_frame_is_never_the_hyakki_turn(self):
        self.assertIsNone(self.probe()._hyakki_turn_image(screen()))

    def test_a_wrong_shaped_frame_is_ignored_instead_of_crashing(self):
        self.assertIsNone(self.probe()._hyakki_turn_image(screen(shape=(720, 1280))))

    def test_the_relaxed_path_still_needs_positive_lock_artwork(self):
        """require_unlocked=False relaxes one clause, not the artwork witness.

        A frame with a uniquely gold own row but no lock artwork anywhere
        must still be refused: an absent arbitrary icon is not a match.
        """
        image = screen()
        rows = [54 + 85 * i for i in range(8)]
        for y in (rows[2],):
            image[y - 12:y + 12, 1108:1220] = GOLD
        self.assertIsNone(self.probe()._hyakki_turn_image(image, require_unlocked=False))


if __name__ == '__main__':
    unittest.main()
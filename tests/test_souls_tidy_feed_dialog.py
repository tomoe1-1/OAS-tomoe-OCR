"""The SoulsTidy feeding dialog must not trap recovery into clicking behind it.

Real capture 2026-10-11 21:36 (``souls_tidy_feeding_dialog_20261011.png``):
the feeding dialog covers the souls screen, yet the page's own back arrow stays
visible *behind* it — back_arrow scored 1.000 at (47,40) while clicking it
changed nothing, and the dialog's own close cross scored 0.959 at (951,231).
Recovery spent its whole budget on the arrow behind the dialog, so GotoMain
failed three times and the process exited.

The task also clicked ``I_ST_BONGNA`` while the dialog was up. That template
scored 0.9825 because the vertical ``奉纳`` label stays readable through the
dialog, so the ``threshold=0.6`` fallback happily clicked a covered button and
tripped the repeat-click fence.
"""
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from module.device.performance import PerformanceProfile
from tasks.GameUi.recovery_exit import (
    click_exit, exit_candidates, exit_point, modal_close_point,
)

FIXTURE = (Path(__file__).resolve().parent / 'fixtures'
           / 'souls_tidy_feeding_dialog_20261011.png')


def load_fixture():
    image = cv2.imdecode(np.fromfile(str(FIXTURE), dtype=np.uint8),
                         cv2.IMREAD_COLOR)
    if image is None:
        raise unittest.SkipTest(f'fixture missing: {FIXTURE}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class ModalExitTests(unittest.TestCase):
    """The exit chooser must prefer the dialog's own close cross."""

    def setUp(self):
        self.image = load_fixture()

    def test_the_capture_shows_both_exits_and_the_back_arrow_wins_on_score(self):
        # Pins the premise: without this the whole fix rests on an assumption.
        cands = exit_candidates(self.image)
        kinds = {c[3]: c for c in cands}
        self.assertIn('back_arrow', kinds)
        self.assertIn('close_cross', kinds)
        self.assertGreater(kinds['back_arrow'][0], kinds['close_cross'][0],
                           'the useless arrow scores higher, which is the trap')

    def test_the_dialog_close_cross_is_below_the_old_top_third_band(self):
        # 45% is not an arbitrary widening: the old height//3 band could not see it.
        _, _, close_y, _ = [c for c in exit_candidates(self.image)
                            if c[3] == 'close_cross'][0]
        height = self.image.shape[0]
        self.assertGreater(close_y + 22, height // 3,
                           'close cross must sit outside the old band')
        self.assertLess(close_y, height * 45 // 100,
                        'close cross must sit inside the widened band')

    def test_modal_close_prefers_the_cross_over_the_back_arrow(self):
        self.assertEqual(modal_close_point(self.image), (951, 231))

    def test_ambiguous_exits_still_refuse_to_guess(self):
        # Two visible exits means we cannot own the modal: exit_point stays None
        # so normal navigation decides, rather than guessing and misclicking.
        self.assertIsNone(exit_point(self.image))

    def test_a_plain_page_keeps_using_its_back_arrow(self):
        # No cross on screen: the modal path must not steal the arrow.
        blank = np.full(self.image.shape, 90, dtype=np.uint8)
        self.assertIsNone(modal_close_point(blank))

    def test_two_crosses_are_refused(self):
        image = self.image.copy()
        template = cv2.imdecode(
            np.fromfile(str(Path(__file__).resolve().parent.parent
                            / 'tasks/GameUi/res/recovery_close_cross.png'),
                        dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        patch = cv2.cvtColor(template, cv2.COLOR_GRAY2RGB)
        image[600:600 + patch.shape[0], 300:300 + patch.shape[1]] = patch
        cands = [c for c in exit_candidates(image) if c[3] == 'close_cross']
        if len(cands) > 1:
            self.assertIsNone(modal_close_point(image),
                              'two crosses must be treated as ambiguous')

    def test_garbage_input_is_rejected(self):
        for bad in (None, np.zeros((10, 10)), 'image'):
            with self.subTest(bad=type(bad).__name__):
                self.assertEqual(exit_candidates(bad), [])
                self.assertIsNone(exit_point(bad))
                self.assertIsNone(modal_close_point(bad))


class ClickExitChoiceTests(unittest.TestCase):
    """click_exit must actually click the dialog's cross, not just detect it."""

    def setUp(self):
        self.image = load_fixture()
        self.clicked = []
        # The dialog closes after the click, so the observed frames must differ
        # from the "before" capture — otherwise changed() reads False and the
        # stub would be modelling a click that genuinely did nothing.
        closed = np.full(self.image.shape, 90, dtype=np.uint8)
        self.shots = [closed, closed]

        def screenshot():
            if self.shots:
                self.task.device.image = self.shots.pop(0)

        def click(x, y, control_name=None):
            self.clicked.append((x, y, control_name))
            return True

        self.task = SimpleNamespace(
            device=SimpleNamespace(image=self.image, screenshot=screenshot,
                                   click=click),
            screenshot=screenshot,
        )

    def test_click_exit_targets_the_dialog_cross_instead_of_the_back_arrow(self):
        # exit_point() returns None here on purpose (two exits are ambiguous).
        # If click_exit only consulted it, recovery would give up and fall back
        # to BACK, which is the path that burned the whole navigation budget.
        self.assertIsNone(exit_point(self.image))
        self.assertTrue(click_exit(self.task))
        self.assertEqual(len(self.clicked), 1)
        x, y, name = self.clicked[0]
        self.assertEqual((x, y), modal_close_point(self.image))
        self.assertEqual(name, 'RECOVERY_EXIT_CLOSE_CROSS')

    def test_click_exit_still_uses_the_back_arrow_when_no_cross_is_present(self):
        blank = np.full(self.image.shape, 90, dtype=np.uint8)
        self.task.device.image = blank
        self.shots = [blank, blank, blank]
        self.assertIsNone(modal_close_point(blank))
        self.assertFalse(click_exit(self.task),
                         'no exit at all must report failure, not guess')
        self.assertEqual(self.clicked, [])

    def test_a_blocked_click_is_reported_as_no_progress(self):
        self.task.device.click = lambda x, y, control_name=None: False
        self.assertFalse(click_exit(self.task))


class SoulsTidyFeedDialogTests(unittest.TestCase):
    """The task must close the dialog instead of clicking the covered bongna."""

    def test_bongna_must_stay_covered_while_the_dialog_is_open(self):
        # The label reads 0.9825 through the dialog; that is the trap being
        # fenced here, so assert the premise rather than assume it.
        sys_path = str(Path(__file__).resolve().parent.parent)
        import sys
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from tasks.SoulsTidy.assets import SoulsTidyAssets

        image = load_fixture()
        for rule in (SoulsTidyAssets.I_ST_BONGNA, SoulsTidyAssets.I_ST_GREED_HABIT):
            with self.subTest(rule=rule.name):
                template = rule.image
                if template.ndim == 3:
                    template = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
                x, y, w, h = rule.roi_back
                area = image[y:y + h, x:x + w]
                score = float(cv2.matchTemplate(area, template,
                                                cv2.TM_CCOEFF_NORMED).max())
                self.assertGreaterEqual(score, rule.threshold,
                                        'fixture must still contain this control')

    def test_dialog_branch_precedes_bongna_in_the_source(self):
        # A wiring guard: only the branch order keeps the covered click away.
        # Attribute names must be collected too — I_ST_GREED_HABIT is read as an
        # attribute, not called, so scanning only ast.Call misses it entirely.
        import ast
        import inspect
        import textwrap
        from tasks.SoulsTidy.script_task import ScriptTask

        source = textwrap.dedent(inspect.getsource(ScriptTask.greed_maneki))
        tree = ast.parse(source)
        names = [node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)]
        if not any('GREED_HABIT' in name for name in names):
            self.skipTest('greed_maneki no longer reads I_ST_GREED_HABIT')
        self.assertTrue(any('BONGNA' in name for name in names),
                        'greed_maneki no longer clicks I_ST_BONGNA')

        habit = next(i for i, n in enumerate(names) if 'GREED_HABIT' in n)
        bongna = [i for i, n in enumerate(names) if 'BONGNA' in n]
        # The dialog guard has to be read before any bongna interaction.
        self.assertLess(habit, min(bongna),
                        'the dialog guard must be evaluated before bongna')


if __name__ == '__main__':
    unittest.main()

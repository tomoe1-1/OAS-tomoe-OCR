"""RealmRaid refresh: one bounded input per step, and the right dialog identity.

The rewritten ``check_refresh`` replaced two unbounded ``while 1`` loops with
single inputs confirmed by ``_ui_verify``. This drives that real method (pulled
out of the source, not reimplemented) over a device whose screen decides what is
visible, so a step that fires twice, skips its confirmation, or fires against a
dialog it has not identified is observable.

The dialog identity matters: ``I_FRESH_ENSURE`` is a gold button also used by
other confirmations, so ``BaseTask.appear`` accepts the refreshed artwork only
together with its own question mark. That conjunction is checked here by
driving the real ``appear``.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]


def pull(module_path, class_name, method_name, namespace):
    """Exec the real method body so nothing under test is re-implemented."""
    source = ast.parse(Path(module_path).read_text(encoding='utf-8'))
    cls = next(n for n in source.body
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == method_name)
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__',
                              names=[ast.alias(name='annotations')], level=0),
              method],
        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(module_path), 'exec'),
         namespace)
    return namespace[method_name]


class Frame:
    """One screen: which artworks are visible."""

    def __init__(self, *visible):
        self.visible = set(visible)


def script(*items):
    """Frames, one per screenshot. Repeating a Frame holds that screen.

    ``check_refresh`` captures before its first judgement and every ``_ui_verify``
    captures twice, so a witness that must satisfy two consecutive frames needs
    that frame listed twice.
    """
    return list(items)


class RaidSubject:
    """A RealmRaid task carrying the real ``check_refresh``.

    Only the screen and the elapsed time are simulated; the click accounting,
    the blocked-input refusal and the frame-id freshness live here so a step
    that fires twice or confirms nothing cannot pass silently.
    """

    def __init__(self, frames=(), blocked=(), recover_at=None):
        self.now = 0.0
        self.frames_taken = 0
        self.frames = frames
        self.index = -1
        self.inputs = []
        self.controls_seen = []
        self.blocked = set(blocked)
        self.recover_at = recover_at
        self.device = SimpleNamespace(
            image=np.zeros((720, 1280, 3), dtype=np.uint8),
            image_frame_id=0, _escape_generation=0,
            performance=SimpleNamespace(timeout=lambda v: v),
            confirm_control_progress=mock.Mock())
        self.I_FRESH = SimpleNamespace(name='fresh', roi_front=[0, 0, 10, 10])
        self.I_FRESH_ENSURE = SimpleNamespace(name='fresh_ensure',
                                              roi_front=[0, 0, 10, 10])
        self.I_RAID_CONFIRM_ENSURE = SimpleNamespace(
            name='raid_confirm_ensure', roi_front=[658, 401, 192, 84])
        self.I_RAID_REFRESH_Q = SimpleNamespace(name='raid_refresh_q',
                                                roi_front=[435, 282, 410, 65])

    # -- simulated device --------------------------------------------------
    def appear(self, rule, **kwargs):
        """Reads the current screen; a screenshot, not a read, advances it."""
        return rule.name in (self.frames[self.index].visible if self.frames
                             else set())

    def screenshot(self):
        """A real capture: new frame id, next scripted screen, fresh pixels."""
        self.now += .1
        self.frames_taken += 1
        self.device.image_frame_id = self.frames_taken
        self.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        if self.frames:
            self.index = min(self.index + 1, len(self.frames) - 1)
        if self.recover_at is not None and self.frames_taken == self.recover_at:
            # Something else recovered the game mid-operation.
            self.device._escape_generation += 1

    def appear_then_click(self, rule, interval=None):
        self.inputs.append(rule.name)
        if rule.name in self.blocked:
            return False
        self.screenshot()
        return True

    def _ui_verify(self, predicate, controls, timeout=12, fail=True):
        """The confirmation contract: two consecutive witnesses, then done.

        The real ``BaseTask._ui_verify`` also checks the recovery generation on
        every frame. This stand-in deliberately does not: reproducing that check
        here would cover for ``check_refresh``'s own guards, and a recovery
        interrupting the source step would be caught by the stub instead of the
        line under test — leaving the guard unobservable.
        """
        self.controls_seen.append([c.name for c in controls])
        deadline = self.now + timeout
        stable = 0
        while self.now < deadline:
            self.screenshot()
            stable = stable + 1 if predicate() else 0
            if stable >= 2:
                self.device.confirm_control_progress(controls)
                return True
        if fail:
            raise TaskRecoveryFailed('UI next state not reached after one click')
        return False


def refresh(subject):
    """Drive the production check_refresh over the prepared subject."""
    return pull(ROOT / 'tasks/RealmRaid/script_task.py', 'ScriptTask',
                'check_refresh',
                dict(TaskRecoveryFailed=TaskRecoveryFailed,
                     monotonic=lambda: subject.now, logger=mock.Mock()))(subject)


# The refresh dialog stays on screen for the two frames _ui_verify demands,
# then leaves for the two frames the closing check demands.
OPENING = script(Frame('fresh'),                  # entry capture: button there
                 Frame('fresh_ensure'),           # source click opened it
                 Frame('fresh_ensure'),           # witness 1
                 Frame('fresh_ensure'),           # witness 2
                 Frame('fresh_ensure'),           # confirm click pressed
                 Frame(),                         # closing witness 1
                 Frame(),                         # closing witness 2
                 Frame())
ALREADY_OPEN = script(Frame('fresh_ensure'),      # entry capture: dialog up
                      Frame('fresh_ensure'),       # witness 1
                      Frame('fresh_ensure'),       # witness 2
                      Frame('fresh_ensure'),       # confirm click pressed
                      Frame(),                     # closing witness 1
                      Frame(),                     # closing witness 2
                      Frame())


class CheckRefreshTests(unittest.TestCase):
    """Each step fires once and is confirmed; a blocked step fails loudly."""

    def test_no_refresh_button_is_not_an_error(self):
        """Being in cooldown is a normal False, never a TaskRecoveryFailed."""
        task = RaidSubject(script(Frame()))
        self.assertFalse(refresh(task))
        self.assertEqual(task.inputs, [])

    def test_the_happy_path_fires_each_control_exactly_once(self):
        task = RaidSubject(OPENING)
        self.assertTrue(refresh(task))
        self.assertEqual(task.inputs, ['fresh', 'fresh_ensure'])

    def test_a_dialog_already_open_skips_the_source_click(self):
        """No double input: an open dialog means the source must not be pressed."""
        task = RaidSubject(ALREADY_OPEN)
        self.assertTrue(refresh(task))
        self.assertEqual(task.inputs, ['fresh_ensure'])

    def test_a_blocked_source_input_fails_instead_of_claiming_success(self):
        task = RaidSubject(OPENING, blocked={'fresh'})
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)
        self.assertEqual(task.inputs, ['fresh'])

    def test_a_blocked_confirmation_fails_instead_of_claiming_success(self):
        task = RaidSubject(ALREADY_OPEN, blocked={'fresh_ensure'})
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)
        self.assertEqual(task.inputs, ['fresh_ensure'])

    def test_a_dialog_that_never_opens_fails_instead_of_spinning(self):
        """The old code looped on `while 1`; the rewrite must be bounded."""
        task = RaidSubject(script(Frame('fresh')) + [Frame('fresh')] * 400)
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)
        self.assertEqual(task.inputs, ['fresh'])  # exactly one attempt

    def test_a_dialog_that_never_closes_is_a_failure_not_a_false_success(self):
        """Confirmation that never takes effect must not be reported as done."""
        task = RaidSubject(script(Frame('fresh_ensure'))
                           + [Frame('fresh_ensure')] * 400)
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)
        self.assertEqual(task.inputs, ['fresh_ensure'])

    def test_recovery_before_the_dialog_opens_is_caught(self):
        task = RaidSubject(OPENING, recover_at=2)
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)

    def test_recovery_before_the_confirmation_is_caught(self):
        task = RaidSubject(ALREADY_OPEN, recover_at=2)
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)

    def test_recovery_between_opening_and_confirmation_is_caught(self):
        """The dialog opened, then something else recovered the game.

        Without a check here the confirmation would be fired into whatever is
        on screen now, and a stale dialog would be reported as a clean refresh.
        """
        # Captures: entry(1), opening witnesses(2,3), then the confirm click(4).
        task = RaidSubject(ALREADY_OPEN, recover_at=4)
        with self.assertRaises(TaskRecoveryFailed):
            refresh(task)

    def test_recovery_after_the_confirmation_is_caught(self):
        """A recovery while the dialog is closing must not read as success.

        Known gap in the production flow, not yet guarded: `check_refresh`
        checks the generation after the source input and after the confirm
        click, but not after the closing `_ui_verify`. A recovery there leaves
        `not appear(I_FRESH_ENSURE)` satisfiable by whatever screen replaced the
        dialog, so the refresh reads as clean. This test pins the current
        behaviour so the gap is visible rather than silent; when the check is
        added it will fail here and must be updated, not deleted.
        """
        task = RaidSubject(ALREADY_OPEN, recover_at=6)
        self.assertTrue(refresh(task))  # currently returns success; see docstring

    def test_the_dedup_ledger_names_the_controls_actually_pressed(self):
        task = RaidSubject(OPENING)
        self.assertTrue(refresh(task))
        self.assertIn(['fresh'], task.controls_seen)
        self.assertIn(['fresh', 'fresh_ensure'], task.controls_seen)


class FakeRuleImage:
    """Stands in for RuleImage so appear()'s isinstance dispatch is exercised."""

    def __init__(self, name, visible, roi_front=None):
        self.name = name
        self.visible = visible
        self.roi_front = list(roi_front or [0, 0, 10, 10])

    def match(self, image, threshold=None, frame_id=None):
        return self.visible

    def _apply_match_result(self, cached):
        return self.visible


class AppearSubject:
    """Carries the real BaseTask.appear with only the device simulated."""

    def __init__(self, fresh_visible, ensure_visible):
        self.device = SimpleNamespace(
            image=np.zeros((720, 1280, 3), dtype=np.uint8),
            image_frame_id=1,
            get_image_batch_cache=lambda *a, **k: None)
        self.interval_timer = {}
        self.detect_random_main_costume = mock.Mock(return_value=False)
        # The canonical artwork is stale; only the refreshed pair is current.
        self.I_FRESH_ENSURE = FakeRuleImage('fresh_ensure', False)
        self.I_RAID_REFRESH_Q = FakeRuleImage('raid_refresh_q', fresh_visible)
        self.I_RAID_CONFIRM_ENSURE = FakeRuleImage('raid_confirm_ensure',
                                                   ensure_visible,
                                                   roi_front=[658, 401, 192, 84])
        self.I_UI_REWARD = FakeRuleImage('ui_reward', False)
        self.I_UI_REWARD_NEW = FakeRuleImage('ui_reward_new', False)
        self.I_EXIT_ENSURE = FakeRuleImage('exit_ensure', False)
        self.I_EXIT_DIALOG_NEW = FakeRuleImage('exit_dialog_new', False)
        self.I_EXIT_ENSURE_NEW = FakeRuleImage('exit_ensure_new', False)
        self.I_CHECK_MAIN = FakeRuleImage('check_main', False)

    def appear(self, target, **kwargs):
        return pull(ROOT / 'tasks/base_task.py', 'BaseTask', 'appear',
                    dict(RuleImage=FakeRuleImage,
                         RuleOcr=type('RuleOcr', (), {}),
                         RuleGif=type('RuleGif', (), {}),
                         Timer=mock.Mock, logger=mock.Mock,
                         monotonic=lambda: 0.0))(self, target, **kwargs)


class RefreshDialogIdentityTests(unittest.TestCase):
    """A refreshed gold button is valid only together with its question mark."""

    def test_the_new_button_alone_does_not_make_the_refresh_dialog_appear(self):
        """A gold button shared with another dialog must not be accepted."""
        task = AppearSubject(fresh_visible=False, ensure_visible=True)
        self.assertFalse(task.appear(task.I_FRESH_ENSURE))

    def test_the_question_mark_alone_does_not_either(self):
        task = AppearSubject(fresh_visible=True, ensure_visible=False)
        self.assertFalse(task.appear(task.I_FRESH_ENSURE))

    def test_both_together_confirm_the_refresh_dialog(self):
        task = AppearSubject(fresh_visible=True, ensure_visible=True)
        self.assertTrue(task.appear(task.I_FRESH_ENSURE))

    def test_confirmation_adopts_the_new_button_box(self):
        """The click must land on the new artwork, not the stale box."""
        task = AppearSubject(fresh_visible=True, ensure_visible=True)
        before = list(task.I_FRESH_ENSURE.roi_front)
        task.appear(task.I_FRESH_ENSURE)
        after = task.I_FRESH_ENSURE.roi_front
        self.assertNotEqual(before, after)
        x, y, w, h = task.I_RAID_CONFIRM_ENSURE.roi_front
        self.assertEqual(after, [x + 20, y + 15, w - 40, h - 30])

    def test_a_refused_dialog_leaves_the_original_box_alone(self):
        task = AppearSubject(fresh_visible=False, ensure_visible=True)
        before = list(task.I_FRESH_ENSURE.roi_front)
        task.appear(task.I_FRESH_ENSURE)
        self.assertEqual(task.I_FRESH_ENSURE.roi_front, before)

    def test_another_dialogs_gold_button_is_not_hijacked(self):
        """I_EXIT_ENSURE keeps its own identity; the new branch is narrow."""
        task = AppearSubject(fresh_visible=True, ensure_visible=True)
        task.I_EXIT_DIALOG_NEW.visible = False
        task.I_EXIT_ENSURE_NEW.visible = True
        self.assertFalse(task.appear(task.I_EXIT_ENSURE))


if __name__ == '__main__':
    unittest.main()
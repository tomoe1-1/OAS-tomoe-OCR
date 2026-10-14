"""Netherworld start confirmation: two witnesses, one input, and proof of entry.

``I_NW_START_CONFIRM`` is a gold button that other dialogs also own, so the new
modal is accepted only when its own question line and its own button agree on
the same frame. Closing that modal is not proof a battle began, so an explicit
in-room witness is required afterwards.

These guards drive the real methods pulled out of the source. Only the screen,
the elapsed time and the control delivery are simulated.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('netherworld_start_confirm_visible', 'confirm_netherworld_start',
           'open_netherworld_challenge', 'netherworld')


def make_subject(screens):
    """A HuntSubject carrying the real netherworld methods.

    A fresh subclass per construction: the extracted code closes over the
    screen, and a class attribute would be rebound by every construction,
    leaving the earliest closure pointing at an object this test no longer owns.
    """
    methods = pull(dict(TaskRecoveryFailed=TaskRecoveryFailed,
                        logger=mock.Mock(), monotonic=lambda: 0.0))
    base = type('BoundHunt', (HuntSubject,), methods)
    return base(screens)


class Screen:
    """One screen: which artworks are up, and whether the battle room is."""

    def __init__(self, question=False, confirm=False, room=False):
        self.question = question
        self.confirm = confirm
        self.room = room

    @property
    def modal(self):
        """Both artworks, on this frame, are the dialog's identity."""
        return self.question and self.confirm


class HuntSubject:
    """A Hunt task carrying the real netherworld confirmation methods."""

    def __init__(self, screens):
        self.now = 0.0
        self.frames_taken = 0
        self.screens = list(screens)
        self.index = -1
        self.inputs = []
        self.blocked = set()
        self.recover_at = None
        self.device = SimpleNamespace(
            image=np.zeros((720, 1280, 3), dtype=np.uint8),
            image_frame_id=0, _escape_generation=0,
            performance=SimpleNamespace(timeout=lambda v: v),
            confirm_control_progress=mock.Mock())
        self.I_NW_START_Q = SimpleNamespace(name='nw_start_q')
        self.I_NW_START_CONFIRM = SimpleNamespace(name='nw_start_confirm')
        self.C_NW_START_CONFIRM = SimpleNamespace(name='nw_start_confirm_click')
                # Spelled as assets declare it: I_NW_CHALLAGE.
        self.I_NW_CHALLAGE = SimpleNamespace(name='nw_challenge')
        self.I_UI_CONFIRM = SimpleNamespace(name='ui_confirm')

    # -- simulated device --------------------------------------------------
    @property
    def screen(self):
        return self.screens[min(self.index, len(self.screens) - 1)]

    def appear(self, rule, **kwargs):
        """Reads the current screen; only a screenshot advances it."""
        if rule is self.I_NW_START_Q:
            return self.screen.question
        if rule is self.I_NW_START_CONFIRM:
            return self.screen.confirm
        if rule is self.I_UI_CONFIRM:
            return False
        return False

    def is_in_room(self, is_screenshot=True):
        return self.screen.room

    def screenshot(self):
        self.now += .1
        self.frames_taken += 1
        self.device.image_frame_id = self.frames_taken
        self.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        if self.index + 1 < len(self.screens):
            self.index += 1
        if self.recover_at is not None and self.frames_taken == self.recover_at:
            self.device._escape_generation += 1

    def appear_then_click(self, target, interval=None, action=None):
        """A click names the control it aimed at, plus the action it pressed."""
        self.inputs.append((target.name, getattr(action, 'name', None)))
        if target.name in self.blocked:
            return False
        self.screenshot()
        return True

    def _ui_verify(self, predicate, controls, timeout=12, fail=True):
        """The confirmation contract: two consecutive witnesses, then done.

        The real ``BaseTask._ui_verify`` also checks the recovery generation on
        every frame. This stand-in deliberately does not: reproducing that check
        here would cover for the guards in the methods under test and leave them
        unobservable.
        """
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


def pull(namespace):
    """Exec the real methods so nothing under test is re-implemented."""
    source = ast.parse(
        (ROOT / 'tasks/Hunt/script_task.py').read_text(encoding='utf-8'))
    cls = next(n for n in source.body
               if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
    body = [n for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__',
                              names=[ast.alias(name='annotations')], level=0),
              *body],
        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'hunt', 'exec'), namespace)
    return {name: namespace[name] for name in METHODS}


class ModalIdentityTests(unittest.TestCase):
    """The dialog is only the dialog when both artworks agree."""

    def test_the_button_alone_is_not_the_netherworld_dialog(self):
        task = make_subject([Screen(question=False, confirm=True)])
        self.assertFalse(task.netherworld_start_confirm_visible())

    def test_the_question_line_alone_is_not_either(self):
        task = make_subject([Screen(question=True, confirm=False)])
        self.assertFalse(task.netherworld_start_confirm_visible())

    def test_both_together_identify_the_dialog(self):
        task = make_subject([Screen(question=True, confirm=True)])
        self.assertTrue(task.netherworld_start_confirm_visible())

    def test_a_generic_gold_button_elsewhere_is_not_consulted(self):
        """I_UI_CONFIRM belongs to other dialogs and must stay out of it."""
        task = make_subject([Screen(question=False, confirm=False)])
        self.assertFalse(task.netherworld_start_confirm_visible())


class ConfirmStartTests(unittest.TestCase):
    """One bounded input, and an in-room witness rather than mere disappearance."""

    def subject(self, screens, blocked=(), recover_at=None):
        task = make_subject(screens)
        task.blocked = set(blocked)
        task.recover_at = recover_at
        return task

    def test_a_closed_modal_alone_is_not_reported_as_a_started_battle(self):
        """The dialog going away proves nothing; the room must be seen.

        The modal opens, is confirmed, and stays gone for good — only the battle
        room never appears. A flow that treated disappearance as success would
        return here instead of failing.
        """
        gone = Screen(question=False, confirm=False)
        task = self.subject([Screen(question=True, confirm=True)] * 3
                            + [gone] * 80)
        with self.assertRaises(TaskRecoveryFailed):
            task.confirm_netherworld_start()
        self.assertEqual(len(task.inputs), 1)

    def test_the_happy_path_confirms_once_and_reaches_the_room(self):
        task = self.subject([Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(question=False, confirm=False),
                             Screen(room=True),
                             Screen(room=True)])
        task.confirm_netherworld_start()
        self.assertEqual(task.inputs, [('nw_start_q', 'nw_start_confirm_click')])

    def test_a_blocked_confirmation_input_fails_loudly(self):
        task = self.subject([Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(room=True)] * 40,
                            blocked={'nw_start_q'})
        with self.assertRaises(TaskRecoveryFailed):
            task.confirm_netherworld_start()
        self.assertEqual(len(task.inputs), 1)

    def test_a_dialog_that_never_appears_fails_instead_of_spinning(self):
        task = self.subject([Screen()] * 200)
        with self.assertRaises(TaskRecoveryFailed):
            task.confirm_netherworld_start()
        self.assertEqual(task.inputs, [])

    def test_recovery_before_the_input_is_caught(self):
        task = self.subject([Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(room=True), Screen(room=True)],
                            recover_at=3)
        with self.assertRaises(TaskRecoveryFailed):
            task.confirm_netherworld_start()

    def test_the_click_aims_at_the_explicit_action_not_the_question(self):
        """The question line is the witness; the action carries the click."""
        task = self.subject([Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True),
                             Screen(room=True), Screen(room=True)])
        task.confirm_netherworld_start()
        target, action = task.inputs[0]
        self.assertEqual(action, 'nw_start_confirm_click')


class OpenChallengeTests(unittest.TestCase):
    """Opening the challenge is one input, then one of several valid outcomes."""

    def subject(self, screens, blocked=()):
        task = make_subject(screens)
        task.blocked = set(blocked)
        return task

    def test_a_blocked_challenge_input_fails_loudly(self):
        task = self.subject([Screen()], blocked={'nw_challenge'})
        with self.assertRaises(TaskRecoveryFailed):
            task.open_netherworld_challenge()
        self.assertEqual(len(task.inputs), 1)

    def test_the_modal_arriving_is_an_accepted_outcome(self):
        task = self.subject([Screen(question=True, confirm=True),
                             Screen(question=True, confirm=True)])
        task.open_netherworld_challenge()
        self.assertEqual(len(task.inputs), 1)

    def test_the_battle_room_is_an_accepted_outcome(self):
        task = self.subject([Screen(room=True), Screen(room=True)])
        task.open_netherworld_challenge()

    def test_nothing_recognised_fails_instead_of_spinning(self):
        task = self.subject([Screen()] * 200)
        with self.assertRaises(TaskRecoveryFailed):
            task.open_netherworld_challenge()
        self.assertEqual(len(task.inputs), 1)  # never retried in a loop


class LoopDispatchTests(unittest.TestCase):
    """The wait loop must hand a pending start dialog to its confirmation."""

    def test_a_dialog_sitting_on_screen_is_handled_before_anything_else(self):
        """Without the modal branch the loop walks past it and clicks blind.

        Only the loop's own dispatch is exercised here: the confirmation and the
        challenge openers are replaced by recorders, so a dismissal shows up as
        an unexpected blind input instead of a confusing downstream failure.
        """
        task = make_subject([Screen(question=True, confirm=True)] * 200)
        handled = []
        task.confirm_netherworld_start = lambda: handled.append('confirm')
        task.open_netherworld_challenge = lambda: handled.append('challenge')
        task._finish_already_challenged = lambda marker: handled.append('done')
        task.run_general_battle = lambda *a, **k: handled.append('battle')
        task.set_next_run = lambda *a, **k: None
        task.tomorrow_kirin_day = False
        task.config = SimpleNamespace(_learning_task_outcomes={},
                                      hunt=SimpleNamespace(
                                          netherworld_battle_config=None))
        task.con_time = SimpleNamespace(netherworld_time=SimpleNamespace(
            hour=19, minute=0, second=0))
        task.I_NW_DONE = SimpleNamespace(name='nw_done')
        task.I_NW = SimpleNamespace(name='nw')
        task.click_fire = lambda: handled.append('fire')
        task.I_FIRE = SimpleNamespace(name='fire')
        # Stop the loop as soon as it has dispatched, so the test cannot spin.
        def stop_after_first_dispatch():
            raise TaskRecoveryFailed('loop stopped by the test')
        task.confirm_netherworld_start = lambda: (
            handled.append('confirm'), stop_after_first_dispatch())

        with self.assertRaises(TaskRecoveryFailed):
            task.netherworld()
        self.assertEqual(handled, ['confirm'])


if __name__ == '__main__':
    unittest.main()
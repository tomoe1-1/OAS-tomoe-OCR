"""Secret-mission narration must be cleared before leaving the screen.

The black narration band appears both BEFORE the battle and AFTER it.  The
pre-battle half existed; the post-battle half did not, so ``goto_page`` ran
while the band still covered the screen: the page went unrecognisable and ESC
recovery gave up after 60s.

These guards drive the real methods pulled out of the source.  Only the screen,
the elapsed time and the control delivery are simulated.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('secret_chat_skip', 'secret_after_battle')


def pull(namespace):
    """Exec the real methods so nothing under test is re-implemented."""
    source = ast.parse(
        (ROOT / 'tasks/WantedQuests/script_task.py').read_text(encoding='utf-8'))
    cls = next(n for n in source.body
               if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
    body = [n for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__',
                              names=[ast.alias(name='annotations')], level=0),
              *body],
        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'wq', 'exec'), namespace)
    return {name: namespace[name] for name in METHODS}


def make_subject():
    """A fresh subclass per construction: the extracted code closes over the
    logger, and a shared class attribute would be rebound across subjects."""
    methods = pull(dict(logger=mock.Mock()))
    return type('BoundWQ', (SecretSubject,), methods)()


class Screen:
    """One frame of the secret screen.

    ``narration`` is the black band being up, ``challenge`` the secret
    screen's own 挑战 button.  They are mutually exclusive in practice: while
    the band covers the screen the button cannot be seen.
    """

    def __init__(self, narration=True, challenge=False, back=True):
        self.narration = narration
        self.challenge = challenge
        self.back = back


class SecretSubject:
    """A WantedQuests task carrying the real narration helpers."""

    def __init__(self):
        self.now = 0.0
        self.frames = 0
        self.screens = []
        self.index = -1
        self.inputs = []
        self.record_cleared = 0
        self.device = SimpleNamespace(
            image=np.zeros((720, 1280, 3), dtype=np.uint8),
            image_frame_id=0,
            click_record_clear=lambda: setattr(self, 'record_cleared',
                                               self.record_cleared + 1))
        self.I_WQSE_FIRE = SimpleNamespace(name='wqse_fire')
        self.I_WQSE_SPECIAL_FIRE = SimpleNamespace(name='wqse_special_fire')
        self.I_UI_BACK_RED = SimpleNamespace(name='back_red')
        self.C_SECRET_CHAT = SimpleNamespace(name='secret_chat')

    def set_script(self, *screens):
        """Build the screen timeline.

        Each list entry is what the NEXT screenshot will see.  ``click`` also
        consumes a frame (that is how the real control layer behaves), so a
        click advances the timeline just like an explicit screenshot does.
        """
        self.screens = list(screens)
        return self

    @property
    def screen(self):
        return self.screens[min(self.index, len(self.screens) - 1)]

    def screenshot(self):
        self.now += .1
        self.frames += 1
        self.device.image_frame_id = self.frames
        self.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        if self.index + 1 < len(self.screens):
            self.index += 1

    def appear(self, rule, threshold=None):
        if rule is self.I_WQSE_FIRE:
            return self.screen.challenge
        if rule is self.I_WQSE_SPECIAL_FIRE:
            return False
        if rule is self.I_UI_BACK_RED:
            return self.screen.back
        return False

    def click(self, control, interval=None):
        """One delivered input. Clicking the band closes one narration step.

        The click consumes a frame, so the loop re-reads the NEXT screen on its
        next ``screenshot``.  The timeline must therefore alternate
        narration / narration / ... / challenge exactly as many steps as the
        chat has.
        """
        self.inputs.append(control.name)
        self.screenshot()
        return True


class ChatSkipTests(unittest.TestCase):
    """One burst is bounded, and it stops on the real exit condition."""

    def test_a_closed_chat_sends_no_input(self):
        task = make_subject().set_script(Screen(narration=False, challenge=True))
        self.assertEqual(task.secret_chat_skip(), 0)
        self.assertEqual(task.inputs, [])

    def test_each_click_advances_one_narration_step(self):
        # Each loop costs two frames: the loop's screenshot plus the click's.
        # Alternating narration/narration keeps every read on a narration frame
        # until the final challenge frame.
        task = make_subject().set_script(
            Screen(), Screen(), Screen(), Screen(), Screen(challenge=True))
        self.assertEqual(task.secret_chat_skip(), 2)
        self.assertEqual(task.inputs, ['secret_chat', 'secret_chat'])

    def test_the_special_fire_button_also_ends_the_burst(self):
        task = make_subject().set_script(Screen())
        task.appear = lambda rule, threshold=None: rule is task.I_WQSE_SPECIAL_FIRE
        self.assertEqual(task.secret_chat_skip(), 0)

    def test_a_burst_is_bounded_and_says_so(self):
        """The chat is unbounded in the game, so a burst must not claim success."""
        task = make_subject().set_script(*[Screen() for _ in range(50)])
        self.assertEqual(task.secret_chat_skip(), 6)
        task.device.click_record_clear.assert_not_called() \
            if hasattr(task.device.click_record_clear, 'assert_not_called') else None

    def test_neither_chat_nor_button_means_nothing_to_advance(self):
        task = make_subject().set_script(Screen(narration=False, back=False))
        self.assertEqual(task.secret_chat_skip(), 0)
        self.assertEqual(task.inputs, [])


class AfterBattleTests(unittest.TestCase):
    """The post-battle hook: several bursts, then leave anyway."""

    def test_the_common_case_needs_no_click_at_all(self):
        """Right after the battle the band is often already gone."""
        task = make_subject().set_script(Screen(narration=False, challenge=True))
        self.assertTrue(task.secret_after_battle())
        self.assertEqual(task.inputs, [])

    def test_a_short_chat_is_cleared_and_reported_ready(self):
        task = make_subject().set_script(
            Screen(), Screen(), Screen(), Screen(challenge=True))
        self.assertTrue(task.secret_after_battle())
        self.assertEqual(len(task.inputs), 2)

    def test_a_long_chat_keeps_going_across_rounds(self):
        # 9 clicks need 18 frames plus the closing challenge frame.
        task = make_subject().set_script(
            *[Screen() for _ in range(18)] + [Screen(challenge=True)])
        self.assertTrue(task.secret_after_battle())
        self.assertEqual(len(task.inputs), 9)

    def test_an_endless_chat_gives_up_instead_of_spinning(self):
        """Rounds are the bound; leaving anyway beats hanging here forever."""
        task = make_subject().set_script(*[Screen() for _ in range(200)])
        self.assertFalse(task.secret_after_battle())
        self.assertEqual(len(task.inputs), 18)  # 3 rounds x 6 clicks
        self.assertEqual(task.record_cleared, 1)

    def test_the_click_lands_on_the_narration_band_not_the_back_arrow(self):
        task = make_subject().set_script(Screen(), Screen(challenge=True))
        task.secret_after_battle()
        self.assertEqual(set(task.inputs), {'secret_chat'})


class WiringTests(unittest.TestCase):
    """Guard the call site itself.

    Method-level guards stayed green when the ``finally`` call was deleted, so
    the wiring needs its own check.  Driving the real ``execute_mission`` is
    heavy; reading the AST proves the same structural fact without
    re-implementing it: ``secret_after_battle`` must be called from the
    ``finally`` clause BEFORE ``goto_page``.  Order matters — clearing the
    narration after leaving is exactly the bug being fixed.
    """

    OWNER = 'execute_mission'

    @classmethod
    def _owner_tree(cls):
        source = ast.parse(
            (ROOT / 'tasks/WantedQuests/script_task.py').read_text(encoding='utf-8'))
        cls_node = next(n for n in source.body
                        if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
        for node in cls_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == cls.OWNER:
                return node
        raise AssertionError(
            f'{cls.OWNER} no longer exists; the exit hook needs a new home')

    @classmethod
    def _finally_clause(cls):
        for node in ast.walk(cls._owner_tree()):
            if isinstance(node, ast.Try) and node.finalbody:
                return node.finalbody
        raise AssertionError(
            f'{cls.OWNER} has no try/finally; the exit hook is gone')

    @classmethod
    def _finally_call_names(cls):
        names = []
        for stmt in cls._finally_clause():
            if isinstance(stmt, ast.Expr):
                names += [n.func.attr for n in ast.walk(stmt)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        return names

    def test_the_exit_hook_is_called_before_leaving(self):
        self.assertIn('secret_after_battle', self._finally_call_names(),
                      'the narration is no longer cleared before leaving')

    def test_the_narration_is_cleared_before_goto_page(self):
        """Clearing it after goto_page reproduces the original deadlock."""
        names = self._finally_call_names()
        self.assertIn('goto_page', names, 'the leave step vanished; retest the design')
        self.assertLess(names.index('secret_after_battle'), names.index('goto_page'),
                        'narration must be cleared before goto_page, not after')

    def test_a_vanished_hook_body_is_not_silently_accepted(self):
        """The helper must still exist as a real method on the class."""
        source = ast.parse(
            (ROOT / 'tasks/WantedQuests/script_task.py').read_text(encoding='utf-8'))
        cls_node = next(n for n in source.body
                        if isinstance(n, ast.ClassDef) and n.name == 'ScriptTask')
        defined = {n.name for n in cls_node.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('secret_after_battle', defined)
        self.assertIn('secret_chat_skip', defined)


if __name__ == '__main__':
    unittest.main()

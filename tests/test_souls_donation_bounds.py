"""Unstable donation state must stop instead of repeatedly selecting souls."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]


def subject(amounts, *, lose_donate=False):
    state = dict(now=0, frames=0, visible={'I_ST_CAT', 'I_ST_ABANDONED_SELECTED', 'I_ST_DONATE'},
                 inputs=[], reads=0)
    methods = {'greed_maneki', '_click_once', '_verify_state', '_discard_page', '_donation_selected'}
    tree = ast.parse((ROOT / 'tasks/SoulsTidy/script_task.py').read_text(encoding='utf-8'))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == 'ScriptTask')
    extracted = ast.Module(body=[ast.ClassDef(name='Subject', bases=[], keywords=[], decorator_list=[],
                                             body=[node for node in original.body
                                                   if isinstance(node, ast.FunctionDef)
                                                   and node.name in methods])], type_ignores=[])
    namespace = dict(monotonic=lambda: state['now'], logger=Mock(), TaskRecoveryFailed=TaskRecoveryFailed)
    exec(compile(ast.fix_missing_locations(extracted), 'souls_donation_subject', 'exec'), namespace)
    obj = namespace['Subject']()
    obj.config = SimpleNamespace(souls_tidy=SimpleNamespace(simple_tidy=SimpleNamespace(
        enable_greed=False, enable_maneki=True)))
    obj.device = SimpleNamespace(_escape_generation=0, image=None, confirm_control_progress=Mock())
    for name in ('I_ST_CAT', 'I_ST_ABANDONED_SELECTED', 'I_ST_DONATE', 'I_UI_BACK_RED',
                 'I_ST_UNSELECTED', 'I_UI_CONFIRM', 'I_ST_GREED_CLOSE', 'I_ST_BONGNA',
                 'I_ST_FEED_NOW', 'L_ONE'):
        setattr(obj, name, SimpleNamespace(name=name))

    def screenshot():
        state['now'] += .5
        state['frames'] += 1
        if state['frames'] > 50:
            raise AssertionError('Donation loop did not stop')
        obj.device.image = state['frames']

    def read_amount(image):
        amount = amounts[state['reads']]
        state['reads'] += 1
        # The button was available in both selection confirmation frames, then
        # disappeared before the final action decision.
        if lose_donate and state['reads'] == 3:
            state['visible'].discard('I_ST_DONATE')
        return amount

    obj.screenshot = Mock(side_effect=screenshot)
    obj.appear = Mock(side_effect=lambda rule, **kwargs: rule.name in state['visible'])
    obj.click = Mock(side_effect=lambda rule, **kwargs: state['inputs'].append(rule.name) or True)
    obj.pre_confirm = Mock()
    obj.find_discard_souls = Mock(side_effect=[True, False])
    obj.O_ST_GOLD = SimpleNamespace(ocr=Mock(side_effect=read_amount))
    obj.donate_and_collect_reward = Mock()
    return obj, state


class DonationBoundsTests(unittest.TestCase):
    def test_amount_fluctuating_after_verified_selection_does_not_select_again(self):
        for invalid in (0, '', None, -1):
            with self.subTest(amount=invalid):
                obj, state = subject([100, 100, invalid])
                with self.assertRaisesRegex(TaskRecoveryFailed, 'amount changed'):
                    obj.greed_maneki()
                self.assertEqual(state['inputs'], ['L_ONE'])
                self.assertEqual(state['reads'], 3)
                obj.find_discard_souls.assert_called_once()
                obj.donate_and_collect_reward.assert_not_called()

    def test_missing_donate_after_verified_selection_does_not_select_again(self):
        obj, state = subject([100, 100, 100], lose_donate=True)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'button disappeared'):
            obj.greed_maneki()
        self.assertEqual(state['inputs'], ['L_ONE'])
        obj.find_discard_souls.assert_called_once()
        obj.donate_and_collect_reward.assert_not_called()

    def test_stable_selection_reaches_one_donation_then_finishes_an_empty_list(self):
        obj, state = subject([100, 100, 100])
        obj.greed_maneki()
        self.assertEqual(state['inputs'], ['L_ONE'])
        obj.donate_and_collect_reward.assert_called_once()
        self.assertEqual(obj.find_discard_souls.call_count, 2)


if __name__ == '__main__':
    unittest.main()

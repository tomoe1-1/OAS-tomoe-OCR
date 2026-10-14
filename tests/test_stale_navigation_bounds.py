"""Replay stale navigation frames without loading drivers or sending input."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from module.exception import TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]


class RuleImage:
    def __init__(self, name):
        self.name = name


class RuleClick:
    def __init__(self, name):
        self.name = name


class RuleOcr:
    pass


class RuleGif(RuleImage):
    pass


def replay(source_path, method_name, visible, transitions=None):
    """Execute the real task method and shared one-input verification helpers."""
    methods = []
    selections = [(ROOT / 'tasks/base_task.py', {'_ui_action_once', '_ui_verify', 'ui_click'}),
                  (ROOT / source_path, {method_name})]
    for path, names in selections:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods.extend(item for item in node.body
                               if isinstance(item, ast.FunctionDef) and item.name in names)
    state = dict(now=0.0, frame=0, visible=set(visible), pending=None, events=[])
    namespace = dict(monotonic=lambda: state['now'], logger=Mock(), RuleImage=RuleImage,
                     RuleGif=RuleGif, RuleOcr=RuleOcr, TaskRecoveryFailed=TaskRecoveryFailed)
    extracted = ast.Module(body=[ast.ImportFrom(module='__future__',
                                                names=[ast.alias(name='annotations')], level=0),
                                  ast.ClassDef(name='Subject', bases=[], keywords=[],
                                               decorator_list=[], body=methods)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(extracted), str(source_path), 'exec'), namespace)
    obj = namespace['Subject']()
    image_rules = ['I_ABYSS_NAVIGATION', 'I_ABYSS_MAP', 'I_SA_NETEASE_GAME_LOGO',
                   'I_SA_ACCOUNT_LOGIN_BTN', 'I_SA_SWITCH_ACCOUNT_BTN', 'I_CHECK_LOGIN_FORM']
    for name in image_rules:
        setattr(obj, name, RuleImage(name))
    obj.C_SA_LOGIN_FORM_USER_CENTER = RuleClick('C_SA_LOGIN_FORM_USER_CENTER')
    obj.device = SimpleNamespace(_escape_generation=0,
                                 performance=SimpleNamespace(timeout=lambda value: value),
                                 confirm_control_progress=Mock())
    obj.change_area = Mock()

    def screenshot():
        state['now'] += 0.5
        state['frame'] += 1
        if state['frame'] > 200:
            raise AssertionError('Navigation did not terminate')
        state['events'].append(('screenshot', state['frame']))
        if state['pending'] is not None:
            state['visible'] = state['pending']
            state['pending'] = None

    def click(control, **kwargs):
        state['events'].append(('input', state['frame'], control.name))
        if transitions and control.name in transitions:
            state['pending'] = set(transitions[control.name])
        return True

    obj.screenshot = Mock(side_effect=screenshot)
    obj.appear = Mock(side_effect=lambda control, **kwargs: control.name in state['visible'])
    obj.click = Mock(side_effect=click)
    obj.appear_then_click = Mock(side_effect=lambda control, **kwargs:
                                click(control, **kwargs) if obj.appear(control) else False)
    obj.ocr_appear_click = Mock(return_value=False)
    return obj, state


class AbyssNavigationBoundsTests(unittest.TestCase):
    def subject(self, visible, transitions=None):
        return replay('tasks/AbyssShadows/script_task.py', 'check_available', visible, transitions)

    def test_map_already_open_uses_fresh_verification_without_an_input(self):
        obj, state = self.subject({'I_ABYSS_MAP'})
        self.assertTrue(obj.check_available(SimpleNamespace(get_areatype=lambda: 'dragon')))
        self.assertGreaterEqual(state['frame'], 3)
        obj.appear_then_click.assert_not_called()

    def test_one_navigation_input_is_followed_by_fresh_map_frames(self):
        obj, state = self.subject({'I_ABYSS_NAVIGATION'},
                                 {'I_ABYSS_NAVIGATION': {'I_ABYSS_MAP'}})
        self.assertTrue(obj.check_available(SimpleNamespace(get_areatype=lambda: 'dragon')))
        obj.appear_then_click.assert_called_once()
        inputs = [event for event in state['events'] if event[0] == 'input']
        self.assertGreaterEqual(state['frame'] - inputs[0][1], 2)

    def test_persistent_navigation_fails_after_one_input(self):
        obj, state = self.subject({'I_ABYSS_NAVIGATION'})
        with self.assertRaises(TaskRecoveryFailed):
            obj.check_available(SimpleNamespace(get_areatype=lambda: 'dragon'))
        obj.appear_then_click.assert_called_once()
        self.assertLessEqual(state['now'], 13)

    def test_unknown_page_terminates_without_input(self):
        obj, state = self.subject(set())
        with self.assertRaises(TaskRecoveryFailed):
            obj.check_available(SimpleNamespace(get_areatype=lambda: 'dragon'))
        obj.click.assert_not_called()
        self.assertLessEqual(state['now'], 13)


class LoginNavigationBoundsTests(unittest.TestCase):
    ACCOUNT = {'I_SA_NETEASE_GAME_LOGO', 'I_SA_ACCOUNT_LOGIN_BTN'}

    def subject(self, visible, transitions=None):
        return replay('tasks/Component/SwitchAccount/login_account.py', 'jump2SelectAccount',
                      visible, transitions)

    def test_already_at_account_page_is_confirmed_in_fresh_frames(self):
        obj, state = self.subject(self.ACCOUNT)
        obj.jump2SelectAccount()
        obj.click.assert_not_called()
        self.assertGreaterEqual(state['frame'], 2)

    def test_two_login_transitions_each_send_once_and_verify_before_the_next_input(self):
        obj, state = self.subject({'I_CHECK_LOGIN_FORM'}, {
            'C_SA_LOGIN_FORM_USER_CENTER': {'I_SA_SWITCH_ACCOUNT_BTN'},
            'I_SA_SWITCH_ACCOUNT_BTN': self.ACCOUNT})
        obj.jump2SelectAccount()
        inputs = [event for event in state['events'] if event[0] == 'input']
        self.assertEqual([event[2] for event in inputs],
                         ['C_SA_LOGIN_FORM_USER_CENTER', 'I_SA_SWITCH_ACCOUNT_BTN'])
        self.assertGreaterEqual(inputs[1][1] - inputs[0][1], 2)
        self.assertGreaterEqual(state['frame'] - inputs[1][1], 2)

    def test_user_center_that_does_not_open_cannot_replay_its_click(self):
        obj, state = self.subject({'I_CHECK_LOGIN_FORM'})
        with self.assertRaises(TaskRecoveryFailed):
            obj.jump2SelectAccount()
        obj.click.assert_called_once_with(obj.C_SA_LOGIN_FORM_USER_CENTER, interval=1.5)
        obj.appear_then_click.assert_not_called()
        self.assertLessEqual(state['now'], 15)

    def test_stuck_switch_button_sends_once_then_fails(self):
        obj, state = self.subject({'I_SA_SWITCH_ACCOUNT_BTN'})
        with self.assertRaises(TaskRecoveryFailed):
            obj.jump2SelectAccount()
        obj.appear_then_click.assert_called_once_with(obj.I_SA_SWITCH_ACCOUNT_BTN, interval=1.5)
        self.assertLessEqual(state['now'], 15)

    def test_unknown_page_has_a_30_second_deadline(self):
        obj, state = self.subject(set())
        with self.assertRaises(TaskRecoveryFailed):
            obj.jump2SelectAccount()
        obj.click.assert_not_called()
        self.assertEqual(state['now'], 30)

    def test_a_login_button_without_the_logo_is_not_reported_as_success(self):
        obj, state = self.subject({'I_SA_SWITCH_ACCOUNT_BTN'},
                                 {'I_SA_SWITCH_ACCOUNT_BTN': {'I_SA_ACCOUNT_LOGIN_BTN'}})
        with self.assertRaises(TaskRecoveryFailed):
            obj.jump2SelectAccount()
        obj.appear_then_click.assert_called_once()
        self.assertLessEqual(state['now'], 33)


if __name__ == '__main__':
    unittest.main()

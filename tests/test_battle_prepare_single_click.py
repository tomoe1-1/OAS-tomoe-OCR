"""Exercise the real fallback GeneralBattle class and page detection contract."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from module.device.performance import PerformanceProfile
from module.base.timer import Timer
from module.exception import TaskRecoveryFailed
from tasks.Component.GeneralBattle.general_battle import GeneralBattle, BattleAction
from tasks.GameUi.navigator import GameUi
from tasks.GameUi.page import page_battle, page_battle_result, page_reward
from tasks.GameUi.session import NavigatorSession

def subject():
    # The navigator injects this fallback into tasks without GameUi inheritance.
    obj=GeneralBattle.__new__(GeneralBattle)
    obj.navigator=NavigatorSession('global')
    obj.navigator.bootstrap([page_battle,page_battle_result,page_reward])
    obj.match_page_once=lambda page:GameUi.match_page_once(obj,page)
    obj.prepare_appear_cache=Mock()
    obj.device=SimpleNamespace(performance=PerformanceProfile(),_escape_generation=0,
                               confirm_control_progress=Mock(),click=Mock())
    obj._prepare_click_ready=Mock(return_value=True)
    obj._run_battle_behavior_once=Mock()
    obj.appear_then_click=Mock(side_effect=lambda rule,**kw:rule is obj.I_PREPARE_HIGHLIGHT)
    obj.appear=lambda rule,**kw:rule in (obj.I_BATTLE_INFO,obj.I_PREPARE_HIGHLIGHT)
    obj.screenshot=Mock()
    return obj,SimpleNamespace(last_page=None),SimpleNamespace()

class PrepareClickTests(unittest.TestCase):
    def test_unlocked_battle_waits_for_screen_to_be_interactive(self):
        obj,_,_=subject()
        obj._prepare_click_ready=GeneralBattle._prepare_click_ready.__get__(obj,GeneralBattle)
        obj._is_member_battle=Mock(return_value=False)
        context=SimpleNamespace(prepare_click_timer=Timer(3))
        config=SimpleNamespace(lock_team_enable=False)
        self.assertFalse(obj._prepare_click_ready(context,config))
        self.assertEqual(context.prepare_click_timer.limit,1.5)
        context.prepare_click_timer._current-=2
        self.assertTrue(obj._prepare_click_ready(context,config))

    def test_locked_battle_keeps_longer_prepare_delay(self):
        obj,_,_=subject()
        obj._prepare_click_ready=GeneralBattle._prepare_click_ready.__get__(obj,GeneralBattle)
        obj._is_member_battle=Mock(return_value=False)
        context=SimpleNamespace(prepare_click_timer=Timer(3))
        self.assertFalse(obj._prepare_click_ready(context,SimpleNamespace(lock_team_enable=True)))
        self.assertEqual(context.prepare_click_timer.limit,3.0)

    def test_real_fallback_enters_verified_battle_with_one_input(self):
        obj,context,config=subject()
        self.assertFalse(hasattr(obj,'detect_page_in'))
        self.assertEqual(obj._handle_prepare(context,config),BattleAction.CONTINUE)
        obj.device.click.assert_called_once_with(*obj.I_PREPARE_HIGHLIGHT.front_center(),
                                                 control_name=obj.I_PREPARE_HIGHLIGHT.name)
        self.assertGreaterEqual(obj.screenshot.call_count,2)
        obj.device.confirm_control_progress.assert_called_once_with([obj.I_PREPARE_HIGHLIGHT])

    def test_unrecognized_transition_does_not_replay_prepare(self):
        obj,context,config=subject()
        obj._ui_verify=Mock(side_effect=TaskRecoveryFailed('no battle'))
        with self.assertRaises(TaskRecoveryFailed):obj._handle_prepare(context,config)
        obj.device.click.assert_called_once()

    def test_ready_button_disappearing_confirms_click_without_replay(self):
        obj,context,config=subject()
        obj.appear=lambda rule,**kw:rule is obj.I_PREPARE_HIGHLIGHT and obj.screenshot.call_count == 0
        obj._handle_prepare(context,config)
        obj.device.click.assert_called_once()
        obj.device.confirm_control_progress.assert_called_once_with([obj.I_PREPARE_HIGHLIGHT])

    def test_unavailable_prepare_button_does_not_wait_for_transition(self):
        obj,context,config=subject()
        obj.appear=lambda rule,**kw:rule is obj.I_BATTLE_INFO
        obj._ui_verify=Mock()
        obj._handle_prepare(context,config);obj._ui_verify.assert_not_called();obj.device.click.assert_not_called()

    def test_new_reward_page_confirms_transition_despite_static_click_area(self):
        obj,context,_=subject();context.last_page=page_battle_result
        obj.device._verified_click=dict(name='SETTLEMENT_RANDOM_CLICK',changed=False)
        obj._confirm_battle_page_transition(context,page_reward)
        obj.device.confirm_control_progress.assert_called_once_with(['SETTLEMENT_RANDOM_CLICK'])

    def test_same_or_unknown_page_does_not_authorize_repeat(self):
        obj,context,_=subject();context.last_page=page_battle_result
        obj.device._verified_click=dict(name='SETTLEMENT_RANDOM_CLICK',changed=False)
        obj._confirm_battle_page_transition(context,page_battle_result)
        obj._confirm_battle_page_transition(context,None)
        obj.device.confirm_control_progress.assert_not_called()

if __name__=='__main__':unittest.main()

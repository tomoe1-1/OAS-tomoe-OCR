"""Post-draft captain/member decisions must not depend on a legible win digit."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from tasks.DraftDuel.assets import DraftDuelAssets as Assets
from module.exception import GameStuckError, TaskEnd
from test_optimization_20260928 import draft_methods


ROOT = Path(__file__).resolve().parents[1]


def subject():
    obj = draft_methods({'_post_draft_lobby_role', '_advance_post_draft_lobby',
                         '_resume_post_draft_match', '_wait_battle_return_impl', '_run_matches'})
    obj.I_POST_DRAFT_ROSTER = deepcopy(Assets.I_POST_DRAFT_ROSTER)
    obj.I_LOBBY_BATTLE = deepcopy(Assets.I_LOBBY_BATTLE)
    obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
        (ROOT / 'tests/fixtures/draft_captain_lobby.png').read_bytes(), np.uint8),
        cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    obj.O_LOBBY_SCORE = SimpleNamespace(ocr=Mock(return_value='番胜 X X'))
    obj.O_LOBBY_BUTTON = SimpleNamespace(ocr=Mock(return_value='战'))
    obj.appear = Mock(return_value=True)
    obj.screenshot = Mock()
    obj.click = Mock(return_value=True)
    obj._require_open_for_match = Mock()
    obj._duel_share_reset = Mock()
    obj._wait_battle_return = Mock()
    obj._screen_text = Mock(return_value='')
    obj._screen_results = Mock(return_value=[])
    obj._battle_ready = Mock(return_value=False)
    obj.device._time_critical_clicks = False
    obj.device.stuck_record_clear = Mock()
    return obj


class DraftCaptainLobbyTests(unittest.TestCase):
    def test_user_screenshot_is_captain_even_when_score_digit_is_missing(self):
        obj = subject()
        obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/draft_captain_lobby.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        obj.appear.side_effect = lambda rule: rule.multi_scale_template_match(obj.device.image)
        self.assertEqual(obj._post_draft_lobby_role(), 'captain')
        obj._advance_post_draft_lobby('captain')
        obj.click.assert_not_called()
        obj._advance_post_draft_lobby(obj._post_draft_lobby_role())
        obj.click.assert_called_once_with(obj.I_LOBBY_BATTLE, interval=1.0)
        self.assertGreater(obj.I_LOBBY_BATTLE.roi_front[0], 1100)

    def test_war_button_without_post_draft_marker_is_not_a_resume_point(self):
        obj = subject()
        obj.appear.side_effect = lambda rule: rule is obj.I_LOBBY_BATTLE
        self.assertIsNone(obj._post_draft_lobby_role())
        obj.O_LOBBY_SCORE.ocr.assert_not_called()
        self.assertFalse(obj._resume_post_draft_match(SimpleNamespace(battle_timeout_seconds=60)))
        obj._wait_battle_return.assert_not_called()

    def test_dimmed_lobby_behind_result_overlay_does_not_start_battle(self):
        obj = subject()
        obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/draft_lobby_result.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        obj.appear.side_effect = lambda rule: rule.multi_scale_template_match(obj.device.image)
        self.assertIsNone(obj._post_draft_lobby_role())
        obj.click.assert_not_called()

    def test_member_waits_without_pressing_war_or_exit(self):
        obj = subject()
        obj.appear.side_effect = lambda rule: rule is obj.I_POST_DRAFT_ROSTER
        obj.O_LOBBY_BUTTON.ocr.return_value = '等待队长'
        self.assertEqual(obj._post_draft_lobby_role(), 'member')
        for _ in range(4):
            obj._advance_post_draft_lobby('member')
        obj.click.assert_not_called()
        obj._require_open_for_match.assert_not_called()

    def test_loading_button_is_waited_for_without_guessing_coordinates(self):
        obj = subject()
        obj.appear.side_effect = lambda rule: rule is obj.I_POST_DRAFT_ROSTER
        obj.O_LOBBY_BUTTON.ocr.return_value = ''
        self.assertEqual(obj._post_draft_lobby_role(), 'waiting')
        obj._advance_post_draft_lobby('waiting')
        obj.click.assert_not_called()

    def test_member_to_captain_transfer_requires_two_fresh_frames(self):
        obj = subject()
        obj._advance_post_draft_lobby('member')
        obj._advance_post_draft_lobby('member')
        obj._advance_post_draft_lobby('captain')
        obj.click.assert_not_called()
        obj._advance_post_draft_lobby('captain')
        obj.click.assert_called_once()

    def test_six_wins_is_terminal_even_if_war_is_visible(self):
        obj = subject()
        obj.O_LOBBY_SCORE.ocr.return_value = '6番胜'
        self.assertEqual(obj._post_draft_lobby_role(), 'finished')
        obj._advance_post_draft_lobby('finished')
        obj.click.assert_not_called()

    def test_war_retry_is_spaced_and_bounded_before_fourth_click(self):
        obj = subject()
        obj._lobby_role = 'captain'
        obj._lobby_stable_frames = 2
        with patch.object(time, 'monotonic', return_value=100):
            obj._advance_post_draft_lobby('captain')
        with patch.object(time, 'monotonic', return_value=104):
            obj._advance_post_draft_lobby('captain')
        self.assertEqual(obj.click.call_count, 1)
        for now in (105, 110):
            with patch.object(time, 'monotonic', return_value=now):
                obj._advance_post_draft_lobby('captain')
        with patch.object(time, 'monotonic', return_value=115):
            with self.assertRaises(GameStuckError):
                obj._advance_post_draft_lobby('captain')
        self.assertEqual(obj.click.call_count, 3)

    def test_blocked_war_click_is_not_recorded_as_started(self):
        obj = subject()
        obj.click.return_value = False
        obj._advance_post_draft_lobby('captain')
        obj._advance_post_draft_lobby('captain')
        self.assertFalse(getattr(obj, '_lobby_start_pending', False))
        self.assertEqual(getattr(obj, '_lobby_start_attempts', 0), 0)

    def test_war_click_rest_flag_restored_even_if_control_fails(self):
        for initial in (False, True):
            obj = subject()
            obj.device._time_critical_clicks = initial
            obj._advance_post_draft_lobby('captain')
            def fail(*args, **kw):
                self.assertTrue(obj.device._time_critical_clicks)
                raise RuntimeError('device unavailable')
            obj.click.side_effect = fail
            with self.assertRaises(RuntimeError):
                obj._advance_post_draft_lobby('captain')
            self.assertIs(obj.device._time_critical_clicks, initial)

    def test_resume_uses_existing_lobby_without_new_matchmaking(self):
        obj = subject()
        settings = SimpleNamespace(battle_timeout_seconds=120)
        self.assertTrue(obj._resume_post_draft_match(settings))
        obj._wait_battle_return.assert_called_once_with(120, [])

    def test_restart_in_active_battle_waits_without_navigating_out(self):
        obj = subject()
        obj._post_draft_lobby_role = Mock(return_value=None)
        obj.appear = Mock(return_value=True)
        self.assertTrue(obj._resume_post_draft_match(SimpleNamespace(battle_timeout_seconds=120)))
        obj._wait_battle_return.assert_called_once_with(120, [])
        obj.click.assert_not_called()

    def test_unrelated_page_does_not_resume_battle(self):
        obj = subject()
        obj._post_draft_lobby_role = Mock(return_value=None)
        obj.appear = Mock(return_value=False)
        self.assertFalse(obj._resume_post_draft_match(SimpleNamespace(battle_timeout_seconds=120)))
        obj._wait_battle_return.assert_not_called()

    def test_run_resumes_before_trying_to_navigate_back_to_town(self):
        obj = subject()
        obj.config = SimpleNamespace(draft_duel=SimpleNamespace(draft_duel_config=
            SimpleNamespace(pick_timeout_seconds=30, completion_count=1, battle_timeout_seconds=120)))
        obj._round_number = Mock(return_value=None)
        obj._enter_first_round = Mock()
        obj.set_next_run = Mock()
        with self.assertRaises(TaskEnd):
            obj._run_matches()
        obj._enter_first_round.assert_not_called()
        obj._wait_battle_return.assert_called_once_with(120, [])
        obj.set_next_run.assert_called_once_with(task='DraftDuel', success=True, finish=True)

    def test_lobby_action_runs_before_unrelated_settlement_ocr(self):
        obj = subject()
        obj._post_draft_lobby_role = Mock(side_effect=['captain', 'captain', 'finished', 'finished'])
        with patch.object(time, 'sleep'):
            obj._wait_battle_return_impl(120, [])
        obj.click.assert_called_once_with(obj.I_LOBBY_BATTLE, interval=1.0)

    def test_verified_preparation_clears_only_war_button_progress(self):
        obj = subject()
        obj._lobby_start_pending = True
        obj._lobby_start_attempts = 2
        obj._post_draft_lobby_role = Mock(side_effect=[None, 'finished', 'finished'])
        obj.O_TEAMMATE_LEAVE = SimpleNamespace(ocr=lambda _: '')
        obj.O_BATTLE_DEFEAT = SimpleNamespace(ocr=lambda _: '')
        obj._victory_screen = Mock(return_value=False)
        obj._battle_ready = Mock(return_value=True)
        obj._select_battle_lineup = Mock()
        with patch.object(time, 'sleep'):
            obj._wait_battle_return_impl(120, [])
        obj.device.confirm_control_progress.assert_called_once_with([obj.I_LOBBY_BATTLE])
        self.assertFalse(obj._lobby_start_pending)
        self.assertEqual(obj._lobby_start_attempts, 0)
        self.assertGreaterEqual(obj._battle_ready.call_count, 2)


if __name__ == '__main__':
    unittest.main()

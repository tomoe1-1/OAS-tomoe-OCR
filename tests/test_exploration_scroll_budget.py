"""The exploration scroll budget must stay reachable under the repeat-click fence.

``c974c20f`` tightened ``performance.click_limit`` from 10 to 4 without touching
the exploration criteria that compared ``click_record.count(...) >= 6``. The
fourth swipe is therefore rejected by ``GameTooManyClickError`` before any
``>= 6`` branch can ever run, so a map with nothing to fight always ended in a
60s pause plus an ESC that landed on the exit dialog. Observed 2026-10-08
onward, four times on tomoe1 alone on 10-11.
"""
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock

import numpy as np

from module.device.performance import PerformanceProfile
from tasks.Exploration.config import ExplorationLevel, UserStatus
from tasks.Exploration import page as pages
from tasks.Exploration.base import (
    EXPLORATION_SCROLL_LIMIT, BaseExploration,
)
from tasks.Exploration.script_task import ScriptTask


class ScrollBudgetTests(unittest.TestCase):
    def subject(self):
        task = BaseExploration.__new__(BaseExploration)
        # A real repeat-click ledger, so the fence behaves as it does on device.
        task.device = SimpleNamespace(click_record=deque(maxlen=15))
        task._match_end = mock.Mock(stable=mock.Mock(return_value=False))
        return task

    def test_scroll_budget_is_reachable_before_the_repeat_click_fence(self):
        # The whole point: the budget must be spent before click_limit trips.
        self.assertLess(EXPLORATION_SCROLL_LIMIT,
                        PerformanceProfile(low_spec=False).click_limit,
                        'scroll budget must be smaller than the click fence')

    def test_swiping_with_nothing_to_fight_still_reaches_the_end_of_the_map(self):
        # Drive the real run_on_exp_main with an empty map and a fence that
        # raises exactly as the device does. The exploration must leave through
        # arrive_end, not through GameTooManyClickError.
        task = self.empty_map_task()
        swipes = self.swipe_until_quit(task)
        self.assertEqual(len(swipes), EXPLORATION_SCROLL_LIMIT,
                         'must slide exactly the budget, then stop')
        task.quit_exp_main.assert_called_once()

    def empty_map_task(self, **overrides):
        """A run_on_exp_main subject whose map proves itself empty.

        The empty-map proof (`_confirm_empty_end`) only accepts a fresh frame
        that still reads as the exploration map with nothing on it, so every
        collaborator it consults is part of that claim.
        """
        task = ScriptTask.__new__(ScriptTask)
        device = SimpleNamespace(click_record=deque(maxlen=15),
                                 image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                 image_frame_id=1, _escape_generation=0,
                                 _accepted_invitation_generation=0)
        for name, value in overrides.items():
            setattr(device, name, value)
        task.device = device
        task._match_end = mock.Mock(stable=mock.Mock(return_value=False))
        task.S_SWIPE_BACKGROUND_RIGHT = SimpleNamespace(name='swipe_background_right')
        task.fire_monster_type = ''
        task.user_status = UserStatus.ALONE
        task.pre_page = None
        task.collect_reward = mock.Mock(return_value=False)
        task.switch_rotate = mock.Mock(return_value=False)
        task.get_fire_button = mock.Mock(return_value=None)
        task.I_TEAM_EMOJI = 'I_TEAM_EMOJI'
        task.quit_exp_main = mock.Mock()
        task.arrive_end = BaseExploration.arrive_end.__get__(task)
        task.screenshot = mock.Mock(
            side_effect=lambda: setattr(task.device, 'image_frame_id',
                                        task.device.image_frame_id + 1))
        task._default_detect_categories = mock.Mock(return_value={'exploration'})
        task.navigator = mock.Mock(all_pages=mock.Mock(
            return_value=[pages.page_exp_main]))
        task.match_page_once = mock.Mock(return_value=True)
        task.appear = mock.Mock(return_value=False)
        task.search_up_fight = mock.Mock(return_value=None)
        task._config = SimpleNamespace(
            exploration_config=SimpleNamespace(collect_paper_reward=False,
                                              exploration_level=ExplorationLevel.EXPLORATION_28))
        task.I_BOSS_BATTLE_BUTTON = 'I_BOSS_BATTLE_BUTTON'
        task.I_E_REWARD_BOX_SMALL = 'I_E_REWARD_BOX_SMALL'
        task.I_E_REWARD_BOX_BIG = 'I_E_REWARD_BOX_BIG'
        task.I_BATTLE_REWARD = 'I_BATTLE_REWARD'
        return task

    def swipe_until_quit(self, task, rounds=EXPLORATION_SCROLL_LIMIT + 3):
        swipes = []

        def swipe(rule, interval=None):
            swipes.append(rule.name)
            task.device.click_record.append(rule.name)
            if sum(1 for name in task.device.click_record if name == rule.name) \
                    >= PerformanceProfile(low_spec=False).click_limit:
                from module.exception import GameTooManyClickError
                raise GameTooManyClickError(f'Too many click for a button: {rule.name}')
            return True

        task.swipe = swipe
        for _ in range(rounds):
            if task.quit_exp_main.called:
                break
            task.run_on_exp_main()
        return swipes

    def test_a_stale_capture_never_counts_as_an_empty_map(self):
        """Re-capturing the same frame is not evidence the screen settled."""
        task = self.empty_map_task()
        task.screenshot = mock.Mock()  # image_frame_id never advances
        task.run_on_exp_main()
        task.quit_exp_main.assert_not_called()

    def test_recovery_during_the_empty_map_proof_is_not_an_empty_map(self):
        task = self.empty_map_task()

        def screenshot():
            task.device.image_frame_id += 1
            task.device._escape_generation = 9

        task.screenshot = screenshot
        task.run_on_exp_main()
        task.quit_exp_main.assert_not_called()

    def test_an_invitation_arriving_mid_proof_is_not_an_empty_map(self):
        task = self.empty_map_task()

        def screenshot():
            task.device.image_frame_id += 1
            task.device._accepted_invitation_generation = 4

        task.screenshot = screenshot
        task.run_on_exp_main()
        task.quit_exp_main.assert_not_called()

    def test_a_page_other_than_the_map_is_not_an_empty_map(self):
        """Recognition that lands somewhere else must never quit exploration."""
        task = self.empty_map_task()
        task.navigator.all_pages.return_value = [pages.page_battle_prepare]
        task.match_page_once.return_value = True
        self.swipe_until_quit(task, rounds=1)
        task.quit_exp_main.assert_not_called()

    def test_a_boss_or_reward_still_on_screen_is_not_an_empty_map(self):
        for marker in ('I_BOSS_BATTLE_BUTTON', 'I_E_REWARD_BOX_SMALL',
                        'I_E_REWARD_BOX_BIG'):
            with self.subTest(marker=marker):
                task = self.empty_map_task()
                task.appear = mock.Mock(side_effect=lambda c: c == marker)
                self.swipe_until_quit(task, rounds=1)
                task.quit_exp_main.assert_not_called()
        task = self.empty_map_task()
        task.search_up_fight = mock.Mock(return_value=object())
        self.swipe_until_quit(task, rounds=1)
        task.quit_exp_main.assert_not_called()

    def test_an_unproven_screen_never_quits_exploration(self):
        """The slide may happen; without two fresh empty-map frames it must not quit."""
        task = self.empty_map_task()
        # The second capture fails to advance the frame id, so only one of the
        # two required observations is fresh.
        captures = {'n': 0}

        def screenshot():
            captures['n'] += 1
            if captures['n'] > 1:
                return
            task.device.image_frame_id += 1

        task.screenshot = screenshot
        self.swipe_until_quit(task, rounds=1)
        task.quit_exp_main.assert_not_called()

    def test_a_blocked_swipe_never_reaches_the_empty_end_proof(self):
        task = self.empty_map_task()
        task.swipe = mock.Mock(return_value=False)
        task.run_on_exp_main()
        task.quit_exp_main.assert_not_called()

    def test_the_budget_is_not_read_from_the_repeat_click_ledger(self):
        # The old criterion counted inside click_record, which the fence clears
        # on every trip. The count must come from its own tally instead.
        task = self.subject()
        task.device.click_record = deque(['swipe_background_right'] * 15)
        self.assertEqual(task._exp_scroll_count(), 0)
        task._exp_scroll_add('swipe_background_right')
        self.assertEqual(task._exp_scroll_count(), 1)

    def test_a_different_swipe_starts_a_fresh_budget(self):
        # Switching from the dog-food list to the map must not inherit the
        # previous tally, or the map would quit before it slides at all.
        task = self.subject()
        for _ in range(EXPLORATION_SCROLL_LIMIT):
            task._exp_scroll_add('swipe_shiki_to_left')
        task._exp_scroll_add('swipe_background_right')
        self.assertEqual(task._exp_scroll_count(), 1)

    def test_winning_a_fight_gives_the_next_screen_a_full_budget(self):
        # Otherwise one exploration run could only ever slide three times.
        task = self.subject()
        for _ in range(EXPLORATION_SCROLL_LIMIT):
            task._exp_scroll_add('swipe_background_right')
        task._exp_scroll_reset()
        task._exp_scroll_name_reset()
        self.assertEqual(task._exp_scroll_count(), 0)
        task._exp_scroll_add('swipe_background_right')
        self.assertEqual(task._exp_scroll_count(), 1)

    def test_entry_resets_the_budget(self):
        task = self.subject()
        task.C_SAFE_RANDOM_CLICK_AREA_EXP = mock.Mock()
        task._config = mock.Mock()
        task._config.switch_soul_config.enable = False
        task._config.switch_soul_config.enable_switch_by_name = False
        # pre_process reads the persisted config model, not the cached one.
        buffs = SimpleNamespace(buff_gold_50_click=False, buff_gold_100_click=False,
                                buff_exp_50_click=False, buff_exp_100_click=False)
        task.config = SimpleNamespace(
            exploration=SimpleNamespace(exploration_config=buffs))
        task.user_status = 'alone'

        for _ in range(EXPLORATION_SCROLL_LIMIT):
            task._exp_scroll_add('swipe_background_right')
        BaseExploration.pre_process(task)
        self.assertEqual(task._exp_scroll_count(), 0)


if __name__ == '__main__':
    unittest.main()

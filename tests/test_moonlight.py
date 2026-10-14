"""Moonlight activity routing, limits, and battle configuration regressions."""
import ast
from datetime import time, timedelta
from enum import Enum
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pydantic import BaseModel, Field, validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path.cwd()))


def moon_class():
    tree = ast.parse((ROOT / 'tasks/Moonlight/activity.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    ns = dict(time=SimpleNamespace(monotonic=Mock(side_effect=range(1000)), monotonic_ns=lambda: 1, sleep=Mock()),
              logger=Mock(), GameStuckError=RuntimeError,
              pages=SimpleNamespace(page_moon_battle='moon_battle', page_moon_lobby='moon_lobby'))
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'moonlight.py', 'exec'), ns)
    return ns['MoonlightAct']


def task():
    d = moon_class()()
    d.device = SimpleNamespace(image=object(), image_save=Mock(), performance=SimpleNamespace(timeout=lambda n: n))
    for name in ('I_MOON_BATTLE', 'I_MOON_CHALLENGE', 'I_UI_CONFIRM', 'I_UI_CONFIRM_SAMLL', 'I_UI_CANCEL', 'I_UI_CANCEL_SAMLL'):
        setattr(d, name, name)
    d.O_MOON_RESOURCE = SimpleNamespace(ocr_digit=Mock(return_value=60))
    d.O_MOON_REWARD_NOTICE = SimpleNamespace(detect_and_ocr=Mock(return_value=[]))
    d.screenshot = Mock()
    d.appear = Mock(side_effect=lambda rule: rule == d.I_MOON_BATTLE)
    d.appear_then_click = Mock(return_value=True)
    d.is_in_battle = Mock(return_value=True)
    return d


class MoonlightTests(unittest.TestCase):
    def test_configuration_is_opt_in_and_has_separate_limit(self):
        from module.config.config_menu import ConfigMenu
        from module.config.config_manual import ConfigManual
        from module.config.scheduler import TaskScheduler
        from module.config.config import Function
        from tasks.Script.config_optimization import ScheduleRule
        from tasks.ActivityShikigami.config import ActivityShikigami
        from tasks.Moonlight.config import Moonlight
        self.assertIn('Moonlight', ConfigMenu().menu['Activity Task'])
        self.assertIn('Moonlight', ConfigManual.SCHEDULER_PRIORITY)
        pending = Function('moonlight', {'scheduler': {
            'enable': True, 'next_run': '2023-01-01 00:00:00', 'priority': 5,
        }})
        self.assertIn(pending, TaskScheduler.schedule(ScheduleRule.FILTER, [pending]))
        self.assertNotIn('moonlight_limit', ActivityShikigami().general_config.model_fields)
        self.assertNotIn('月华流光', ActivityShikigami().general_config.task_sequence_v)
        conf = Moonlight()
        self.assertNotIn('switch_soul_config', Moonlight.model_fields)
        self.assertEqual(conf.general_config.challenge_limit, 1)
        self.assertEqual(conf.general_config.limit_for('moonlight'), 1)
        self.assertEqual(conf.general_config.limit_for('rich_man'), 0)
        self.assertEqual(Moonlight(general_config={'challenge_limit': -1}).general_config.challenge_limit, 1)

    def test_challenge_click_only_after_page_recognition(self):
        d = task()
        d.appear.return_value = False
        d.appear.side_effect = None
        with self.assertRaises(RuntimeError):
            d._enter_moonlight_battle()
        d.appear_then_click.assert_not_called()

    def test_ap_shortage_needs_two_reads_and_sends_no_click(self):
        d = task()
        d.O_MOON_RESOURCE.ocr_digit.side_effect = [5, 4]
        self.assertFalse(d._enter_moonlight_battle())
        d.appear_then_click.assert_not_called()
        d = task()
        d.O_MOON_RESOURCE.ocr_digit.side_effect = [0, 60]
        self.assertTrue(d._enter_moonlight_battle())
        d.appear_then_click.assert_called_once_with(d.I_MOON_CHALLENGE, interval=0)

    def test_unentered_battle_times_out_without_repeated_clicks(self):
        d = task()
        d.is_in_battle.return_value = False
        with self.assertRaises(RuntimeError):
            d._enter_moonlight_battle()
        self.assertEqual(d.appear_then_click.call_count, 1)

    def test_resource_dialog_is_cancelled_not_confirmed(self):
        d = task()
        d.is_in_battle.return_value = False
        d.appear.side_effect = lambda r: r in (d.I_MOON_BATTLE, d.I_UI_CONFIRM)
        self.assertFalse(d._enter_moonlight_battle())
        self.assertEqual([call.args[0] for call in d.appear_then_click.call_args_list],
                         [d.I_MOON_CHALLENGE, d.I_UI_CANCEL])

    def test_reward_cap_notice_confirms_once_then_enters_battle(self):
        d = task()
        d.is_in_battle.side_effect = [False, True]
        d.appear.side_effect = lambda r: r in (d.I_MOON_BATTLE, d.I_UI_CONFIRM)
        d.O_MOON_REWARD_NOTICE.detect_and_ocr.return_value = [
            SimpleNamespace(ocr_text='今日映月之光奖励已达获取上限，'),
            SimpleNamespace(ocr_text='是否继续挑战？'),
        ]
        self.assertTrue(d._enter_moonlight_battle())
        self.assertEqual([call.args[0] for call in d.appear_then_click.call_args_list],
                         [d.I_MOON_CHALLENGE, d.I_UI_CONFIRM])

    def test_richman_settings_reused_and_count_only_after_entry(self):
        d = task()
        d.action_limit = Mock(return_value=2)
        d.time_limit_reached = Mock(return_value=False)
        d.goto_page = Mock()
        d.prepare_next_action = Mock(side_effect=[True, True, False])
        d.record_action = Mock()
        d._enter_moonlight_battle = Mock(return_value=True)
        source = SimpleNamespace(preset_enable=True, copy=Mock(side_effect=lambda **kw: kw['update']))
        d.battle_config = Mock(return_value=source)
        d.run_general_battle = Mock()
        d.run_moonlight()
        self.assertTrue(all(c.args == ('rich_man',) for c in d.battle_config.call_args_list))
        self.assertEqual(d.record_action.call_count, 2)
        self.assertEqual([c.args[0]['preset_enable'] for c in d.run_general_battle.call_args_list], [True, False])
        self.assertTrue(all(c.kwargs['exit_matcher'] == d.I_MOON_BATTLE for c in d.run_general_battle.call_args_list))
        d.prepare_next_action = Mock(return_value=True)
        d._enter_moonlight_battle.return_value = False
        d.record_action.reset_mock()
        d.run_moonlight()
        d.record_action.assert_not_called()

    def test_entry_search_uses_existing_column_toggle(self):
        tree = ast.parse((ROOT / 'tasks/Moonlight/page.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'find_moonlight_entry']
        ns = dict(time=SimpleNamespace(sleep=Mock()), logger=Mock(),
                  GamePageUnknownError=RuntimeError, _settle_activity_auxiliary=Mock(),
                  MoonlightAssets=SimpleNamespace(I_MOON_ENTRY='new'),
                  RightActivityAssets=SimpleNamespace(I_TOGGLE_BUTTON='toggle'))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'page.py', 'exec'), ns)
        d = SimpleNamespace(screenshot=Mock(), I_CHECK_MAIN='main', appear_then_click=Mock(return_value=True))
        d.appear = Mock(side_effect=lambda r: r != 'new' or d.appear_then_click.call_count >= 2)
        self.assertTrue(ns['find_moonlight_entry'](d))
        self.assertEqual(d.appear_then_click.call_count, 2)
        self.assertIn('new', [call.args[0] for call in d.appear.call_args_list])


if __name__ == '__main__':
    unittest.main()


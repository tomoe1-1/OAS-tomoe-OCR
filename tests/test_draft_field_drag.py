from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
import numpy as np

from tasks.DraftDuel.field_drag import drag_to_field
from tasks.DraftDuel.script_task import ScriptTask
from tasks.DraftDuel.strategy import is_fire_supplier, plan_lineup
from module.device.method.scrcpy import const
from module.device.method.minitouch import CommandBuilder


class Builder:
    def __init__(self, events):
        self.events = events

    def down(self, *point):
        self.events.append(('down', point))
        return self

    def move(self, *point):
        self.events.append(('move', point))
        return self

    def up(self):
        self.events.append(('up',))
        return self

    def commit(self):
        return self

    def wait(self, ms):
        self.events.append(('wait', ms))
        return self

    def clear(self):
        return self


def device(method='minitouch'):
    events = []
    d = SimpleNamespace(
        config=SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method=method))),
        handle_control_check=Mock(return_value=True),
        _invalidate_image_batch_cache=Mock(),
        minitouch_builder=Builder(events), minitouch_send=Mock(),
        sleep=lambda seconds: events.append(('wait_seconds', seconds)),
        scrcpy_ensure_running=Mock(), _scrcpy_control_socket_lock=nullcontext(),
        _scrcpy_control=SimpleNamespace(touch=lambda *args: events.append(('touch', args))),
        u2=SimpleNamespace(touch=SimpleNamespace(
            down=lambda *p: events.append(('down', p)),
            move=lambda *p: events.append(('move', p)),
            up=lambda *p: events.append(('up', p)))),
    )
    return d, events


def lineup():
    obj = ScriptTask.__new__(ScriptTask)
    obj.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8),
                                 confirm_control_progress=Mock())
    obj._battle_detail = (['a', 'b', 'c', 'd', 'e'], {}, {})
    obj._battle_plan = ['a']
    obj._battle_locked = []
    obj._card_locations = Mock(side_effect=lambda names: {
        name: {'point': ((320, 435, 550, 665, 780)[obj._battle_detail[0].index(name)], 550), 'score': .9}
        for name in names})
    obj._battle_ready = Mock(side_effect=[True, True, False, False])
    obj.screenshot = Mock()
    obj._selected_cards = Mock(return_value=set())
    obj._card_saturation = Mock(return_value=80)
    obj._spotlight_x = Mock(return_value=(250, 320))
    obj._selected_cross = Mock(side_effect=[(80, 0), (320, .8)])
    obj._wait_drag_selection = Mock(return_value=True)
    obj.click = Mock(return_value=True)
    return obj


PATH = [[320, 595], [275, 420], [250, 320]]


class DraftFieldDragTests(unittest.TestCase):
    def test_live_default_card_cross_is_recognized_without_matching_portraits(self):
        image = np.array(Image.open(Path(__file__).parent / 'fixtures/draft_selected_card.png').convert('RGB'))
        self.assertEqual(ScriptTask._selected_cards(image), {0})
        x, score = ScriptTask._selected_cross(image)
        self.assertLess(abs(x - 320), 15)
        self.assertGreater(score, .7)

    def test_blank_cards_do_not_pass_selection_check(self):
        self.assertEqual(ScriptTask._selected_cards(np.zeros((720, 1280, 3), np.uint8)), set())

    def test_minitouch_holds_before_moving_and_releases_at_spotlight(self):
        d, events = device()
        with patch('tasks.DraftDuel.field_drag.insert_swipe', return_value=PATH):
            self.assertTrue(drag_to_field(d, (320, 595), (250, 320)))
        self.assertEqual(events[0:2], [('down', (320, 595)), ('wait', 800)])
        self.assertEqual(events[-3:], [('move', (250, 320)), ('wait', 400), ('up',)])
        self.assertEqual(sum(e[0] == 'down' for e in events), 1)
        self.assertEqual(sum(e[0] == 'up' for e in events), 1)
        d._invalidate_image_batch_cache.assert_called_once()

    def test_minitouch_error_still_releases_pressed_finger(self):
        d, events = device()
        d.minitouch_builder.move = Mock(side_effect=RuntimeError('move failed'))
        with patch('tasks.DraftDuel.field_drag.insert_swipe', return_value=PATH), \
                self.assertRaises(RuntimeError):
            drag_to_field(d, (320, 595), (250, 320))
        self.assertEqual(events[-1], ('up',))

    def test_real_minitouch_packets_never_release_before_final_position(self):
        d, _ = device()
        d.max_x, d.max_y, d.orientation = 1280, 720, 0
        d.config.DEVICE_OVER_HTTP = False
        d.minitouch_builder = CommandBuilder(d)
        packets = []
        def send():
            packets.append(d.minitouch_builder.to_minitouch())
            d.minitouch_builder.clear()
        d.minitouch_send = send
        with patch('tasks.DraftDuel.field_drag.insert_swipe', return_value=PATH):
            drag_to_field(d, (320, 595), (250, 320))
        commands = ''.join(packets).splitlines()
        self.assertEqual(sum(line.startswith('d ') for line in commands), 1)
        self.assertEqual(sum(line.startswith('u ') for line in commands), 1)
        self.assertIn('d 0 320 595 100', packets[0])
        self.assertIn('w 800', packets[0])
        self.assertIn('m 0 250 320 100', packets[-2])
        self.assertIn('w 400', packets[-2])
        self.assertTrue(packets[-1].startswith('u 0\n'))
        self.assertFalse(any('u 0' in packet for packet in packets[:-1]))

    def test_scrcpy_keeps_same_touch_until_endpoint(self):
        d, events = device('scrcpy')
        with patch('tasks.DraftDuel.field_drag.insert_swipe', return_value=PATH):
            drag_to_field(d, (320, 595), (250, 320))
        self.assertEqual(events[0], ('touch', (320, 595, const.ACTION_DOWN)))
        self.assertEqual(events[1], ('wait_seconds', .8))
        self.assertEqual(events[-1], ('touch', (250, 320, const.ACTION_UP)))
        self.assertEqual(events[-2], ('wait_seconds', .4))

    def test_other_control_methods_use_continuous_touch_instead_of_tap(self):
        for method in ('uiautomator2', 'ADB', 'window_message'):
            d, events = device(method)
            with patch('tasks.DraftDuel.field_drag.insert_swipe', return_value=PATH):
                drag_to_field(d, (320, 595), (250, 320))
            self.assertEqual(events[0:2], [('down', (320, 595)), ('wait_seconds', .8)])
            self.assertEqual(events[-1], ('up', (250, 320)))

    def test_guard_rejection_sends_no_touch(self):
        d, events = device()
        d.handle_control_check.return_value = False
        self.assertFalse(drag_to_field(d, (320, 595), (250, 320)))
        self.assertEqual(events, [])
        d.minitouch_send.assert_not_called()

    def test_confirmation_is_clicked_only_after_drag_release(self):
        obj = lineup()
        events = []
        def drag(*args, **kw):
            events.extend(('hold', 'move', 'release'))
            return True
        obj.click.side_effect = lambda *args, **kw: events.append('confirm') or True
        with patch('tasks.DraftDuel.script_task.drag_to_field', side_effect=drag) as helper, \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        self.assertEqual(events, ['hold', 'move', 'release', 'confirm'])
        helper.assert_called_once_with(obj.device, p1=(320, 595), p2=(250, 320))
        obj.click.assert_called_once_with(obj.C_BATTLE_CONFIRM, interval=.3)
        self.assertEqual(obj._battle_locked, ['a'])
        obj.device.confirm_control_progress.assert_called_once_with(
            [obj.C_BATTLE_CONFIRM, 'draft_battle_field'])

    def test_rejected_drag_does_not_click_confirmation(self):
        obj = lineup()
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=False):
            obj._select_battle_lineup_timed([])
        obj.click.assert_not_called()

    def test_unconfirmed_selection_does_not_click_confirmation(self):
        obj = lineup()
        obj._wait_drag_selection.return_value = False
        obj._selected_cross.side_effect = [(80, 0), (80, 0)]
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True), \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        obj.click.assert_not_called()

    def test_preparation_expired_after_drag_does_not_click_confirmation(self):
        obj = lineup()
        obj._battle_ready.side_effect = [True, False]
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True), \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        obj.click.assert_not_called()

    def test_blocked_confirmation_does_not_mark_card_locked(self):
        obj = lineup()
        obj.click.return_value = False
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True), \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        self.assertEqual(obj._battle_locked, [])

    def test_lineup_has_at_most_one_fire_supplier(self):
        names = ['因幡辉夜姬', '流光追月神', '辉夜姬', '玉藻前', '妖刀姬', '茨木童子']
        selected = plan_lineup(names, team_size=3, max_fire=1)
        self.assertEqual(len(selected), 3)
        self.assertLessEqual(sum(is_fire_supplier(name) for name in selected), 1)

    def test_existing_fire_supplier_is_retained_without_another(self):
        names = ['辉夜姬', '流光追月神', '玉藻前', '妖刀姬', '茨木童子']
        selected = plan_lineup(names, team_size=3, locked=['辉夜姬'], max_fire=1)
        self.assertIn('辉夜姬', selected)
        self.assertNotIn('流光追月神', selected)

    def test_fire_constraint_does_not_relax_when_other_candidates_missing(self):
        with self.assertRaises(ValueError):
            plan_lineup(['辉夜姬', '流光追月神'], team_size=2, max_fire=1)

    def test_existing_double_fire_is_not_extended(self):
        with self.assertRaises(ValueError):
            plan_lineup(['玉藻前'], team_size=3,
                        locked=['辉夜姬', '流光追月神'], max_fire=1)

    def test_cached_double_fire_plan_is_replaced_before_dragging(self):
        obj = lineup()
        # 本方最多 2 只火机；缓存计划含 3 只火系（超限），拖拽前必须先丢弃并按 max_fire=1 重新规划。
        obj._battle_detail = (['辉夜姬', '流光追月神', '玉藻前', '妖刀姬', '因幡辉夜姬'], {}, {})
        obj._battle_locked = ['辉夜姬']
        obj._battle_plan = ['辉夜姬', '流光追月神', '因幡辉夜姬']
        obj._selected_cross.side_effect = [(80, 0), (550, .8)]
        with patch('tasks.DraftDuel.script_task.plan_lineup', return_value=('辉夜姬', '玉藻前')) as planner, \
                patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True) as drag, \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        planner.assert_called_once()
        self.assertEqual(obj._battle_plan, ('辉夜姬', '玉藻前'))
        self.assertEqual(planner.call_args.kwargs['max_fire'], 1)
        drag.assert_called_once_with(obj.device, p1=(550, 595), p2=(250, 320))
        self.assertEqual(obj._battle_locked, ['辉夜姬', '玉藻前'])

    def test_fallback_selected_fire_card_is_not_confirmed_twice(self):
        obj = lineup()
        obj._wait_drag_selection.return_value = False
        obj._battle_detail = (['辉夜姬', '流光追月神', '玉藻前', '妖刀姬', '茨木童子'], {}, {})
        obj._battle_locked = ['辉夜姬']
        obj._battle_plan = ['辉夜姬', '玉藻前']
        obj._battle_drag_failures = {'玉藻前': 1}
        obj._selected_cross.side_effect = [(80, 0), (80, 0)]
        obj._selected_cards.side_effect = [set(), {1}]
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True), \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        obj.click.assert_not_called()
        self.assertEqual(obj._battle_locked, ['辉夜姬'])

    def test_default_selected_card_still_drags_before_confirmation(self):
        obj = lineup()
        obj._selected_cards.return_value = {0}
        obj._selected_cross.side_effect = [(320, .8), (320, .8)]
        events = []
        def drag(*args, **kw):
            events.extend(['down', 'move', 'up'])
            return True
        obj.click.side_effect = lambda *args, **kw: events.append('confirm') or True
        with patch('tasks.DraftDuel.script_task.drag_to_field', side_effect=drag), \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        self.assertEqual(events, ['down', 'move', 'up', 'confirm'])

    def test_failed_drag_never_confirms_other_default_card(self):
        obj = lineup()
        obj._wait_drag_selection.return_value = False
        obj._selected_cross.side_effect = [(80, 0), (80, 0)]
        obj._selected_cards.side_effect = [set(), {1}]
        obj._battle_drag_failures = {'a': 1}
        with patch('tasks.DraftDuel.script_task.drag_to_field', return_value=True), \
                patch('tasks.DraftDuel.script_task.time.sleep'):
            obj._select_battle_lineup_timed([])
        obj.click.assert_not_called()
        obj.device.confirm_control_progress.assert_not_called()

    def test_drag_selection_waits_through_reordering_and_requires_two_stable_frames(self):
        obj = lineup()
        del obj._wait_drag_selection
        obj._selected_cross.side_effect = [(500, .95), (550, .95), (550, .95)]
        obj._card_locations.side_effect = [
            {'a': None}, {'a': {'point': (545, 563)}}, {'a': {'point': (545, 563)}}]
        with patch('tasks.DraftDuel.script_task.time.sleep'):
            self.assertTrue(obj._wait_drag_selection('a'))
        self.assertEqual(obj.screenshot.call_count, 3)
        obj.click.assert_not_called()

    def test_drag_selection_resets_stability_when_target_disappears(self):
        obj = lineup()
        del obj._wait_drag_selection
        obj._selected_cross.return_value = (550, .95)
        obj._selected_cross.side_effect = None
        obj._card_locations.side_effect = [
            {'a': {'point': (545, 563)}}, {'a': None},
            {'a': {'point': (545, 563)}}, {'a': {'point': (545, 563)}}]
        with patch('tasks.DraftDuel.script_task.time.sleep'):
            self.assertTrue(obj._wait_drag_selection('a'))
        self.assertEqual(obj.screenshot.call_count, 4)

    def test_drag_selection_timeout_cannot_confirm_another_selected_card(self):
        obj = lineup()
        del obj._wait_drag_selection
        obj._selected_cross.return_value = (320, .95)
        obj._selected_cross.side_effect = None
        obj._card_locations.return_value = {'a': {'point': (545, 563)}}
        obj._card_locations.side_effect = None
        with patch('tasks.DraftDuel.script_task.time.sleep'), \
                patch('tasks.DraftDuel.script_task.time.monotonic', side_effect=[0, 0, 1, 5]):
            self.assertFalse(obj._wait_drag_selection('a'))
        obj.click.assert_not_called()

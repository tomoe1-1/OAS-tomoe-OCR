"""Offline regressions for bounded, verified special-mall searches.

Load the real methods without importing device drivers or initializing a game
connection. Frames and input delivery are simulated; guard and UI verification
methods are the production implementations.
"""
import ast
import re
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from module.device.performance import PerformanceProfile
from module.exception import GameTooManyClickError, TaskEnd, TaskRecoveryFailed


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_POS = (1209, 128)
OTHER_SIDEBAR_POS = (1209, 227)


def paint_sidebar(frame, pos, selected):
    x, y = pos
    frame[y - 40:y + 40, x - 40:x + 40] = (135, 105, 85)
    if selected:
        frame[y - 10:y + 10, x - 10:x + 10] = (220, 140, 170)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 0.2
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def load_class(path, name, namespace, methods=None, bases=None):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    node = next(item for item in tree.body
                if isinstance(item, ast.ClassDef) and item.name == name)
    if methods is not None:
        node.body = [item for item in node.body
                     if isinstance(item, ast.FunctionDef) and item.name in methods]
    if bases is not None:
        node.bases = [ast.Name(id=base, ctx=ast.Load()) for base in bases]
        node.keywords = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(ROOT / path), 'exec'), namespace)
    return namespace[name]


def production_types(clock):
    namespace = dict(np=np, cv2=cv2, re=re, time=clock, monotonic=clock.monotonic,
                     logger=Mock(), RuleImage=object, SpecialRoom=object,
                     WeeklyPurchase=object, TaskRecoveryFailed=TaskRecoveryFailed,
                     GameTooManyClickError=GameTooManyClickError, TaskEnd=TaskEnd)
    # Include any future string conversion so this test catches name mismatches.
    swipe = load_class('module/atom/swipe.py', 'RuleSwipe', namespace,
                       ('__init__', 'coord', '__str__', '__repr__'), bases=[])
    base = load_class('tasks/base_task.py', 'BaseTask', namespace,
                      ('swipe', '_ui_verify'), bases=[])
    namespace.update(GameUi=base, WeeklyPurchaseAssets=type('Assets', (), {}),
                     Buy=type('Buy', (), {}))
    navbar = load_class('tasks/WeeklyPurchase/mall/navbar.py', 'MallNavbar', namespace)
    special = load_class('tasks/WeeklyPurchase/mall/special.py', 'Special', namespace)
    device = load_class('module/device/device.py', 'Device', namespace,
                        ('handle_control_check', 'click_record_add',
                         'click_record_clear', 'click_record_remove',
                         'click_record_check', 'confirm_control_progress'), bases=[])
    task = load_class('tasks/WeeklyPurchase/script_task.py', 'ScriptTask', namespace,
                      ('run',), bases=[])
    return special, navbar, device, swipe, task


def configuration(**changes):
    values = dict(enable=True, totem_pass=True, medium_bondling_discs=0,
                  low_bondling_discs=0)
    values.update(changes)
    return SimpleNamespace(**values)


def subject(*, target_after=0, motion=True, config=None):
    clock = Clock()
    special, navbar, device_type, swipe_type, task_type = production_types(clock)
    obj = special.__new__(special)
    device = device_type.__new__(device_type)
    device.performance = PerformanceProfile()
    device.click_record = deque(maxlen=15)
    device._await_recovery_frame = False
    device._last_control_time = None
    device._click_recovery_at = None
    device._click_recovery_reason = ''
    device._click_recovery_buttons = set()
    device._escape_generation = 0
    device._verified_click = None
    device._check_login_deadline = Mock(return_value=False)
    device._check_click_recovery = Mock(return_value=False)
    device.stuck_record_clear = Mock()
    device.confirm_control_progress = Mock(wraps=device.confirm_control_progress)
    obj.device = device
    obj.S_SP_DOWN = swipe_type((200, 420, 100, 20), (200, 300, 100, 20),
                              'default', name='sp_down')
    for name in ('I_SP_BUY_TOTEM', 'I_SP_BUY_MEDIUM', 'I_SP_BUY_LOW',
                 'I_SP_SWIPE_CHECK', 'I_MALL_SUNDRY_CHECK', 'I_SIDE_CHECK_SPECIAL'):
        setattr(obj, name, name)
    obj.config = SimpleNamespace(weekly_purchase=SimpleNamespace(
        special_room=config or configuration()))
    obj._enter_special = Mock(return_value=True)
    obj._special_totom = Mock()
    obj._special_totem_sold_out = Mock(return_value=False)
    obj._special_medium = Mock()
    obj._special_low = Mock()
    state = SimpleNamespace(frame=np.zeros((720, 1280, 3), np.uint8),
                            special=True, bottom=False, target_after=target_after,
                            requests=0, dispatched=0, snapshots=0, motion=motion,
                            after_swipe=None, next_frames=[], seen_rules=[])
    paint_sidebar(state.frame, SIDEBAR_POS, True)
    obj._mall_sidebar_position = SIDEBAR_POS
    obj._mall_sidebar_control = 'RM_NAVBAR_SPECIAL'

    def screenshot():
        state.snapshots += 1
        if state.next_frames:
            state.frame = state.next_frames.pop(0)
        device.image = state.frame.copy()

    def appear(rule):
        state.seen_rules.append(rule)
        if rule in (obj.I_MALL_SUNDRY_CHECK, obj.I_SIDE_CHECK_SPECIAL):
            return state.special
        if rule == obj.I_SP_SWIPE_CHECK:
            return state.bottom
        if rule == obj.I_SP_BUY_TOTEM:
            return state.target_after is not None and state.dispatched >= state.target_after
        return False

    def dispatch_swipe(*, p1, p2, control_name):
        state.requests += 1
        if device.handle_control_check(control_name) is False:
            return False
        state.dispatched += 1
        if state.motion:
            state.frame = state.frame.copy()
            state.frame[115:595, 160:1050] = 255 if state.dispatched % 2 else 0
        if state.after_swipe:
            state.after_swipe()
        # The real device returns None on successful input delivery.
        return None

    obj.screenshot = Mock(side_effect=screenshot)
    obj.appear = Mock(side_effect=appear)
    device.swipe = Mock(side_effect=dispatch_swipe)
    device.image = state.frame.copy()
    return obj, state, navbar, task_type


class SpecialMallProgressTests(unittest.TestCase):
    def test_all_disabled_items_do_not_enter_or_search(self):
        obj, state, _, _ = subject(config=configuration(totem_pass=False))
        obj.execute_special()
        obj._enter_special.assert_not_called()
        obj.screenshot.assert_not_called()
        self.assertEqual(state.dispatched, 0)

    def test_disabled_discs_do_not_require_finding_their_products(self):
        obj, state, _, _ = subject(target_after=0)
        obj.execute_special()
        obj._special_totom.assert_called_once_with(True)
        obj._special_medium.assert_not_called()
        obj._special_low.assert_not_called()
        self.assertNotIn(obj.I_SP_BUY_MEDIUM, state.seen_rules)
        self.assertNotIn(obj.I_SP_BUY_LOW, state.seen_rules)
        self.assertEqual(state.dispatched, 0)

    def test_five_real_list_moves_release_only_the_swipe_guard(self):
        obj, state, _, _ = subject(target_after=5)
        obj.device.click_record_add('unrelated')
        obj.execute_special()
        self.assertEqual(state.requests, 5)
        self.assertEqual(state.dispatched, 5)
        self.assertEqual(list(obj.device.click_record), ['unrelated'])
        self.assertEqual(obj.device.confirm_control_progress.call_count, 5)
        for call in obj.device.confirm_control_progress.call_args_list:
            self.assertEqual(call.args[0], ['sp_down'])
        obj._special_totom.assert_called_once()

    def test_static_list_fails_after_one_input_without_clearing_guard(self):
        obj, state, _, _ = subject(target_after=None, motion=False)
        state.bottom = True  # Legacy marker is present, but only motion proves an end.
        with self.assertRaises(TaskRecoveryFailed):
            obj.execute_special()
        self.assertEqual(state.requests, 1)
        self.assertEqual(state.dispatched, 1)
        self.assertEqual(list(obj.device.click_record), ['sp_down'])
        obj.device.confirm_control_progress.assert_not_called()

    def test_currency_and_sidebar_changes_are_not_list_movement(self):
        obj, state, _, _ = subject(target_after=None, motion=False)
        def animate_outside_list():
            state.frame = state.frame.copy()
            state.frame[:100] = 255
            state.frame[:, :120] = 255
        state.after_swipe = animate_outside_list
        with self.assertRaises(TaskRecoveryFailed):
            obj.execute_special()
        self.assertEqual(state.dispatched, 1)
        obj.device.confirm_control_progress.assert_not_called()

    def test_one_changed_observation_cannot_clear_guard(self):
        obj, state, _, _ = subject(target_after=None, motion=False)
        def transient_frames():
            moved = state.frame.copy()
            moved[115:595, 160:1050] = 255
            state.next_frames = [frame.copy() for _ in range(40)
                                 for frame in (moved, state.frame)]
        state.after_swipe = transient_frames
        with self.assertRaises(TaskRecoveryFailed):
            obj.execute_special()
        self.assertEqual(state.dispatched, 1)
        obj.device.confirm_control_progress.assert_not_called()

    def test_leaving_page_after_swipe_stops_without_more_inputs(self):
        obj, state, _, _ = subject(target_after=None)
        state.after_swipe = lambda: setattr(state, 'special', False)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'page changed'):
            obj.execute_special()
        self.assertEqual(state.requests, 1)
        obj.device.confirm_control_progress.assert_not_called()

    def test_special_sidebar_deselected_despite_wallet_icons_stops_swiping(self):
        obj, state, _, _ = subject(target_after=None)
        paint_sidebar(state.frame, SIDEBAR_POS, False)
        paint_sidebar(state.frame, OTHER_SIDEBAR_POS, True)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'page changed'):
            obj.execute_special()
        self.assertTrue(state.special)  # Currency/page context still matches.
        self.assertEqual(state.requests, 0)
        obj.device.confirm_control_progress.assert_not_called()

    def test_recovery_during_verification_does_not_confirm_progress(self):
        obj, state, _, _ = subject(target_after=None)
        original = obj.screenshot.side_effect
        def screenshot():
            original()
            if state.dispatched:
                obj.device._escape_generation += 1
        obj.screenshot.side_effect = screenshot
        with self.assertRaisesRegex(TaskRecoveryFailed, 'interrupted by recovery'):
            obj.execute_special()
        self.assertEqual(state.dispatched, 1)
        obj.device.confirm_control_progress.assert_not_called()

    def test_strong_symbol_marker_does_not_stop_search_for_later_product(self):
        obj, state, _, _ = subject(target_after=2)
        state.bottom = True
        obj.execute_special()
        self.assertEqual(state.requests, 2)
        self.assertEqual(state.dispatched, 2)
        obj._special_totom.assert_called_once_with(True)
        self.assertEqual(obj.device.confirm_control_progress.call_count, 2)

    def test_strong_symbol_marker_after_processing_all_products_is_normal_completion(self):
        obj, state, _, _ = subject(target_after=0)
        state.bottom = True
        obj.execute_special()
        obj._special_totom.assert_called_once()
        self.assertEqual(state.requests, 0)

    def test_purchase_requires_fresh_page_check_before_search_continues(self):
        obj, state, _, _ = subject(target_after=0)
        snapshots_at_buy = []
        def purchase(_):
            snapshots_at_buy.append(state.snapshots)
            state.special = False
        obj._special_totom.side_effect = purchase
        with self.assertRaisesRegex(TaskRecoveryFailed, 'page changed'):
            obj.execute_special()
        self.assertGreater(state.snapshots, snapshots_at_buy[0])
        self.assertEqual(state.requests, 0)

    def test_search_limit_propagates_without_scheduling_weekly_success(self):
        obj, state, _, task = subject(target_after=None)
        owner = SimpleNamespace(config=SimpleNamespace(weekly_purchase=SimpleNamespace(
            thousand_things=None, shrine=None, guild_store=None, itachi_coin_shop=None)),
            execute_tt=Mock(), execute_shrine=Mock(), execute_guild=Mock(),
            execute_itachi_coin_shop=Mock(), execute_mall=obj.execute_special,
            set_next_run=Mock())
        with self.assertRaisesRegex(TaskRecoveryFailed, 'exceeded 20'):
            task.run(owner)
        self.assertEqual(state.requests, 20)
        self.assertEqual(state.dispatched, 20)
        owner.set_next_run.assert_not_called()

    def test_failed_entry_is_not_silent_special_completion(self):
        obj, state, _, _ = subject()
        obj._enter_special.return_value = False
        with self.assertRaisesRegex(TaskRecoveryFailed, 'Enter special mall failed'):
            obj.execute_special()
        self.assertEqual(state.requests, 0)


class SidebarTests(unittest.TestCase):
    def sidebar(self, *, ready=False, transition=True, blocked=False):
        obj, state, navbar, _ = subject()
        del obj._mall_sidebar_position
        del obj._mall_sidebar_control
        visible = {'target': ready}
        # Wallet icons are visible regardless of which category is selected.
        obj.appear.side_effect = lambda _: True
        paint_sidebar(state.frame, SIDEBAR_POS, ready)
        def click(*, x, y, control_name):
            if blocked:
                return False
            obj.device.click_record_add(control_name)
            visible['target'] = transition
            paint_sidebar(state.frame, SIDEBAR_POS, transition)
            return None
        obj.device.click = Mock(side_effect=click)
        return obj, state, navbar, visible

    def test_sidebar_clicks_once_and_confirms_two_fresh_destination_frames(self):
        obj, state, navbar, _ = self.sidebar()
        self.assertTrue(navbar.click_and_check(obj, SIDEBAR_POS, 'RM_NAVBAR_SPECIAL', 'target'))
        obj.device.click.assert_called_once_with(x=1209, y=128, control_name='RM_NAVBAR_SPECIAL')
        self.assertGreaterEqual(state.snapshots, 3)
        obj.device.confirm_control_progress.assert_called_once_with(['RM_NAVBAR_SPECIAL'])
        self.assertEqual(list(obj.device.click_record), [])
        self.assertEqual(obj._mall_sidebar_position, SIDEBAR_POS)
        self.assertEqual(obj._mall_sidebar_control, 'RM_NAVBAR_SPECIAL')

    def test_missing_destination_does_not_replay_sidebar_click(self):
        obj, _, navbar, _ = self.sidebar(transition=False)
        with self.assertRaises(TaskRecoveryFailed):
            navbar.click_and_check(obj, SIDEBAR_POS, 'RM_NAVBAR_SPECIAL', 'target')
        obj.device.click.assert_called_once()
        obj.device.confirm_control_progress.assert_not_called()
        self.assertFalse(hasattr(obj, '_mall_sidebar_position'))

    def test_already_selected_sidebar_verifies_without_click(self):
        obj, _, navbar, _ = self.sidebar(ready=True)
        self.assertTrue(navbar.click_and_check(obj, SIDEBAR_POS, 'RM_NAVBAR_SPECIAL', 'target'))
        obj.device.click.assert_not_called()
        obj.device.confirm_control_progress.assert_called_once_with([])

    def test_missing_sidebar_position_preserves_failure_without_click(self):
        obj, _, navbar, _ = self.sidebar(ready=True)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'target missing'):
            navbar.click_and_check(obj, None, 'RM_NAVBAR_SPECIAL', 'target')
        obj.device.click.assert_not_called()
        obj.device.confirm_control_progress.assert_not_called()

    def test_blocked_sidebar_click_does_not_authorize_guard_clear(self):
        obj, _, navbar, _ = self.sidebar(blocked=True)
        with self.assertRaisesRegex(TaskRecoveryFailed, 'not dispatched'):
            navbar.click_and_check(obj, SIDEBAR_POS, 'RM_NAVBAR_SPECIAL', 'target')
        obj.device.click.assert_called_once()
        obj.device.confirm_control_progress.assert_not_called()

    def test_neighbor_highlight_never_confirms_target_sidebar(self):
        obj, state, navbar, _ = self.sidebar(transition=False)
        paint_sidebar(state.frame, OTHER_SIDEBAR_POS, True)
        with self.assertRaises(TaskRecoveryFailed):
            navbar.click_and_check(obj, SIDEBAR_POS, 'RM_NAVBAR_SPECIAL', 'target')
        obj.device.click.assert_called_once()
        obj.device.confirm_control_progress.assert_not_called()

    def test_random_click_point_is_projected_to_matching_button_center(self):
        obj, _, navbar, _ = self.sidebar()
        obj.L_RM_NAVBAR = SimpleNamespace(_target=SimpleNamespace(roi_front=(1188, 107, 42, 42),
                                                                roi_back=(1178, 88, 60, 531)),
                                         targets={}, image_appear=Mock(return_value=(1190, 109)))
        self.assertTrue(navbar.click_and_check(obj, (1190, 109), 'RM_NAVBAR_SPECIAL', 'target'))
        obj.device.click.assert_called_once_with(x=1209, y=128, control_name='RM_NAVBAR_SPECIAL')
        self.assertEqual(obj._mall_sidebar_position, SIDEBAR_POS)

    def test_target_dictionary_supplies_matching_center(self):
        obj, _, _, _ = self.sidebar()
        obj.L_RM_NAVBAR = SimpleNamespace(_target=None, targets={
            'wrong': SimpleNamespace(roi_front=(1188, 107, 42, 42), roi_back=(1178, 88, 60, 531)),
            'target': SimpleNamespace(roi_front=(1188, 206, 42, 42), roi_back=(1178, 88, 60, 531))})
        self.assertEqual(obj._mall_sidebar_center((1190, 210)), OTHER_SIDEBAR_POS)

    def test_unmatched_honor_search_region_cannot_override_matched_duel_center(self):
        obj, _, _, _ = self.sidebar()
        search_area = (1178, 88, 60, 531)
        obj.L_RM_NAVBAR = SimpleNamespace(_target=None, targets={
            'honor': SimpleNamespace(roi_front=search_area, roi_back=search_area),
            'duel': SimpleNamespace(roi_front=(1187, 211, 40, 40), roi_back=search_area)})
        self.assertEqual(obj._mall_sidebar_center((1200, 220)), (1207, 231))

    def test_highlight_alone_without_mall_context_does_not_confirm(self):
        obj, _, _, _ = self.sidebar(ready=True)
        obj.screenshot()
        obj.appear.side_effect = lambda rule: rule != obj.I_MALL_SUNDRY_CHECK
        self.assertFalse(obj._mall_sidebar_selected(SIDEBAR_POS, 'target'))
        obj.appear.side_effect = lambda rule: rule != 'target'
        self.assertFalse(obj._mall_sidebar_selected(SIDEBAR_POS, 'target'))

    def test_real_selected_and_unselected_button_fixture(self):
        fixture = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/special_mall_selected.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        self.assertEqual(fixture.shape, (160, 80, 3))
        obj, state, _, _ = self.sidebar()
        state.frame[:] = 0
        for pos, tile in ((SIDEBAR_POS, fixture[:80]),
                          (OTHER_SIDEBAR_POS, fixture[80:])):
            x, y = pos
            state.frame[y - 40:y + 40, x - 40:x + 40] = tile
        obj.screenshot()
        self.assertTrue(obj._mall_sidebar_selected(SIDEBAR_POS, 'target'))
        self.assertFalse(obj._mall_sidebar_selected(OTHER_SIDEBAR_POS, 'target'))
        obj._mall_sidebar_position = SIDEBAR_POS
        obj._mall_sidebar_control = 'RM_NAVBAR_SPECIAL'
        obj._require_special_page()
        obj._mall_sidebar_position = OTHER_SIDEBAR_POS
        with self.assertRaises(TaskRecoveryFailed):
            obj._require_special_page()

    def moving_sidebar(self, *, label_survives=True):
        obj, state, navbar, _ = self.sidebar()
        search_area = (1178, 88, 60, 531)
        label = SimpleNamespace(center=(1208, 500), present=True)
        matched_rule = SimpleNamespace(roi_front=(1188, 480, 40, 40), roi_back=search_area)
        obj.L_RM_NAVBAR = SimpleNamespace(_target=matched_rule, targets={})
        paint_sidebar(state.frame, SIDEBAR_POS, False)
        paint_sidebar(state.frame, label.center, False)

        def match(image, name):
            self.assertEqual(name, 'friendship')
            if not label.present:
                return False
            x, y = label.center
            matched_rule.roi_front = (x - 20, y - 20, 40, 40)
            return x - 10, y - 10  # Matching returns a random label-interior point.

        obj.L_RM_NAVBAR.image_appear = Mock(side_effect=match)

        def click(*, x, y, control_name):
            obj.device.click_record_add(control_name)
            paint_sidebar(state.frame, (1208, 500), False)
            if label_survives:
                label.center = (1209, 330)
                paint_sidebar(state.frame, label.center, True)
            else:
                label.present = False
                # An old pink row remains; its color must not substitute for the label.
                paint_sidebar(state.frame, (1208, 500), True)
            return None

        obj.device.click = Mock(side_effect=click)
        return obj, state, navbar, label

    def test_target_recentered_after_click_is_relocated_on_two_fresh_frames(self):
        obj, state, navbar, label = self.moving_sidebar()
        self.assertTrue(navbar.click_and_check(obj, (1208, 500),
                                             'RM_NAVBAR_FRIENDSHIP', 'target'))
        obj.device.click.assert_called_once_with(x=1208, y=500,
                                                 control_name='RM_NAVBAR_FRIENDSHIP')
        self.assertEqual(label.center, (1209, 330))
        self.assertGreaterEqual(obj.L_RM_NAVBAR.image_appear.call_count, 3)
        self.assertGreaterEqual(state.snapshots, 3)
        obj.device.confirm_control_progress.assert_called_once_with(['RM_NAVBAR_FRIENDSHIP'])
        # The stored input point can be stale because every later guard relocates the label.
        self.assertTrue(obj._mall_sidebar_selected((1208, 500), 'target',
                                                   'RM_NAVBAR_FRIENDSHIP'))

    def test_actual_charm_control_name_relocates_its_current_label(self):
        obj, state, _, _ = self.sidebar()
        current = (1209, 330)
        paint_sidebar(state.frame, SIDEBAR_POS, False)
        paint_sidebar(state.frame, current, True)
        obj.L_RM_NAVBAR = SimpleNamespace(
            _target=SimpleNamespace(roi_front=(1189, 310, 40, 40),
                                    roi_back=(1178, 88, 60, 531)),
            targets={}, image_appear=Mock(return_value=(1200, 320)))
        obj.screenshot()
        self.assertTrue(obj._mall_sidebar_selected(SIDEBAR_POS, 'target', 'RM_NAVBAR_CHARM'))
        self.assertEqual(obj.L_RM_NAVBAR.image_appear.call_args.args[1], 'charm')
        obj.L_RM_NAVBAR.image_appear.return_value = False
        # No current label means failure even when the stale input point turns pink.
        paint_sidebar(state.frame, SIDEBAR_POS, True)
        obj.screenshot()
        self.assertFalse(obj._mall_sidebar_selected(SIDEBAR_POS, 'target', 'RM_NAVBAR_CHARM'))
        obj.device.click.assert_not_called()

    def test_missing_label_cannot_confirm_unchanged_old_pink_position(self):
        obj, _, navbar, _ = self.moving_sidebar(label_survives=False)
        with self.assertRaises(TaskRecoveryFailed):
            navbar.click_and_check(obj, (1208, 500), 'RM_NAVBAR_FRIENDSHIP', 'target')
        obj.device.click.assert_called_once()
        obj.device.confirm_control_progress.assert_not_called()
        self.assertFalse(hasattr(obj, '_mall_sidebar_position'))

    def test_special_search_requires_matching_label_even_when_old_row_is_pink(self):
        obj, state, _, _ = subject(target_after=None)
        obj.L_RM_NAVBAR = SimpleNamespace(_target=None, targets={},
                                         image_appear=Mock(return_value=False))
        with self.assertRaisesRegex(TaskRecoveryFailed, 'page changed'):
            obj.execute_special()
        obj.L_RM_NAVBAR.image_appear.assert_called_once()
        self.assertEqual(obj.L_RM_NAVBAR.image_appear.call_args.args[1], 'special')
        self.assertEqual(state.requests, 0)
        obj.device.confirm_control_progress.assert_not_called()


class TotemSoldOutTests(unittest.TestCase):
    def sold_subject(self, title='御灵境之钥', status='售罄', pos=(174, 427)):
        obj, state, _, _ = subject(target_after=None)
        del obj._special_totem_sold_out
        obj.buy_more = Mock()
        namespace = obj._special_totem_sold_out.__func__.__globals__
        matcher = load_class('module/atom/image.py', 'RuleImage',
                             dict(np=np, cv2=cv2, logger=Mock()),
                             ('corp', '_template_image_invalid', '_update_roi_front',
                              'template_match'), bases=[])

        class TemplateRule(matcher):
            debug_mode = False

            def __init__(self, **values):
                self.roi_front = list(values['roi_front'])
                self.roi_back = values['roi_back']
                self.threshold = values['threshold']
                self.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
                    (ROOT / values['file']).read_bytes(), np.uint8),
                    cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)

        ocr_calls = []
        def ocr_rule(**values):
            ocr_calls.append(values)
            text = title if values['name'].endswith('_title') else status
            return SimpleNamespace(ocr=Mock(return_value=text))

        namespace.update(RuleImage=TemplateRule, RuleOcr=ocr_rule)
        template = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tasks/WeeklyPurchase/mall/special/special_sp_buy_totem_sold.png').read_bytes(),
            np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        self.assertEqual(template.shape, (113, 148, 3))
        x, y = pos
        state.frame[y:y + 113, x:x + 148] = template
        original_appear = obj.appear.side_effect
        obj.appear.side_effect = lambda rule: (
            rule.template_match(obj.device.image) if isinstance(rule, TemplateRule)
            else original_appear(rule))
        obj.screenshot()
        return obj, state, ocr_calls

    def test_real_dark_template_requires_matching_item_and_both_ocr_regions(self):
        obj, state, calls = self.sold_subject()
        self.assertTrue(obj._special_totem_sold_out())
        self.assertEqual(obj._special_totem_sold_rule.roi_front, [174, 427, 148, 113])
        self.assertEqual(calls[0]['roi'], (164, 540, 168, 32))
        self.assertEqual(calls[1]['roi'], (188, 382, 120, 40))
        self.assertTrue(all(call['mode'] == 'Single' for call in calls))
        calls.clear()
        state.frame[427:540, 174:322] = 0
        obj.screenshot()
        self.assertFalse(obj._special_totem_sold_out())
        self.assertEqual(calls, [])

    def test_exact_title_and_sold_status_are_both_required(self):
        for title, status in (('御灵之钥', '售罄'), ('御灵境之钥礼包', '售罄'),
                              ('中级契灵盘', '售罄'), (None, '售罄'),
                              ('御灵境之钥', '限购1次'), ('御灵境之钥', '售罄中'),
                              ('御灵境之钥', None)):
            with self.subTest(title=title, status=status):
                obj, _, _ = self.sold_subject(title, status)
                self.assertFalse(obj._special_totem_sold_out())

    def test_ocr_whitespace_is_normalized_without_weakening_title(self):
        obj, _, _ = self.sold_subject(' 御灵境之钥\n', ' 售\n罄 ')
        self.assertTrue(obj._special_totem_sold_out())

    def test_ocr_regions_follow_newly_matched_product_position(self):
        obj, _, calls = self.sold_subject(pos=(400, 250))
        self.assertTrue(obj._special_totem_sold_out())
        self.assertEqual(calls[0]['roi'], (390, 363, 168, 32))
        self.assertEqual(calls[1]['roi'], (414, 205, 120, 40))

    def test_two_fresh_sold_observations_finish_without_buy_or_swipe(self):
        obj, state, calls = self.sold_subject()
        before = state.snapshots
        obj.execute_special()
        self.assertGreaterEqual(state.snapshots - before, 4)
        self.assertGreaterEqual(len(calls), 6)  # Initial detection plus two fresh checks.
        obj._special_totom.assert_not_called()
        obj.buy_more.assert_not_called()
        self.assertEqual(state.requests, 0)
        obj.device.confirm_control_progress.assert_called_once_with([])

    def test_single_sold_frame_does_not_finish_or_resume_input(self):
        obj, state, _, = self.sold_subject()
        original_screenshot = obj.screenshot.side_effect
        count = 0
        def screenshot():
            nonlocal count
            count += 1
            if count > 1:
                state.frame[427:540, 174:322] = 0
            original_screenshot()
        obj.screenshot.side_effect = screenshot
        with self.assertRaises(TaskRecoveryFailed):
            obj.execute_special()
        obj._special_totom.assert_not_called()
        obj.buy_more.assert_not_called()
        self.assertEqual(state.requests, 0)
        obj.device.confirm_control_progress.assert_not_called()

    def test_partial_sold_card_after_strong_symbol_marker_is_found_with_one_more_swipe(self):
        obj, state, calls = self.sold_subject(pos=(174, 530))
        state.bottom = True  # Strong exploration symbol is visible in the earlier row.
        self.assertFalse(obj._special_totem_sold_out())
        self.assertEqual(calls, [])  # The incomplete template cannot reach OCR.
        template = state.frame[530:643, 174:322].copy()
        state.after_swipe = lambda: state.frame.__setitem__((slice(420, 533), slice(174, 322)), template)
        obj.execute_special()
        self.assertEqual(state.requests, 1)
        self.assertEqual(state.dispatched, 1)
        self.assertEqual(obj._special_totem_sold_rule.roi_front, [174, 420, 148, 113])
        obj._special_totom.assert_not_called()
        obj.buy_more.assert_not_called()
        self.assertGreaterEqual(len(calls), 6)
        self.assertEqual([call.args[0] for call in obj.device.confirm_control_progress.call_args_list],
                         [['sp_down'], []])

    def test_wrong_title_or_status_cannot_complete_when_list_is_static(self):
        for title, status in (('中级契灵盘', '售罄'), ('御灵境之钥', '限购1次')):
            with self.subTest(title=title, status=status):
                obj, state, _ = self.sold_subject(title, status)
                state.bottom = True
                state.motion = False
                with self.assertRaises(TaskRecoveryFailed):
                    obj.execute_special()
                obj._special_totom.assert_not_called()
                obj.buy_more.assert_not_called()
                self.assertEqual(state.requests, 1)
                self.assertEqual(list(obj.device.click_record), ['sp_down'])
                obj.device.confirm_control_progress.assert_not_called()


if __name__ == '__main__':
    unittest.main()

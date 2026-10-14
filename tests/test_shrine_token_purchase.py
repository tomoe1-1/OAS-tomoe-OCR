"""Only Shrine purchases use the modal's token button, once per purchase."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from tasks.WeeklyPurchase.shrine import Shrine


ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        self.now += .5
        return self.now


def subject():
    obj = Shrine.__new__(Shrine)
    obj.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8), _escape_generation=0)
    obj.I_S_TOKEN_PAY = deepcopy(obj.I_S_TOKEN_PAY)
    obj.screenshot = Mock()
    obj.appear = Mock(return_value=False)
    obj.click = Mock(return_value=True)
    obj.appear_then_click = Mock(return_value=False)
    obj.ui_reward_appear_click = Mock()
    obj._shrine_reward_visible = Mock(return_value=False)
    obj._read_shrine_offer = Mock(return_value=1200)
    obj.O_S_TOTAL = SimpleNamespace(ocr=Mock(return_value=1500))
    return obj


class ShrineTokenTests(unittest.TestCase):
    def test_supplied_popup_currency_button_title_and_price_region(self):
        obj = subject()
        src = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/shrine_five_purchase.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        h, w = src.shape[:2]
        obj.device.image[110:110+h, 420:420+w] = src
        obj.appear.side_effect = lambda rule: rule.multi_scale_template_match(obj.device.image)
        del obj._read_shrine_offer
        rules = []
        def ocr_rule(**kw):
            rules.append(kw)
            return SimpleNamespace(ocr=lambda _: '五星奉为达摩' if kw['mode'] == 'Single' else 1200)
        with patch('tasks.WeeklyPurchase.shrine.RuleOcr', side_effect=ocr_rule):
            self.assertEqual(obj._read_shrine_offer('五星奉为达摩'), 1200)
        x, y, width, height = obj.I_S_TOKEN_PAY.roi_front
        self.assertLess(abs(x - 553), 3)
        self.assertLess(abs(y - 512), 3)
        self.assertEqual(rules[1]['roi'][0], x + width)
        self.assertEqual(rules[1]['roi'][1], y)

    def test_wrong_product_title_never_reaches_cost_or_purchase(self):
        obj = subject()
        del obj._read_shrine_offer
        obj.appear.return_value = True
        with patch('tasks.WeeklyPurchase.shrine.RuleOcr', return_value=
                   SimpleNamespace(ocr=lambda _: '四星奉为达摩')) as constructor:
            self.assertIsNone(obj._read_shrine_offer('五星奉为达摩'))
        self.assertEqual(constructor.call_count, 1)
        obj.click.assert_not_called()

    def test_four_star_contract_title_with_quantity_controls(self):
        obj = subject()
        del obj._read_shrine_offer
        obj.I_S_TOKEN_PAY.roi_front = [554, 552, 83, 63]
        obj.appear.return_value = True
        rules = []
        def ocr_rule(**kw):
            rules.append(kw)
            return SimpleNamespace(ocr=lambda _: '契约书·奉为达摩' if kw['mode'] == 'Single' else 200)
        with patch('tasks.WeeklyPurchase.shrine.RuleOcr', side_effect=ocr_rule):
            self.assertEqual(obj._read_shrine_offer('四星奉为达摩'), 200)
            self.assertIsNone(obj._read_shrine_offer('五星奉为达摩'))
        title_roi = rules[0]['roi']
        self.assertLess(title_roi[1], 150)
        self.assertGreater(title_roi[1] + title_roi[3], 175)

    def test_open_popup_resumes_without_clicking_underlying_product(self):
        obj = subject()
        obj.appear.side_effect = lambda rule: rule is obj.I_S_TOKEN_PAY
        obj._buy_opened_shrine_item = Mock(return_value=True)
        self.assertTrue(obj._purchase_shrine_item(obj.I_S_WHITE_FIVE, '五星奉为达摩', 1200))
        obj.click.assert_not_called()
        obj._buy_opened_shrine_item.assert_called_once_with('五星奉为达摩', 1200)

    def test_product_opens_once_and_waits_for_token_button(self):
        obj = subject()
        visible = iter((False, False, True, False))
        obj.appear.side_effect = lambda rule: next(visible)
        obj._find_shrine_product = Mock(return_value=obj.I_S_WHITE_FIVE)
        obj._buy_opened_shrine_item = Mock(return_value=True)
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertTrue(obj._purchase_shrine_item(obj.I_S_WHITE_FIVE, '五星奉为达摩', 1200))
        obj.click.assert_called_once_with(obj.I_S_WHITE_FIVE, interval=1.0)

    def test_open_timeout_does_not_repeat_product_click(self):
        obj = subject()
        obj.appear.side_effect = lambda rule: rule is obj.I_S_WHITE_FIVE
        obj._find_shrine_product = Mock(return_value=obj.I_S_WHITE_FIVE)
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            with self.assertRaises(Exception) as caught:
                obj._purchase_shrine_item(obj.I_S_WHITE_FIVE, '五星奉为达摩', 1200)
        self.assertIn('商品弹窗未出现', str(caught.exception))
        obj.click.assert_called_once()

    def test_two_row_grid_finds_four_star_in_second_row(self):
        obj = subject()
        obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/shrine_two_row_grid.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        with patch('tasks.WeeklyPurchase.shrine.RuleOcr', return_value=
                   SimpleNamespace(ocr=lambda _: 200)):
            target = obj._find_shrine_product(obj.I_S_WHITE_FOUR, '四星奉为达摩')
        self.assertIsNotNone(target)
        self.assertGreater(target.roi_front[1], 400)
        self.assertLess(target.roi_front[0], 350)

    def test_five_star_does_not_select_four_star_with_same_face(self):
        obj = subject()
        obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            (ROOT / 'tests/fixtures/shrine_two_row_grid.png').read_bytes(), np.uint8),
            cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        with patch('tasks.WeeklyPurchase.shrine.RuleOcr', return_value=
                   SimpleNamespace(ocr=lambda _: 200)):
            self.assertIsNone(obj._find_shrine_product(obj.I_S_WHITE_FIVE, '五星奉为达摩'))

    def test_four_star_200_price_with_250_balance_is_purchased(self):
        obj = subject()
        obj._read_shrine_offer.return_value = 200
        obj.O_S_TOTAL.ocr.side_effect = [250, 250, 50]
        # 四星白蛋会先选最大数量（读数量 OCR 需要真实弹窗），本用例只覆盖
        # 「余额足够 -> 单次御札点击」这条购买路径，数量选择单独隔离。
        obj._select_shrine_four_maximum = Mock(return_value=200)
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertTrue(obj._buy_opened_shrine_item('四星奉为达摩', 400))
        obj.click.assert_called_once_with(obj.I_S_TOKEN_PAY, interval=1.0)

    def test_insufficient_balance_closes_popup_before_next_item(self):
        obj = subject()
        obj.appear.side_effect = lambda rule: rule is obj.I_S_TOKEN_PAY
        obj._buy_opened_shrine_item = Mock(return_value=False)
        self.assertFalse(obj._purchase_shrine_item(obj.I_S_BLACK, '御行达摩', 1500))
        obj.appear_then_click.assert_called_once_with(obj.I_UI_BACK_RED, interval=1.0)

    def test_product_opens_before_checking_actual_price(self):
        obj = subject()
        obj.appear.side_effect = [False, True, True]
        obj._find_shrine_product = Mock(return_value=obj.I_S_WHITE_FOUR)
        obj.shrine_check_money = Mock(return_value=False)
        obj._buy_opened_shrine_item = Mock(return_value=False)
        self.assertFalse(obj._purchase_shrine_item(obj.I_S_WHITE_FOUR, '四星奉为达摩', 400))
        obj.click.assert_called_once_with(obj.I_S_WHITE_FOUR, interval=1.0)
        obj.shrine_check_money.assert_not_called()

    def test_price_changes_or_exceeds_cap_prevent_spending(self):
        for costs in ([1200, 1100], [1201], [None]):
            obj = subject()
            obj._read_shrine_offer.side_effect = costs
            self.assertFalse(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
            obj.click.assert_not_called()

    def test_unknown_or_insufficient_balance_prevents_spending(self):
        for money in (1199, None, '1500'):
            obj = subject()
            obj.O_S_TOTAL.ocr.return_value = money
            self.assertFalse(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
            obj.click.assert_not_called()

    def test_recovery_during_validation_aborts_purchase(self):
        obj = subject()
        obj.screenshot.side_effect = lambda: setattr(obj.device, '_escape_generation', 1)
        self.assertFalse(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        obj.click.assert_not_called()

    def test_blocked_click_is_not_purchase_success(self):
        obj = subject()
        obj.click.return_value = False
        self.assertFalse(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        obj.click.assert_called_once_with(obj.I_S_TOKEN_PAY, interval=1.0)

    def test_reward_confirms_single_token_click(self):
        obj = subject()
        rewards = iter((True, False))
        obj.appear.side_effect = lambda rule, **kw: next(rewards) if rule is obj.I_UI_REWARD else False
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertTrue(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        obj.click.assert_called_once_with(obj.I_S_TOKEN_PAY, interval=1.0)
        obj.ui_reward_appear_click.assert_called_once()

    def test_exact_token_decrease_confirms_purchase_when_reward_frame_missed(self):
        obj = subject()
        obj.O_S_TOTAL.ocr.side_effect = [1500, 1500, 300]
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertTrue(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        obj.click.assert_called_once()

    def test_delayed_reward_after_deduction_is_closed_before_return(self):
        obj = subject()
        obj.O_S_TOTAL.ocr.side_effect = [1500, 1500, 300]
        obj._shrine_reward_visible.side_effect = [False, True, False]
        obj.appear.return_value = False
        obj.ui_reward_appear_click.return_value = False
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertTrue(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        self.assertEqual(obj.click.call_args_list, [
            unittest.mock.call(obj.I_S_TOKEN_PAY, interval=1.0),
            unittest.mock.call(obj.C_UI_REWARD, interval=0.6)])

    def test_dialog_disappearance_alone_is_not_purchase_success(self):
        obj = subject()
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertFalse(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        obj.click.assert_called_once()

    def test_existing_price_button_without_result_is_not_clicked_repeatedly(self):
        obj = subject()
        obj.appear.side_effect = lambda rule, **kw: rule is obj.I_S_TOKEN_PAY
        with patch('tasks.WeeklyPurchase.shrine.time', ClockWithSleep()):
            self.assertFalse(obj._buy_opened_shrine_item('五星奉为达摩', 1200))
        obj.click.assert_called_once()

    def test_each_shrine_item_uses_its_existing_spend_cap(self):
        obj = subject()
        obj._purchase_shrine_item = Mock()
        for method, product, name, limit in (
            (obj.shrine_black_daruma, obj.I_S_BLACK, '御行达摩', 1500),
            (obj.shrine_white_five, obj.I_S_WHITE_FIVE, '五星奉为达摩', 1200),
            (obj.shrine_white_four, obj.I_S_WHITE_FOUR, '四星奉为达摩', 400),
        ):
            method()
            obj._purchase_shrine_item.assert_called_with(product, name, limit)


class ClockWithSleep(Clock):
    def sleep(self, _):
        pass


if __name__ == '__main__':
    unittest.main()

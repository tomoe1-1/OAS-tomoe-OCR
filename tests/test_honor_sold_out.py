from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import cv2
import numpy as np

from tasks.WeeklyPurchase.mall.honor import Honor


def subject():
    obj = Honor.__new__(Honor)
    obj.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8))
    obj.screenshot = Mock()
    obj.appear = Mock(return_value=False)
    obj._enter_honor = Mock(return_value=True)
    obj._honor_sold_out = Mock(return_value=False)
    obj.swipe = Mock(return_value=False)
    obj.buy_more = Mock()
    obj._special_check_remain = Mock(return_value=1)
    obj.mall_check_money = Mock(return_value=True)
    return obj


def config():
    return SimpleNamespace(enable=True, mystery_amulet=True, black_daruma_scrap=True)


class HonorSoldOutTests(unittest.TestCase):
    def test_supplied_dark_items_require_their_own_sold_label(self):
        obj = subject()
        del obj._honor_sold_out
        fixture = Path(__file__).parent / 'fixtures/honor_sold_out.png'
        obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            fixture.read_bytes(), np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        obj.I_HONOR_BLUE_SOLD = deepcopy(obj.I_HONOR_BLUE_SOLD)
        obj.I_HONOR_BLACK_SOLD = deepcopy(obj.I_HONOR_BLACK_SOLD)
        obj.appear.side_effect = lambda rule: rule.multi_scale_template_match(obj.device.image)
        for target in (obj.I_HONOR_BLUE, obj.I_HONOR_BLACK):
            with patch('tasks.WeeklyPurchase.mall.honor.RuleOcr', return_value=
                       SimpleNamespace(ocr=lambda _: '售罄')):
                self.assertTrue(obj._honor_sold_out(target))
            with patch('tasks.WeeklyPurchase.mall.honor.RuleOcr', return_value=
                       SimpleNamespace(ocr=lambda _: '限购1次')):
                self.assertFalse(obj._honor_sold_out(target))

    def test_both_sold_out_finish_without_purchase_or_swipe(self):
        obj = subject()
        obj._honor_sold_out.return_value = True
        obj.execute_honor(config())
        obj.buy_more.assert_not_called()
        obj.swipe.assert_not_called()
        obj._special_check_remain.assert_not_called()

    def test_full_live_screen_finds_both_sold_items(self):
        obj = subject()
        del obj._honor_sold_out
        fixture = Path(__file__).parent / 'fixtures/honor_live_sold_out.png'
        obj.device.image = cv2.cvtColor(cv2.imdecode(np.frombuffer(
            fixture.read_bytes(), np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        obj.I_HONOR_BLUE_SOLD = deepcopy(obj.I_HONOR_BLUE_SOLD)
        obj.I_HONOR_BLACK_SOLD = deepcopy(obj.I_HONOR_BLACK_SOLD)
        obj.appear.side_effect = lambda rule: rule.multi_scale_template_match(obj.device.image)
        with patch('tasks.WeeklyPurchase.mall.honor.RuleOcr', return_value=
                   SimpleNamespace(ocr=lambda _: '售罄')):
            obj.execute_honor(config())
        obj.buy_more.assert_not_called()
        obj.swipe.assert_not_called()

    def test_search_uses_three_long_vertical_swipes_at_most(self):
        obj = subject()
        obj.swipe.return_value = True
        with patch('tasks.WeeklyPurchase.mall.honor.time.monotonic', return_value=0), \
                patch('tasks.WeeklyPurchase.mall.honor.time.sleep'):
            obj.execute_honor(config())
        self.assertEqual(obj.swipe.call_count, 3)
        obj.swipe.assert_called_with(obj.S_HONOR_DOWN, interval=2)
        self.assertGreater(obj.S_HONOR_DOWN.roi_front[1] - obj.S_HONOR_DOWN.roi_back[1], 300)

    def test_only_available_item_is_purchased(self):
        obj = subject()
        obj._honor_sold_out.side_effect = lambda target: target is obj.I_HONOR_BLUE
        obj.appear.side_effect = lambda rule: rule is obj.I_HONOR_BLACK
        obj.execute_honor(config())
        obj.buy_more.assert_called_once_with(obj.I_HONOR_BLACK)
        obj.swipe.assert_not_called()

    def test_direct_purchase_rechecks_sold_state(self):
        obj = subject()
        obj._honor_sold_out.return_value = True
        obj._honor_mystery_amulet(True)
        obj._honor_black_daruma_scrap(True)
        obj.buy_more.assert_not_called()
        obj._special_check_remain.assert_not_called()
        obj.mall_check_money.assert_not_called()

    def test_failed_swipes_cannot_loop_forever(self):
        obj = subject()
        counter = iter(range(0, 60, 10))
        with patch('tasks.WeeklyPurchase.mall.honor.time.monotonic', side_effect=lambda: next(counter)):
            obj.execute_honor(config())
        self.assertLessEqual(obj.swipe.call_count, 4)
        obj.buy_more.assert_not_called()

    def test_entry_failure_does_not_inspect_another_shop(self):
        obj = subject()
        obj._enter_honor.return_value = False
        obj.execute_honor(config())
        obj.screenshot.assert_not_called()

    def test_disabled_shop_does_not_enter(self):
        obj = subject()
        con = config()
        con.enable = False
        obj.execute_honor(con)
        obj._enter_honor.assert_not_called()


if __name__ == '__main__':
    unittest.main()

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import re
from copy import copy

import cv2

from module.logger import logger
from module.atom.ocr import RuleOcr
from module.exception import GameStuckError

from tasks.GameUi.page import page_main, page_summon
from tasks.GameUi.game_ui import GameUi
from tasks.WeeklyPurchase.assets import WeeklyPurchaseAssets
from tasks.WeeklyPurchase.config import Shrine as ConfigShrine


class Shrine(GameUi, WeeklyPurchaseAssets):

    def execute_shrine(self, con: ConfigShrine):
        logger.hr('Start Shrine')
        if not con.enable:
            logger.info('Shrine is disabled')
            return
        self.screenshot()
        if self._shrine_reward_visible():
            self._dismiss_shrine_reward()
        self.goto_page(page_summon)

        while 1:
            self.screenshot()
            if self.appear(self.I_S_NEXT_PERIOD):
                break
            if self.appear_then_click(self.I_S_SUMMON_TO_SHRINE, interval=2):
                continue
        logger.info('Enter Shrine')
        time.sleep(0.5)
        if con.black_daruma:
            self.shrine_black_daruma()
        if con.white_daruma_five:
            self.shrine_white_five()
        if con.white_daruma_four:
            self.shrine_white_four()

    def shrine_check_money(self, mix: int) -> bool:
        self.screenshot()
        current = self.O_S_TOTAL.ocr(self.device.image)
        if not isinstance(current, int):
            logger.warning('OCR current money failed')
            return False
        if current >= mix:
            logger.info('Money is enough')
            return True
        logger.info('Money is not enough')
        return False

    def _check_bought(self, target) -> bool:
        """
        检查是否已经购买, 弃用，无法识别任何文字
        :return: True 已经购买
        """
        self.screenshot()
        result = target.ocr(self.device.image)
        if '已' in result or '兑' in result or '换' in result:
            logger.info('Already bought')
            return True
        logger.info('Not bought')
        return False

    def shrine_black_daruma(self):
        logger.hr('Shrine black daruma', 2)
        return self._purchase_shrine_item(self.I_S_BLACK, '御行达摩', 1500)

    def shrine_white_five(self):
        logger.hr('Shrine white five', 2)
        return self._purchase_shrine_item(self.I_S_WHITE_FIVE, '五星奉为达摩', 1200)

    def shrine_white_four(self):
        logger.hr('Shrine white four', 2)
        return self._purchase_shrine_item(self.I_S_WHITE_FOUR, '四星奉为达摩', 400)

    def _read_shrine_offer(self, expected_title):
        """先核对弹窗商品，再读取御札图标右侧的实际价格。"""
        if not self.appear(self.I_S_TOKEN_PAY):
            return None
        x, y, width, height = self.I_S_TOKEN_PAY.roi_front
        scale = width / 83
        title_roi = (max(0, x - int(150 * scale)), max(0, y - int(500 * scale)),
                     int(480 * scale), int(230 * scale))
        title_rule = RuleOcr(roi=title_roi, area=title_roi, mode='Single',
                             method='Default', keyword='', name='shrine_offer_title')
        title = re.sub(r'\s+', '', str(title_rule.ocr(self.device.image) or ''))
        title = title.replace('达磨', '达摩').replace('5星', '五星').replace('4星', '四星')
        normalized = title.replace('·', '').replace('•', '')
        four_contract = (expected_title == '四星奉为达摩'
                         and normalized in ('契约书奉为达摩', '奉为达摩契约书'))
        if expected_title not in title and not four_contract:
            logger.warning(f'Shrine offer does not match {expected_title}: {title}')
            return None
        cost_roi = (x + width, y, int(width * 1.2), height)
        cost_rule = RuleOcr(roi=cost_roi, area=cost_roi, mode='Digit',
                            method='Default', keyword='', name='shrine_offer_cost')
        cost = cost_rule.ocr(self.device.image)
        return cost if type(cost) is int and cost > 0 else None

    def _purchase_shrine_item(self, product, title, max_cost):
        """打开商品一次；弹窗已打开时直接接续，不点击弹窗下的商品。"""
        self.screenshot()
        if not self.appear(self.I_S_TOKEN_PAY):
            target = self._find_shrine_product(product, title)
            if target is None:
                logger.info(f'Shrine product unavailable: {title}')
                return False
            if self.click(target, interval=1.0) is False:
                return False
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                self.screenshot()
                if self.appear(self.I_S_TOKEN_PAY):
                    break
                time.sleep(0.2)
            else:
                raise GameStuckError(f'神龛商品弹窗未出现：{title}')
        purchased = self._buy_opened_shrine_item(title, max_cost)
        self.screenshot()
        if self.appear(self.I_S_TOKEN_PAY):
            self.appear_then_click(self.I_UI_BACK_RED, interval=1.0)
        return purchased

    def _find_shrine_product(self, product, title):
        """两排图标均参与匹配，用标价区分外观相似的四星和五星白蛋。"""
        x, y, width, height = product.roi_back
        source = self.device.image[y:y + height, x:x + width]
        template = product.image
        h, w = template.shape[:2]
        if source.shape[0] < h or source.shape[1] < w:
            return None
        scores = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
        allowed_prices = {'御行达摩': (1500,), '五星奉为达摩': (1200,),
                          '四星奉为达摩': (200, 400)}[title]
        for _ in range(3):
            _, score, _, location = cv2.minMaxLoc(scores)
            if score <= product.threshold:
                break
            px, py = location
            tx, ty = x + px, y + py
            price_roi = (max(0, tx - 15), ty + h + 78, 140, 45)
            price_rule = RuleOcr(roi=price_roi, area=price_roi, mode='Digit',
                                 method='Default', keyword='', name='shrine_product_price')
            price = price_rule.ocr(self.device.image)
            if type(price) is int and price in allowed_prices:
                target = copy(product)
                target.roi_front = [tx, ty, w, h]
                return target
            # 同一商品只检查一次，接着检查其他商品图标。
            scores[max(0, py - h):py + h, max(0, px - w):px + w] = -1
        return None

    def _shrine_quantity(self, pay):
        x, y, width, _ = pay.roi_front
        scale = width / 83
        roi = (int(x + 20 * scale), int(y - 145 * scale),
               int(75 * scale), int(95 * scale))
        return RuleOcr(roi=roi, area=roi, mode='Digit', method='Default',
                       keyword='', name='shrine_selected_quantity').ocr(self.device.image)

    def _select_shrine_four_maximum(self, title, unit_limit):
        # Verify the product before selecting its maximum quantity once.
        self.screenshot()
        cost = self._read_shrine_offer(title)
        pay = copy(self.I_S_TOKEN_PAY)
        before = self._shrine_quantity(pay)
        if (type(cost) is not int or type(before) is not int or before <= 0
                or cost % before or cost // before > unit_limit):
            raise GameStuckError('神龛四星白蛋数量或单价无法确认，不购买')
        unit_cost = cost // before
        target = copy(self.I_S_BUY_UP)
        x, y, width, _ = pay.roi_front
        scale = width / 83
        target.roi_back = [int(x + 180 * scale), int(y - 160 * scale),
                           int(120 * scale), int(150 * scale)]
        if not self.appear(target):
            raise GameStuckError('神龛四星白蛋最大数量按钮未识别，不购买')
        tx, ty = target.coord()
        if self.device.click(x=tx, y=ty, control_name=target.name) is False:
            raise GameStuckError('神龛最大数量点击未发出，不购买')
        previous = [None]
        selected = [None]
        def verified():
            current_cost = self._read_shrine_offer(title)
            current = self._shrine_quantity(pay)
            valid = (type(current) is int and current >= before
                     and current_cost == current * unit_cost)
            stable = valid and previous[0] == current
            previous[0] = current
            if stable:
                selected[0] = current
            return stable
        self._ui_verify(verified, [target])
        logger.info(f'Shrine four-star maximum quantity verified: {selected[0]}')
        return selected[0] * unit_cost

    def _buy_opened_shrine_item(self, title, max_cost):
        generation = self.device._escape_generation
        if title == '四星奉为达摩':
            max_cost = self._select_shrine_four_maximum(title, max_cost)
        previous_cost = None
        for _ in range(2):
            self.screenshot()
            cost = self._read_shrine_offer(title)
            money = self.O_S_TOTAL.ocr(self.device.image)
            if (cost is None or cost > max_cost or type(money) is not int or money < cost
                    or generation != self.device._escape_generation):
                logger.warning(f'Shrine purchase skipped: {title}, cost={cost}, balance={money}')
                if type(cost) is int and type(money) is int and money < cost:
                    logger.info(f'Shrine insufficient tokens: {title}, need={cost}, balance={money}')
                return False
            if previous_cost is not None and cost != previous_cost:
                logger.warning('Shrine price changed between frames; do not purchase')
                return False
            previous_cost = cost
        # 一次御札按钮点击就是购买，不等待旧的右侧详情和二级确认按钮。
        if self.click(self.I_S_TOKEN_PAY, interval=1.0) is False:
            return False
        deadline = time.monotonic() + 8
        balance_confirmed = False
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(self.I_UI_REWARD, threshold=0.6) or self._shrine_reward_visible():
                dismiss_deadline = time.monotonic() + 6
                while time.monotonic() < dismiss_deadline:
                    if self.ui_reward_appear_click() is False:
                        self.click(self.C_UI_REWARD, interval=0.6)
                    self.screenshot()
                    if (not self.appear(self.I_UI_REWARD, threshold=0.6)
                            and not self._shrine_reward_visible()):
                        time.sleep(0.5)
                        logger.info(f'Shrine purchase confirmed by reward: {title}, cost={cost}')
                        return True
                logger.warning(f'Shrine reward did not close: {title}')
                return False
            if not balance_confirmed and not self.appear(self.I_S_TOKEN_PAY):
                after = self.O_S_TOTAL.ocr(self.device.image)
                if type(after) is int and money - after == cost:
                    balance_confirmed = True
            time.sleep(0.2)
        if balance_confirmed:
            logger.info(f'Shrine purchase confirmed by token balance: {title}, cost={cost}')
            return True
        logger.warning(f'Shrine purchase result unconfirmed; do not click again: {title}')
        return False

    def _shrine_reward_visible(self):
        roi = (400, 155, 480, 120)
        rule = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                       keyword='', name='shrine_reward_title')
        return '获得奖励' in re.sub(r'\s+', '', str(rule.ocr(self.device.image) or ''))

    def _dismiss_shrine_reward(self):
        # 核对两帧标题后关闭上一次购买留下的奖励。
        self.screenshot()
        if not self._shrine_reward_visible():
            return
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.click(self.C_UI_REWARD, interval=0.6)
            self.screenshot()
            if not self._shrine_reward_visible():
                time.sleep(0.5)
                return
        raise GameStuckError('神龛奖励弹窗未关闭')



if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = Shrine(c, d)

    # t.shrine_white_four()
    t.execute_shrine(t.config.model.weekly_purchase.shrine)
    # t.screenshot()
    # print(t.appear(t.I_S_BUY_WHITE_FIVE, threshold=0.9))

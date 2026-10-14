# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
import re
import cv2
import numpy as np

from module.logger import logger
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskRecoveryFailed

from tasks.GameUi.page import page_main, page_guild
from tasks.WeeklyPurchase.mall.navbar import MallNavbar
from tasks.Component.Buy.buy import Buy
from tasks.WeeklyPurchase.assets import WeeklyPurchaseAssets
from tasks.WeeklyPurchase.config import SpecialRoom


class Special(Buy, MallNavbar):

    def execute_special(self, con: SpecialRoom = None):
        if not con:
            con = self.config.weekly_purchase.special_room
        if not con.enable:
            logger.info('Special room is not enable')
            return
        if not con.totem_pass and con.medium_bondling_discs <= 0 and con.low_bondling_discs <= 0:
            logger.info('No special mall items configured')
            return
        if not self._enter_special():
            raise TaskRecoveryFailed('Enter special mall failed')
        # 向下滑找到购买的物品
        totem_bought = not con.totem_pass
        medium_bought = con.medium_bondling_discs <= 0
        low_bought = con.low_bondling_discs <= 0
        swipes = 0
        while True:
            self.screenshot()
            self._require_special_page()
            if not totem_bought and self._special_totem_sold_out():
                self._ui_verify(self._special_totem_sold_out, [], timeout=6)
                logger.info('Special mall totem passes are sold out; processed')
                totem_bought = True
                continue
            if not totem_bought and self.appear(self.I_SP_BUY_TOTEM):
                self._special_totom(con.totem_pass)
                totem_bought = True
                continue
            if not medium_bought and self.appear(self.I_SP_BUY_MEDIUM):
                self._special_medium(con.medium_bondling_discs)
                medium_bought = True
                continue
            if not low_bought and self.appear(self.I_SP_BUY_LOW):
                self._special_low(con.low_bondling_discs)
                low_bought = True
                continue

            if totem_bought and medium_bought and low_bought:
                logger.info('All bought')
                break
            # The old end marker is a product on the penultimate row. Items
            # below it can still be clipped by the footer, so keep searching
            # until they are processed or a verified swipe cannot move them.
            if swipes >= 20:
                raise TaskRecoveryFailed('Special mall product search exceeded 20 verified swipes')
            self._special_swipe_once()
            swipes += 1

    def _require_special_page(self):
        if (getattr(self, '_mall_sidebar_control', None) != 'RM_NAVBAR_SPECIAL'
                or not self._mall_sidebar_selected(getattr(self, '_mall_sidebar_position', None),
                                                   self.I_SIDE_CHECK_SPECIAL, 'RM_NAVBAR_SPECIAL')):
            raise TaskRecoveryFailed('Special mall page changed; stop product search')

    @staticmethod
    def _special_list_signature(image):
        # Exclude currency, sidebar, navigation and decorative edge animations.
        crop = image[115:595, 160:1050]
        return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), (180, 96))

    def _special_swipe_once(self):
        self.screenshot()
        self._require_special_page()
        before = self._special_list_signature(self.device.image)
        if not self.swipe(self.S_SP_DOWN):
            raise TaskRecoveryFailed('Special mall swipe was not dispatched')
        time.sleep(2)

        def progressed():
            self._require_special_page()
            current = self._special_list_signature(self.device.image)
            delta = np.abs(before.astype(np.int16) - current.astype(np.int16))
            return float(np.mean(delta > 30)) > .12

        # Release the repeated-control guard only after actual list movement
        # has been observed in two fresh frames on the same shopping page.
        self._ui_verify(progressed, [self.S_SP_DOWN.name], timeout=6)

    def _special_totem_sold_out(self):
        rule = getattr(self, '_special_totem_sold_rule', None)
        if rule is None:
            rule = RuleImage(roi_front=(174, 427, 148, 113),
                             roi_back=(130, 115, 900, 480), threshold=.85,
                             method='Template matching',
                             file='./tasks/WeeklyPurchase/mall/special/special_sp_buy_totem_sold.png')
            self._special_totem_sold_rule = rule
        if not self.appear(rule):
            return False
        x, y, width, height = rule.roi_front
        title_roi = (x - 10, y + height, width + 20, 32)
        status_roi = (x + width // 2 - 60, y - 45, 120, 40)
        title = RuleOcr(roi=title_roi, area=title_roi, mode='Single', method='Default',
                        keyword='', name='special_totem_sold_title')
        status = RuleOcr(roi=status_roi, area=status_roi, mode='Single', method='Default',
                         keyword='', name='special_totem_sold_status')
        return (re.sub(r'\s+', '', title.ocr(self.device.image) or '') == '御灵境之钥'
                and re.sub(r'\s+', '', status.ocr(self.device.image) or '') == '售罄')

    def _special_totom(self, totem_pass: bool):
        """
        购买御灵，要求必须下滑出现御灵
        :return:
        """
        logger.hr('Buy totem', 3)
        if not totem_pass:
            logger.info('Buy totem is disabled')
            return
        # 检查剩余数量
        buy_number = 40
        remain_number = self._special_check_remain(self.I_SP_BUY_TOTEM)
        if remain_number < buy_number:
            logger.warning(f'Totem is not enough: {remain_number}')
            if remain_number <= 0:
                logger.warning('Totem remain is empty')
                return
            buy_number = remain_number
        # 用金币买的没必要再检查金币
        # 购买
        self.buy_more(self.I_SP_BUY_TOTEM)
        time.sleep(1)

    def _special_medium(self, buy_number: int = 10):
        """
        购买中级盘，要求必须下滑出现中级盘
        :return:
        """
        logger.hr('Buy medium', 3)
        if buy_number <= 0:
            logger.info('Buy medium is disabled')
            return
        # 检查剩余数量
        remain_number = self._special_check_remain(self.I_SP_BUY_MEDIUM)
        if remain_number < buy_number:
            logger.warning(f'Medium is not enough: {remain_number}')
            if remain_number <= 0:
                logger.warning('Medium remain is empty')
                return
            buy_number = remain_number
        # 用金币买的没必要再检查金币
        # 购买 , 中级盘一次最多购买20个
        buy_max = 20
        logger.info(f'Buy number is {buy_number}')
        if buy_number >= buy_max:
            buy_cycles_number = buy_number // buy_max
            buy_res_number = buy_number % buy_max
        else:
            buy_cycles_number = None
            buy_res_number = buy_number

        if buy_cycles_number:
            for i in range(buy_cycles_number):
                self.buy_more(self.I_SP_BUY_MEDIUM)
                time.sleep(0.5)
        if buy_res_number:
            self.buy_more(self.I_SP_BUY_MEDIUM, buy_res_number)
            time.sleep(0.5)

    def _special_low(self, buy_number: int = 10):
        """
        购买低级盘，要求必须下滑出现低级盘
        :return:
        """
        logger.hr('Buy low', 3)
        if buy_number <= 0:
            logger.info('Buy low is disabled')
            return
        # 检查剩余数量
        remain_number = self._special_check_remain(self.I_SP_BUY_LOW)
        if remain_number < buy_number:
            logger.warning(f'Low is not enough: {remain_number}')
            if remain_number <= 0:
                logger.warning('Low remain is empty')
                return
            buy_number = remain_number
        # 用金币买的没必要再检查金币
        # 购买 , 低级级盘一次最多购买20个
        buy_max = 20
        logger.info(f'Buy number is {buy_number}')
        if buy_number >= buy_max:
            buy_cycles_number = buy_number // buy_max
            buy_res_number = buy_number % buy_max
        else:
            buy_cycles_number = None
            buy_res_number = buy_number
        if buy_cycles_number:
            for i in range(buy_cycles_number):
                ret = self.buy_more(self.I_SP_BUY_LOW)
                # 溢出了就不买了
                if ret is not None and not ret:
                    break
                time.sleep(0.5)
        if buy_res_number:
            self.buy_more(self.I_SP_BUY_LOW, buy_res_number)
            time.sleep(0.5)

    def _special_check_remain(self, target: RuleImage):
        """
        检查这个种类的剩余， 要求必须这个出现在当前的页面
        :param target:
        :return:
        """
        upper_midpoint = target.roi_front[0] + target.roi_front[2] // 2, target.roi_front[1]
        # 重设roi
        roi = self.O_SP_RES_NUMBER.roi
        self.O_SP_RES_NUMBER.roi[0] = upper_midpoint[0] - roi[2] // 2
        self.O_SP_RES_NUMBER.roi[1] = upper_midpoint[1] - roi[3]
        # logger.info(f'图片的ROI是: {target.roi_front}')
        # logger.info(f'上中点是：{upper_midpoint}')
        # logger.info(f'数字的ROI是: {self.O_SP_RES_NUMBER.roi}')
        txt = self.O_SP_RES_NUMBER.ocr(self.device.image)
        txt = txt.replace('？', '2').replace('?', '2').replace(':', '；').replace('火', '次').replace('教', '数').replace('刺', '剩').replace('头', '买')
        match = re.search(r'(\d+)', txt)
        result = int(match.group(1)) if match else 0
        logger.info(f'Remain [{result}]')
        return result


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = Special(c, d)

    t.execute_special()

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time
import numpy as np

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger
from module.exception import TaskRecoveryFailed

from tasks.GameUi.page import page_main, page_guild
from tasks.GameUi.game_ui import GameUi
from tasks.Component.Buy.buy import Buy
from tasks.WeeklyPurchase.assets import WeeklyPurchaseAssets



class MallNavbar(GameUi, WeeklyPurchaseAssets):

    def _enter_consignment(self):
        """
        进入寄售屋
        :return:
        """
        self.ui_click(self.I_MALL_CONSIGNMENT, self.I_MALL_CONSIGNMENT_CHECK)

    def _enter_scales(self):
        """
        进入密卷屋 蛇皮
        :return:
        """
        self.ui_click(self.I_MALL_SCCALES, self.I_MALL_SCCALES_CHECK)

    def _enter_bondlings(self):
        """
        进入契灵
        :return:
        """
        self._enter_scales()
        self.ui_click(self.I_MALL_BONDLINGS_SURE, self.I_MALL_BONDLINGS_ON)

    def _enter_sundry(self):
        """
        进入杂货铺
        :return:
        """
        self.ui_click(self.I_MALL_SUNDRY, self.I_MALL_SUNDRY_CHECK)

    def _enter_special(self) -> bool:
        """
        进入特殊
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='special', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_SPECIAL', self.I_SIDE_CHECK_SPECIAL)

    def click_and_check(self, pos, control_name, check_rule: RuleImage) -> bool:
        """
        点击并检查
        :param pos: 点击位置
        :param control_name: 控件名称
        :param check_rule: 检查规则
        :return: 是否点击并检查成功
        """
        if not pos:
            raise TaskRecoveryFailed(f'Mall sidebar target missing: {control_name}')
        pos = self._mall_sidebar_center(pos)
        self.screenshot()
        if self._mall_sidebar_selected(pos, check_rule, control_name):
            result = self._ui_verify(lambda: self._mall_sidebar_selected(pos, check_rule, control_name), [])
            self._mall_sidebar_position = pos
            self._mall_sidebar_control = control_name
            return result
        if self.device.click(x=pos[0], y=pos[1], control_name=control_name) is False:
            raise TaskRecoveryFailed(f'Mall sidebar click was not dispatched: {control_name}')
        result = self._ui_verify(lambda: self._mall_sidebar_selected(pos, check_rule, control_name), [control_name])
        self._mall_sidebar_position = pos
        self._mall_sidebar_control = control_name
        return result

    def _mall_sidebar_center(self, pos):
        # list_find may return a random point inside the matching label.
        navbar = getattr(self, 'L_RM_NAVBAR', None)
        candidates = [getattr(navbar, '_target', None)]
        candidates.extend(getattr(navbar, 'targets', {}).values())
        for rule in candidates:
            if rule is None:
                continue
            if tuple(rule.roi_front) == tuple(rule.roi_back):
                continue  # Unmatched rules still cover the entire search area.
            x, y, width, height = rule.roi_front
            if x <= pos[0] < x + width and y <= pos[1] < y + height:
                return x + width // 2, y + height // 2
        return pos

    def _mall_sidebar_selected(self, pos, check_rule, control_name=None):
        # Current mall shows all wallet icons at once. A currency icon alone
        # therefore cannot prove that a sidebar category is selected.
        if not pos or not self.appear(self.I_MALL_SUNDRY_CHECK) or not self.appear(check_rule):
            return False
        names = {'RM_NAVBAR_SPECIAL': 'special', 'RM_NAVBAR_HONOR': ['honor', 'duel'],
                 'RM_NAVBAR_FRIENDSHIP': 'friendship', 'RM_NAVBAR_MEDAL': 'medal',
                 'RM_NAVBAR_CHARM': 'charm'}
        navbar = getattr(self, 'L_RM_NAVBAR', None)
        if navbar is not None and control_name in names:
            # The sidebar recenters its entries after a category is selected.
            # Match the label again on this frame; the input position is stale.
            current = navbar.image_appear(self.device.image, names[control_name])
            if not current:
                return False
            pos = self._mall_sidebar_center(current)
        x, y = pos
        image = self.device.image
        height, width = image.shape[:2]
        crop = image[max(0, y - 40):min(height, y + 40), max(0, x - 40):min(width, x + 40)]
        if not crop.size:
            return False
        red, green, blue = np.moveaxis(crop.astype(np.int16), -1, 0)
        pink = (red > 180) & (blue - green > 8) & (red - green > 15)
        return float(np.mean(pink)) > .01

    def _enter_honor(self) -> bool:
        """
        进入荣誉 屋
        :return:
        """
        self._enter_sundry()
        img_names = ['honor', 'duel']
        pos = self.list_find(self.L_RM_NAVBAR, name=img_names, max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_HONOR', self.I_SIDE_CHECK_HONOR)

    def _enter_friendship(self):
        """
        友情点
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='friendship', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_FRIENDSHIP', self.I_SIDE_CHECK_FRIENDS)

    def _enter_medal(self):
        """
        勋章
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='medal', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_MEDAL', self.I_SIDE_CHECK_MEDAL)

    def _enter_charisma(self):
        """
        魅力
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='charm', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_CHARM', self.I_SIDE_CHECK_CHARISMA)

    def back_mall(self):
        """
        返回商城
        :return:
        """
        self.ui_click(self.I_UI_BACK_YELLOW, self.I_CHECK_MALL)

    def mall_resource(self, index: int) -> int:
        """
        获取商城资源，
        :param index: 从左开始数
        :return:
        """
        match = {
            1: self.O_MALL_RESOURCE_1,
            2: self.O_MALL_RESOURCE_2,
            3: self.O_MALL_RESOURCE_3,
            4: self.O_MALL_RESOURCE_4,
            5: self.O_MALL_RESOURCE_5,
            6: self.O_MALL_RESOURCE_6,
        }
        self.screenshot()
        result = match[index].ocr(self.device.image)
        # match = re.search(r'\d+', result)
        # result = int(match.group())
        if not isinstance(result, int):
            logger.warning(f'Get mall resource {index} error, result: {result}')
        if result == 0:
            logger.warning(f'Get mall resource {index} error, result: {result}')
        return result

    def mall_check_money(self, index: int, least: int) -> bool:
        return self.mall_resource(index) >= least

if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = MallNavbar(c, d)

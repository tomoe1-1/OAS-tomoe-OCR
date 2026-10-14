# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep, monotonic
from datetime import timedelta, datetime, time
from cached_property import cached_property

from module.exception import TaskEnd, TaskRecoveryFailed
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records
from tasks.SoulsTidy.assets import SoulsTidyAssets
from tasks.SoulsTidy.config import SimpleTidy
from typing import Optional


class ScriptTask(GameUi, SoulsTidyAssets):
    def _verify_state(self, predicate, label, timeout=12):
        """Wait for two fresh matching frames; never replay the preceding click."""
        deadline = monotonic() + timeout
        generation = self.device._escape_generation
        stable = 0
        while monotonic() < deadline:
            self.screenshot()
            if self.device._escape_generation != generation:
                raise TaskRecoveryFailed(f'SoulsTidy left its page during {label}')
            stable = stable + 1 if predicate() else 0
            if stable >= 2:
                logger.info(f'SoulsTidy verified: {label}')
                return
        raise TaskRecoveryFailed(f'SoulsTidy state verification timed out: {label}')

    def _click_once(self, control, predicate, label):
        if not self.click(control, interval=0.1):
            raise TaskRecoveryFailed(f'SoulsTidy click blocked: {control.name}')
        self._verify_state(predicate, label)
        self.device.confirm_control_progress([control])

    def _discard_page(self):
        return self.appear(self.I_ST_CAT) and self.appear(self.I_ST_ABANDONED_SELECTED)

    def _donation_selected(self):
        if not self._discard_page() or not self.appear(self.I_ST_DONATE):
            return False
        amount = self.O_ST_GOLD.ocr(self.device.image)
        return isinstance(amount, int) and amount > 0

    def run(self):
        self.goto_page(page_shikigami_records)
        con = self.config.souls_tidy
        if con.simple_tidy.enable_greed or con.simple_tidy.enable_maneki:
            self.goto_souls()
            self.greed_maneki()
            self.goto_page(page_shikigami_records)

        self.set_next_run(task='SoulsTidy', success=True, finish=False)
        raise TaskEnd('SoulsTidy')

    def goto_souls(self):
        """
        进入到御魂的主界面
        :return:
        """
        while 1:
            self.screenshot()
            if self.appear(self.I_ST_GREED) and self.appear(self.I_ST_TIDY):
                break

            if self.appear_then_click(self.I_ST_REPLACE, interval=1):
                continue
            if self.appear_then_click(self.I_ST_SOULS, interval=1):
                continue
            if self.appear_then_click(self.I_ST_SOULS_CLOSE, interval=1):
                continue
            if self.click(self.C_ST_DETAIL, interval=2):
                continue
        # 御魂超过上限的提示
        self.ocr_appear_click(self.O_ST_OVERFLOW)
        logger.info('Enter souls page')

    def greed_maneki(self):
        """
        贪吃鬼和招财猫
        :return:
        """
        # 先是贪吃鬼
        if self.config.souls_tidy.simple_tidy.enable_greed:
            logger.hr('Greed Ghost')
            self.ui_click(self.I_ST_GREED, self.I_ST_GREED_HABIT)
            self.ui_click(self.I_ST_GREED_HABIT, self.I_ST_FEED_NOW)
            logger.info('Feed greed ghost')
            feed_sent = False
            feed_started = None
            deadline = monotonic() + 20
            generation = self.device._escape_generation
            while 1:
                self.screenshot()
                if self.device._escape_generation != generation or monotonic() >= deadline:
                    raise TaskRecoveryFailed('SoulsTidy greed feeding did not settle')
                if self.appear(self.I_ST_UNSELECTED):
                    self._click_once(self.I_ST_UNSELECTED,
                                     lambda: not self.appear(self.I_ST_UNSELECTED),
                                     'feeding option selected')
                    continue
                if self.appear(self.I_UI_CONFIRM):
                    self._click_once(self.I_UI_CONFIRM,
                                     lambda: not self.appear(self.I_UI_CONFIRM),
                                     'feeding confirmation dismissed')
                    continue
                if feed_sent and monotonic() - feed_started >= 3 and self.appear(self.I_ST_FEED_NOW):
                    self._verify_state(lambda: self.appear(self.I_ST_FEED_NOW)
                                       and not self.appear(self.I_UI_CONFIRM)
                                       and not self.appear(self.I_ST_UNSELECTED),
                                       'feeding page settled')
                    break
                if not feed_sent and self.appear(self.I_ST_FEED_NOW):
                    if not self.click(self.I_ST_FEED_NOW, interval=0.1):
                        raise TaskRecoveryFailed('SoulsTidy feed click blocked')
                    feed_sent = True
                    feed_started = monotonic()
                    continue
            logger.info('Feed greed ghost done')
        # 关闭贪吃鬼, 进入奉纳
        while 1:
            self.screenshot()
            if self.appear(self.I_ST_CAT):
                # 出现招财猫
                break

            # https://github.com/runhey/OnmyojiAutoScript/issues/662
            if self.appear(self.I_ST_UNSELECTED):
                self.ui_click_until_disappear(self.I_ST_UNSELECTED)
                continue
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.5):
                continue

            # 「进食小屋」弹窗会盖住整个御魂界面，但右侧竖排的「奉纳」二字
            # 透过弹窗依然清晰可见（实测 2026-10-11 模板分 0.9825，阈值 0.6
            # 必然通过）。此时点奉纳打在弹窗上毫无响应，最后被防连点拦死。
            # 进食小屋自带关闭叉，必须先关掉它再谈奉纳。
            if self.appear(self.I_ST_GREED_HABIT):
                logger.info('Feeding dialog is open; close it before bongna')
                if self.appear_then_click(self.I_ST_GREED_CLOSE, interval=0.7):
                    continue
                # 关闭叉没生效时不许继续点被遮挡的奉纳，交给外层恢复处理。
                self._verify_state(lambda: not self.appear(self.I_ST_GREED_HABIT),
                                   'feeding dialog closed')
                continue

            if self.appear_then_click(self.I_ST_GREED_CLOSE, interval=0.7):
                continue
            if self.appear_then_click(self.I_ST_BONGNA, interval=1, threshold=0.6):
                continue
        if self.config.souls_tidy.simple_tidy.enable_maneki:
            logger.hr('Enter bongna')
            # 确保已弃置界面
            while True:
                self.screenshot()
                if self.appear(self.I_ST_ABANDONED_SELECTED):
                    break
                # 防止因为好友消息导致误点击到好友聊天界面
                if self.appear(self.I_UI_BACK_RED):
                    self.click(self.I_UI_BACK_RED, interval=0.8)
                    continue
                self.click(self.I_ST_ABANDONED_SELECTED, interval=1.5)
            self.pre_confirm()
            self._verify_state(self._discard_page, 'discard list ready')
            self.device.confirm_control_progress([
                self.I_ST_FEED_NOW, self.I_ST_UNSELECTED, self.I_UI_CONFIRM,
                self.I_ST_GREED_CLOSE, self.I_ST_BONGNA,
            ])
            # 开始奉纳
            while True:
                found = self.find_discard_souls()
                if not found:
                    logger.info('No discardable soul found, exit')
                    break
                self._click_once(self.L_ONE, self._donation_selected, 'donation selection ready')
                gold_amount = self.O_ST_GOLD.ocr(self.device.image)
                if not isinstance(gold_amount, int) or gold_amount <= 0:
                    raise TaskRecoveryFailed('SoulsTidy donation amount changed after selection verification')
                # 点击奉纳收取奖励
                if not self.appear(self.I_ST_DONATE):
                    raise TaskRecoveryFailed('SoulsTidy donate button disappeared after selection verification')
                self.donate_and_collect_reward()
                logger.info('Donate one')

        logger.info('Bongna done')

    def pre_confirm(self):
        """前置确认：确保是按照等级来排序的"""
        logger.info('Sort by level')
        while 1:
            self.screenshot()
            # 防止因为好友消息导致误点击到好友聊天界面
            if self.appear(self.I_UI_BACK_RED):
                self.click(self.I_UI_BACK_RED, interval=0.8)
                continue
            if self.ocr_appear(self.O_ST_SORT_LEVEL_1):
                break
            if self.ocr_appear_click(self.O_ST_SORT_LEVEL_2, interval=0.6):
                continue
            if self.ocr_appear_click(self.O_ST_SORT_TIME, interval=2):
                continue
            if self.ocr_appear_click(self.O_ST_SORT_TYPE, interval=2):
                continue
            if self.ocr_appear_click(self.O_ST_SORT_LOCATION, interval=2):
                continue

    def find_discard_souls(self) -> bool:
        """寻找是否有可弃置的御魂
        :return: True有 False没有/识别失败
        """
        timeout_timer = Timer(3).start()
        interval_timer = Timer(0.6).start()
        while not timeout_timer.reached():
            self.screenshot()
            if not self._discard_page():
                raise TaskRecoveryFailed('SoulsTidy cannot check souls outside the discard list')
            # 控制检测频率
            if not interval_timer.started():
                interval_timer.start()
            elif interval_timer.reached():
                interval_timer.reset()
            else:
                continue
            if self.appear(self.I_ST_SOUL_STACK) or self.appear(self.I_ST_SOUL_STACK_1):
                logger.info('Find stacked discard souls')
                return True
            if self.appear(self.I_ST_LEVEL_0):
                logger.info('Find level 0 discard souls')
                return True
            first_soul_level = self.O_ST_FIRST_LEVEL.ocr(self.device.image)
            if not first_soul_level or first_soul_level.strip() == '':
                continue
            if first_soul_level.strip() in ['+0', '古']:
                logger.info('Find level 0 discard souls')
                return True
        return False

    def donate_and_collect_reward(self):
        """点击奉纳和收取奖励"""
        generation = self.device._escape_generation
        deadline = monotonic() + 30
        if not self._donation_selected():
            raise TaskRecoveryFailed('SoulsTidy donation selection is not verified')
        if not self.click(self.I_ST_DONATE, interval=0.1):
            raise TaskRecoveryFailed('SoulsTidy donate click blocked')
        saw_response = False
        stable = 0
        while monotonic() < deadline:
            self.screenshot()
            if self.device._escape_generation != generation:
                raise TaskRecoveryFailed('SoulsTidy reward collection interrupted by recovery')
            if self.appear(self.I_UI_CONFIRM):
                saw_response = True
                self._click_once(self.I_UI_CONFIRM,
                                 lambda: not self.appear(self.I_UI_CONFIRM),
                                 'donation confirmation dismissed')
                continue
            reward = next((rule for rule in (
                self.I_ST_GOD_PRESENT, self.I_ST_LUCK, self.I_ST_SMALL_LUCK,
                self.I_UI_REWARD,
            ) if self.appear(rule)), None)
            if reward is not None:
                saw_response = True
                # Use the common reward overlay's blank area, not the old title-side box.
                self._click_once(self.C_UI_REWARD,
                                 lambda: not self.appear(reward),
                                 f'{reward.name} dismissed')
                stable = 0
                continue
            # OCR failure is not proof that selection has been cleared.
            returned = (self._discard_page()
                        and self.O_ST_GOLD.ocr(self.device.image) == 0)
            stable = stable + 1 if saw_response and returned else 0
            if stable >= 2:
                self.device.confirm_control_progress([
                    self.I_ST_DONATE, self.L_ONE, self.C_UI_REWARD, self.I_UI_CONFIRM,
                ])
                logger.info('Donation reward collected; discard list verified')
                return
        raise TaskRecoveryFailed('SoulsTidy donation reward did not return to discard list')

if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    #t.greed_maneki()
    t.run()

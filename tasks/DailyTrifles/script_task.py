# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import copy
from time import sleep, monotonic

import difflib
from datetime import time, datetime, timedelta
from module.atom.image import RuleImage
from module.ocr.common import BoxedResult

from tasks.Component.config_base import Time
from tasks.DailyTrifles.page import page_store_gift_room, page_friends_luck, page_guild_wish

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_summon, page_guild, page_mall, page_mall_recommend, page_friends, page_courtyard_affairs
from tasks.DailyTrifles.config import DailyTriflesConfig
from tasks.DailyTrifles.courtyard_claim import close_courtyard_claim_popups, courtyard_claim_succeeded
from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.DailyTrifles.store_sign import find_daily_tab, daily_tab_selected, gift_room_visible, daily_gift_claim_center, daily_gift_claimed_dismiss
from tasks.Component.Summon.summon import Summon

from module.logger import logger
from module.exception import TaskEnd, TaskRecoveryFailed
from module.base.timer import Timer
from tasks.DailyTrifles.config import SummonType
import re
from typing import Any, Optional, List, Callable


class ScriptTask(GameUi, Summon, DailyTriflesAssets):

    def run(self):
        con = self.config.daily_trifles.trifles_config
        try:
            # 每日召唤
            if con.one_summon:
                self.run_one_summon()
            if con.courtyard_affairs:
                self.run_courtyard_affairs()
            if con.pickup_email:
                self.run_pickup_email()
            if self.config.daily_trifles.guild_donate.enable:
                self.run_guild_donate()
            # 吉闻
            if con.luck_msg:
                self.run_luck_msg()
            # 商店签到 or 购买寿司
            if con.store_sign or con.buy_sushi_count > 0:
                self.run_store()
        finally:
            # A later failed subtask must not erase already verified daily dates
            # when the runner reloads configuration to schedule its retry.
            self.config.save()
        self.plan_next_dt()
        raise TaskEnd('DailyTrifles')

    def run_one_summon(self):
        logger.hr('daily summon', 2)
        if self.config.daily_trifles.today_is_done('summon'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_summon)
        config = self.config.daily_trifles.trifles_config
        if config.summon_type == SummonType.default:
            self.summon_one(draw_mystery_pattern=config.draw_mystery_pattern)
            self.check_time()
        elif config.summon_type == SummonType.recall:
            self.summon_recall()
        self.back_summon_main()
        self.config.daily_trifles.done_record.summon_dt = datetime.now()

    def check_time(self):
        config = self.config.daily_trifles.trifles_config
        now = datetime.now()
        next_run = now + self.config.daily_trifles.scheduler.success_interval
        # 检查是否跨月（next_run的月份与当前月份不同）
        if next_run.month != now.month:
            # 跨月重置神秘图案触发状态
            if not config.draw_mystery_pattern:
                config.draw_mystery_pattern = True
                logger.info(
                    f"reset draw_mystery_pattern to True, next_run: {next_run}")
        else:
            # 如果还是在同一月份，则没必要再绘制神秘图案
            config.draw_mystery_pattern = False
        self.config.save()

    def summon_recall(self):
        """
        确保在召唤界面,每日召唤一次
        召唤结束后回到 召唤主界面
        :return:
        """
        list = [self.O_SELECT_SM2, self.O_SELECT_SM3, self.O_SELECT_SM4]
        count = 0
        while True:
            count += 1

            for i in range(len(list)):
                sleep(1)
                self.goto_page(page_summon)
                self.appear_then_click(self.I_UI_BACK_RED, interval=1)
                x, y = list[i].coord()
                self.device.click(x, y)
                sleep(1)
                self.screenshot()
                if self.appear(self.I_RECALL_TICKET):
                    break
                logger.info("Select preset group RECALL")

            self.screenshot()
            if self.appear(self.I_RECALL_TICKET):
                break
            if count >= 3:
                self.config.notifier.push(title='今忆召唤抽卡失败', content='每日任务,今忆召唤抽卡失败!!!')
                return

        logger.info('Summon one RECALL')
        self.wait_until_appear(self.I_RECALL_TICKET)
        while True:
            ticket_info = self.O_RECALL_TICKET_AREA.ocr(self.device.image)
            # 处理 None 和空字符串
            if ticket_info is None or ticket_info == '':
                ticket_info = 0
            else:
                # 使用正则表达式提取字符串中的数字
                match = re.search(r'\d+', ticket_info)
                if match:
                    ticket_info = int(match.group())
                else:
                    logger.warning(f'Invalid ticket_info value: {ticket_info}, expected a numeric string')
                    ticket_info = 0  # 将无效值设置为默认值 0
            if ticket_info <= 0:
                logger.warning('There is no any one RECALL ticket')
                return
            # 某些情况下滑动异常
            self.S_RANDOM_SWIPE_1.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_2.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_3.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_4.name = 'S_RANDOM_SWIPE'
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_ONE_TICKET):
                    break
                if self.appear_then_click(self.I_RECALL_TICKET, interval=1):
                    continue

            # 画一张票
            sleep(1)
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_SM_CONFIRM, interval=0.6):
                    self.ui_click_until_disappear(self.I_RECALL_SM_CONFIRM)
                    break
                if self.appear(self.I_SM_CONFIRM_2, interval=0.6):
                    self.ui_click_until_disappear(self.I_SM_CONFIRM_2)
                    break
                if self.appear(self.I_RECALL_ONE_TICKET, interval=1):
                    # 某些时候会点击到 “语言召唤”
                    if self.appear_then_click(self.I_UI_CANCEL, interval=0.8):
                        continue
                    self.summon()
                    continue
            logger.info('Summon one success')

    def run_guild_donate(self):
        logger.hr('guild donate', 2)
        if self.config.daily_trifles.today_is_done('guild_donate'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_guild_wish)
        timeout_timer = Timer(2).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_DT_GW_THANKS):
                self.ui_click(self.I_DT_GW_THANKS, self.I_DT_GW_THANKED, interval=0.8)
                timeout_timer.reset()
                continue
        self.appear_then_click(self.I_UI_BACK_RED)
        donate_datas: list = [
            (self.config.daily_trifles.guild_donate.guild_member_list_v,
             lambda : self.switch_select(self.I_DT_GW_GUILD_MEMBER_SELECTED, self.I_DT_GW_FRIEND_SELECTED, self.I_DT_GW_SELECT_GUILD_MEMBER),
             self.config.daily_trifles.guild_donate.name_check),
            (self.config.daily_trifles.guild_donate.friend_list_v,
             lambda : self.switch_select(self.I_DT_GW_FRIEND_SELECTED, self.I_DT_GW_GUILD_MEMBER_SELECTED, self.I_DT_GW_SELECT_FRIEND),
             self.config.daily_trifles.guild_donate.name_check)
        ]
        all_done = True
        for name_list, switch_func, name_check in donate_datas:
            all_done = all_done and self.donate(name_list, switch_func, name_check)
        if self.config.daily_trifles.guild_donate.auto_get_rewards:
            self.guild_donate_get_reward()
        self.config.daily_trifles.done_record.guild_donate_finish = all_done
        self.goto_page(page_main)

    def guild_donate_get_reward(self):
        """领取捐赠碎片的奖励"""
        timeout_timer = Timer(3).start()
        has_reward = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_UI_BACK_RED, interval=0.6):
                break
            if self.appear(self.I_DT_GW_DONATE_RECORD_RED):
                self.ui_click(self.I_DT_GW_DONATE_RECORD, self.I_UI_BACK_RED, interval=1.2)
                has_reward = True
                continue
        if not has_reward:
            logger.info('No reward can get, exit')
            return
        timeout_timer.reset()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.6):
                continue
            if self.appear(self.I_DT_GW_DONATE_RECORD_THANKS):  # 受赠界面的一键感谢
                self.ui_get_reward(self.I_DT_GW_DONATE_RECORD_THANKS)
                timeout_timer.reset()
                continue
            if self.appear(self.I_DT_GW_DONATE_RED, interval=2.5):  # 赠予界面的一键领取
                self.ui_click(self.I_DT_GW_GIVE, self.I_DT_GW_ONE_COLLECT)
                self.appear_then_click(self.I_DT_GW_ONE_COLLECT, interval=0.6)
                timeout_timer.reset()
                continue
        self.ui_click_until_disappear(self.I_UI_BACK_RED)

    def donate(self, name_list: List[str], switch_func: Callable, name_check: bool) -> bool:
        """执行碎片捐赠流程

        :param name_list: 待捐赠碎皮的名称列表
        :param switch_func: 切换好友/阴阳寮/...的方法
        :param name_check: 是否使用ocr检查用户名
        :return: 是否全部捐赠成功 (出现检索名称后为空或者碎皮不足都是False, 仅全部捐成功才是True)
        """
        all_done = True
        for name in name_list:
            switch_func()
            self.swipe(self.S_DT_GW_OPEN_SEARCH, interval=1.2)  # 向下滑动拉出搜索框
            # 从按照交换搜索切换到按名称搜索
            self.switch_select(self.I_DT_GW_SEARCH_BY_NAME, self.I_DT_GW_SEARCH_BY_SWAP, self.I_DT_GW_SELECT_BY_NAME)
            self.appear_then_click(self.I_DT_GW_CLEAR_SEARCH)  # 清除搜索框内容
            self.ui_click(self.C_DT_GW_INPUT_SEARCH, self.I_DT_GW_CONFIRM, interval=1.5)  # 点击搜索框
            self.click(self.C_DT_GW_CLICK_INPUT)  # 点击名称输入框
            self.device.adb.send_keys(name)  # 输入名称
            self.ui_click_until_disappear(self.I_DT_GW_CONFIRM, interval=1.5)  # 点击确定
            donate_btn = self.I_DT_GW_DONATE
            if name_check:  # 若有多个相同前缀名称, 则需要取出一样的或最相近的名称
                name_roi = self.find_target_name(name)
                if name_roi is None:
                    logger.warning(f'{name} check failed, maybe not wish or not find')
                    all_done = False
                    continue
                # 设置赠与按钮back与对应name同一行
                donate_btn.roi_back = [name_roi[0], name_roi[1] - 15, max(name_roi[2] + 700, 1280),
                                       max(name_roi[3] + 60, 720)]
            self.I_DT_GW_FULL.roi_back = donate_btn.roi_back  # 设置已捐满标志back区域和赠与按钮同一行
            self.I_DT_GW_INSUFFICIENT.roi_back = donate_btn.roi_back  # 设置碎片不足标志back区域和赠与按钮同一行
            donate_ret = self.process_donate(donate_btn, name)
            all_done = all_done and donate_ret  # 有一次没成功则all_done永远False
        return all_done

    def process_donate(self, donate_btn: RuleImage, name: str) -> bool:
        """捐赠式神碎片

        :param name: 被捐方名称
        :param donate_btn: 赠与按钮
        :return: 捐赠是否成功
        """
        timeout_timer = Timer(3).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.6):
                continue
            if self.appear(self.I_DT_GW_SEARCH_EMPTY):
                logger.warning('Maybe not wish or not find, skip')
                if self.config.daily_trifles.guild_donate.notify_enable:
                    self.config.notifier.push(title='好友搜索失败', content=f'{name} 搜索失败, 没有搜索到对应用户, 无法捐赠')
                return False
            if self.appear_then_click(donate_btn, interval=0.6):
                timeout_timer.reset()
                continue
            if self.appear(self.I_DT_GW_INSUFFICIENT, interval=0.6):
                logger.warning('Not enough fragment to donate, skip')
                if self.config.daily_trifles.guild_donate.notify_enable:
                    self.config.notifier.push(title='捐赠碎片不足', content=f'捐给{name}的碎片不足, 请上线查看')
                return False
            if self.appear(self.I_DT_GW_FULL, interval=1.2):
                logger.info(f'Donate success!')
                return True
        return False

    def find_target_name(self, name) -> List[int]:
        """寻找目标名称
        :param name: 名称
        :return: [x, y, w, h]
        """
        timeout_timer = Timer(3).start()
        name_roi: List[int] = None
        # TODO: 这里只找了第一页, 若相似名称过多后续需要添加翻页继续找功能
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_DT_GW_SEARCH_EMPTY):  # 空的直接退出
                if self.config.daily_trifles.guild_donate.notify_enable:
                    self.config.notifier.push(title='好友搜索失败', content=f'没有搜索到对应用户 {name}, 无法捐赠')
                return None
            text_results = self.O_DT_GW_NAME.detect_and_ocr(self.device.image)
            mx_similarity = 0.5
            for result in text_results:
                if result.ocr_text == name:
                    return self.extract_roi(result)  # 名称一模一样则直接返回
                similarity = difflib.SequenceMatcher(None, result.ocr_text, name).ratio()
                if similarity > mx_similarity:
                    mx_similarity = similarity
                    name_roi = self.extract_roi(result)
            if name_roi is None:
                continue
            return name_roi  # 找到了直接退出
        return name_roi

    def extract_roi(self, result: BoxedResult) -> list[int]:
        """从ocr结果提取对应的roi坐标"""
        x = self.O_DT_GW_NAME.roi[0] + result.box[0, 0]
        y = self.O_DT_GW_NAME.roi[1] + result.box[0, 1]
        w, h = result.box[1, 0] - result.box[0, 0], result.box[2, 1] - result.box[0, 1]
        return [x, y, w, h]

    def switch_select(self, target: RuleImage, other: RuleImage, select: RuleImage):
        """切换选中的元素"""
        while True:
            self.screenshot()
            if self.appear(target):
                break
            if self.appear_then_click(select, interval=0.6):
                continue
            if self.appear_then_click(other, interval=1.8):
                continue

    def run_luck_msg(self):
        logger.hr('luck msg', 2)
        if self.config.daily_trifles.today_is_done('luck_msg'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_friends_luck)
        logger.info('Start luck msg')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()

            if self.appear_then_click(self.I_CLICK_BLESS, interval=1):
                continue
            if self.appear_then_click(self.I_ONE_CLICK_BLESS, interval=1):
                continue
            if self.ui_reward_appear_click():
                logger.info('Get reward of luck msg')
                break
            if check_timer.reached():
                logger.warning('There is no any luck msg')
                break

        self.goto_page(page_main)
        self.config.daily_trifles.done_record.luck_msg_dt = datetime.now()

    def run_store(self):
        if self.check_store_all_done():
            logger.info('Store all done, skip')
            return
        try:
            self.goto_page(page_mall_recommend, confirm_wait=3, accepted_pages=(page_mall,),
                           max_steps=12, total_timeout=60, skip_first_screenshot=False)
            if self.config.daily_trifles.trifles_config.store_sign:
                self.run_store_sign()
            if self.config.daily_trifles.trifles_config.buy_sushi_count > 0:
                self.run_buy_sushi()
            self.goto_page(page_main, max_steps=12, total_timeout=60, skip_first_screenshot=False)
        except TaskRecoveryFailed as exc:
            # Keep other completed subtasks, then use the global failure interval.
            self.config.save()
            if self.check_store_all_done():
                logger.warning(f'Store business completed; page cleanup failed separately: {exc}')
                return
            raise TaskRecoveryFailed(f'Daily store attempt stopped at its limit; unfinished work retained: {exc}') from exc

    def _store_wait(self, predicate, label, controls=()):
        """Require two fresh matching frames, with both time and frame limits."""
        deadline = monotonic() + self.device.performance.timeout(12)
        generation = self.device._escape_generation
        stable = 0
        for _ in range(24):
            if monotonic() >= deadline:
                break
            self.screenshot()
            if generation != self.device._escape_generation:
                raise TaskRecoveryFailed(f'Store sign interrupted by recovery: {label}')
            stable = stable + 1 if predicate() else 0
            if stable >= 2:
                self.device.confirm_control_progress(list(controls))
                return True
        raise TaskRecoveryFailed(f'Store sign did not reach {label} within 24 frames / 12 seconds')

    def _store_daily_page(self):
        # Re-locate the label on every frame: the sidebar can move after selection.
        return gift_room_visible(self.device.image) and daily_tab_selected(self.device.image)

    def run_store_sign(self):
        logger.hr('store sign', 2)
        if self.config.daily_trifles.today_is_done('store_sign'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_store_gift_room, max_steps=12, total_timeout=60, skip_first_screenshot=False)
        self._store_wait(lambda: gift_room_visible(self.device.image)
                         and find_daily_tab(self.device.image) is not None, 'unique daily tab')
        position = find_daily_tab(self.device.image)
        if not daily_tab_selected(self.device.image):
            if position is None or self.device.click(*position, control_name='STORE_DAILY_TAB') is False:
                raise TaskRecoveryFailed('Store daily tab click was not dispatched')
            self._store_wait(self._store_daily_page, 'selected daily tab', ['STORE_DAILY_TAB'])
        else:
            self._store_wait(self._store_daily_page, 'selected daily tab')
        logger.info('Enter store sign')
        self._store_wait(lambda: daily_gift_claimed_dismiss(self.device.image) is not None
                         or (self._store_daily_page() and self.appear(self.I_GIFT_SIGN)
                         and daily_gift_claim_center(self.device.image, self.I_GIFT_SIGN.roi_front) is not None),
                         'available or already claimed sign reward')
        if daily_gift_claimed_dismiss(self.device.image) is not None:
            self._save_store_sign_receipt()
            return
        position = daily_gift_claim_center(self.device.image, self.I_GIFT_SIGN.roi_front)
        if position is None:
            raise TaskRecoveryFailed('Daily gift claim label was not uniquely identified')
        x, y = position
        if self.device.click(x, y, control_name='STORE_SIGN_CLAIM') is False:
            raise TaskRecoveryFailed('Store sign claim click was not dispatched')
        self._store_wait(lambda: daily_gift_claimed_dismiss(self.device.image) is not None
                         or self.appear(self.I_UI_REWARD, threshold=0.6),
                         'sign reward or claimed receipt', ['STORE_SIGN_CLAIM'])
        if daily_gift_claimed_dismiss(self.device.image) is None:
            if not self.click(self.C_UI_REWARD, interval=0.1):
                raise TaskRecoveryFailed('Store sign reward continuation was not dispatched')
            self._store_wait(lambda: daily_gift_claimed_dismiss(self.device.image) is not None
                             or (not self.appear(self.I_UI_REWARD, threshold=0.6)
                                 and self._store_daily_page()),
                             'reward closed or free gift claimed', [self.C_UI_REWARD])
        self._save_store_sign_receipt()

    def _save_store_sign_receipt(self):
        # The caller has verified the free claimed card, or the legacy reward
        # closure, in two fresh frames. Save before any subsequent navigation.
        position = daily_gift_claimed_dismiss(self.device.image)
        if position is not None:
            self._store_wait(lambda: daily_gift_claimed_dismiss(self.device.image) is not None,
                             'free gift claimed receipt')
            position = daily_gift_claimed_dismiss(self.device.image)
        self.config.daily_trifles.done_record.store_sign_dt = datetime.now()
        self.config.save()
        logger.info('Get reward of gift sign; verified and saved')
        if position is not None:
            if self.device.click(*position, control_name='STORE_SIGN_RECEIPT_DISMISS') is False:
                raise TaskRecoveryFailed('Claimed daily gift popup close was not dispatched')
            self._store_wait(lambda: daily_gift_claimed_dismiss(self.device.image) is None
                             and not self.appear(self.I_UI_REWARD, threshold=0.6)
                             and self._store_daily_page(),
                             'claimed receipt closed', ['STORE_SIGN_RECEIPT_DISMISS'])

    def run_buy_sushi(self):
        logger.hr('store sushi', 2)
        if self.config.daily_trifles.today_is_done('sushi'):
            logger.info('Today is done, skip')
            return
        # 进入Special
        while 1:
            from tasks.WeeklyPurchase.assets import WeeklyPurchaseAssets
            self.screenshot()
            if self.appear(WeeklyPurchaseAssets.I_SIDE_CHECK_SPECIAL):
                break
            if self.appear_then_click(WeeklyPurchaseAssets.I_MALL_SUNDRY, interval=1):
                continue


        def detect_buy_count(base_element) -> (int, int):
            # 返回count,price
            MAX_PRICE = 9999
            MAX_COUNT = 9999
            roi = copy.deepcopy(base_element.roi_front)
            roi[0] = roi[0] + roi[2]
            roi[1] = roi[1] + roi[3] - 30
            roi[2] = 60
            roi[3] = 30
            self.O_STORE_SUSHI_PRICE.roi = roi
            _price = self.O_STORE_SUSHI_PRICE.detect_text(self.device.image)
            # 保守策略，避免OCR错误购买
            try:
                _price = int(_price)
            except Exception as e:
                _price = MAX_PRICE

            if _price < 60:
                return 0, MAX_PRICE
            _count = (_price - 60) / 20
            return _count, _price

        roi = None
        # 购买体力
        while 1:
            self.screenshot()
            # count, price = detect_buy_count(roi)
            # if count >= self.config.model.daily_trifles.trifles_config.buy_sushi_count:
            #     break
            if self.appear(self.I_STORE_COST_TYPE_JADE):
                count, price = detect_buy_count(self.I_STORE_COST_TYPE_JADE)
                if count >= self.config.daily_trifles.trifles_config.buy_sushi_count:
                    break
                self.ui_click_until_disappear(self.I_STORE_COST_TYPE_JADE, interval=2)
                logger.info(f"Buy Sushi With {price} Jade")
                continue

            if self.appear(self.I_SPECIAL_SUSHI):
                # 此处确定当前购买体力所需勾玉数量的位置,用于后续识别
                count, price = detect_buy_count(self.I_SPECIAL_SUSHI)
                if count >= self.config.daily_trifles.trifles_config.buy_sushi_count:
                    break
                self.ui_click(self.I_SPECIAL_SUSHI, stop=self.I_STORE_COST_TYPE_JADE, interval=2)
                continue
        self.config.daily_trifles.done_record.sushi_dt = datetime.now()

    def run_courtyard_affairs(self):
        """庭院事务"""
        logger.hr('courtyard affairs', 2)
        self.goto_page(page_main)
        timeout_timer = Timer(3).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_ENTER_COURTYARD_AFFAIRS, interval=1.2):
                self.goto_page(page_courtyard_affairs)
                timeout_timer.reset()
                break
        if timeout_timer.reached():
            logger.info('Not have courtyard affairs, exit')
            return
        while True:
            self.screenshot()
            if self.appear(self.I_CHECK_IN_DAILY, interval=0.5):
                break
            if self.appear_then_click(self.I_ENTER_DAILY, interval=1):
                continue
        self.appear_then_click(self.I_ONE_COMPLETE, interval=1)
        claim_state = {'confirmed': False}
        def save_claim_success():
            if not courtyard_claim_succeeded(self.device.image):
                raise TaskRecoveryFailed('Courtyard 领取成功 title was not observed')
            if not claim_state['confirmed']:
                self.config.daily_trifles.done_record.courtyard_affairs_dt = datetime.now()
                self.config.save()
                claim_state['confirmed'] = True
                logger.info('Courtyard claim completed: 领取成功 title confirmed and saved')
        # The fortune card may obscure the last title character. Close it first,
        # then save the full receipt title before dismissing that receipt.
        try:
            close_courtyard_claim_popups(self, on_success=save_claim_success)
            if not claim_state['confirmed']:
                raise TaskRecoveryFailed('Courtyard claim lacked the 领取成功 title')
            self.goto_page(page_main)
        except TaskRecoveryFailed as exc:
            if not claim_state['confirmed']:
                raise
            logger.warning(f'Courtyard claim completed; popup/navigation cleanup failed separately: {exc}')

    def run_pickup_email(self):
        """领取邮件"""
        logger.hr('pick up email', 2)
        self.screenshot()
        # A claim confirmation can still be open from an earlier attempt.
        mail_opened = self.appear(self.I_HARVEST_MAIL_CONFIRM)
        if not mail_opened:
            self.goto_page(page_main)
        timeout_timer = Timer(30).start()
        idle_timer = Timer(3).start()
        claim_wait_timer = None
        collect_clicks = 0
        read_clicks = 0
        while not timeout_timer.reached():
            self.screenshot()
            # The top-right mail icon remains visible behind this modal. Handle
            # the confirmation first so it cannot reopen/close the mailbox.
            if self.appear_then_click(self.I_HARVEST_MAIL_CONFIRM, interval=0.5):
                claim_wait_timer = None
                idle_timer.reset()
                continue
            if claim_wait_timer is not None and not claim_wait_timer.reached():
                continue
            claim_wait_timer = None
            if collect_clicks < 2 and self.appear_then_click(self.I_HARVEST_MAIL_ALL, interval=1):
                collect_clicks += 1
                claim_wait_timer = Timer(2).start()
                idle_timer.reset()
                continue
            # Mark-all-read may be a no-op when every message is already read.
            # Do not issue a second request after its unchanged click receipt.
            if read_clicks < 1 and self.appear_then_click(self.I_READ_ALL_MAIL, interval=2):
                read_clicks += 1
                idle_timer.reset()
                continue
            if not mail_opened and (
                self.appear_then_click(self.I_DT_HARVEST_MAIL_COPY2, interval=1.2)
                or self.appear_then_click(self.I_HARVEST_MAIL, interval=1.2)
                or self.appear_then_click(self.I_HARVEST_MAIL_COPY, interval=1.2)
            ):
                mail_opened = True
                idle_timer.reset()
                continue
            if idle_timer.reached():
                break
        # A final claim may open just as the overall timeout expires.
        for _ in range(3):
            self.screenshot()
            if not self.appear_then_click(self.I_HARVEST_MAIL_CONFIRM):
                break
            sleep(0.5)
        else:
            logger.warning('Mail claim confirmation is still open; retry next run')
            return
        self.goto_page(page_main)
        self.config.daily_trifles.done_record.pickup_email_dt = datetime.now()

    def plan_next_dt(self):
        # 定时领体力（每天 12-14、20-22 时内各有 20 体力）
        now = datetime.now()
        # 如果时间在00:00-12:00之间则设定时间为当日 12 时
        if now.time() < time(12, 0):
            self.custom_next_run(task='DailyTrifles', custom_time=Time(12, 0), time_delta=0)
        # 如果时间在12:00-20:00之间则设定时间为当日 20 时
        elif time(12, 0) <= now.time() < time(20, 0):
            self.custom_next_run(task='DailyTrifles', custom_time=Time(20, 0), time_delta=0)
        # 如果时间在20:00-23:59之间则设定时间为次日 12 时
        else:
            self.custom_next_run(task='DailyTrifles', custom_time=Time(12, 0), time_delta=1)

    def check_store_all_done(self) -> bool:
        """判断商店任务是否都做完了, 做完了则不再进入商店"""
        if self.config.daily_trifles.trifles_config.store_sign and not self.config.daily_trifles.today_is_done('store_sign'):
            return False
        if self.config.daily_trifles.trifles_config.buy_sushi_count > 0 and not self.config.daily_trifles.today_is_done('sushi'):
            return False
        return True


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas2')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run_guild_donate()

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from time import sleep

import random
import re
from cached_property import cached_property
from enum import Enum
from datetime import timedelta
from module.atom.image import RuleImage
from module.atom.click import RuleClick

from module.exception import TaskEnd, RequestHumanTakeover, TaskRecoveryFailed
from module.logger import logger
from module.base.timer import Timer
from module.atom.ocr import RuleOcr
from tasks.CollectiveMissions.config import MC
from tasks.CollectiveMissions.page import page_collective_missions

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_guild
from tasks.CollectiveMissions.assets import CollectiveMissionsAssets


class ScriptTask(GameUi, CollectiveMissionsAssets):
    """阴阳寮集体任务"""

    current_mission: MC = None

    def run(self):
        try:
            # A previous attempt may have stopped at a reward overlay.
            self._dismiss_collective_reward()
            self.goto_page(page_collective_missions)
            logger.info('Start to detect missions')
            self.get_task_reward()
            if self.is_finish():
                self._finish_collective_task('daily submissions already completed')
            self.current_mission = None
            self.select_and_update_cur_mission(self.config.collective_missions.missions_config.missions_select)
            if self.current_mission is None:
                raise TaskRecoveryFailed('No collective mission could be selected')
            match self.current_mission:
                case MC.FEED:
                    self._feed()
                case MC.AW1 | MC.AW2 | MC.AW3 | MC.GR1 | MC.GR2 | MC.GR3:
                    self._donate()
                case MC.SO1 | MC.SO2:
                    self._soul()
        except TaskRecoveryFailed:
            # Verified business completion survives a reward/return failure.
            if self.is_finish():
                self._finish_collective_task('daily submissions completed; cleanup needs recovery')
            raise
        if not self.is_finish():
            raise TaskRecoveryFailed('Collective submissions are not yet complete')
        self._finish_collective_task('daily submissions completed')

    def _finish_collective_task(self, reason):
        try:
            self.finish_completed_task('CollectiveMissions', reason, verify=self.is_finish)
        except TaskEnd:
            # Business completion must reach the scheduler even if returning
            # from this modal fails. Independent recovery verifies home before
            # another business task; do not repeat reward clicks here.
            self.config.task_runtime_outcome = {
                'task': 'CollectiveMissions', 'status': 'success', 'cleanup_pending': True,
            }
            raise

    def _close_collective_panel(self):
        generation = getattr(self.device, '_escape_generation', 0)
        previous = None
        for _ in range(2):
            self.screenshot()
            if getattr(self.device, '_escape_generation', 0) != generation:
                raise TaskRecoveryFailed('Collective close verification interrupted by recovery')
            if not self._collective_panel_visible():
                if previous is None:
                    return False
                self._ui_verify(lambda: not self._collective_panel_visible(), [], timeout=12)
                return True
            if not self.appear(self.I_CM_CLOSE):
                raise TaskRecoveryFailed('Collective close button not found')
            center = self.I_CM_CLOSE.front_center()
            if previous is not None and (abs(center[0] - previous[0]) > 4 or abs(center[1] - previous[1]) > 4):
                raise TaskRecoveryFailed('Collective close button position is not stable')
            previous = center
        x, y = center
        click = RuleClick(roi_front=(x, y, 1, 1), roi_back=tuple(self.I_CM_CLOSE.roi_front),
                          name='CM_PANEL_CLOSE')
        if self.device.click(x=x, y=y, control_name=click.name) is False:
            raise TaskRecoveryFailed('Collective close input blocked')
        self._ui_verify(lambda: not self._collective_panel_visible(), [click], timeout=12)
        return True

    def _dismiss_collective_reward(self):
        self.screenshot()
        if not self.appear(self.I_UI_REWARD):
            return False
        self.screenshot()
        if not self.appear(self.I_UI_REWARD):
            return False
        height, width = self.device.image.shape[:2]
        # Bottom-center blank space avoids the items and the old right edge area.
        x, y = width // 2, max(0, height - 22)
        click = RuleClick(roi_front=(x, y, 1, 1), roi_back=(x, y, 1, 1),
                          name='CM_REWARD_DISMISS')
        if self.device.click(x=x, y=y, control_name=click.name) is False:
            raise TaskRecoveryFailed('Collective reward dismiss input blocked')
        self._ui_verify(lambda: not self.appear(self.I_UI_REWARD), [click], timeout=12)
        return True

    def _collective_panel_visible(self):
        image = self.device.image
        if image is None or image.ndim != 3:
            return False
        height, width = image.shape[:2]
        roi = (width // 3, 0, width // 3, height // 6)
        title = RuleOcr(roi=roi, area=roi, mode='Full', method='Default', keyword='',
                        name='cm_completion_context')
        texts = ''.join(re.sub(r'\s+', '', item.ocr_text) for item in
                        title.detect_and_ocr(image, logDisplay=False))
        return '集体任务' in texts

    def select_and_update_cur_mission(self, mission: MC) -> bool:
        """尝试选择对应任务并更新当前任务, 若选择失败(无法切换)则会停留在当前任务
        :return: 成功选择返回True
        """
        pre_mission = ''
        switch_fail_cnt, max_retry = 0, random.randint(2, 3)  # 点了没反应, 可能之前已经做了其他任务导致无法切换
        switch_cnt, max_switch = 0, random.randint(12, 15)  # 尝试最多15次内能中奖找到对应任务
        while True:
            if switch_fail_cnt >= max_retry:
                logger.warning(f'Cannot switch next mission, stop select and try run')
                return False
            if switch_cnt >= max_switch:
                logger.warning(f'Cannot find target mission: {mission.value}, exit')
                return False
            self.screenshot()
            # 识别当前任务
            mission_text = self.O_CM_2.ocr(self.device.image)
            try:
                detect_mission = MC(mission_text)
                logger.info(f"Current: {detect_mission.value}, target: {mission.value}")
                self.current_mission = detect_mission
                if detect_mission == mission:
                    logger.info(f"Success select mission[{mission_text}]")
                    return True
            except ValueError as e:
                logger.warning(f'Unknown {mission_text}, skip')
            logger.info("Try switch to next mission")
            switch_fail_cnt = 0 if pre_mission != mission_text else (switch_fail_cnt + 1)
            pre_mission = mission_text
            if self.appear_then_click(self.I_CM_SWITCH, interval=0.6):
                # Observe the new title after one input. A locked/disabled
                # refresh cannot be repaired by replaying the same click.
                if not self._ui_verify(lambda: self.O_CM_2.ocr(self.device.image) != mission_text,
                                       [self.I_CM_SWITCH], timeout=3, fail=False):
                    logger.info('Mission refresh is unavailable; keep the verified current mission')
                    return False
                switch_cnt += 1
                self.device.click_record_clear()

    def _donate(self):
        """捐材料"""
        self.ui_click(self.C_CM_1, self.I_CM_PRESENT, interval=1.5)
        logger.info('Start to donate')
        # 判断哪一个的材料最多
        self.screenshot()
        max_index = 0
        max_number = 0
        for i, ocr in enumerate([self.O_CM_1_MATTER, self.O_CM_2_MATTER,
                                 self.O_CM_3_MATTER, self.O_CM_4_MATTER]):
            curr, remain, total = ocr.ocr(self.device.image)
            if total > max_number:
                max_number = total
                max_index = i
        if max_number <= 30:
            logger.info('The number of all matter is less than 30')
            logger.info('Please check your game resolution')
            raise RequestHumanTakeover
        match_swipe = {
            0: self.S_CM_MATTER_1,
            1: self.S_CM_MATTER_2,
            2: self.S_CM_MATTER_3,
            3: self.S_CM_MATTER_4,
        }
        # 滑动到最多的材料
        random_click = [self.I_CM_ADD_1, self.I_CM_ADD_2, self.I_CM_ADD_3, self.I_CM_ADD_4]
        window_control = self.config.script.device.control_method == 'window_message'
        swipe_count = 0
        click_count = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_CM_MATTER):
                break
            if not window_control and self.swipe(match_swipe[max_index], interval=2.5):
                swipe_count += 1
                time.sleep(1.5)
                continue
            # 为什么使用window_message无法滑动
            if window_control and click_count > 30:
                logger.info('Swipe to the most matter failed')
                logger.info('Please check your game resolution')
                break
            if window_control and self.click(random.choice(random_click), interval=0.7):
                click_count += 1
                continue
            if not window_control and swipe_count >= 5:
                logger.info('Swipe to the most matter failed')
                logger.info('Please check your game resolution')
                raise RequestHumanTakeover
        logger.info('Swipe to the most matter')
        self.get_reward_and_close(self.I_CM_PRESENT)
        logger.info('Donate finished')
        return True

    def _soul(self):
        """提交御魂"""
        self.ui_click(self.C_CM_1, self.I_SL_SUBMIT)
        while 1:
            self.screenshot()
            number_text = self.O_SL_NUMBER.ocr(self.device.image)
            submit_number = int(re.findall(r'\d+', number_text)[-1])
            if submit_number > 0:
                break
            if self.ocr_appear(self.O_SL_LEVEL):
                # 如果没有识别到这个，那就说明没有御魂可以提交了，要退出
                logger.warning('No soul can be submit')
                self.ui_click(self.I_UI_BACK_RED, self.I_CM_RECORDS)
                return False
            if self.click(self.L_SL_LONG, interval=2.5):
                time.sleep(1)
                continue
        logger.info('Start to collect soul rewards')
        self.get_reward_and_close(self.I_SL_SUBMIT)
        logger.info('Finish to collect soul rewards')
        return True

    def _feed(self):
        """提交N卡"""
        logger.info('Start to feed N')
        self.ui_click(self.C_CM_1, self.I_FEED_HEAP)
        logger.info('Submit to feed N')
        click_list = random.sample([self.L_FEED_CLICK_1, self.L_FEED_CLICK_2, self.L_FEED_CLICK_3, self.L_FEED_CLICK_4], 2)
        while 1:
            self.screenshot()
            if self.appear(self.I_FEED_SUBMIT):
                break
            for click in click_list:
                self.click(click)
        logger.info('Start to collect feed N rewards')
        self.get_reward_and_close(self.I_FEED_SUBMIT)
        logger.info('Finish to collect feed N rewards')
        return True

    def is_finish(self):
        """判断寮三十是否已经捐满"""
        self.screenshot()
        for _ in range(2):
            self.screenshot()
            if not self._collective_panel_visible():
                return False
            current, remain, total = self.O_CM_NUMBER.ocr(self.device.image)
            if current != 30 or total != 30:
                break
        else:
            logger.info('Today\'s missions have been completed in two fresh frames')
            return True
        # The configurable donation card has its own daily cap. Other fixed
        # guild/team cards do not make this already completed card refreshable.
        card = RuleOcr(roi=(150, 405, 320, 60), area=(150, 405, 320, 60),
                       mode='Full', method='Default', keyword='', name='cm_submission_daily_count')
        for _ in range(2):
            self.screenshot()
            if not self._collective_panel_visible():
                return False
            texts = ''.join(re.sub(r'\s+', '', r.ocr_text) for r in
                            card.detect_and_ocr(self.device.image, logDisplay=False))
            match = re.search(r'今日已完成(\d+)次', texts)
            if not match or int(match.group(1)) not in (10, 30):
                return False
        logger.info('Configurable collective mission card has completed its daily submissions')
        return True

    def get_task_reward(self):
        """获取其他已完成的任务奖励"""
        timeout_timer = Timer(3).start()
        process_reward = False
        while not timeout_timer.reached():
            self.maybe_screenshot()
            if not self.appear(self.I_CM_GET_REWARD):
                break
            process_reward = True
            logger.info('Discover the tasks that have been completed')
            self.get_reward_and_close(self.I_CM_GET_REWARD)
            timeout_timer.reset()
        if process_reward:
            logger.info('Get task reward finished')
        else:
            logger.info('No task reward')

    def get_reward_and_close(self, target: RuleImage):
        # One submit/claim input; at most two separately confirmed reward panels.
        self.screenshot()
        if not self.appear(self.I_UI_REWARD):
            if not self.appear_then_click(target, interval=1):
                raise TaskRecoveryFailed('Collective submit or reward claim unavailable')
            self._ui_verify(lambda: self.appear(self.I_UI_REWARD), [target], timeout=12)
        for _ in range(2):
            if not self._dismiss_collective_reward():
                return
        self.screenshot()
        if self.appear(self.I_UI_REWARD):
            raise TaskRecoveryFailed('Unexpected extra collective reward panel')



if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

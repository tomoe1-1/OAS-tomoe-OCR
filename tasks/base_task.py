# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from time import sleep, time, monotonic
from math import isfinite

import random
from datetime import datetime, time as clock_time, timedelta
from module.atom.animate import RuleAnimate
from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.list import RuleList
from module.atom.long_click import RuleLongClick
from module.atom.ocr import RuleOcr
from module.atom.swipe import RuleSwipe
from module.base.timer import Timer
from module.config.config import Config
from module.config.utils import convert_to_underscore
from module.device.device import Device
from module.device.escape_popup import game_exit_dialog_present, game_exit_cancel_point
from module.device.native_exit_cancel import cancel_game_exit_dialog
from module.device.confirm_retry import click_confirm_once
from module.device.confirmation_checkbox import ensure_confirmation_option
from module.exception import ScriptError, TaskRecoveryFailed, TaskEnd
from module.image.rpc import get_image_client
from module.logger import logger
from module.ocr.base_ocr import OcrMode
from tasks.Component.Costume.costume_base import CostumeBase
from tasks.Component.claim_gift import claim_gift_popup
from tasks.Component.escape_recovery import handle_escape_recovery
from tasks.Component.config_base import Time
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.GlobalGame.config_emergency import FriendInvitation
from typing import Union


class BaseTask(GlobalGameAssets, CostumeBase):
    config: Config = None
    device: Device = None

    folder: str
    name: str
    stage: str

    limit_time: timedelta = None  # 限制运行的时间，是软时间，不是硬时间
    limit_count: int = None  # 限制运行的次数
    current_count: int = None  # 当前运行的次数

    def __init__(self, config: Config, device: Device) -> None:
        """

        :rtype: object
        """
        self.config = config
        self.device = device
        self.device._global_invitation_enabled = True

        self.interval_timer = {}  # 这个是用来记录每个匹配的运行间隔的，用于控制运行频率
        self.animates = {}  # 保存缓存
        self.start_time = datetime.now()  # 启动的时间
        self.check_costume(self.config.global_game.costume_config)
        # self.friend_timer = None  # 这个是用来记录勾协的时间的
        # if self.config.global_game.emergency.invitation_detect_interval:
        #     self.interval_time = self.config.global_game.emergency.invitation_detect_interval
        #     self.friend_timer = Timer(self.interval_time)
        #     self.friend_timer.start()

        # 战斗次数相关
        self.current_count = 0  # 战斗次数

    def _burst(self) -> bool:
        """
        游戏界面突发异常检测
        :return: 没有出现返回False, 其他True
        """
        image = self.device.image
        appear_invitation = self.appear(self.I_G_ACCEPT)
        if not appear_invitation:
            return False
        identity = self._invitation_identity()
        logger.info('Invitation appearing')
        invite_type = self.config.global_game.emergency.friend_invitation
        detect_record = set(self.device.detect_record)
        match invite_type:
            case FriendInvitation.ACCEPT:
                logger.info(f"Accept friend invitation")
                click_button = self.I_G_ACCEPT
            case FriendInvitation.REJECT:
                logger.info(f"Reject friend invitation")
                click_button = self.I_G_REJECT
            case FriendInvitation.ONLY_JADE:
                # 勾协
                logger.info(f"Only accept jade invitation")
                if self.appear(self.I_G_JADE):
                    click_button = self.I_G_ACCEPT
                else:
                    click_button = self.I_G_IGNORE
            case FriendInvitation.JADE_AND_FOOD:
                # 如果是接受勾协和粮协
                logger.info(f"Accept jade and food invitation")
                if self.appear(self.I_G_JADE) or self.appear(self.I_G_CAT_FOOD) or self.appear(self.I_G_DOG_FOOD):
                    click_button = self.I_G_ACCEPT
                else:
                    click_button = self.I_G_IGNORE
            case FriendInvitation.IGNORE:
                # 如果是忽略
                logger.info(f"Ignore friend invitation")
                click_button = self.I_G_IGNORE
            case _:
                raise ScriptError(f'Unknown friend invitation type: {invite_type}')
        if not click_button:
            raise ScriptError(f'Unknown click button type: {invite_type}')
        deadline = monotonic() + self.device.performance.timeout(12)
        generation = self.device._escape_generation
        clicked, stable = False, 0
        try:
            # Direct capture avoids recursively handling this invitation through screenshot().
            while monotonic() < deadline:
                self.device.screenshot()
                if self.device._escape_generation != generation:
                    raise TaskRecoveryFailed('Invitation verification interrupted by recovery')
                vanished = not self.appear(self.I_G_ACCEPT) and not self.appear(click_button)
                # A queued invitation can replace this card without hiding Accept.
                # Two fresh frames must show a changed sender banner before continuing.
                replaced = clicked and identity is not None and self._invitation_identity_changed(identity)
                vanished = vanished or replaced
                stable = stable + 1 if vanished else 0
                if stable >= 2:
                    if clicked:
                        self.device.confirm_control_progress([click_button])
                    logger.info('Deal with invitation done')
                    break
                if not clicked and not vanished:
                    if self.appear(click_button):
                        if not self.appear_then_click(click_button):
                            raise TaskRecoveryFailed('Invitation input was blocked')
                        clicked = True
            else:
                raise TaskRecoveryFailed('Invitation did not close after one click')
        finally:
            # Preserve the caller's battle guard even when invitation verification fails.
            self.device.detect_record = detect_record
        # 如果接受邀请则立即执行悬赏任务
        if clicked and click_button == self.I_G_ACCEPT:
            self.set_next_run(task='WantedQuests', target=datetime.now().replace(microsecond=0), server=False)
            self.device._accepted_invitation_generation = getattr(self.device, '_accepted_invitation_generation', 0) + 1
        return True

    def _invitation_identity(self):
        image = self.device.image
        if image is None or not hasattr(image, 'shape') or image.shape[:2] != (720, 1280):
            return None
        # Exclude the animated avatar, monster illustration and close-button sparkle.
        return image[240:276, 578:772].copy()

    def _invitation_identity_changed(self, previous):
        import numpy as np
        current = self._invitation_identity()
        if current is None or current.shape != previous.shape:
            return False
        difference = np.max(np.abs(current.astype(np.int16) - previous.astype(np.int16)), axis=2)
        return float(np.mean(difference > 35)) > 0.12

    def screenshot(self):
        """
        截图 引入中间函数的目的是 为了解决如协作的这类突发的事件
        :return:
        """
        # nemu_ipc 返回为RGB
        # 其他方式未知
        self.device.screenshot()
        cancel_point = game_exit_dialog_present(self.device.image)
        if cancel_point:
            cancel_game_exit_dialog(self.device)
        handle_escape_recovery(self)
        # Device.screenshot handles invitations, including direct device callers.
        # Claim the modal before navigation, OCR or battle actions use this frame.
        claim_gift_popup(self)

        # # 判断网络异常
        # if self.appear(self.I_NETWORK_ABNORMAL):
        #     logger.warning(f"Network abnormal")
        #     raise GameStuckError
        #
        # # 判断网络错误
        # if self.appear(self.I_NETWORK_ERROR):
        #     logger.warning(f"Network error")
        #     raise GameStuckError

        return self.device.image

    def maybe_screenshot(self, soft_skip: bool = False):
        """
        可能截图
        :param soft_skip: True跳过截图(但保证设备一定有图才跳过,否则依然截图)
        :return:
        """
        if not soft_skip or not self.exist_image():
            return self.screenshot()
        return self.device.image

    def exist_image(self) -> bool:
        """
        判断当前设备是否有图片
        :return: 有返回True，没有返回False
        """
        return hasattr(self.device, 'image') and self.device.image is not None

    def prepare_appear_cache(self, targets: list[RuleImage]) -> None:
        """在当前截图帧上批量预取一组 `RuleImage` 的匹配结果。"""
        if not self.exist_image():
            return
        frame_id = self.device.image_frame_id
        if frame_id is None:
            return

        unique_targets = []
        seen = set()
        for target in targets:
            if not isinstance(target, RuleImage):
                continue
            cache_key = id(target)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            if self.device.get_image_batch_cache(target, frame_id=frame_id) is not None:
                continue
            unique_targets.append(target)

        if not unique_targets:
            return

        results = get_image_client().match_many(
            rules_data=[target.to_service_payload() for target in unique_targets],
            image=self.device.image,
            frame_id=frame_id,
        )
        self.device.update_image_batch_cache(unique_targets, results, frame_id=frame_id)

    def appear(self,
               target: RuleImage | RuleGif | RuleOcr,
               interval: float = None,
               threshold: float = None):
        """

        :param target: 匹配的目标可以是RuleImage, 也可以是RuleOcr
        :param interval:
        :param threshold:
        :return: interval时间到达且匹配成功则返回True, 否则False
        """
        timer_key = target.name
        if interval:
            if timer_key in self.interval_timer:
                if self.interval_timer[timer_key].limit != interval:
                    self.interval_timer[timer_key] = Timer(interval)
            else:
                self.interval_timer[timer_key] = Timer(interval)
            if not self.interval_timer[timer_key].reached():
                return False
        if isinstance(target, RuleOcr):
            appear = self.ocr_appear(target, interval)
        elif isinstance(target, RuleImage):
            if threshold is None:
                cached_result = self.device.get_image_batch_cache(target, frame_id=self.device.image_frame_id)
                if cached_result is not None:
                    appear = target._apply_match_result(cached_result)
                else:
                    appear = target.match(self.device.image, threshold=threshold, frame_id=self.device.image_frame_id)
            else:
                appear = target.match(self.device.image, threshold=threshold, frame_id=self.device.image_frame_id)
        else:
            appear = target.match(self.device.image, threshold=threshold, frame_id=self.device.image_frame_id)

        if not appear and target is self.I_UI_REWARD:
            # Preserve the canonical rule/timer for all existing callers while
            # accepting the updated popup title at its stricter threshold.
            appear = self.I_UI_REWARD_NEW.match(self.device.image,
                                                frame_id=self.device.image_frame_id)
            if appear:
                target.roi_front = list(self.I_UI_REWARD_NEW.roi_front)

        if not appear and target is getattr(self, 'I_EXIT_ENSURE', None):
            # The updated button is valid only inside the battle-exit dialog.
            appear = (self.I_EXIT_DIALOG_NEW.match(self.device.image,
                                                  frame_id=self.device.image_frame_id)
                      and self.I_EXIT_ENSURE_NEW.match(self.device.image,
                                                     frame_id=self.device.image_frame_id))
            if appear:
                target.roi_front = list(self.I_EXIT_ENSURE_NEW.roi_front)

        if (not appear and target is getattr(self, 'I_FRESH_ENSURE', None)
                and getattr(self, 'I_RAID_REFRESH_Q', None) is not None):
            # RealmRaid refresh must match its own question, not a gold button
            # shared with battle-exit or re-challenge confirmation dialogs.
            appear = (self.I_RAID_REFRESH_Q.match(self.device.image,
                                                 frame_id=self.device.image_frame_id)
                      and self.I_RAID_CONFIRM_ENSURE.match(self.device.image,
                                                         frame_id=self.device.image_frame_id))
            if appear:
                x, y, w, h = self.I_RAID_CONFIRM_ENSURE.roi_front
                target.roi_front = [x + 20, y + 15, w - 40, h - 30]

        if (
            not appear
            and isinstance(target, RuleImage)
            and target is getattr(self, 'I_CHECK_MAIN', None)
        ):
            appear = self.detect_random_main_costume(threshold=threshold)

        if appear and interval:
            self.interval_timer[timer_key].reset()

        return appear

    def appear_then_click(self,
                          target: RuleImage | RuleGif | RuleOcr,
                          action: Union[RuleClick, RuleLongClick] = None,
                          interval: float = None,
                          threshold: float = None,
                          duration: float = None):
        """
        出现了就点击，默认点击图片的位置，如果添加了click参数，就点击click的位置
        :param duration: 如果是长按，可以手动指定duration，不指定默认.单位是ms！！！！
        :param action: 可以是RuleClick, 也可以是RuleLongClick
        :param target: 可以是RuleImage后续支持RuleOcr
        :param interval:
        :param threshold:
        :return: True or False
        """
        appear = self.appear(target, interval=interval, threshold=threshold)
        if appear and not action:
            x, y = target.coord()
            if self.device.click(x, y, control_name=target.name) is False:
                return False

        elif appear and action:
            x, y = action.coord()
            if isinstance(action, RuleLongClick):
                if duration is None:
                    if self.device.long_click(x, y, duration=action.duration / 1000, control_name=target.name) is False:
                        return False
                else:
                    if self.device.long_click(x, y, duration=duration / 1000, control_name=target.name) is False:
                        return False
            elif isinstance(action, RuleClick):
                if self.device.click(x, y, control_name=target.name) is False:
                    return False

        return appear

    def _default_wait_timeout(self) -> float:
        """Bound implicit waits without shortening a configured long battle or gathering."""
        global_battle = getattr(getattr(self.config, 'global_game', None), 'battle', None)
        context = getattr(self, '_battle_context', None)
        candidates = (420, getattr(global_battle, 'battle_timeout', None),
                      getattr(getattr(context, 'battle_timer', None), 'limit', None))
        limits = []
        for value in candidates:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if isfinite(value) and value > 0:
                limits.append(value)
        return max(limits)

    def wait_until_appear(self,
                          target: RuleImage | RuleOcr,
                          skip_first_screenshot=False,
                          wait_time: int | float = None) -> bool:
        """
        等待直到出现目标
        :param wait_time: 等待时间，单位秒
        :param target:
        :param skip_first_screenshot:
        :return:
        """
        implicit = wait_time is None
        budget = self._default_wait_timeout() if implicit else wait_time
        wait_timer = Timer(self.device.performance.timeout(budget)).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.screenshot()
            if isinstance(target, RuleImage) and self.appear(target):
                return True
            if isinstance(target, RuleOcr) and self.ocr_appear(target):
                return True
            if wait_timer.reached():
                logger.warning(f"Wait until appear {target.name} timeout")
                if implicit:
                    raise TaskRecoveryFailed(f'Expected {target.name} did not appear within {budget}s')
                return False

    def wait_until_appear_then_click(self,
                                     target: RuleImage,
                                     action: Union[RuleClick, RuleLongClick] = None,
                                     wait_time: int = None) -> bool:
        """
        等待直到出现目标，然后点击
        :param wait_time:
        :param action:
        :param target:
        :return:
        """
        if not self.wait_until_appear(target, wait_time=wait_time):
            return False
        click_x, click_y = target.coord()
        if action is None:
            if self.device.click(click_x, click_y, control_name=target.name) is False:
                return False
        elif isinstance(action, RuleLongClick):
            if self.device.long_click(click_x, click_y, duration=action.duration / 1000, control_name=target.name) is False:
                return False
        elif isinstance(action, RuleClick):
            if self.device.click(click_x, click_y, control_name=target.name) is False:
                return False
        return True

    def wait_until_disappear(self, target: RuleImage, timeout: float = None) -> bool:
        """
        等待目标从屏幕上消失
        :param target: 目标元素
        :param timeout: 显式超时返回False；默认使用有界等待，到期保留任务并触发恢复
        """
        implicit = timeout is None
        budget = self._default_wait_timeout() if implicit else timeout
        timeout_timer = Timer(self.device.performance.timeout(budget)).start()
        while 1:
            self.screenshot()
            if not self.appear(target):
                return True
            if timeout_timer.reached():
                logger.warning(f'{target.name} still visible after {budget}s')
                if implicit:
                    raise TaskRecoveryFailed(f'{target.name} did not disappear within {budget}s')
                return False

    def wait_until_pos_stable(self, target: RuleImage, stable_time: float = 0.3, timeout: float = 2,
                              threshold: float = None, skip_first_screenshot: bool = True) -> bool:
        """
        等待直到在同一位置稳定出现
        :param skip_first_screenshot:
        :param threshold: target匹配阈值
        :param target: 目标图像
        :param stable_time: 判断是否稳定的时间
        :param timeout: 等待稳定的超时时间
        :return: timer时间内稳定出现则返回True, 否则False
        """
        logger.info(f'Wait until {target.name} position stable')
        timeout_timer = Timer(self.device.performance.timeout(timeout)).start()
        stable_timer = Timer(stable_time).start()
        pre_roi_front, cur_roi_front = None, None
        origin_roi_back = target.roi_back
        while not timeout_timer.reached():
            self.maybe_screenshot(skip_first_screenshot)
            skip_first_screenshot = False
            # 当前页面能够匹配到target
            if target.match(self.device.image, threshold=threshold, frame_id=self.device.image_frame_id):
                cur_roi_front = target.roi_front
                logger.info(f'Current:{cur_roi_front}, pre:{pre_roi_front}')
                target.roi_back = pre_roi_front
                # 上一次匹配到的位置还能匹配到target
                if pre_roi_front is not None and target.match(self.device.image, threshold=threshold,
                                                              frame_id=self.device.image_frame_id):
                    # 到达稳定时间
                    if stable_timer.reached():
                        logger.info(f'{target.name} position has stabilized')
                        target.roi_back = origin_roi_back
                        return True
                else:
                    stable_timer.reset()  # 上一次匹配到的位置这次匹配不到了, 重置定时器
            else:
                stable_timer.reset()  # 当前页面都匹配不到, 重置定时器
            # 记录这一次的target位置
            pre_roi_front = cur_roi_front
            # 还原target的匹配区域
            target.roi_back = origin_roi_back
        logger.warning(f'Wait until pos stable({target}) timeout')
        return False

    def wait_until_stable(self,
                          target: RuleImage,
                          timer=Timer(0.3, count=1),
                          timeout=Timer(5, count=10),
                          skip_first_screenshot=True):
        """
        等待目标稳定，即连续多次匹配成功
        :param target:
        :param timer:
        :param timeout:
        :param skip_first_screenshot:
        :return:
        """
        target._match_init = False
        # Use local timers so repeated low-spec calls cannot compound budgets.
        timer = Timer(timer.limit, count=timer.count)
        timeout = Timer(self.device.performance.timeout(timeout.limit), count=timeout.count)
        timeout.reset()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.screenshot()

            if target._match_init:
                if target.match(self.device.image, frame_id=self.device.image_frame_id):
                    if timer.reached():
                        break
                else:
                    # button.load_color(self.device.image)
                    timer.reset()
            else:
                # target.load_color(self.device.image)
                target._match_init = True

            if timeout.reached():
                logger.warning(f'Wait_until_stable({target}) timeout')
                break

    def wait_animate_stable(self, rule: RuleAnimate, interval: float = None, timeout: float = None):
        """
        不同与上面的wait_until_stable，这个将会匹配连续的两帧图片的特定区域
        @param rule:
        @param interval:
        @param timeout:
        @return:
        """
        if not isinstance(rule, RuleAnimate):
            rule = RuleAnimate(rule)
        implicit = timeout is None
        budget = self._default_wait_timeout() if implicit else timeout
        timeout_timer = Timer(self.device.performance.timeout(budget)).start()
        while 1:
            self.screenshot()

            if interval:
                if rule.name in self.interval_timer:
                    if self.interval_timer[rule.name].limit != interval:
                        self.interval_timer[rule.name] = Timer(interval)
                else:
                    self.interval_timer[rule.name] = Timer(interval)
                if not self.interval_timer[rule.name].reached():
                    return False

            stable = rule.stable(self.device.image, frame_id=self.device.image_frame_id)
            if stable:
                if interval:
                    self.interval_timer[rule.name].reset()
                return True

            if timeout_timer.reached():
                logger.info(f'Wait_animate_stable({rule}) timeout')
                if implicit:
                    raise TaskRecoveryFailed(f'Animation {rule.name} did not stabilize within {budget}s')
                return False

    def swipe(self, swipe: RuleSwipe, interval: float = None) -> bool:
        """

        :param interval:
        :param swipe:
        :return:
        """
        if not isinstance(swipe, RuleSwipe):
            return False

        if interval:
            if swipe.name in self.interval_timer:
                # 如果传入的限制时间不一样，则替换限制新的传入的时间
                if self.interval_timer[swipe.name].limit != interval:
                    self.interval_timer[swipe.name] = Timer(interval)
            else:
                # 如果没有限制时间，则创建限制时间
                self.interval_timer[swipe.name] = Timer(interval)
            # 如果时间还没到达，则不执行
            if not self.interval_timer[swipe.name].reached():
                return False

        x1, y1, x2, y2 = swipe.coord()
        if self.device.swipe(p1=(x1, y1), p2=(x2, y2), control_name=swipe.name) is False:
            return False

        # 执行后，如果有限制时间，则重置限制时间
        if interval:
            # logger.info(f'Swipe {swipe.name}')
            self.interval_timer[swipe.name].reset()
        return True

    def click(self, click: Union[RuleClick, RuleLongClick, RuleImage, RuleOcr] = None, interval: float = None) -> bool:
        """
        点击或者长按
        :param interval:
        :param click:
        :return: 返回值不是click是否成功，而是interval是否设置以及是否到时间
        """
        if not click:
            return False

        if interval:
            if click.name in self.interval_timer:
                # 如果传入的限制时间不一样，则替换限制新的传入的时间
                if self.interval_timer[click.name].limit != interval:
                    self.interval_timer[click.name] = Timer(interval)
            else:
                # 如果没有限制时间，则创建限制时间
                self.interval_timer[click.name] = Timer(interval)
            # 如果时间还没到达，则不执行
            if not self.interval_timer[click.name].reached():
                return False

        # One physical input; the device captures fresh result frames before returning.
        x, y = click.coord()
        if not isinstance(click, RuleLongClick):
            ensure_confirmation_option(self, (x, y), click.name)
        if isinstance(click, RuleLongClick):
            if self.device.long_click(x=x, y=y, duration=click.duration / 1000, control_name=click.name) is False:
                return False
        elif isinstance(click, (RuleClick, RuleImage, RuleOcr)):
            if self.device.click(x=x, y=y, control_name=click.name) is False:
                return False

        # 执行后，如果有限制时间，则重置限制时间
        if interval:
            self.interval_timer[click.name].reset()
            return True
        return False

    def ocr_appear(self, target: RuleOcr, interval: float = None) -> bool:
        """
        ocr识别目标
        :param interval:
        :param target:
        :return: 如果target有keyword或者是keyword存在，返回是True，否则返回False
                 但是没有指定keyword，返回的是匹配到的值，具体取决于target的mode
        """
        if not isinstance(target, RuleOcr):
            return None

        if interval:
            if target.name in self.interval_timer:
                # 如果传入的限制时间不一样，则替换限制新的传入的时间
                if self.interval_timer[target.name].limit != interval:
                    self.interval_timer[target.name] = Timer(interval)
            else:
                # 如果没有限制时间，则创建限制时间
                self.interval_timer[target.name] = Timer(interval)
            # 如果时间还没到达，则不执行
            if not self.interval_timer[target.name].reached():
                return None

        result = target.ocr(self.device.image)
        appear = False

        if not target.keyword or target.keyword == '':
            appear = False
        match target.mode:
            case OcrMode.FULL:  # 全匹配
                appear = result != (0, 0, 0, 0)
            case OcrMode.SINGLE:
                appear = result == target.keyword
            case OcrMode.DIGIT:
                appear = result == int(target.keyword)
            case OcrMode.DIGITCOUNTER:
                appear = result == target.ocr_str_digit_counter(target.keyword)
            case OcrMode.DURATION:
                appear = result == target.parse_time(target.keyword)

        if interval:
            self.interval_timer[target.name].reset()

        return appear

    def ocr_appear_click(self,
                         target: RuleOcr,
                         action: Union[RuleClick, RuleLongClick] = None,
                         interval: float = None,
                         duration: float = None) -> bool:
        """
        ocr识别目标，如果目标存在，则触发动作
        :param target:
        :param action:
        :param interval:
        :param duration:
        :return:
        """
        appear = self.ocr_appear(target, interval)

        if not appear:
            return False

        if action:
            x, y = action.coord()
            self.click(action, interval)
        else:
            x, y = target.coord()
            if self.device.click(x=x, y=y, control_name=target.name) is False:
                return False
        return True

    def list_find(self, target: RuleList, name: str | list[str], max_swipe: int = 10) -> bool | tuple:
        """
        会一致在列表寻找目标，找到了就退出。
        如果是图片列表会一直往下找
        如果是纯文字的，会自动识别自己的位置，根据位置选择向前还是向后翻
        :param max_swipe: 最大滑动次数
        :param target:
        :param name:
        :return:
        """
        swipe_down = False
        swipe_distance_ratio = None
        result = None
        if not target:
            return False
        appear = False
        for _ in range(max_swipe):
            self.screenshot()
            if target.is_image:
                result = target.image_appear(self.device.image, name=name, frame_id=self.device.image_frame_id)
                swipe_down = True
            elif target.is_ocr:
                result = target.ocr_appear(self.device.image, name=name)
                swipe_down = result is not None and isinstance(result, int) and result > 0
                swipe_distance_ratio = 1
            # 结果是坐标证明找到了, 非坐标都是没找到
            if result is not None and isinstance(result, tuple):
                appear = True
                break
            if swipe_distance_ratio:
                x1, y1, x2, y2 = target.swipe_pos(number=swipe_distance_ratio, after=swipe_down)
            else:
                x1, y1, x2, y2 = target.swipe_pos(after=swipe_down)
            self.device.swipe(p1=(x1, y1), p2=(x2, y2))
            sleep(random.uniform(0.8, 1.3))  # 等待滑动完成, 待优化
        if appear:
            return result
        return False

    def list_appear_click(self, target: RuleList, interval: float = None, max_swipe: int = 10) -> bool:
        if interval:
            if target.name in self.interval_timer:
                # 如果传入的限制时间不一样，则替换限制新的传入的时间
                if self.interval_timer[target.name].limit != interval:
                    self.interval_timer[target.name] = Timer(interval)
            else:
                # 如果没有限制时间，则创建限制时间
                self.interval_timer[target.name] = Timer(interval)
            # 如果时间还没到达，则不执行
            if not self.interval_timer[target.name].reached():
                return False
        appear = self.list_find(target, name=target.array[0], max_swipe=max_swipe)
        if isinstance(appear, tuple) and interval:
            x, y = appear
            self.device.click(x, y)
            self.interval_timer[target.name].reset()
            return True
        return False

    def finish_completed_task(self, task, reason, *, verify=None, schedule=None):
        """End verified whole-task completion before optional return navigation."""
        if verify is not None:
            self._ui_verify(verify, [])
        if schedule is None:
            self.set_next_run(task=task, success=True)
        else:
            schedule()
        outcomes = getattr(self.config, '_learning_task_outcomes', None)
        if outcomes is not None:
            outcomes[convert_to_underscore(task)] = True
        logger.info(f'{task} completed: {reason}')
        # The scheduler clears failure/recovery records and returns to main.
        raise TaskEnd(task)

    def set_next_run(self, task: str, finish: bool = False,
                     success: bool = None, server: bool = True, target: datetime = None) -> None:
        """
        设置下次运行时间  当然这个也是可以重写的
        :param target: 可以自定义的下次运行时间
        :param server: True
        :param success: 判断是成功的还是失败的时间间隔
        :param task: 任务名称，大驼峰的
        :param finish: 是完成任务后的时间为基准还是开始任务的时间为基准
        :return:
        """
        if finish:
            start_time = datetime.now().replace(microsecond=0)
        else:
            start_time = self.start_time
        self.config.task_delay(task, start_time=start_time, success=success, server=server, target=target)

    def set_next_run_next_monday(self, task: str, scheduler) -> None:
        """周目标达成后改到下周一，保留显式配置的运行时刻。"""
        finished_at = datetime.now().replace(microsecond=0)
        configured_time = scheduler.server_update
        run_time = (
            finished_at.time()
            if configured_time == clock_time(hour=9)
            else configured_time
        )
        next_monday = finished_at.date() + timedelta(
            days=7 - finished_at.weekday()
        )
        target = datetime.combine(next_monday, run_time)
        self.set_next_run(task=task, server=False, target=target)

    def custom_next_run(self, task: str, custom_time: Time = None, time_delta: float = 1) -> None:
        """
        设置下次自定义运行时间
        :param task: 任务名称，大驼峰的
        :param custom_time: 可以自定义的下次运行时间
        :param time_delta: 下次运行日期为几天后，默认为第二天
        :return:
        """
        target_time = (datetime.now() + timedelta(days=time_delta)).replace(hour=custom_time.hour,
                                                                            minute=custom_time.minute,
                                                                            second=custom_time.second)
        self.set_next_run(task, target=target_time)

    #  ---------------------------------------------------------------------------------------------------------------
    #
    #  ---------------------------------------------------------------------------------------------------------------
    def ui_reward_appear_click(self, screenshot=False) -> bool:
        """
        如果出现 ‘获得奖励’ 就点击
        :return:
        """
        if screenshot:
            self.screenshot()
        if not self.appear(self.I_UI_REWARD, interval=0.4, threshold=0.6):
            return False
        return self._dismiss_reward_once()

    def _dismiss_reward_once(self):
        # This is the already displayed reward, never the source claim button.
        return click_confirm_once(
            self, lambda: self.C_UI_REWARD if self.appear(self.I_UI_REWARD, threshold=0.6) else None,
            [self.C_UI_REWARD, self.I_UI_REWARD], dismiss_anywhere=True)

    def ui_get_reward(self, click_image: RuleImage | RuleOcr | RuleClick, click_interval: float = 1):
        """
        传进来一个点击图片 或是 一个ocr， 会点击这个图片，然后等待‘获得奖励’，
        最后当获得奖励消失后 退出
        :param click_interval:
        :param click_image:
        :return:
        """
        _timer = Timer(self.device.performance.timeout(10)).start()
        source_clicked = False
        generation = self.device._escape_generation
        while 1:
            self.screenshot()
            if self.device._escape_generation != generation:
                raise TaskRecoveryFailed('Reward claim interrupted by recovery')

            if self.appear(self.I_UI_REWARD, threshold=0.6):
                if not self._dismiss_reward_once():
                    raise TaskRecoveryFailed('Reward dismiss input was blocked')
                self._ui_verify(lambda: not self.appear(self.I_UI_REWARD, threshold=0.6),
                                [self.I_UI_REWARD])
                logger.info('Get reward success')
                return True
            if _timer.reached():
                logger.warning('Get reward timeout')
                if source_clicked:
                    raise TaskRecoveryFailed('Reward was not observed after one claim input')
                return False

            # A delayed reward is observed after one source input, never by re-clicking it.
            if not source_clicked:
                if isinstance(click_image, (RuleImage, RuleGif, RuleOcr)) and not self.appear(click_image):
                    continue
                if not self._ui_action_once(click_image, interval=None):
                    raise TaskRecoveryFailed('Reward claim input was blocked')
                source_clicked = True

    def _ui_action_once(self, control, interval=1):
        if isinstance(control, (RuleImage, RuleGif)):
            return self.appear_then_click(control, interval=interval)
        if isinstance(control, RuleOcr):
            return self.ocr_appear_click(control, interval=interval)
        return self.click(control, interval=interval)

    def _ui_verify(self, predicate, controls, timeout=12, fail=True):
        deadline = monotonic() + self.device.performance.timeout(timeout)
        generation = self.device._escape_generation
        stable = 0
        while monotonic() < deadline:
            self.screenshot()
            if self.device._escape_generation != generation:
                raise TaskRecoveryFailed('UI state verification interrupted by recovery')
            stable = stable + 1 if predicate() else 0
            if stable >= 2:
                self.device.confirm_control_progress(controls)
                logger.info('UI next state verified in two fresh frames')
                return True
        if fail:
            raise TaskRecoveryFailed('UI next state not reached after one click')
        return False

    def ui_click(self, click, stop, interval=1):
        self.screenshot()
        if self.appear(stop):
            return self._ui_verify(lambda: self.appear(stop), [], timeout=12)
        # Wait for the source to become actionable, then issue exactly one input.
        deadline = monotonic() + 12
        while monotonic() < deadline:
            if self._ui_action_once(click, interval):
                return self._ui_verify(lambda: self.appear(stop), [click])
            self.screenshot()
            if self.appear(stop):
                return self._ui_verify(lambda: self.appear(stop), [])
        raise TaskRecoveryFailed('UI source control was not actionable')

    def ui_clicks(self, clicks: list[RuleImage | RuleOcr | RuleClick], stop: RuleImage, interval=1):
        sent = set()
        deadline = monotonic() + 20
        while monotonic() < deadline:
            self.screenshot()
            if self.appear(stop):
                return self._ui_verify(lambda: self.appear(stop), clicks)
            for index, click in enumerate(clicks):
                if index not in sent and self._ui_action_once(click, interval):
                    sent.add(index)
                    # The shared device layer has captured and checked fresh frames.
                    break
        raise TaskRecoveryFailed('UI sequence did not reach its destination')

    def ui_click_until_disappear(self, click, interval: float = 1):
        # Explicit identity: generic "confirm" names also include purchases.
        if (click is self.I_UI_BACK_RED
                or click is getattr(self, 'I_SOU_SWITCH_SURE', None)):
            return click_confirm_once(self, lambda: click if self.appear(click) else None, [click])
        self.screenshot()
        if not self.appear(click):
            return False
        deadline = monotonic() + 12
        while monotonic() < deadline:
            if self.appear_then_click(click, interval=interval):
                self._ui_verify(lambda: not self.appear(click), [click])
                return True
            self.screenshot()
            if not self.appear(click):
                return False
        raise TaskRecoveryFailed('UI dismiss control was not actionable')

    def ui_click_until_appear_or_timeout(self, click, stop=None, interval: float = 1, timeout: float = 10) -> bool:
        deadline = monotonic() + self.device.performance.timeout(timeout)
        while monotonic() < deadline:
            self.screenshot()
            if stop is not None and self.appear(stop):
                return self._ui_verify(lambda: self.appear(stop), [], timeout=timeout, fail=False)
            if self._ui_action_once(click, interval):
                remaining = max(.1, deadline-monotonic())
                return self._ui_verify(lambda: stop is not None and self.appear(stop),
                                       [click], timeout=remaining, fail=False)
        return False

    def ui_click_until_smt_disappear(self, click, stop, interval: float = 1):
        self.screenshot()
        if not self.appear(stop):
            return
        deadline = monotonic() + 12
        while monotonic() < deadline:
            if self._ui_action_once(click, interval):
                return self._ui_verify(lambda: not self.appear(stop), [click])
            self.screenshot()
            if not self.appear(stop):
                return
        raise TaskRecoveryFailed('UI close control was not actionable')

    def push_notify(self, content='', title=None, level=3):
        logger.info(f'Push notify: {content}')

    def save_image(self, task_name=None, content=None, wait_time=2, image_type=False, push_flag=False, level=3):
        logger.info(f'Save image: {task_name}')

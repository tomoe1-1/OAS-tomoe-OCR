# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from datetime import datetime, timedelta, time as dt_time
import random

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.RyouToppa.assets import RyouToppaAssets
from tasks.RyouToppa.page_recognition import is_ryou_toppa_page, is_ryou_toppa_context
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.config_base import ConfigBase, Time
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_realm_raid, page_main, page_kekkai_toppa, page_shikigami_records
from tasks.RealmRaid.assets import RealmRaidAssets

from module.logger import logger
from module.exception import TaskEnd
from module.atom.image_grid import ImageGrid
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.base.utils import point2str
from module.exception import GamePageUnknownError, TaskRecoveryFailed


area_map = (
    {
        "fail_sign": (RyouToppaAssets.I_AREA_1_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_1_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_1,
        "finished_sign": (RyouToppaAssets.I_AREA_1_FINISHED, RyouToppaAssets.I_AREA_1_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_2_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_2_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_2,
        "finished_sign": (RyouToppaAssets.I_AREA_2_FINISHED, RyouToppaAssets.I_AREA_2_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_3_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_3_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_3,
        "finished_sign": (RyouToppaAssets.I_AREA_3_FINISHED, RyouToppaAssets.I_AREA_3_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_4_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_4_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_4,
        "finished_sign": (RyouToppaAssets.I_AREA_4_FINISHED, RyouToppaAssets.I_AREA_4_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_5_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_5_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_5,
        "finished_sign": (RyouToppaAssets.I_AREA_5_FINISHED, RyouToppaAssets.I_AREA_5_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_6_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_6_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_6,
        "finished_sign": (RyouToppaAssets.I_AREA_6_FINISHED, RyouToppaAssets.I_AREA_6_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_7_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_7_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_7,
        "finished_sign": (RyouToppaAssets.I_AREA_7_FINISHED, RyouToppaAssets.I_AREA_7_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_8_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_8_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_8,
        "finished_sign": (RyouToppaAssets.I_AREA_8_FINISHED, RyouToppaAssets.I_AREA_8_FINISHED_NEW)
    }
)

TOPPA_CLICK_FAILURE_LIMIT = 4
TOPPA_POPUP_WAIT_TIMEOUT = 3.0
# 点「挑战」到真正进入战斗的等待上限。
# 历史实测快则 1.4s、慢则 2.7s（见 10-05 日志多次 RES_FIRE -> GENERAL BATTLE START）。
# 原值 5.0 在结界突破高峰期不够：10-06 13:55:47 实测先判定「未进入战斗」，
# 50 毫秒后战斗才真正开始（GENERAL BATTLE START -> page_battle_prepare），
# 于是关闭浮窗与战斗启动撞在一起，脚本随后整个崩溃重启。
# 判定失败前另有一次 is_in_battle 复核（见 _skip_unavailable_area），
# 这里放宽到 12s 覆盖高峰期抖动，代价只是真失败时多等几秒。
TOPPA_BATTLE_WAIT_TIMEOUT = 12.0


def random_delay(min_value: float = 1.0, max_value: float = 2.0, decimal: int = 1):
    """
    生成一个指定范围内的随机小数
    """
    random_float_in_range = random.uniform(min_value, max_value)
    return round(random_float_in_range, decimal)


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, RyouToppaAssets):
    medal_grid: ImageGrid = None

    def _ensure_ryou_toppa_page(self):
        """每次点击挑战目标前重新识别；返回恢复后先导航回寮突破。"""
        self.screenshot()
        if is_ryou_toppa_page(self) or self._ryou_raid_complete():
            return
        current = self.get_current_page(skip_first_screenshot=True, fallback=True)
        logger.warning(f'RyouToppa page changed to {current}; navigating back')
        self.goto_page(page_kekkai_toppa)
        self.screenshot()
        if not (is_ryou_toppa_page(self) or self._ryou_raid_complete()):
            raise GamePageUnknownError('寮突破页面特征未确认，停止点击挑战区域')
        self.device.click_record_clear()

    def run(self):
        """
        执行
        :return:
        """
        ryou_config = self.config.ryou_toppa
        time_limit: Time = ryou_config.raid_config.limit_time
        time_delta = timedelta(hours=time_limit.hour, minutes=time_limit.minute, seconds=time_limit.second)
        self.medal_grid = ImageGrid([RealmRaidAssets.I_MEDAL_5, RealmRaidAssets.I_MEDAL_4, RealmRaidAssets.I_MEDAL_3,
                                     RealmRaidAssets.I_MEDAL_2, RealmRaidAssets.I_MEDAL_1, RealmRaidAssets.I_MEDAL_0])

        if ryou_config.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(ryou_config.switch_soul_config.switch_group_team)

        if ryou_config.switch_soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(ryou_config.switch_soul_config.group_name, ryou_config.switch_soul_config.team_name)

        self.goto_page(page_kekkai_toppa)
        ryou_toppa_start_flag = True
        ryou_toppa_success_penetration = False
        ryou_toppa_admin_flag = False
        # 点击突破
        while 1:
            self.screenshot()
            if self.appear_then_click(RealmRaidAssets.I_REALM_RAID, interval=1):
                continue
            if self.appear(self.I_REAL_RAID_REFRESH, threshold=0.8):
                if self.appear_then_click(self.I_RYOU_TOPPA, interval=1):
                    continue
            # 攻破阴阳寮，说明寮突已开，则退出
            elif self._ryou_raid_complete():
                self._ui_verify(self._ryou_raid_complete, [])
                ryou_toppa_start_flag = True
                ryou_toppa_success_penetration = True
                break
            # 出现选择寮突说明寮突未开
            elif self.appear(self.I_SELECT_RYOU_BUTTON, threshold=0.8):
                ryou_toppa_start_flag = False
                ryou_toppa_admin_flag = True
                break
            # 出现晴明说明寮突未开
            elif self.appear(self.I_NO_SELECT_RYOU, threshold=0.8):
                ryou_toppa_start_flag = False
                break
            # 出现寮奖励， 说明寮突已开
            elif self.appear(self.I_RYOU_REWARD, threshold=0.8) or self.appear(self.I_RYOU_REWARD_90, threshold=0.8):
                ryou_toppa_start_flag = True
                break

        logger.attr('ryou_toppa_start_flag', ryou_toppa_start_flag)
        logger.attr('ryou_toppa_success_penetration', ryou_toppa_success_penetration)
        # 寮突未开 并且有权限， 开开寮突，没有权限则标记失败
        if not ryou_toppa_start_flag:
            if ryou_config.raid_config.ryou_access and ryou_toppa_admin_flag:
                # 作为寮管理，开启今天的寮突
                logger.info("As the manager of the ryou, try to start ryou toppa.")
                self.start_ryou_toppa()
            else:
                logger.info("The ryou toppa is not open and you are a ryou member.")
                # A verified unopened guild raid is unavailable for this run.
                # Wait for the next scheduled check without blocking other tasks.
                self.set_next_run(task='RyouToppa', finish=True, server=True, success=True)
                raise TaskEnd

        # 100% 攻破, 第二天再执行
        if ryou_toppa_success_penetration:
            logger.info('RyouToppa is 100%')
            self.plan_tomorrow_ryoutoppa()
            raise TaskEnd
        if self.config.ryou_toppa.general_battle_config.lock_team_enable:
            logger.info("Lock team.")
            self.ui_click(self.I_TOPPA_UNLOCK_TEAM, self.I_TOPPA_LOCK_TEAM)
        else:
            logger.info("Unlock team.")
            self.ui_click(self.I_TOPPA_LOCK_TEAM, self.I_TOPPA_UNLOCK_TEAM)
        # --------------------------------------------------------------------------------------------------------------
        # 开始突破
        # --------------------------------------------------------------------------------------------------------------
        area_index = 0
        while 1:
            # 设置长任务标志,用来寻找寮突可进攻的目标
            self.device.stuck_record_add('PREPARE_BEFORE_BATTLE')
            self._ensure_ryou_toppa_page()
            if self._ryou_raid_complete():
                self._ui_verify(self._ryou_raid_complete, [])
                self.plan_tomorrow_ryoutoppa()
                raise TaskEnd
            if not self.has_ticket():
                logger.info('No attack chances left; finish this run and wait for the configured interval.')
                break
            if self.current_count >= ryou_config.raid_config.limit_count:
                logger.warning("We have attacked the limit count.")
                break
            if datetime.now() >= self.start_time + time_delta:
                logger.warning("We have attacked the limit time.")
                break
            # 进攻
            res = self.attack_area(area_index)
            # 如果战斗失败或区域不可用，则弹出当前区域索引，开始进攻下一个
            if not res:
                area_index += 1
                if area_index >= len(area_map):
                    self._finish_no_attackable_targets()
                continue

        self.goto_page(page_main)
        # Ticket exhaustion and configured limits are verified stopping
        # conditions. Real navigation/battle failures still propagate normally.
        self.set_next_run(task='RyouToppa', finish=True, server=True, success=True)
        raise TaskEnd

    def plan_tomorrow_ryoutoppa(self):
        # 安排下次寮突破，便于复用
        now = datetime.now()
        # 如果时间在00:00-5:00之间则设定时间为当天的自定义时间
        if now.time() < dt_time(5, 0):  # 不确定 time 的使用范围，重命名 datetime 中的 time
            self.custom_next_run(task='RyouToppa', custom_time=self.config.ryou_toppa.raid_config.next_ryoutoppa_time, time_delta=0)
        # 如果时间在05:00-23:59之间则设定时间为明天的自定义时间
        else:
            self.custom_next_run(task='RyouToppa', custom_time=self.config.ryou_toppa.raid_config.next_ryoutoppa_time, time_delta=1)
        # Preserve the explicit daily time while declaring the completed run.
        outcomes = getattr(self.config, '_learning_task_outcomes', None)
        if outcomes is not None:
            outcomes['ryou_toppa'] = True

    def start_ryou_toppa(self):
        """
        开启寮突破
        :return:
        """
        # 点击寮突
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_SELECT_RYOU_BUTTON, interval=1):
                break
        logger.info(f'Click {self.I_SELECT_RYOU_BUTTON.name}')

        # 选择第一个寮
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_GUILD_ORDERS_REWARDS, action=self.C_SELECT_FIRST_RYOU, interval=1):
                break
        logger.info(f'Click {self.C_SELECT_FIRST_RYOU.name}')

        # 点击开始突入
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_START_TOPPA_BUTTON, interval=1):
                continue
            # 出现寮奖励， 说明寮突已开
            if self.appear(self.I_RYOU_REWARD, threshold=0.8):
                break
        logger.info(f'Click {self.I_START_TOPPA_BUTTON.name}')

    def has_ticket(self) -> bool:
        """
        如果没有票了，那么就返回False
        :return:
        """
        # Always inspect the visible count, including the night period.
        # A verified 0/6 means there are no remaining defeats for this run.
        self.wait_until_appear(self.I_TOPPA_RECORD)
        self.screenshot()
        cu, res, total = self.O_NUMBER.ocr(self.device.image)
        if cu == 0 and total > 0 and cu + res == total:
            logger.info('RyouToppa remaining defeat count is zero; this run is complete.')
            return False
        return True

    def check_area(self, index: int) -> bool:
        """
        检查该区域是否攻略失败
        :return:
        """
        f1, f2 = area_map[index].get("fail_sign")
        self.screenshot()
        # 只有整个阴阳寮已攻破才结束本轮；一个已攻破的目标只换下一个。
        if self._ryou_raid_complete():
            self._ui_verify(self._ryou_raid_complete, [])
            self.plan_tomorrow_ryoutoppa()
            raise TaskEnd
        if self._area_finished(index):
            self._ui_verify(lambda: (not self.appear(RealmRaidAssets.I_FIRE, threshold=0.8)
                                    and (self._ryou_raid_complete()
                                         or (is_ryou_toppa_page(self) and self._area_finished(index)))), [])
            if self._ryou_raid_complete():
                self._ui_verify(self._ryou_raid_complete, [])
                self.plan_tomorrow_ryoutoppa()
                raise TaskEnd
            logger.info(f'Area [{index + 1}] is already broken; select another target')
            return False
        # 如果该区域攻略失败返回 False
        if self.appear(f1, threshold=0.8) or self.appear(f2, threshold=0.8):
            logger.info('Area [%s] is futile attack, skip.' % str(index + 1))
            return False
        return True

    def _ryou_raid_complete(self):
        """Use the global progress status, never an individual target's broken stamp."""
        if self.appear(self.I_SUCCESS_PENETRATION, threshold=0.8):
            return True
        if not is_ryou_toppa_context(self):
            return False
        roi = (130, 365, 250, 65)
        notice = RuleOcr(roi=roi, area=roi, mode='Full', method='Default',
                         keyword='', name='ryou_raid_complete')
        notice.score = 0.8
        completed = {'100%', '100.0%', '100.00%', '已攻破', '攻破完成',
                     '阴阳寮已攻破'}
        return any(''.join(result.ocr_text.split()).strip('！!。.').replace('％', '%') in completed
                   for result in notice.detect_and_ocr(self.device.image))

    def _area_finished(self, index):
        f3, f4 = area_map[index]['finished_sign']
        if self.appear(f3, threshold=0.8) or self.appear(f4, threshold=0.8):
            return True
        image = self.device.image
        if image is None or len(image.shape) < 2:
            return False
        # Restrict the plain card label to this target. Another card's label must
        # neither skip an available target nor block popup dismissal verification.
        roi = tuple(f3.roi_back)
        notice = RuleOcr(roi=roi, area=roi, mode='Full', method='Default',
                         keyword='', name='ryou_target_already_broken')
        notice.score = 0.8
        return any(''.join(result.ocr_text.split()).strip('！!。.') == '已攻破'
                   for result in notice.detect_and_ocr(image))

    def _no_attackable_targets(self):
        """Broken cards sink; the leading group must be explicitly unavailable."""
        if (not is_ryou_toppa_page(self)
                or self.appear(RealmRaidAssets.I_FIRE, threshold=0.8)):
            return False
        for index, area in enumerate(area_map):
            if self._area_finished(index):
                continue
            if any(self.appear(rule, threshold=0.8) for rule in area['fail_sign']):
                continue
            # An available or unrecognized card is not completion evidence.
            return False
        return True

    def _finish_no_attackable_targets(self):
        # Recheck the current leading group in fresh frames: failed battle
        # returns and click failures alone must never mark this run complete.
        # Keep the normal success interval; the guild is not necessarily 100%.
        self.finish_completed_task(
            'RyouToppa', 'All leading targets are broken or skipped failures',
            verify=self._no_attackable_targets,
            schedule=lambda: self.set_next_run(task='RyouToppa', finish=True,
                                               server=True, success=True))

    def flush_area_cache(self):
        time.sleep(2)
        duration = 0.352
        count = random.randint(1, 3)
        for i in range(count):
            # 测试过很多次 win32api, win32gui 的 MOUSEEVENTF_WHEEL, WM_MOUSEWHEEL
            # 都出现过很多次离奇的事件，索性放弃了使用以下方法，参数是精心调试的
            # 每次执行刚好刷新一组（2个）设定随机刷新 1 - 3 次
            safe_pos_x = random.randint(540, 1000)
            safe_pos_y = random.randint(320, 540)
            p1 = (safe_pos_x, safe_pos_y)
            p2 = (safe_pos_x, safe_pos_y - 101)
            logger.info('Swipe %s -> %s, %s ' % (point2str(*p1), point2str(*p2), duration))
            self.device.swipe(
                p1,
                p2,
                duration=duration,
                control_name='RYOU_TOPPA_REFRESH',
            )
            time.sleep(2)

    def attack_area(self, index: int):
        """
        :return: 战斗成功(True) or 战斗失败(False) or 区域不可用（False） or 没有进攻机会（设定下次运行并退出）
        """
        # 每次进攻前检查页面和区域可用性。
        self._ensure_ryou_toppa_page()
        if not self.check_area(index):
            return False
        # 正式进攻会设定 2s - 10s 的随机延迟，避免攻击间隔及其相近被检测为脚本。
        if self.config.ryou_toppa.raid_config.random_delay:
            delay = random_delay()
            time.sleep(delay)
        rcl = area_map[index].get("rule_click")
        # 塔塔开！
        popup_failure_count = 0
        popup_wait_timer = None
        battle_wait_timer = None
        self.device.click_record_clear()
        while True:
            self.screenshot()
            if self.is_in_battle(False):
                logger.info("Start attach area [%s]" % str(index + 1))
                return self.run_general_battle(config=self.config.ryou_toppa.general_battle_config)
            if self._ryou_raid_complete():
                if self.appear(RealmRaidAssets.I_FIRE, threshold=0.8):
                    return self._skip_unavailable_area()
                self._ui_verify(self._ryou_raid_complete, [])
                self.plan_tomorrow_ryoutoppa()
                raise TaskEnd

            if popup_failure_count >= TOPPA_CLICK_FAILURE_LIMIT:
                logger.warning(
                    f'挑战进入实际失败达到{TOPPA_CLICK_FAILURE_LIMIT}次，'
                    f'关闭浮窗并跳过当前区域：popup={popup_failure_count}'
                )
                return self._skip_unavailable_area()

            # 已被其他成员攻打的目标不会进入战斗。只挑战一次，读到提示
            # 或等待超时后关闭浮窗并换目标，不能继续点击同一个挑战按钮。
            if battle_wait_timer is not None:
                if self._area_already_attacked():
                    logger.info('该结界已被攻打，关闭挑战浮窗并切换目标')
                    return self._skip_unavailable_area()
                if not battle_wait_timer.reached():
                    continue
                logger.warning('单次挑战后未进入战斗，关闭浮窗并切换目标')
                return self._skip_unavailable_area()

            # 挑战浮窗已出现时点击挑战按钮，并开启独立的战斗等待窗口。
            if self.appear(RealmRaidAssets.I_FIRE, threshold=0.8):
                popup_wait_timer = None
                if self.appear_then_click(
                    RealmRaidAssets.I_FIRE,
                    interval=2,
                    threshold=0.8,
                ):
                    battle_wait_timer = Timer(TOPPA_BATTLE_WAIT_TIMEOUT).start()
                continue

            # 点击结界卡片后等待挑战浮窗出现。浮窗等待超时才算一次
            # 结界卡片点击失败，不能把“已经发出点击”直接当作失败。
            if popup_wait_timer is not None:
                if not popup_wait_timer.reached():
                    continue
                popup_wait_timer = None
                popup_failure_count += 1
                logger.warning(
                    '点击目标后挑战浮窗未出现：'
                    f'{popup_failure_count}/'
                    f'{TOPPA_CLICK_FAILURE_LIMIT}'
                )
                continue

            self._ensure_ryou_toppa_page()
            if self._ryou_raid_complete():
                continue
            if self.click(rcl, interval=5):
                time.sleep(random.uniform(0, 0.3))
                popup_wait_timer = Timer(TOPPA_POPUP_WAIT_TIMEOUT).start()
                continue

    def _area_already_attacked(self):
        """Only the explicit unavailable-target notice ends the battle wait early."""
        image = self.device.image
        if image is None or len(image.shape) < 2:
            return False
        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            return False
        roi = (0, 0, width, height)
        notice = RuleOcr(roi=roi, area=roi, mode='Full', method='Default',
                         keyword='', name='ryou_already_attacked')
        notice.score = 0.8
        notices = {'该结界已被攻打', '该结界已被攻破', '该结界已攻破'}
        return any(''.join(result.ocr_text.split()).strip('！!。.') in notices
                   for result in notice.detect_and_ocr(image))

    def _attack_exit_ready(self):
        # A late battle start must be handled by the battle controller. Otherwise
        # the raid page and absence of its challenge popup must both be verified.
        if self.is_in_battle(False):
            return True
        return (not self.appear(RealmRaidAssets.I_FIRE, threshold=0.8)
                and (is_ryou_toppa_page(self) or self._ryou_raid_complete())
                and not self._area_already_attacked())

    def _skip_unavailable_area(self):
        """Close one detected popup, verify fresh frames, then let run select another area.

        「关闭浮窗」与「战斗开始」存在竞态：结界突破高峰期点挑战后可能要好几秒
        才进入战斗，若此时已判定失败并去关浮窗，两边会撞在一起导致脚本崩溃重启
        （10-06 13:55:47 实测）。因此在关浮窗**之前**先用新鲜帧复核一次战斗态，
        确认真没进战斗才动手。
        """
        self.screenshot()
        if self.is_in_battle(False):
            return self.run_general_battle(config=self.config.ryou_toppa.general_battle_config)
        # 竞态复核：判定与关窗之间战斗可能才刚开始（实测间隔仅 50ms）。
        settle = Timer(1.5).start()
        while not settle.reached():
            self.screenshot()
            if self.is_in_battle(False):
                logger.info('Battle started while closing the popup; resume the battle')
                return self.run_general_battle(config=self.config.ryou_toppa.general_battle_config)
        controls = []
        if self.appear(RealmRaidAssets.I_FIRE, threshold=0.8):
            if self.device.click(*self.C_SAFE_AREA.coord(),
                                 control_name=self.C_SAFE_AREA.name) is False:
                raise TaskRecoveryFailed('寮突破挑战浮窗关闭点击被阻止')
            controls = [self.C_SAFE_AREA]
        self._ui_verify(self._attack_exit_ready, controls)
        if self.is_in_battle(False):
            return self.run_general_battle(config=self.config.ryou_toppa.general_battle_config)
        if self._ryou_raid_complete():
            self._ui_verify(self._ryou_raid_complete, [])
            self.plan_tomorrow_ryoutoppa()
            raise TaskEnd
        return False


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    t = ScriptTask(config, device)
    t.run()

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import timedelta, datetime, time
from cached_property import cached_property

from module.exception import TaskEnd, TaskRecoveryFailed
from module.logger import logger

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_hunt, page_hunt_kirin, page_shikigami_records
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Hunt.assets import HuntAssets
from tasks.Hunt.kirin_assist import kirin_assist_visible, confirm_kirin_assist


class ScriptTask(GameUi, GeneralBattle, GeneralInvite, SwitchSoul, HuntAssets):
    kirin_day = True  # 不是麒麟就是阴界之门
    tomorrow_kirin_day = True  # 明天是麒麟还是阴界之门

    def run(self):
        self.con_time = self.config.hunt.hunt_time
        if not self.check_datetime():
            # 设置下次运行时间 为今天的晚上七点钟
            raise TaskEnd('Hunt')
        con = self.config.hunt.hunt_config
        if con.kirin_group_team != '-1,-1' or con.netherworld_group_team != '-1,-1':
            self.goto_page(page_shikigami_records)

            if self.kirin_day:
                if con.kirin_group_team != '-1,-1':
                    self.run_switch_soul(con.kirin_group_team)
            else:
                if con.netherworld_group_team != '-1,-1':
                    self.run_switch_soul(con.netherworld_group_team)
        if self.kirin_day:
            self.goto_page(page_hunt_kirin)
            self.kirin()
        else:
            self.goto_page(page_hunt)
            self.netherworld()
        sleep(1)

        self.plan_tomorrow_hunt()
        raise TaskEnd('Hunt')

    def check_datetime(self) -> bool:
        """
        检查日期和时间, 会设置是麒麟还是阴界之门
        :return: 符合麒麟19:00-21:00、阴界19:00-23:00的时间返回True, 否则返回False
        """
        now = datetime.now()
        day_of_week = now.weekday()
        if 0 <= day_of_week <= 3:
            self.kirin_day = True
        elif 4 <= day_of_week <= 6:
            self.kirin_day = False
        
        if 3 <= day_of_week <= 5:
            self.tomorrow_kirin_day = False
        else:
            self.tomorrow_kirin_day = True

        now = datetime.now()
        # 如果时间在可执行时间(麒麟日6:00、阴界日19:00)之前则设定时间为当天的自定义时间，返回False
        # 如果是在可执行时间则返回True
        if self.kirin_day:
            logger.info('Today is the Kirin day')
            if now.time() < time(6, 0):
                self.custom_next_run(task='Hunt', custom_time=self.con_time.kirin_time, time_delta=0)
                raise TaskEnd('Hunt')
            # 如果是麒麟日在23:00-23:59之间则设定时间为明天的自定义时间，返回False
            elif now.time() > time(23, 0):
                self.plan_tomorrow_hunt()
                raise TaskEnd('Hunt')
            else:
                return True
        else:
            logger.info('Today is the Netherworld day')
            if now.time() < time(17, 0):
                self.custom_next_run(task='Hunt', custom_time=self.con_time.netherworld_time, time_delta=0)
                raise TaskEnd('Hunt')
            # 如果是阴界日在23:00-23:59之间则设定时间为明天的自定义时间，返回False
            elif now.time() > time(23, 0):
                self.plan_tomorrow_hunt()
                raise TaskEnd('Hunt')
            else:
                return True

    def plan_tomorrow_hunt(self):
        next_time = (self.con_time.kirin_time if self.tomorrow_kirin_day
                     else self.con_time.netherworld_time)
        target = (datetime.now() + timedelta(days=1)).replace(
            hour=next_time.hour, minute=next_time.minute, second=next_time.second,
            microsecond=0)
        # Keep the configured hunt time instead of the generic server reset.
        self.set_next_run(task='Hunt', server=False, target=target)
        outcomes = getattr(self.config, '_learning_task_outcomes', None)
        if outcomes is not None:
            outcomes['hunt'] = True

    def _finish_already_challenged(self, marker):
        self._ui_verify(lambda: self.appear(marker), [])
        logger.info('Hunt already challenged today; mark completed, not failed')
        self.plan_tomorrow_hunt()
        # Close/navigation is handled by the scheduler after successful completion.
        raise TaskEnd('Hunt')

    def kirin(self):
        logger.hr('kirin', 2)
        while 1:
            self.screenshot()
            # The foreground modal must precede all dimmed background matches.
            if kirin_assist_visible(self):
                confirm_kirin_assist(self)
                break
            if self.appear(self.I_KIRIN_END):
                self._finish_already_challenged(self.I_KIRIN_END)
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                break
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.9):
                continue
            if self.appear_then_click(self.I_KIRIN_CHALLAGE, interval=1.5):
                continue
        logger.info('Start battle')
        self.run_general_battle(
            self.config.hunt.kirin_battle_config,
            battle_key="hunt_kirin",
            exit_matcher=page_hunt_kirin,
        )

    def netherworld_start_confirm_visible(self):
        # Both rules must match the same fresh image; no generic gold-button match.
        return self.appear(self.I_NW_START_Q) and self.appear(self.I_NW_START_CONFIRM)

    def confirm_netherworld_start(self):
        generation = self.device._escape_generation
        self._ui_verify(self.netherworld_start_confirm_visible, [], timeout=5)
        self.screenshot()
        if self.device._escape_generation != generation:
            raise TaskRecoveryFailed('Netherworld start interrupted before confirmation')
        if not self.netherworld_start_confirm_visible():
            raise TaskRecoveryFailed('Netherworld start confirmation changed before input')
        if not self.appear_then_click(self.I_NW_START_Q, action=self.C_NW_START_CONFIRM):
            raise TaskRecoveryFailed('Netherworld start confirmation input was blocked')
        if self.device._escape_generation != generation:
            raise TaskRecoveryFailed('Netherworld start interrupted after confirmation')
        self._ui_verify(lambda: not self.netherworld_start_confirm_visible(),
                        [self.I_NW_START_Q, self.C_NW_START_CONFIRM], timeout=15)
        # Modal disappearance alone does not prove a battle has started.
        self._ui_verify(lambda: self.is_in_room(False), [], timeout=15)
        logger.info('Netherworld starting-round confirmation reached battle room')

    def open_netherworld_challenge(self):
        generation = self.device._escape_generation
        if not self.appear_then_click(self.I_NW_CHALLAGE):
            raise TaskRecoveryFailed('Netherworld challenge input was blocked')
        if self.device._escape_generation != generation:
            raise TaskRecoveryFailed('Netherworld challenge interrupted after input')
        self._ui_verify(lambda: self.netherworld_start_confirm_visible()
                        or self.appear(self.I_UI_CONFIRM) or self.is_in_room(False),
                        [self.I_NW_CHALLAGE], timeout=15)

    def netherworld(self):
        logger.hr('netherworld', 2)
        while 1:
            self.screenshot()
            if self.netherworld_start_confirm_visible():
                self.confirm_netherworld_start()
                continue
            if self.appear(self.I_NW_DONE):
                self._finish_already_challenged(self.I_NW_DONE)
            if self.is_in_room(False):
                self.screenshot()
                if not self.appear(self.I_FIRE):
                    continue
                self.click_fire()
                break

            if self.appear_then_click(self.I_NW, interval=0.9):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.9):
                continue
            if self.appear(self.I_NW_CHALLAGE):
                self.open_netherworld_challenge()
                continue
        logger.info('Start battle')
        self.run_general_battle(
            self.config.hunt.netherworld_battle_config,
            battle_key="hunt_netherworld",
            exit_matcher=page_hunt,
        )


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from module.exception import TaskEnd, TaskRecoveryFailed
from module.logger import logger
from module.base.timer import Timer
import tasks.GameUi.page as pages

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_team, page_shikigami_records
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.GeneralRoom.round_progress import TeamChallengeRoundProgress
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.ExperienceYoukai.assets import ExperienceYoukaiAssets


class ScriptTask(GameUi, GeneralBattle, GeneralRoom, GeneralInvite, SwitchSoul, ExperienceYoukaiAssets):

    def before_run(self):
        page_battle_result = self.navigator.resolve_page(pages.page_battle_result)
        page_battle_result.recognizer = pages.any_of(self.I_EXP_WIN, page_battle_result.recognizer)

    def run(self):
        self.before_run()
        con = self.config.experience_youkai.experience_youkai
        self.round_progress = TeamChallengeRoundProgress(self.config.config_name, 'ExperienceYoukai')
        self.round_progress.begin()
        if self.round_progress.completed_wins == self.round_progress.target:
            self.experience_exit(con)
        # 切换御魂
        if self.config.experience_youkai.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(self.config.experience_youkai.switch_soul.switch_group_team)

        if self.config.experience_youkai.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.experience_youkai.switch_soul.group_name,
                                         self.config.experience_youkai.switch_soul.team_name)

        # 开启加成
        if con.buff_exp_50_click or con.buff_exp_100_click:
            self.goto_page(page_main)
            self.open_buff()
            if con.buff_exp_50_click:
                self.exp_50()
            if con.buff_exp_100_click:
                self.exp_100()
            self.close_buff()
        while self.round_progress.completed_wins < self.round_progress.target:
            self.goto_page(page_team)
            self.check_zones('经验妖怪')
            cooldown = self.check_team_cooldown('经验妖怪')
            if cooldown:
                self.experience_exit(con, cooldown)
            # 开始
            if not self.create_room():
                raise TaskRecoveryFailed('ExperienceYoukai room creation failed; retain round progress')
            self.ensure_public()
            self.create_ensure()
            # 进入到了房间里面
            wait_timer = Timer(50)
            wait_timer.start()
            while 1:
                self.screenshot()

                if not self.is_in_room():
                    continue
                if wait_timer.reached():
                    # 超过时间依然挑战
                    logger.warning('Wait for too long and start the challenge')
                    self.click_fire()
                    if self.run_general_battle() is not True:
                        raise TaskRecoveryFailed('ExperienceYoukai battle did not win; retain round progress')
                    self.round_progress.record_win()
                    logger.info(f'ExperienceYoukai confirmed victories: {self.round_progress.completed_wins}/2')
                    break
                if not self.appear(self.I_ADD_5_1):
                    # 有人进来了，可以进行挑战
                    logger.info('There is someone in the room and start the challenge')
                    self.click_fire()
                    if self.run_general_battle() is not True:
                        raise TaskRecoveryFailed('ExperienceYoukai battle did not win; retain round progress')
                    self.round_progress.record_win()
                    logger.info(f'ExperienceYoukai confirmed victories: {self.round_progress.completed_wins}/2')
                    break
        # 退出 (要么是在组队界面要么是在庭院)
        self.experience_exit(con)

    def experience_exit(self, con, cooldown=None):
        self.goto_page(page_main)
        if con.buff_exp_50_click or con.buff_exp_100_click:
            self.open_buff()
            if con.buff_exp_50_click:
                self.exp_50(False)
            if con.buff_exp_100_click:
                self.exp_100(False)
            self.close_buff()

        if cooldown:
            self.schedule_team_cooldown('ExperienceYoukai', cooldown)
        if self.round_progress.completed_wins != self.round_progress.target:
            raise TaskRecoveryFailed('ExperienceYoukai round is incomplete; retain progress')
        if self.round_progress.next_cycle_at is None:
            self.set_next_run(task='ExperienceYoukai', success=True, finish=False)
            self.round_progress.mark_scheduled(self.config.experience_youkai.scheduler.next_run)
        else:
            self.set_next_run(task='ExperienceYoukai', server=False,
                              target=self.round_progress.next_cycle_at)
        logger.info('ExperienceYoukai Completed 2/2 confirmed victories')
        raise TaskEnd('ExperienceYoukai')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

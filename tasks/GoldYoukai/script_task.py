# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from cached_property import cached_property
from time import sleep

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
from tasks.GoldYoukai.assets import GoldYoukaiAssets


class ScriptTask(GameUi, GeneralBattle, GeneralRoom, GeneralInvite, SwitchSoul, GoldYoukaiAssets):

    def _gold_zone_selected(self):
        return '金币' in self.O_GR_ZONES_NAME.ocr(self.device.image)

    def check_zones(self, name: str) -> bool:
        if name != '金币妖怪':
            return super().check_zones(name)
        # The selected row can be clipped while the right-hand title is visible.
        generation = self.device._escape_generation
        selected = []
        for _ in range(2):
            self.screenshot()
            if self.device._escape_generation != generation:
                raise TaskRecoveryFailed('GoldYoukai selection interrupted by recovery')
            selected.append(self.appear(self.I_CHECK_TEAM) and self._gold_zone_selected())
        if all(selected):
            return True
        position = self.list_find(self.L_TEAM_LIST, name)
        if not position:
            raise TaskRecoveryFailed('GoldYoukai row not found after bounded scrolling')
        if self.device.click(x=position[0], y=position[1],
                             control_name='GoldYoukai_zone') is False:
            raise TaskRecoveryFailed('GoldYoukai row click was blocked')
        return self._ui_verify(self._gold_zone_selected, ['GoldYoukai_zone'])

    def list_find(self, target, name, max_swipe: int = 10):
        if target is not self.L_TEAM_LIST or name != '金币妖怪':
            return super().list_find(target, name, max_swipe=max_swipe)
        # Also used by check_team_cooldown: reveal the whole row and its timer.
        generation = self.device._escape_generation
        previous = None
        swipes = 0
        x, y, width, height = target.roi_back
        margin = target.size[1] // 2 + 12
        for _ in range(2 * max_swipe + 2):
            self.screenshot()
            if self.device._escape_generation != generation:
                raise TaskRecoveryFailed('GoldYoukai list search interrupted by recovery')
            if not self.appear(self.I_CHECK_TEAM):
                raise TaskRecoveryFailed('GoldYoukai list search left the team page')
            result = target.ocr_appear(self.device.image, name=name)
            position = result if isinstance(result, tuple) and result != (0, 0) else None
            if position and x <= position[0] < x + width and y + margin <= position[1] <= y + height - margin:
                if previous and max(abs(position[i] - previous[i]) for i in (0, 1)) <= 4:
                    return position
                previous = position
                continue
            previous = None
            if swipes >= max_swipe:
                return False
            # Positive means later entries. A missing OCR result must not reverse
            # upward when GoldYoukai is just below the visible bottom edge.
            after = not isinstance(result, int) or result >= 0
            if position:
                after = position[1] > y + height - margin
            p1x, p1y, p2x, p2y = target.swipe_pos(number=2, after=after)
            if self.device.swipe(p1=(p1x, p1y), p2=(p2x, p2y),
                                 control_name='GoldYoukai_list') is False:
                raise TaskRecoveryFailed('GoldYoukai list swipe was blocked')
            swipes += 1
            sleep(0.9)
        return False

    def before_run(self):
        page_battle_result = self.navigator.resolve_page(pages.page_battle_result)
        page_battle_result.recognizer = pages.any_of(self.I_GOLD_WIN, page_battle_result.recognizer)

    def run(self):
        self.before_run()
        con = self.config.gold_youkai.gold_youkai
        self.round_progress = TeamChallengeRoundProgress(self.config.config_name, 'GoldYoukai')
        self.round_progress.begin()
        if self.round_progress.completed_wins == self.round_progress.target:
            self.gold_exit(con)
        # 切换御魂
        if self.config.gold_youkai.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(self.config.gold_youkai.switch_soul.switch_group_team)

        if self.config.gold_youkai.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.gold_youkai.switch_soul.group_name,
                                         self.config.gold_youkai.switch_soul.team_name)

        # 开启加成
        if con.buff_gold_50_click or con.buff_gold_100_click:
            self.goto_page(page_main)
            self.open_buff()
            if con.buff_gold_50_click:
                self.gold_50()
            if con.buff_gold_100_click:
                self.gold_100()
            self.close_buff()
        while self.round_progress.completed_wins < self.round_progress.target:
            self.goto_page(page_team)
            self.check_zones('金币妖怪')
            cooldown = self.check_team_cooldown('金币妖怪')
            if cooldown:
                self.gold_exit(con, cooldown)
            # 开始
            if not self.create_room():
                raise TaskRecoveryFailed('GoldYoukai room creation failed; retain round progress')
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
                        raise TaskRecoveryFailed('GoldYoukai battle did not win; retain round progress')
                    self.round_progress.record_win()
                    logger.info(f'GoldYoukai confirmed victories: {self.round_progress.completed_wins}/2')
                    break
                if not self.appear(self.I_ADD_5_1):
                    # 有人进来了，可以进行挑战
                    logger.info('There is someone in the room and start the challenge')
                    self.click_fire()
                    if self.run_general_battle() is not True:
                        raise TaskRecoveryFailed('GoldYoukai battle did not win; retain round progress')
                    self.round_progress.record_win()
                    logger.info(f'GoldYoukai confirmed victories: {self.round_progress.completed_wins}/2')
                    break
        # 退出 (要么是在组队界面要么是在庭院)
        self.gold_exit(con)

    def gold_exit(self, con, cooldown=None):
        self.goto_page(page_main)
        if con.buff_gold_50_click or con.buff_gold_100_click:
            self.open_buff()
            if con.buff_gold_50_click:
                self.gold_50(False)
            if con.buff_gold_100_click:
                self.gold_100(False)
            self.close_buff()

        if cooldown:
            self.schedule_team_cooldown('GoldYoukai', cooldown)
        if self.round_progress.completed_wins != self.round_progress.target:
            raise TaskRecoveryFailed('GoldYoukai round is incomplete; retain progress')
        if self.round_progress.next_cycle_at is None:
            self.set_next_run(task='GoldYoukai', success=True, finish=False)
            self.round_progress.mark_scheduled(self.config.gold_youkai.scheduler.next_run)
        else:
            self.set_next_run(task='GoldYoukai', server=False,
                              target=self.round_progress.next_cycle_at)
        logger.info('GoldYoukai Completed 2/2 confirmed victories')
        raise TaskEnd('GoldYoukai')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

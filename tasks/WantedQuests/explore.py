from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.logger import logger
from tasks.Exploration import page as pages
from tasks.Exploration.base import BaseExploration
from tasks.Exploration.config import UpType, UserStatus
from tasks.Exploration.script_task import ScriptTask as ExplorationScriptTask
from tasks.Exploration.version import HighLight
from typing import Optional
from datetime import datetime
from module.exception import TaskRecoveryFailed


class ExploreWantedBoss(Exception):
    # 出现一种情况，要求的怪是仅仅最后的Boss，其他小怪不是
    pass


class WQExplore(ExplorationScriptTask, HighLight):
    _explor_cnt: int = 0  # 探索次数
    _max_cnt: int = 0  # 探索最大次数
    wanted_boss_only: bool = False

    def _default_detect_categories(self) -> set[str]:
        categories = super()._default_detect_categories()
        categories.add("exploration")
        return categories

    def search_up_fight(self, up_type: UpType = None) -> Optional[RuleImage | RuleGif]:
        if self.appear(self.TEMPLATE_GIF):
            self.fire_monster_type = 'wq_normal'
            return self.TEMPLATE_GIF
        if self.wanted_boss_only:
            button = BaseExploration.search_up_fight(self, UpType.ALL)
            if button is not None:
                self.fire_monster_type = 'normal'
                logger.info('Wanted boss: clear exploration monsters to unlock boss')
            return button
        return None

    def check_exit(self, current_page: pages.Page | None) -> bool:
        if current_page in (pages.page_battle_prepare, pages.page_battle,
                            pages.page_battle_result, pages.page_reward):
            return False
        need_exit = self._explor_cnt >= self._max_cnt
        # 探索次数已经够了但是任何怪物都没打过
        if need_exit and self.fire_monster_type == '':
            raise ExploreWantedBoss
        return need_exit

    def arrive_end(self) -> bool:
        arrived_end = BaseExploration.arrive_end(self)
        if self.wanted_boss_only:
            if arrived_end:
                raise TaskRecoveryFailed('Wanted boss did not appear after clearing the visible exploration map')
            return False
        self._explor_cnt += 1 if arrived_end else 0
        return arrived_end

    def run_on_battle(self):
        won = self.run_general_battle(self._config.general_battle_config,
                                      exit_matcher=pages.page_exp_main)
        self._match_end.refresh()
        self.wait_start_time = datetime.now()
        if not won:
            raise TaskRecoveryFailed('Wanted exploration battle did not finish with a victory')
        self._ui_verify(lambda: self.get_current_page() == pages.page_exp_main,
                        [], timeout=10)
        if self.fire_monster_type == 'boss':
            self._explor_cnt += 1
            logger.info(f'Wanted boss victory verified: {self._explor_cnt}/{self._max_cnt}')

    def explore(self, goto: RuleImage, num: int):
        logger.info(f'Start exploring with number: {num}')
        self._max_cnt = num
        self._explor_cnt = 0
        while True:
            self.screenshot()
            if pages.page_exp_entrance == self.get_current_page():
                break
            if self.appear_then_click(goto, interval=2):
                continue
        self.user_status = UserStatus.ALONE
        self.exec_exp_page()

if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from tasks.WantedQuests.assets import WantedQuestsAssets

    config = Config('oas1')
    device = Device(config)
    t = WQExplore(config, device)
    t.explore(goto=WantedQuestsAssets.I_GOTO_1, num=2)

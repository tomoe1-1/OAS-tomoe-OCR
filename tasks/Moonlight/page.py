"""月映千灯入口及月华流光两级页面。"""
import time

from module.exception import GamePageUnknownError
from module.logger import logger
from tasks.ActivityShikigami.page import (handle_activity_reward,
                                          handle_activity_close,
                                          handle_activity_story)
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import Page, page_main
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.Moonlight.assets import MoonlightAssets


def find_moonlight_entry(task) -> bool:
    """沿用庭院右侧栏目切换查找，最多八轮。"""
    switched = 0
    for _ in range(8):
        task.screenshot()
        if not task.appear(task.I_CHECK_MAIN):
            return False
        if task.appear(MoonlightAssets.I_MOON_ENTRY):
            return True
        if task.appear(RightActivityAssets.I_TOGGLE_BUTTON):
            time.sleep(1.)
            task.screenshot()
            if task.appear_then_click(RightActivityAssets.I_TOGGLE_BUTTON, interval=0):
                switched += 1
        time.sleep(.5)
    task.screenshot()
    if task.appear(MoonlightAssets.I_MOON_ENTRY):
        return True
    logger.warning(f'Moonlight entry not found after {switched} column switches')
    raise GamePageUnknownError('Cannot find Moonlight entry')


page_moon_lobby = Page(MoonlightAssets.I_MOON_LOBBY, priority=75)
page_moon_battle = Page(MoonlightAssets.I_MOON_BATTLE, priority=75)
page_moon_lobby.add_enter_failure_hooks(
    find_moonlight_entry, handle_activity_reward,
    handle_activity_close, handle_activity_story,
)
page_main.connect(page_moon_lobby, MoonlightAssets.I_MOON_ENTRY, key='main->moon_lobby')
page_moon_lobby.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key='moon_lobby->main')
page_moon_lobby.connect(page_moon_battle, MoonlightAssets.I_MOON_ENTER_BATTLE, key='moon_lobby->moon_battle')
page_moon_battle.connect(page_moon_lobby, GlobalGameAssets.I_UI_BACK_YELLOW, key='moon_battle->moon_lobby')

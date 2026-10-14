# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import datetime

from module.exception import (
    GameNotRunningError,
    GamePageUnknownError,
    GameStuckError,
    GameTooManyClickError,
    TaskEnd,
)
from module.logger import logger
from tasks.GameUi.courtyard import ensure_courtyard_menu_visible
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.Restart.server_update import (
    delay_pending_tasks_for_server_update,
    is_server_update_window,
)


class ScriptTask(GameUi):

    def run(self) -> None:
        self.device.stuck_record_clear()
        try:
            self.goto_page(page_main, recovery_navigation=True)
        except (GamePageUnknownError, GameStuckError, GameTooManyClickError, GameNotRunningError) as e:
            # 异常兑换：若处于停服窗口期，把"游戏页面异常 / 卡死 / 未运行"统一延后而不是抛回 Scheduler 触发 Restart
            if is_server_update_window() and self._delay_for_server_update(
                reason=f'{type(e).__name__} during goto_main inside server update window'
            ):
                raise TaskEnd('Goto main aborted due to server update window')
            raise e

        # 认出门庭不等于菜单可用。page_main 的识别器是 courtyard_visible，
        # 庭院卷轴收起时它照样命中，于是 goto_page 会报「到达主页面」，
        # 但底部菜单整排入口不可用。实测 10-06 GotoMain 连续多次假性成功
        # （10:00:47 / 10:02:36 / 10:08:00 均 Page arrived page_main），
        # 紧接着每个依赖底部入口的任务各自报一遍导航失败。
        # 这里统一展开一次；展开不了就标记 incomplete 结束本轮，
        # 让调度器保留任务稍后重试，而不是让后续任务继续撞墙。
        if not ensure_courtyard_menu_visible(self):
            logger.warning('Courtyard scroll is collapsed; bottom menu unreachable, '
                           'end this round and retry later')
            # 与 _delay_for_server_update 同一套写法：ScriptTask 拿不到 Script 的
            # _set_task_runtime_outcome，只能直接写 config.task_runtime_outcome，
            # 由 Script.run() 在捕获 TaskEnd 时读走并判为未完成。
            self.config.task_runtime_outcome = {'task': 'GotoMain', 'status': 'incomplete'}
            raise TaskEnd('Courtyard scroll is collapsed; bottom menu unreachable')

        raise TaskEnd

    def _delay_for_server_update(self, reason: str) -> bool:
        delay_target = delay_pending_tasks_for_server_update(self.config, reason=reason)
        outcome = {
            'task': 'GotoMain',
            'status': 'server_update_delayed',
            'wait_until': delay_target,
        }
        self.config.task_runtime_outcome = outcome
        return True

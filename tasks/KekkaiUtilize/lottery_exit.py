"""Exit a confirmed guild lottery screen before handing it to another task."""
from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.exception import TaskRecoveryFailed
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
from tasks.GameUi.page import page_guild

LOTTERY_TITLE = RuleOcr(roi=(105,8,210,66), area=(105,8,210,66),
                        mode='Single', method='Default', keyword='抽奖箱', name='guild_lottery_title')


def guild_lottery_visible(task):
    return task.appear(KekkaiUtilizeAssets.I_CHECK_GUILD_LOTTERY) and '抽奖箱' in LOTTERY_TITLE.ocr(task.device.image)


def exit_guild_lottery(task):
    task._ui_verify(lambda: guild_lottery_visible(task), [], timeout=12)
    if not task.appear(GlobalGameAssets.I_UI_BACK_YELLOW):
        raise TaskRecoveryFailed('Guild lottery exit arrow was not located')
    roi = GlobalGameAssets.I_UI_BACK_YELLOW.roi_front
    rule = RuleClick(roi_front=roi, roi_back=roi, name='GUILD_LOTTERY_EXIT')
    x, y = rule.coord()
    if task.device.click(x=x, y=y, control_name=rule.name) is False:
        raise TaskRecoveryFailed('Guild lottery exit was blocked')
    destination = task.navigator.resolve_page(page_guild)
    return task._ui_verify(lambda: task.match_page_once(destination), [rule], timeout=12)

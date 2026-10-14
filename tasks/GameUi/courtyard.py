"""Recognize courtyard scroll state and click the collapsed scroll body."""
from pathlib import Path

from module.atom.image import RuleImage
from module.atom.click import RuleClick
from module.exception import TaskRecoveryFailed
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.town import town_visible


class CourtyardScrollStuckError(TaskRecoveryFailed):
    """庭院卷轴收起且两轮点击都没能展开。

    单独建类而不是继续用 TaskRecoveryFailed，是为了让日志能一眼区分
    「卷轴卡住、底部入口整体不可用」与「普通页面跳转失败」：
    前者会让所有依赖 page_main 底部入口的任务连续失败，
    原因只有一个，不需要每个任务各报一遍导航预算耗尽。
    """

    def __init__(self, center=None):
        spot = f' (last click at {center})' if center else ''
        super().__init__(
            f'Courtyard scroll is collapsed and did not expand after two attempts{spot}; '
            'bottom menu entries stay unreachable until it is expanded manually'
        )


# 卷轴本体在收起态的实测位置：原生截图 1280x720 下 (1147,589)-(1239,714)，中心 (1193,652)。
# 收起态三张不同实例的截图全部在该位置命中 1.000，位置固定，因此按固定 ROI 搜索即可。
SCROLL_BODY_SIZE = (93, 126)
SCROLL_BODY_SEARCH = (1080, 540, 160, 180)


def collapsed_scroll_button(task):
    """Locate the collapsed scroll body only when the courtyard menu row is really collapsed.

    收起态的可靠判据是「底部菜单入口消失」且「卷轴关闭标识出现」：
    故障截图实测 shikigami_records 0.28 / team 0.49（阈值 0.7 均不命中）、
    scroll_close 0.98（阈值 0.8 命中）；健康截图对应 0.99 / 0.99 / 0.36。
    早期版本用 goto_exploration 打头判据，但它在收起态只有 0.77~0.84、
    横跨自身阈值 0.8，会让整个判定在部分画面上直接短路返回 None。

    ⚠️ 模板是**卷轴本体**（收起时倾斜、缠红绳），不是它上方的金色回转箭头 ——
    那个回转箭头是右侧限时活动的查看入口，点它只会打开活动页，永远展不开卷轴。
    本体模板实测：收起态 1.000、展开态 0.45~0.50，区分度干净。
    """
    image = getattr(task.device, 'image', None)
    if image is None or image.ndim != 3:
        return None
    if task.appear(GameUiAssets.I_MAIN_GOTO_SHIKIGAMI_RECORDS):
        return None
    if not task.appear(GameUiAssets.I_MAIN_SCROLL_CLOSE):
        return None
    height, width = image.shape[:2]
    body_w, body_h = SCROLL_BODY_SIZE
    x, y, search_w, search_h = SCROLL_BODY_SEARCH
    # 小分辨率设备上裁剪框可能越界，按比例收窄搜索区。
    x = min(x, max(width - search_w, 0))
    y = min(y, max(height - search_h, 0))
    search_w = min(search_w, width - x)
    search_h = min(search_h, height - y)
    if search_w < body_w or search_h < body_h:
        return None
    template = str(Path(__file__).resolve().parent / 'page' / 'courtyard_scroll_expand.png')
    button = RuleImage(roi_front=(x, y, body_w, body_h),
                       roi_back=(x, y, search_w, search_h),
                       method='Template matching', threshold=0.85, file=template)
    if height < button.image.shape[0] or width < button.image.shape[1]:
        return None
    return button if task.appear(button) else None


def courtyard_visible(task) -> bool:
    """庭院可见：标志命中、菜单行完整，或卷轴处于收起态。

    收起态同样属于庭院（ESC 恢复要能认出它），判据与 collapsed_scroll_button 对齐：
    菜单入口消失但卷轴关闭标识出现。

    ⚠️ 町中画面必须在这里让位。町中沿用同一排底部菜单，实测 2026-10-06 真机截图
    （tests/fixtures/town_scene_201006.png）上庭院三个判据全部命中：
        I_MAIN_GOTO_SHIKIGAMI_RECORDS 0.947 / I_MAIN_GOTO_TEAM 0.934 / I_CHECK_MAIN 0.620
    而町中判据 I_CHECK_TOWN 0.958、7 个入口标签全中。若两者都返回 True，
    同优先级下 page_main 注册更早（default_pages.py:191 早于 :321）会抢到当前页面，
    导航器于是去走 page_main -> page_onmyodo，点底部菜单的「阴阳寮」按钮。
    该按钮在町中画面上其实**存在且可点**（I_MAIN_GOTO_ONMYODO 0.922，落在 (996,618)
    那本红色书卷上），但它的目的地 page_onmyodo 认不出来 ——
    I_CHECK_ONMYODO 在町中画面上只有 0.202（阈值 0.8），于是报
    `Transition cannot reach page_onmyodo` 与
    `Click state unchanged; repeat blocked: PAGE_MAIN_GOTO_ONMYODO`。

    语义上也该让位：町中判据比庭院判据更特异（町中画面必然同时可见底部菜单，
    反之不成立），两者冲突时町中是更具体的答案。判据冲突不是靠调优先级掩盖，
    而是在识别层就表达清楚。
    """
    if town_visible(task):
        return False
    if task.appear(GameUiAssets.I_CHECK_MAIN):
        return True
    if task.appear(GameUiAssets.I_MAIN_GOTO_SHIKIGAMI_RECORDS) and task.appear(GameUiAssets.I_MAIN_GOTO_TEAM):
        return True
    if task.appear(GameUiAssets.I_MAIN_GOTO_SHIKIGAMI_RECORDS):
        return True
    return task.appear(GameUiAssets.I_MAIN_SCROLL_CLOSE)


def expand_courtyard_scroll(task) -> bool:
    """Expand the collapsed courtyard scroll, retrying once before giving up.

    卷轴展开是动画过程，单帧未出现菜单不代表失败。实测 10-06 收起态连续 14 次
    触发本函数，每次都在第一次点击后 12 秒超时抛错、且没有第二次机会；
    这里把「点击—验证」做成最多两轮。

    两轮都失败时抛出带明确诊断信息的 CourtyardScrollStuckError：
    底层入口（式神录/阴阳寮等）此时必然持续不可见，把「庭院卷轴收起、需手动展开」
    直接暴露给日志，避免十几个任务各自报一遍 Navigation recovery limit exceeded
    而看不出共同原因。
    """
    generation = getattr(task.device, '_escape_generation', 0)
    for attempt in range(2):
        previous = None
        center = None
        for _ in range(2):
            task.screenshot()
            if getattr(task.device, '_escape_generation', 0) != generation:
                raise TaskRecoveryFailed('Courtyard scroll matching interrupted by recovery')
            button = collapsed_scroll_button(task)
            if button is None:
                # 菜单已经展开，本轮无需再点。
                return attempt > 0
            current = button.front_center()
            if previous is not None and (abs(current[0] - previous[0]) > 4
                                         or abs(current[1] - previous[1]) > 4):
                return False
            previous = current
            center = current
        x, y = center
        # The safe click area follows the latest match; no screen coordinate fallback.
        click = RuleClick(roi_front=(x - 4, y - 4, 8, 8), roi_back=tuple(button.roi_front),
                          name='courtyard_scroll_expand')
        if not task.click(click, interval=1):
            return False
        logger.info(f'Expand courtyard scroll by clicking scroll body at {center} (attempt {attempt + 1})')
        try:
            task._ui_verify(
                lambda: task.appear(GameUiAssets.I_MAIN_GOTO_SHIKIGAMI_RECORDS)
                and collapsed_scroll_button(task) is None,
                [click], timeout=12,
            )
        except TaskRecoveryFailed:
            if attempt == 1:
                raise CourtyardScrollStuckError(center) from None
            logger.info('Courtyard scroll did not settle after one click; retry once more')
            continue
        return True
    return False


def courtyard_menu_ready(task) -> bool:
    """底部菜单当前是否可直接点击（展开态）。

    供需要 page_main 底部入口的任务在开工前自检：收起态先展开，
    展开不了就不要再反复硬点，直接让调用方跳过本轮。
    """
    return not collapsed_scroll_button(task)


def ensure_courtyard_menu_visible(task) -> bool:
    """确保底部菜单处于展开态；展开不了返回 False，由调用方决定跳过。"""
    if courtyard_menu_ready(task):
        return True
    return expand_courtyard_scroll(task)

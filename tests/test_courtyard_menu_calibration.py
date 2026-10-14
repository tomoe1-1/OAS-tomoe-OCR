"""守卫：庭院底部菜单按钮必须能被搜到，且搜索余量不得被吃掉。

历史（这里踩过两次相反的错，都留下了记录）：

1. 2026-10-06 一版诊断脚本报告「底部菜单按钮实际位置比声明的 roi_front
   低 21~53px」，据此有人提议重新标定全部按钮坐标。**那个结论的归因是错的** ——
   该脚本手写 `cv2.matchTemplate(rule.corp(img), rule.image, ...)`，对同一份
   像素（src/mat 的 sum、shape、连续性完全一致，仅缓冲区地址不同）算出 -0.3308，
   而 OAS 真实路径 `RuleImage.template_match` 算出 0.9215。手搓路径确实不可信。

2. 但「偏移为 0、声明值准确、无需标定」这个结论**同样是错的**（本文件初版
   断言 dx=dy=0，两条用例长期报红）。2026-10-07 用真实路径在
   `courtyard_menu_drift_{a,b,c}.png`（2026-10-04 / 10-05 / 10-06，
   分属不同实例）上复测，**四颗按钮的偏移在三张夹具上完全一致**，
   且经 NMS 单峰确认与开裁图核对身份，排除了「夹具选错图」与「多峰取错参照物」：

       I_MAIN_GOTO_ONMYODO          声明 (960,590)  实测 (996,618)  偏移 (+36,+28)
       I_MAIN_GOTO_FRIENDS          声明 (845,590)  实测 (886,624)  偏移 (+41,+34)
       I_MAIN_GOTO_SHIKIGAMI_RECORDS 声明 (1080,590) 实测 (1098,611) 偏移 (+18,+21)
       I_MAIN_GOTO_TEAM             声明 (395,590)  实测 (436,642)  偏移 (+41,+52)

   ⇒ **assets.py 的 roi_front 起始值普遍偏低 20~50px，这是稳定事实。**

现状与取舍（2026-10-07 大哥拍板走「承认偏移」路线）：
偏移本身**不影响点击** —— roi_back 有 120x100 余量，四颗按钮都能搜到。
但余量并不宽裕，最紧的是 I_MAIN_GOTO_TEAM，命中框下沿正好压在 roi_back 边界上
（剩余 0px），FRIENDS 也只剩 11px。所以守卫改为盯住**余量**而不是「零偏移」：
只要余量还在基准之上就放过，一旦 UI 改版把偏移继续推大、或有人把 roi_back
改小，就明确报红。

本测试要守住的是：
1. 菜单按钮在真庭院上必须全部命中（守住「不误判」：搜不到时导航器会以为庭院不可用，
   而 page_main 可能靠其他模板侥幸通过，故障会飘到别处）；
2. 偏移不得超过 MEASURED_OFFSET 的容差范围，且
3. 命中框必须完整落在 roi_back 内，且余量不低于 MIN_BACK_SLACK
   —— 这两条才是真正的安全边界；
4. roi_front 是运行时可变状态（每次匹配成功就被 _update_roi_front 覆写），
   所以基准必须取自 assets.py 的**字面量**，手改 assets.py 初值或在运行时读取
   都是自证循环，见 MEASURED_OFFSET 上方说明；
5. 町中画面上菜单同样可见 —— 若将来有人把町中误判回庭院，页面归属层
   （tests/test_town_page_priority.py）应先暴露问题，而不是让本文件去兜。
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assets_literal  # noqa: E402  与本测试同目录，tests/ 不是 package

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

# 三张真庭院帧：2026-10-04/ 10-05 / 10-06，不同实例、不同日期。
# 筛选条件见文件末尾的 Selection 记录。
COURTYARD_FIXTURES = [
    'courtyard_menu_drift_a.png',
    'courtyard_menu_drift_b.png',
    'courtyard_menu_drift_c.png',
]

# 四颗按钮与它们在 assets.py 里声明的 roi_front 起始位置。
# 声明值取自 tasks/GameUi/assets.py（SHIKIGAMI_RECORDS:168, ONMYODO:170,
# FRIENDS:172, TEAM:180），断言时与 assets.py 的字面量比对，不在本文件硬编码坐标。
MENU_BUTTONS = [
    'I_MAIN_GOTO_ONMYODO',
    'I_MAIN_GOTO_FRIENDS',
    'I_MAIN_GOTO_SHIKIGAMI_RECORDS',
    'I_MAIN_GOTO_TEAM',
]

# 2026-10-07 用 OAS 真实路径（RuleImage.template_match）在上述三张夹具上实测的
# 偏移基线（实测落点 - assets.py 声明值）。三张夹具分属不同日期与实例，
# 数值完全一致，故视为稳定基线。
#
# ⚠️ 这不是「标定值」，而是「已知偏移」：assets.py 的 roi_front 初值不改，
# 让 roi_back 的搜索余量去吸收它。这样菜单仍能搜到，而一旦偏移继续变大，
# 下面的容差与余量断言会先报红。
MEASURED_OFFSET = {
    'I_MAIN_GOTO_ONMYODO': (36, 28),
    'I_MAIN_GOTO_FRIENDS': (41, 34),
    'I_MAIN_GOTO_SHIKIGAMI_RECORDS': (18, 21),
    'I_MAIN_GOTO_TEAM': (41, 52),
}

# 偏移允许在基线之上再漂多少像素。
# 取 8px（约按钮宽度的 1/5）：足以吸收菜单自身的小幅抖动，
# 又足够灵敏 —— 任何系统性挪位（改版、资源换图）都会突破它。
OFFSET_TOLERANCE = 8

# 命中框四边到 roi_back 边界必须保留的最小余量（像素）。
# 最紧的 TEAM 实测下沿余量已经是 0，所以这个下限只作回归哨兵：
# 一旦报红，说明余量被吃光，模板再挪一点就会搜不到。
MIN_BACK_SLACK = 0


def _load(name):
    """读夹具为 RGB。

    必须用 imdecode + BGR2RGB，与 module/atom/image.py 的 load_image 一致；
    通道顺序错了所有分数都会失真（见类注释）。
    """
    import cv2
    from numpy import uint8, fromfile
    path = FIXTURES / name
    if not path.exists():
        return None
    image = cv2.imdecode(fromfile(str(path), dtype=uint8), -1)
    if image is None:
        return None
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class _RealTask:
    """驱动真实 RuleImage 匹配。

    ⚠️ 必须走 RuleImage.template_match()，不要手搓
    cv2.matchTemplate(rule.corp(img), rule.image, ...)。后者在本机会对
    同一份数据给出 -0.3308（真实路径是 0.9215），曾导致一版错误的
    「菜单坐标漂移 21~53px」结论。本类只暴露 OAS 自己的路径。
    """

    def __init__(self, image):
        self.device = type('Device', (), {'image': image})()

    def appear(self, rule, **kwargs):
        return rule.template_match(self.device.image)


class MenuButtonCalibrationTest(unittest.TestCase):
    """菜单按钮必须命中，且命中点等于声明位置。"""

    def _each_fixture(self):
        from tasks.GameUi.assets import GameUiAssets
        for name in COURTYARD_FIXTURES:
            image = _load(name)
            self.assertIsNotNone(image, f'缺少真机夹具或无法解码: {name}')
            self.assertEqual(image.shape[:2], (720, 1280),
                             f'{name} 应为 1280x720 原始截图，实际 {image.shape}')
            # ⚠️ 基准必须来自 assets.py 的**字面量**，不能读运行时对象的
            # roi_front：RuleImage._update_roi_front 会在每次匹配成功时把它
            # 改写成实测位置，拿它当基准等于自证循环 —— 无论声明写成什么，
            # 匹配后两者都相等，守卫会永远绿。已实测：把 ONMYODO 声明改成
            # (990,610) 后，用运行时对象做基准的测试仍然 OK，完全守不住。
            declared = {n: assets_literal.declared_roi_front(n) for n in MENU_BUTTONS}
            yield name, _RealTask(image), declared

    def test_fixtures_are_pure_courtyard(self):
        """夹具前提：判为庭院、且不是町中。

        若这条红了，说明夹具本身失效（游戏改版或图选错了），
        后面所有偏移断言都失去意义。
        """
        from tasks.GameUi.courtyard import courtyard_visible
        from tasks.GameUi.town import town_visible
        for name, task, _ in self._each_fixture():
            self.assertTrue(courtyard_visible(task), f'{name} 应识别为庭院')
            self.assertFalse(town_visible(task), f'{name} 不该被识别为町中')

    def test_all_menu_buttons_hit(self):
        """四颗按钮在真庭院上必须全部命中。

        这条守住「不误判」：按钮搜不到时导航器会以为庭院不可用，
        而 page_main 判据本身还可能靠其他模板侥幸通过，故障会飘到别处。
        """
        from tasks.GameUi.assets import GameUiAssets
        for name, task, _ in self._each_fixture():
            for button in MENU_BUTTONS:
                rule = getattr(GameUiAssets, button)
                # 用声明阈值，不放宽 —— 放宽会把漂移掩盖成「还能搜到」。
                self.assertTrue(task.appear(rule),
                                f'{name} 上 {button} 未命中（声明阈值 {rule.threshold}）')

    def test_offset_stays_near_measured_baseline(self):
        """核心断言：偏移必须落在实测基线的容差范围内。

        这条取代了初版「命中点必须等于声明位置（dx=dy=0）」的写法 ——
        那个前提本身是错的，实测四颗按钮都稳定偏低 20~50px（见 MEASURED_OFFSET）。

        它守的是「偏移不再变大」：若游戏改版让菜单挪位，这里会明确报红
        并打印真实偏移，而不是让人靠肉眼看图猜。零偏移并不是正确期望，
        「偏移不超出已知范围」才是。
        """
        from tasks.GameUi.assets import GameUiAssets
        for name, task, declared in self._each_fixture():
            for button in MENU_BUTTONS:
                rule = getattr(GameUiAssets, button)
                self.assertTrue(task.appear(rule), f'{name} 上 {button} 未命中，无法比对偏移')
                x, y, w, h = rule.roi_front
                dx, dy = x - declared[button][0], y - declared[button][1]
                base = MEASURED_OFFSET[button]
                drift_x, drift_y = dx - base[0], dy - base[1]
                self.assertLessEqual(
                    abs(drift_x), OFFSET_TOLERANCE,
                    f'{name} 上 {button} x 偏移 {dx} 偏离基线 {base[0]} 达 {drift_x}px'
                    f'（容差 {OFFSET_TOLERANCE}）。实测 ({x},{y}) vs 声明 {declared[button][:2]}。'
                    f'若游戏确实改了 UI，请连同 assets.py 的 roi_front/roi_back 与 '
                    f'MEASURED_OFFSET 一起更新，并同步替换夹具；不要只改一边。')
                self.assertLessEqual(
                    abs(drift_y), OFFSET_TOLERANCE,
                    f'{name} 上 {button} y 偏移 {dy} 偏离基线 {base[1]} 达 {drift_y}px'
                    f'（容差 {OFFSET_TOLERANCE}）。实测 ({x},{y}) vs 声明 {declared[button][:2]}。'
                    f'若游戏确实改了 UI，请连同 assets.py 的 roi_front/roi_back 与 '
                    f'MEASURED_OFFSET 一起更新，并同步替换夹具；不要只改一边。')

    def test_offsets_stay_small_even_if_declared_is_moved(self):
        """冗余保险：无论声明值被改成什么，偏移都必须还在已知范围内。

        作用是捕捉「有人把 roi_front 手改成另一个数」这种误操作 ——
        那种改动会让上一条测试的偏差值变成巨大数字。这里独立断言实际偏移
        不超过基线 + 容差，语义明确：偏移是已知的、可控的，不是随意的。
        """
        from tasks.GameUi.assets import GameUiAssets
        for name, task, declared in self._each_fixture():
            for button in MENU_BUTTONS:
                rule = getattr(GameUiAssets, button)
                if not task.appear(rule):
                    continue
                x, y, w, h = rule.roi_front
                base = MEASURED_OFFSET[button]
                dx = abs((x - declared[button][0]) - base[0])
                dy = abs((y - declared[button][1]) - base[1])
                self.assertLessEqual(
                    max(dx, dy), OFFSET_TOLERANCE,
                    f'{name} 上 {button} 偏移偏离基线 ({base[0]},{base[1]}) 达 ({dx},{dy})，'
                    f'疑似声明值被手改，或菜单发生了真实挪位')


class RoiFrontIsRuntimeStateTest(unittest.TestCase):
    """roi_front 是运行时可变状态 —— 这是「手改初值无效」的根据。"""

    def test_successful_match_rewrites_roi_front(self):
        """匹配成功后 _update_roi_front 会把 roi_front 改成实测位置。"""
        import inspect
        from module.atom.image import RuleImage
        source = inspect.getsource(RuleImage._update_roi_front)
        self.assertIn('self.roi_front[0] = loc[0]', source,
                      '_update_roi_front 应写入 roi_front[0]')
        self.assertIn('self.roi_front[1] = loc[1]', source,
                      '_update_roi_front 应写入 roi_front[1]')

    def test_template_match_overwrites_front(self):
        """真机夹具上跑一次 template_match，roi_front 应被改写成命中点。"""
        from tasks.GameUi.assets import GameUiAssets
        image = _load(COURTYARD_FIXTURES[0])
        self.assertIsNotNone(image)
        rule = GameUiAssets.I_MAIN_GOTO_ONMYODO
        # 先污染 roi_front，确认匹配确实会把它纠正回来。
        rule.roi_front = [1, 2, 39, 36]
        self.assertTrue(rule.template_match(image))
        self.assertNotEqual(tuple(rule.roi_front)[:2], (1, 2),
                            'template_match 成功却没有更新 roi_front —— '
                            '若真是这样，手改 assets.py 初值反而会生效，'
                            '本文件的前提需要重新评估')
        # 恢复原值，避免污染其他测试。
        rule.roi_front = [960, 590, 39, 36]

    def test_search_region_covers_measured_position(self):
        """roi_back 的搜索余量必须完整覆盖实测命中框。

        这是本文件真正的安全边界。实测余量并不宽裕：
            ONMYODO          右侧 45px / 下侧 36px
            FRIENDS          右侧 24px / 下侧 11px
            SHIKIGAMI_RECORDS 右侧 46px / 下侧 15px
            TEAM             右侧 41px / 下侧  0px  ← 正好压在边界上

        TEAM 下沿余量为 0 意味着「再多挪一点就搜不到」。这条断言把每条边
        的余量都显式断言一遍并打印出来，让余量变化能报红，而不是等某天
        按钮彻底搜不到、故障飘到「庭院不可用」这种无关现场才被发现。
        """
        from tasks.GameUi.assets import GameUiAssets
        for name, task, declared in MenuButtonCalibrationTest()._each_fixture():
            for button in MENU_BUTTONS:
                rule = getattr(GameUiAssets, button)
                self.assertTrue(task.appear(rule), f'{name}/{button} 未命中')
                x, y, w, h = rule.roi_front
                bx, by, bw, bh = rule.roi_back
                slacks = {
                    'left': x - bx,
                    'top': y - by,
                    'right': (bx + bw) - (x + w),
                    'bottom': (by + bh) - (y + h),
                }
                detail = ', '.join(f'{k}={v}px' for k, v in slacks.items())
                for edge, slack in slacks.items():
                    self.assertGreaterEqual(
                        slack, MIN_BACK_SLACK,
                        f'{name}/{button} 命中框超出 roi_back 的 {edge} 边'
                        f'（余量 {detail}）。rois_back={rule.roi_back} 命中=({x},{y},{w},{h})。'
                        f'偏移被吃掉 ⇒ 模板再挪一点就搜不到，导航会误判庭院不可用。')


# ---------------------------------------------------------------------------
# Fixture selection record (2026-10-06, reproducible):
#   扫 log/error 全部 1280x720 真机截图 → 判为纯庭院（courtyard_visible 且
#   非 town_visible）96 帧 → 四颗按钮全中 69 帧，横跨 2026-09-30 ~ 10-06
#   共 7 天、多个实例。
#   注：I_CHECK_MAIN（阈值 0.95）在全部 96 帧上都不命中，故筛选用
#   「四按钮全中 + 判为庭院」为准，不用 I_CHECK_MAIN 把关。
#   选中三张：
#     a  2026-10-06 18-44-58  tomoe1_1791283499130
#     b  2026-10-05 12-33-10  tomoe1_1791174790817
#     c  2026-10-04 00-08-02  tomoe1_1791043683010
#
# ⚠️ 2026-10-07 更正：本记录初版写的是「其中 dx=dy=0 的 69 帧」，并据此
# 断定「声明值准确、无需标定」。那个筛选口径本身就是错的 —— 它多半是在
# 测量环节把实测落点写回了 roi_front 再比对，等于自证循环（同 MEASURED_OFFSET
# 上方说明：用运行时 roi_front 当基准，匹配后两者必然相等）。
# 用真实路径复测后，这三张夹具上的偏移是 (+36,+28)/(+41,+34)/(+18,+21)/(+41,+52)，
# 三帧完全一致，已写入 MEASURED_OFFSET 作为基线。
# ---------------------------------------------------------------------------


if __name__ == '__main__':
    unittest.main()

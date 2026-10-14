"""守卫：I_CHECK_MAIN 的搜索区必须容得下命中框，否则庭院判据恒假。

故障（2026-10-10，另一台电脑报的障，日志 ./log/error/oas3_1791640286603）：
游戏早就进了庭院，脚本却在 `Restart -> app_handle_login` 的 while 循环里
空转 60 秒，最后 `Wait too long: set()` → ESC 恢复 → BACK 在庭院里无效
→ `TaskRecoveryFailed: ESC recovery did not identify a page within 60s`。

根因不是「别的电脑分辨率不一样」，而是本仓库自己的判据在这台机器上恒假：

    I_CHECK_MAIN roi_front=(807,108,76,45)   模板 45px 高
                   roi_back =(49,98,1033,61)  搜索区只到 y=159

真实路径（RuleImage.template_match）实测落点是 y=117，即命中框占 y 117..162，
下沿被 roi_back 裁掉 3 行 → 0.9934 削成 0.9308，跨不过 0.95 阈值。
`courtyard_visible()` 于是永远 False，`_app_handle_login` 里
`I_MAIN_GOTO_SHIKIGAMI_RECORDS` 的确认分支永远进不去。

⚠️ 本文件所有数字都来自真实路径。手搓 `cv2.matchTemplate(rule.corp(img), ...)`
在本机会对同一份像素给出 -0.3308（真实路径 0.9215），曾导致过一版错误的
「菜单坐标漂移 21~53px」结论，见 tests/test_courtyard_menu_calibration.py 类注释。
复现识图一律走 `rule.template_match(image)`。

夹具来源（三张都必须留着，缺一张就失去对照意义）：
  courtyard_otherpc_20261010.jpg  另一台电脑的 1280x720 真机帧，
                                   底部菜单整条未渲染，是故障现场本身
  courtyard_menu_drift_{a,b,c}    本机三张健康庭院帧
判定标准：真机帧与健康帧都要判为庭院，町中与卷轴收起帧都不能判为庭院。
"""
import copy
import sys
import unittest
from pathlib import Path

import cv2
from numpy import fromfile, uint8

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assets_literal  # noqa: E402  与本测试同目录，tests/ 不是 package

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

# 故障现场：另一台电脑 1280x720，底部菜单整条没画出来。
OTHER_PC = 'courtyard_otherpc_20261010.jpg'
# 本机健康庭院帧（2026-10-04 / 10-05 / 10-06，不同实例）。
HEALTHY = [
    'courtyard_menu_drift_a.png',
    'courtyard_menu_drift_b.png',
    'courtyard_menu_drift_c.png',
]
# 反例：町中画面必须让位给庭院判据。
TOWN = [
    'town_scene_201006.png',
    'town_scene_201006_b.png',
]

# roi_back 声明高度下限。实测命中框下沿在 y=162，搜索区必须至少到那里。
MIN_BACK_BOTTOM = 163


def _load(name):
    """读夹具为 RGB，与 module/atom/image.py 的 load_image 一致。"""
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
    """驱动真实 RuleImage 匹配，只暴露 OAS 自己的路径。"""

    def __init__(self, image):
        self.device = type('Device', (), {'image': image})()

    def appear(self, rule, **kwargs):
        return rule.template_match(self.device.image)


class CheckMainSearchWindowTest(unittest.TestCase):
    """I_CHECK_MAIN 的搜索区必须装得下命中框。"""

    def test_declared_search_window_contains_hit_box(self):
        """roi_back 的下沿必须低于实测命中框下沿。

        这是本故障的直接守卫：高度退回 61 就会把 3 行裁掉，
        0.9934 掉到 0.9308，庭院判据在所有机器上恒假。
        """
        from tasks.GameUi.assets import GameUiAssets
        rule = GameUiAssets.I_CHECK_MAIN
        _x, y, _w, h = rule.roi_back
        bottom = y + h
        self.assertGreaterEqual(
            bottom, MIN_BACK_BOTTOM,
            f'I_CHECK_MAIN 的 roi_back 下沿只有 {bottom}，实测命中框下沿 {MIN_BACK_BOTTOM}，'
            f'多出来的部分会被裁掉并拉低匹配分数。'
            f'当前声明 roi_back={rule.roi_back}，命中框实测占 y 117..{117 + rule.image.shape[0]}。'
            f'恢复下沿只会让庭院判据在真机上恒假（见本文件 docstring 的故障记录）。')

    def test_search_window_height_matches_literal(self):
        """基准取自 assets.py 字面量，不读运行时对象。

        `_update_roi_front` 会在每次匹配成功后改写 roi_front/roi_back，
        读运行时对象做基准等于自证循环 —— 无论声明写成什么，
        匹配后两者都相等，守卫会永远绿。
        """
        declared = assets_literal.declared_roi_back('I_CHECK_MAIN')
        self.assertGreaterEqual(
            declared[1] + declared[3], MIN_BACK_BOTTOM,
            f'assets.py 里 I_CHECK_MAIN 的 roi_back 字面量 {declared} 下沿不足 {MIN_BACK_BOTTOM}，'
            f'会裁掉命中框。')

    def test_real_courtyard_frames_hit(self):
        """真机帧与健康帧都必须命中（不放宽阈值）。

        对方那台电脑的帧是关键样本：它的底部菜单整条没渲染，
        四颗菜单按钮全落空（实测 0.13~0.51），I_CHECK_MAIN 是唯一判据。
        """
        from tasks.GameUi.assets import GameUiAssets
        rule = GameUiAssets.I_CHECK_MAIN
        for name in [OTHER_PC] + HEALTHY:
            image = _load(name)
            self.assertIsNotNone(image, f'缺少真机夹具或无法解码: {name}')
            self.assertEqual(image.shape[:2], (720, 1280),
                             f'{name} 应为 1280x720 原始截图，实际 {image.shape}')
            task = _RealTask(image)
            self.assertTrue(
                task.appear(rule),
                f'{name} 上 I_CHECK_MAIN 未命中（声明阈值 {rule.threshold}）。'
                f'庭院判据会因此恒假，登录循环空转到卡死。')

    def test_search_window_still_rejects_non_courtyard(self):
        """放宽搜索区不得顺带放进町中 —— 这条守「不误判」。

        判据冲突必须靠特异性解决，不能靠调搜索区把两个答案都收进来。
        """
        from tasks.GameUi.assets import GameUiAssets
        rule = GameUiAssets.I_CHECK_MAIN
        for name in TOWN:
            image = _load(name)
            self.assertIsNotNone(image, f'缺少町中夹具: {name}')
            task = _RealTask(image)
            self.assertFalse(
                task.appear(rule),
                f'{name}（町中画面）上 I_CHECK_MAIN 命中了，'
                f'放宽搜索区把町中误认成庭院。')

    def test_courtyard_visible_end_to_end(self):
        """整条判据：真机帧判为庭院，町中判为町中。

        只测单个模板不够 —— 模板命中不等于 courtyard_visible() 返回 True，
        町中让位、卷轴收起等分支任一失灵都会让登录循环重新卡死。
        """
        from tasks.GameUi.courtyard import courtyard_visible
        from tasks.GameUi.town import town_visible

        for name in [OTHER_PC] + HEALTHY:
            image = _load(name)
            self.assertIsNotNone(image, f'缺少夹具: {name}')
            task = _RealTask(image)
            self.assertFalse(town_visible(task), f'{name} 不该被识别为町中')
            self.assertTrue(courtyard_visible(task), f'{name} 应识别为庭院')

        for name in TOWN:
            image = _load(name)
            task = _RealTask(image)
            self.assertTrue(town_visible(task), f'{name} 应识别为町中')
            self.assertFalse(courtyard_visible(task),
                             f'{name}（町中）被判成庭院，页面归属会点错按钮。')


class LoginLoopUsesCourtyardTest(unittest.TestCase):
    """登录循环必须能被庭院判据唤醒 —— 守卫接线点，不只守单个模板。"""

    def test_login_loop_checks_courtyard_before_enter_game(self):
        """`_app_handle_login` 必须在「式神录确认并 break」之前先认庭院。

        只测抽出来的纯函数守不住「到底调没调」，所以这里读源码 AST。

        断言的是**行号顺序**：`courtyard_visible(...)` 所在行必须早于
        那个 `if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, ...): break`
        所在行。理由是本次故障的机制 —— 庭院判据为假时登录循环没有任何
        能醒过来的分支，只能空转到 60s 卡死。

        ⚠️ 这里刻意不比「第一个 I_MAIN_GOTO_SHIKIGAMI_RECORDS 引用」：
        判据层那句 `courtyard_visible(self) and not self.appear(
        self.I_MAIN_GOTO_SHIKIGAMI_RECORDS)` 与 courtyard_visible 同行，
        按出现顺序排会得到「庭院的判断依据反而排在庭院判据之后」的假红。
        要比的是**能结束循环的那个分支**，不是任何一次引用。
        """
        import ast

        path = Path(__file__).resolve().parent.parent / 'tasks' / 'Component' / 'Login' / 'service.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_app_handle_login':
                fn = node
                break
        self.assertIsNotNone(fn, '找不到 _app_handle_login，接线守卫失效')

        yard_lines = [n.lineno for n in ast.walk(fn)
                      if isinstance(n, ast.Call) and 'courtyard_visible' in ast.dump(n.func)]
        self.assertTrue(yard_lines,
                        '_app_handle_login 里没有调用 courtyard_visible，'
                        '庭院判据一旦为假就会空转到卡死')

        # 找到「确认式神录后 break」的那个 If：其 test 命中 RECORDS，
        # 且 orelse 为空、body 里含 break。
        confirm_lines = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            if not any(isinstance(x, ast.Attribute) and x.attr == 'I_MAIN_GOTO_SHIKIGAMI_RECORDS'
                       for x in ast.walk(node.test)):
                continue
            has_break = any(isinstance(x, ast.Break)
                            for stmt in node.body for x in ast.walk(stmt))
            if has_break:
                confirm_lines.append(node.lineno)
        self.assertTrue(confirm_lines,
                        '找不到「式神录出现即 break」的确认分支，测试前提已变；'
                        '若登录循环改用别的收敛条件，这里要跟着改。')

        self.assertLess(min(yard_lines), min(confirm_lines),
                        f'庭院判据在第 {min(yard_lines)} 行，'
                        f'而结束循环的确认分支在第 {min(confirm_lines)} 行 —— '
                        f'庭院判据必须在前，否则确认分支永远等不到庭院帧。')


if __name__ == '__main__':
    unittest.main()
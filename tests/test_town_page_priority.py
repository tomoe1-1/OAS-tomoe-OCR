"""町中画面必须被认成 page_town，而不是被 page_main 抢走。

背景：2026-10-06 20:12 两个实例启动 Duel 连续失败，日志
（log/error/tomoe2_1791288782561/log.txt）给出的时序是：

    20:12:44  [UI] page_main          <- 起始画面其实是町中，判错了
    20:12:45  GOTO PAGE_ONMYODO       <- 走 page_main -> page_onmyodo
    20:12:52  [UI] page_duel          <- 斗技居然进成了
    20:12:53  [UI] page_main          <- 又被判回庭院
    20:13:02  TaskRecoveryFailed: Click state unchanged;
               repeat blocked: PAGE_MAIN_GOTO_ONMYODO

根因是町中沿用同一排底部菜单，庭院判据在町中画面上全部命中
（式神录 0.947 / 组队 0.934 / 庭院标志 0.620），而 page_main 与 page_town
同优先级、page_main 注册更早，于是当前页面被判成庭院。导航器接着走
page_main -> page_onmyodo 去点「阴阳寮」按钮 —— 该按钮在町中画面上确实存在
（0.922，落在那本红色书卷上），但目的地 page_onmyodo 认不出来
（I_CHECK_ONMYODO 仅 0.202，阈值 0.8），所以点完到不了目标页，
最终报 Click state unchanged; repeat blocked: PAGE_MAIN_GOTO_ONMYODO。

本测试用真机截图锁死行为：町中 -> page_town，且真庭院不受影响。
反向验证方式见提交说明（把两处改动回退，本测试必须报红）。
"""
from pathlib import Path
import unittest

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
TOWN_FIXTURES = [
    # 2026-10-06 20:13:02 tomoe2 真实报错截图（Duel 启动失败现场）
    'town_scene_201006.png',
    # 同一现场 18 秒后的第二张，两张独立帧可排除偶发
    'town_scene_201006_b.png',
]


def _load(name):
    """读夹具为 RGB。测试必须用 imdecode+BGR2RGB，与 module/atom/image.py 一致。

    通道顺序不一致会让所有模板匹配分数失真（见记忆：验证脚本本身要测）。
    """
    import cv2
    from numpy import uint8, fromfile
    path = FIXTURES / name
    if not path.exists():
        return None
    image = cv2.imdecode(fromfile(str(path), dtype=uint8), -1)
    if image is None:
        return None
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class _RealImageTask:
    """用真机截图驱动真实 RuleImage，不 mock appear()。

    刻意不用 Mock 抄近路：mock 掉 appear() 会跳过 matchTemplate，
    分数与阈值关系全部丢失，守不住真正的回归。
    """

    def __init__(self, image):
        import numpy as np
        self.device = type('Device', (), {'image': image, 'shape': image.shape})()
        self._image = image
        self._np = np

    def appear(self, rule, **kwargs):
        source = rule.corp(self._image)
        mat = rule.image
        if source.shape[0] < mat.shape[0] or source.shape[1] < mat.shape[1]:
            return False
        import cv2
        res = cv2.matchTemplate(source, mat, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(res)[1]) > rule.threshold

    def score(self, rule):
        """返回最高匹配分数，供测试直接断言分数与阈值的间距。"""
        import cv2
        source = rule.corp(self._image)
        mat = rule.image
        if source.shape[0] < mat.shape[0] or source.shape[1] < mat.shape[1]:
            return -1.0
        res = cv2.matchTemplate(source, mat, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(res)[1])


class TownBeatsCourtyardTest(unittest.TestCase):
    """町中画面：判据层与排序层都要判给 page_town。"""

    def _each_fixture(self):
        for name in TOWN_FIXTURES:
            image = _load(name)
            self.assertIsNotNone(image, f'缺少真机夹具或无法解码: {name}')
            self.assertEqual(image.shape[:2], (720, 1280),
                             f'{name} 应为 1280x720 原始截图，实际 {image.shape}')
            yield name, _RealImageTask(image)

    def test_town_visible_on_real_screenshots(self):
        """真机町中截图必须命中町中判据。"""
        from tasks.GameUi.town import town_visible
        for name, task in self._each_fixture():
            self.assertTrue(town_visible(task), f'{name} 应识别为町中')

    def test_courtyard_visible_yields_to_town(self):
        """核心回归：町中画面不得再被判为庭院。

        这条断言直接对应「20:12:44 [UI] page_main」那次误判。
        """
        from tasks.GameUi.courtyard import courtyard_visible
        for name, task in self._each_fixture():
            self.assertFalse(courtyard_visible(task),
                             f'{name} 是町中，courtyard_visible 必须让位，否则又会被判成 page_main')

    def test_town_priority_beats_courtyard(self):
        """排序层兜底：page_town.priority 必须严格高于 page_main。

        判据层已经让位，但同优先级下 page_main 注册更早（default_pages.py:191
        早于 :321）仍会抢到 —— 这层断言保证排序结论不依赖注册顺序。
        """
        from tasks.GameUi.page import page_main, page_town
        self.assertGreater(page_town.priority, page_main.priority,
                           'page_town 优先级必须高于 page_main，否则注册顺序会决定归属')

    def test_town_courtyard_button_present_but_leads_nowhere(self):
        """锁住报错点的真实原因：按钮在町中**存在**，但点它到不了 page_onmyodo。

        ⚠️ 修正一个曾经的错误判断：早期以为「阴阳寮」按钮在町中画面上不存在
        （误把 I_MAIN_GOTO_TOWN 0.298 当成它）。实测该按钮在町中底部菜单上
        命中 0.92，单峰、落点 (996,618) 正是那本红色书卷 —— 按钮完全可见可点。
        真正的问题是它的目的地：page_onmyodo 的标志 I_CHECK_ONMYODO 在町中
        画面上只有 0.202（阈值 0.8），所以点进去也认不出阴阳寮页，
        表现为 changed=False / Transition cannot reach page_onmyodo。

        顺带澄清两个曾被写错的说法（2026-10-07 用真实路径复测后更正）：
        - 「町中按钮位置比声明值低 21~53px」的**归因**是错的：那是诊断脚本手搓
          matchTemplate 的伪影（同一份数据 OAS 真实路径给 0.9215，手搓给
          -0.3308）。但**偏移本身确实存在** —— 真庭院上四颗按钮同样稳定偏低
          20~50px（ONMYODO +36+28 / TEAM +41+52 等），详见
          tests/test_courtyard_menu_calibration.py 的 MEASURED_OFFSET。
          即：错的是「不该有偏移」，不是「有偏移」。
        - roi_front 是运行时可变状态，RuleImage._update_roi_front 会在每次
          匹配成功时覆写它为实测位置（日志 Click (1014,644) 即由此而来），
          手动改 assets.py 里的初值没有意义；偏移由 roi_back 的余量吸收。
        """
        from tasks.GameUi.assets import GameUiAssets
        for name, task in self._each_fixture():
            self.assertTrue(task.appear(GameUiAssets.I_MAIN_GOTO_ONMYODO),
                            f'{name} 上阴阳寮按钮本应存在（底部菜单可见）')
            self.assertLess(task.score(GameUiAssets.I_CHECK_ONMYODO),
                            GameUiAssets.I_CHECK_ONMYODO.threshold,
                            f'{name} 不是阴阳寮页，I_CHECK_ONMYODO 不该命中；'
                            '若命中说明这张图不是町中')

    def test_town_to_courtyard_edge_is_available(self):
        """町中回庭院的路必须存在且可用（点「庭院」牌坊）。

        这条边本来就有（default_pages.py:322 I_TOWN_GOTO_MAIN），此前从未被走到，
        因为页面归属判错。修好归属后它就是「回庭院」的主路径。
        """
        from tasks.GameUi.page import page_main, page_town
        for name, task in self._each_fixture():
            self.assertTrue(task.appear(GameUiAssets_for_test().I_TOWN_GOTO_MAIN),
                            f'{name} 上「庭院」牌坊应可见，否则无法从町中回庭院')
        self.assertTrue(
            any(t.destination.key == page_main.key for t in page_town.transitions),
            'page_town 必须有一条回 page_main 的边')

    def test_town_to_duel_path_exists(self):
        """斗技入口在町中页面上，且有直达 page_duel 的边。"""
        from tasks.GameUi.page import page_duel, page_town
        for name, task in self._each_fixture():
            self.assertTrue(task.appear(GameUiAssets_for_test().I_TOWN_GOTO_DUEL),
                            f'{name} 上斗技入口应可见')
        self.assertTrue(
            any(t.destination.key == page_duel.key for t in page_town.transitions),
            'page_town 必须有直达 page_duel 的边')


def GameUiAssets_for_test():
    from tasks.GameUi.assets import GameUiAssets
    return GameUiAssets


class CourtyardUnaffectedTest(unittest.TestCase):
    """反向验证：真庭院不能因为这次改动被误判走。

    用町中夹具无法证明这一点 —— 它本来就是町中。这里改用纯逻辑守卫：
    判据让位只在「町中判据成立」时发生，其余分支行为必须与改动前一致。
    """

    def _task(self, hits):
        from unittest.mock import Mock
        task = Mock()
        task.appear.side_effect = lambda rule: hits.get(str(rule), False)
        return task

    def test_town_absent_keeps_courtyard_true(self):
        """町中判据不成立时，庭院照旧识别（判据让位不能连累真庭院）。"""
        from tasks.GameUi.courtyard import courtyard_visible
        task = self._task({
            'PAGE_CHECK_MAIN': True,
            'PAGE_MAIN_GOTO_SHIKIGAMI_RECORDS': True,
            'PAGE_MAIN_GOTO_TEAM': True,
        })
        self.assertTrue(courtyard_visible(task))

    def test_town_present_turns_courtyard_off(self):
        """町中判据成立时，庭院必须让位（本次修复的目标行为）。"""
        from tasks.GameUi.courtyard import courtyard_visible
        from tasks.GameUi.town import TOWN_ENTRANCES
        hits = {'PAGE_CHECK_MAIN': True, 'PAGE_MAIN_GOTO_SHIKIGAMI_RECORDS': True,
                'PAGE_MAIN_GOTO_TEAM': True}
        for entrance in TOWN_ENTRANCES:
            hits[str(entrance)] = True
        task = self._task(hits)
        self.assertFalse(courtyard_visible(task))

    def test_collapsed_scroll_state_still_counts_as_courtyard(self):
        """卷轴收起态仍属于庭院 —— ESC 恢复依赖它，不能被町中让位逻辑误伤。"""
        from tasks.GameUi.courtyard import courtyard_visible
        task = self._task({'PAGE_MAIN_SCROLL_CLOSE': True})
        self.assertTrue(courtyard_visible(task))

    def test_neither_marker_is_not_courtyard(self):
        """两者都不成立时不得判为庭院。"""
        from tasks.GameUi.courtyard import courtyard_visible
        task = self._task({})
        self.assertFalse(courtyard_visible(task))


if __name__ == '__main__':
    unittest.main()

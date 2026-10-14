"""庭院卷轴收起态必须能被识别，并且展开动作可以重试而不是一次就放弃。

背景：2026-10-06 两个实例在 09:53-10:09 连续失败 14 次。根因是游戏把庭院卷轴收起，
底部菜单整排入口不可见，`page_main -> page_guild` 的点击落空。恢复逻辑
`expand_courtyard_scroll()` 当时在第一次点击后固定等 12 秒就抛错，且它依赖的
`goto_exploration` 判据在收起态只有 0.77~0.84、横跨自身阈值 0.8，会随机短路。
"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import TaskRecoveryFailed
from tasks.GameUi import courtyard


def _task(image_shape=(720, 1280, 3), shikigami=True, scroll_close=False,
          exploration=True, arrow=True, click_ok=True):
    """构造一个只关心 appear() 命中集合的最小 task 替身。

    注意 GameUiAssets.* 与 courtyard 内部临时 new 出来的 button 都是 RuleImage
    实例，appear() 收到的都是对象；只能用 str(rule)（模板文件名大写）区分。
    """
    task = Mock()
    task.device = SimpleNamespace(image=Mock(ndim=3, shape=image_shape),
                                  _escape_generation=0)
    task.appear.side_effect = lambda rule: {
        'PAGE_MAIN_GOTO_SHIKIGAMI_RECORDS': shikigami,
        'PAGE_MAIN_GOTO_TEAM': shikigami,
        'PAGE_MAIN_SCROLL_CLOSE': scroll_close,
        'PAGE_MAIN_GOTO_EXPLORATION': exploration,
        'PAGE_CHECK_MAIN': True,
        # courtyard 内部临时构造的展开箭头
        'COURTYARD_SCROLL_EXPAND': arrow,
    }.get(str(rule), False)
    task.click.return_value = click_ok
    return task


class CollapsedScrollJudgementTest(unittest.TestCase):
    """收起态判定必须以「菜单入口消失 + 卷轴关闭标识出现」为准。"""

    def test_expanded_menu_is_not_collapsed(self):
        """菜单入口可见时不得判为收起，避免误点箭头把正常界面点乱。"""
        task = _task(shikigami=True, scroll_close=False)
        with patch.object(courtyard, 'collapsed_scroll_button', wraps=courtyard.collapsed_scroll_button):
            self.assertIsNone(courtyard.collapsed_scroll_button(task))

    def test_collapsed_menu_is_detected(self):
        """入口消失且卷轴关闭标识出现时，才认为是收起态。"""
        task = _task(shikigami=False, scroll_close=True, arrow=True)
        with patch.object(courtyard, 'RuleImage') as rule_cls:
            instance = Mock()
            instance.image = Mock(shape=(43, 43, 3))
            # 临时 button 的 str() 取自模板文件名，patch 后需显式给出。
            instance.__str__ = Mock(return_value='COURTYARD_SCROLL_EXPAND')
            rule_cls.return_value = instance
            self.assertIsNotNone(courtyard.collapsed_scroll_button(task))

    def test_goto_exploration_alone_does_not_decide(self):
        """收起态下 goto_exploration 分数横跨阈值，不能单独作为判据。"""
        task = _task(shikigami=False, scroll_close=False, exploration=True)
        with patch.object(courtyard, 'RuleImage') as rule_cls:
            instance = Mock()
            instance.image = Mock(shape=(43, 43, 3))
            instance.__str__ = Mock(return_value='COURTYARD_SCROLL_EXPAND')
            rule_cls.return_value = instance
            # 入口已消失、但卷轴关闭标识没出现 -> 不足以判定为收起
            self.assertIsNone(courtyard.collapsed_scroll_button(task))


class ExpandCourtyardScrollTest(unittest.TestCase):
    """展开动作必须允许重试，不能一次超时就整个任务失败。"""

    def test_single_click_success_returns_true(self):
        task = _task(shikigami=False, scroll_close=True, click_ok=True)
        calls = {'verify': 0}

        def verify(_fn, _clicks, timeout=None):
            calls['verify'] += 1
            # 第一次验证就通过：模拟菜单已展开
            task.appear.side_effect = lambda rule: {
                'PAGE_MAIN_GOTO_SHIKIGAMI_RECORDS': True,
                'PAGE_MAIN_GOTO_TEAM': True,
                'PAGE_CHECK_MAIN': True,
            }.get(str(rule), False)

        task._ui_verify.side_effect = verify
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=None):
            # collapsed 返回 None 表示无需展开 -> 直接 False，不该点
            self.assertFalse(courtyard.expand_courtyard_scroll(task))
        self.assertEqual(task.click.call_count, 0)

    def test_first_click_timeout_then_retry_succeeds(self):
        """第一次点击后菜单没出现时，应重试一次并成功，而不是直接抛错。"""
        task = _task(shikigami=False, scroll_close=True)
        button = SimpleNamespace(front_center=lambda: (1207, 542),
                                 roi_front=[1187, 521, 43, 43],
                                 image=Mock(shape=(43, 43, 3)))
        state = {'verify': 0}
        def verify(_fn, _clicks, timeout=None):
            state['verify'] += 1
            if state['verify'] == 1:
                raise TaskRecoveryFailed('not settled')
            # 第二轮验证通过

        task._ui_verify.side_effect = verify
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=button):
            self.assertTrue(courtyard.expand_courtyard_scroll(task))
        self.assertEqual(task.click.call_count, 2)
        self.assertEqual(state['verify'], 2)

    def test_two_rounds_timeout_raises(self):
        """两轮都没展开才抛错，且此时确实点过两次。"""
        task = _task(shikigami=False, scroll_close=True)
        button = SimpleNamespace(front_center=lambda: (1207, 542),
                                 roi_front=[1187, 521, 43, 43],
                                 image=Mock(shape=(43, 43, 3)))

        def verify(_fn, _clicks, timeout=None):
            raise TaskRecoveryFailed('not settled')

        task._ui_verify.side_effect = verify
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=button):
            with self.assertRaises(courtyard.CourtyardScrollStuckError):
                courtyard.expand_courtyard_scroll(task)
        self.assertEqual(task.click.call_count, 2)

    def test_stuck_error_names_the_real_cause(self):
        """兜底异常必须自带诊断信息，且仍是 TaskRecoveryFailed 以免破坏现有捕获。"""
        error = courtyard.CourtyardScrollStuckError((1207, 542))
        self.assertIsInstance(error, TaskRecoveryFailed)
        message = str(error)
        self.assertIn('collapsed', message)
        self.assertIn('(1207, 542)', message)
        self.assertIn('manually', message)


class RealDeviceSnapshotTest(unittest.TestCase):
    """用真机截图跑真实 RuleImage，锁死「点到卷轴本体、不是活动箭头」。

    素材：2026-10-06 12:09:18 tomoe2 真实报错截图（卷轴收起态）的局部，
    已裁成 220x220 存进 tests/fixtures，避免依赖被 gitignore 的 log/ 目录。
    画面布局：上方金色回转箭头 = 限时活动入口（约 1211,544），
    下方斜躺缠红绳的 = 卷轴本体（1147,589)-(1239,714)。
    """

    FIXTURE = 'tests/fixtures/courtyard_scroll_collapsed.png'
    # 真机上确认的位置（原图坐标）
    BODY_TOPLEFT = (1147, 589)
    OLD_ARROW_CLICK = (1211, 544)

    def _load(self):
        import cv2
        from pathlib import Path
        from numpy import uint8, fromfile
        path = Path(self.FIXTURE)
        self.assertTrue(path.exists(), f'缺少真机夹具: {path}')
        image = cv2.imdecode(fromfile(str(path), dtype=uint8), -1)
        return None if image is None else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def test_real_snapshot_locates_scroll_body_center(self):
        """真实 RuleImage 匹配后，点击点必须落在本体中心 (1193,652)。"""
        import cv2
        from module.atom.image import RuleImage
        image = self._load()
        self.assertIsNotNone(image)
        # 夹具是原图裁剪 (1060,500)-(1280,720)，换算成夹具内坐标
        offset_x, offset_y = 1060, 500
        body_w, body_h = courtyard.SCROLL_BODY_SIZE
        button = RuleImage(roi_front=(0, 0, body_w, body_h),
                           roi_back=(0, 0, image.shape[1], image.shape[0]),
                           method='Template matching', threshold=0.85,
                           file='./tasks/GameUi/page/courtyard_scroll_expand.png')
        self.assertTrue(button.template_match(image), '真机收起态截图应命中卷轴本体模板')
        x, y, _, _ = button.roi_front
        center = button.front_center()
        # 换算回原图坐标
        real_x, real_y = center[0] + offset_x, center[1] + offset_y
        expected = (self.BODY_TOPLEFT[0] + body_w // 2, self.BODY_TOPLEFT[1] + body_h // 2)
        self.assertEqual((real_x, real_y), expected,
                         f'点击点应为卷轴本体中心 {expected}，实际 {(real_x, real_y)}')

    def test_click_point_is_far_from_activity_arrow(self):
        """点击点必须与旧错误位置（活动箭头）拉开距离，防止再退化。"""
        import cv2
        from module.atom.image import RuleImage
        image = self._load()
        body_w, body_h = courtyard.SCROLL_BODY_SIZE
        button = RuleImage(roi_front=(0, 0, body_w, body_h),
                           roi_back=(0, 0, image.shape[1], image.shape[0]),
                           method='Template matching', threshold=0.85,
                           file='./tasks/GameUi/page/courtyard_scroll_expand.png')
        self.assertTrue(button.template_match(image))
        center = button.front_center()
        real = (center[0] + 1060, center[1] + 500)
        dy = real[1] - self.OLD_ARROW_CLICK[1]
        dx = real[0] - self.OLD_ARROW_CLICK[0]
        self.assertGreater(abs(dy), 80, f'点击点纵向只差 {dy}px，仍疑似点在活动箭头上')
        # 旧错误位置不在本体内
        self.assertFalse(
            self.BODY_TOPLEFT[0] <= self.OLD_ARROW_CLICK[0] <= self.BODY_TOPLEFT[0] + body_w
            and self.BODY_TOPLEFT[1] <= self.OLD_ARROW_CLICK[1] <= self.BODY_TOPLEFT[1] + body_h,
            '活动箭头坐标竟落在卷轴本体内，两者是同一元素？')

    def test_click_point_is_on_scroll_pixels_not_background(self):
        """点击点周围应是卷轴的暖红棕色，而非蓝底背景。"""
        image = self._load()
        cx, cy = 1193 - 1060, 652 - 500
        patch = image[cy - 20:cy + 20, cx - 20:cx + 20].reshape(-1, 3).mean(axis=0)
        background = image[60:100, 40:140].reshape(-1, 3).mean(axis=0)
        # 蓝底背景 B 通道明显偏高；卷轴本体偏红（R > B）
        self.assertGreater(patch[0], patch[2], f'点击点非暖色: {patch.round(1)}')
        self.assertGreater(background[2], background[0], f'夹具背景应为蓝底: {background.round(1)}')


class ScrollBodyTemplateTest(unittest.TestCase):
    """模板必须是「卷轴本体」，不能是它上方的金色回转箭头。

    回归：模板曾经误用金色回转箭头（右侧限时活动的查看入口），
    点它只会打开活动页、永远展不开卷轴 —— OAS 却把它当成展开按钮点了 14 次。
    判别方法：卷轴本体在收起态是「倾斜 + 缠红绳」，与展开态（竖直金筒）差异极大。
    """

    def test_template_size_matches_scroll_body(self):
        """模板尺寸应为卷轴本体 93x126，而不是旧箭头的 43x43。"""
        from pathlib import Path
        import cv2
        path = Path(courtyard.__file__).resolve().parent / 'page' / 'courtyard_scroll_expand.png'
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(image, f'模板缺失: {path}')
        height, width = image.shape[:2]
        self.assertEqual((width, height), courtyard.SCROLL_BODY_SIZE)
        self.assertEqual((width, height), (93, 126))

    def test_search_region_covers_body_position(self):
        """固定搜索区必须覆盖收起态本体实测位置 (1147,589)-(1239,714)。"""
        x, y, w, h = courtyard.SCROLL_BODY_SEARCH
        self.assertLessEqual(x, 1147, '搜索区左边界漏掉了本体')
        self.assertLessEqual(y, 589, '搜索区上边界漏掉了本体')
        self.assertGreaterEqual(x + w, 1147 + 93, '搜索区右边界不够')
        self.assertGreaterEqual(y + h, 589 + 126, '搜索区下边界不够')

    def test_body_is_not_the_activity_arrow(self):
        """模板区域必须覆盖本体中心，而不是上方回转箭头（约 1211,544）。"""
        body_center = (1147 + 93 // 2, 589 + 126 // 2)
        arrow_center = (1211, 544)
        # 本体中心明显在箭头下方；两者不可能落在同一格
        self.assertGreater(body_center[1], arrow_center[1] + 50)


class CourtyardMenuGuardTest(unittest.TestCase):
    """任务开工前的自检：收起态先展开，展开不了交给调用方跳过。"""

    def test_menu_ready_when_expanded(self):
        task = _task(shikigami=True, scroll_close=False)
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=None):
            self.assertTrue(courtyard.courtyard_menu_ready(task))
            self.assertTrue(courtyard.ensure_courtyard_menu_visible(task))
        self.assertEqual(task.click.call_count, 0)

    def test_menu_not_ready_when_collapsed(self):
        button = SimpleNamespace(front_center=lambda: (1207, 542),
                                 roi_front=[1187, 521, 43, 43],
                                 image=Mock(shape=(43, 43, 3)))
        task = _task(shikigami=False, scroll_close=True)
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=button):
            self.assertFalse(courtyard.courtyard_menu_ready(task))

    def test_ensure_expands_when_collapsed(self):
        """收起态下 ensure 应该真的去展开，而不是直接返回 False。"""
        button = SimpleNamespace(front_center=lambda: (1207, 542),
                                 roi_front=[1187, 521, 43, 43],
                                 image=Mock(shape=(43, 43, 3)))
        task = _task(shikigami=False, scroll_close=True)
        task._ui_verify.side_effect = lambda *a, **k: None
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=button):
            self.assertTrue(courtyard.ensure_courtyard_menu_visible(task))
        self.assertEqual(task.click.call_count, 1)

    def test_ensure_reports_failure_instead_of_raising(self):
        """兜底路径不允许把异常抛给调度器，调用方据此跳过本轮即可。"""
        button = SimpleNamespace(front_center=lambda: (1207, 542),
                                 roi_front=[1187, 521, 43, 43],
                                 image=Mock(shape=(43, 43, 3)))
        task = _task(shikigami=False, scroll_close=True)
        task._ui_verify.side_effect = lambda *a, **k: (_ for _ in ()).throw(
            TaskRecoveryFailed('nope'))
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=button):
            with self.assertRaises(courtyard.CourtyardScrollStuckError):
                courtyard.ensure_courtyard_menu_visible(task)

    def test_unstable_match_aborts_without_click(self):
        """两帧匹配位置抖动超过 4px 时不点击，避免点到错位置。"""
        task = _task(shikigami=False, scroll_close=True)
        centers = iter([(1207, 542), (1250, 600)])
        button = SimpleNamespace(front_center=lambda: next(centers),
                                 roi_front=[1187, 521, 43, 43],
                                 image=Mock(shape=(43, 43, 3)))
        with patch.object(courtyard, 'collapsed_scroll_button', return_value=button):
            self.assertFalse(courtyard.expand_courtyard_scroll(task))
        self.assertEqual(task.click.call_count, 0)


class CourtyardVisibleTest(unittest.TestCase):
    def test_check_main_short_circuits(self):
        task = _task(shikigami=False, scroll_close=False)
        self.assertTrue(courtyard.courtyard_visible(task))

    def test_menu_row_visible_is_visible(self):
        task = _task(shikigami=True, scroll_close=False)
        self.assertTrue(courtyard.courtyard_visible(task))

    def test_collapsed_state_still_counts_as_courtyard(self):
        """收起态仍属于庭院，否则 ESC 恢复会误判成未知页面。"""
        task = _task(shikigami=False, scroll_close=True)
        task.appear.side_effect = lambda rule: {
            'PAGE_CHECK_MAIN': False,
            'PAGE_MAIN_GOTO_SHIKIGAMI_RECORDS': False,
            'PAGE_MAIN_GOTO_TEAM': False,
            'PAGE_MAIN_SCROLL_CLOSE': True,
        }.get(str(rule), False)
        self.assertTrue(courtyard.courtyard_visible(task))

    def test_neither_marker_is_not_courtyard(self):
        task = _task(shikigami=False, scroll_close=False)
        task.appear.side_effect = lambda rule: {
            'PAGE_CHECK_MAIN': False,
            'PAGE_MAIN_GOTO_SHIKIGAMI_RECORDS': False,
            'PAGE_MAIN_GOTO_TEAM': False,
            'PAGE_MAIN_SCROLL_CLOSE': False,
        }.get(str(rule), False)
        self.assertFalse(courtyard.courtyard_visible(task))


if __name__ == '__main__':
    unittest.main()

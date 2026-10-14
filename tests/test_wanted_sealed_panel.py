"""守卫：悬赏全封印面板必须能被识别为「已完成」。

背景（2026-10-07）：悬赏实际已完成，任务却反复报
`TaskRecoveryFailed: Wanted quest completion panel could not be verified`，
连抛 8 次（tomoe1_1791288643092~ 1791304842484）。

根因不是 OCR 抖动，而是 `verify_quest_panel_complete` 里 `sealed_panel`
的判据选错了证据：它要求面板 OCR 读出竖排「已封印」，或三列死位置
（x=250/550/850）的窄条 OCR 读出「已封印」。但真机实测：

  * 全页 OCR 对同一张卡时对时错 —— 1791304842484 读成「记封」，
    1791298141383 读成「记封印」，1791106609021 读成「已封」，
    而后者转存为 JPEG 夹具后又读出标准「已封印」。重复 5 次结果一致，
    不是随机抖动，是系统性首字误识；
  * 窄条 OCR 是位置巧合 —— 列位置偏移 ±15px 就三列全失效，
    未完成面板在 shift−30 时还会假阳性；
  * 印章确实**是**该卡的状态标签（已封印卡写「已封印」，未完成卡写任务
    类型名「鬼魂奇闻」「食材采集」「雷男」「帚神」，见大哥 2026-10-07 提供
    的未完成面板截图），但它是**唯一读不稳的那一个**。

于是每轮 `identified=False`，4 轮耗尽后抛错。

完成这件事的真值信号是「追踪控件消失 + 面板内没有任何 x/y 未完成进度」，
两者都已由 `verify_quest_panel_complete` 现成算出，旧判据却没有用上。

⚠️ 本文件必须驱动**真实的** `verify_quest_panel_complete`，
只 mock 设备交互（点击/翻页/goto_page），识别一律走真路径
（RuleImage.template_match + RuleOcr.detect_and_ocr）。
只测 `_panel_looks_sealed` 这个纯函数属于「抄近路」：实测把判据改回
旧版（重新要求印章 OCR）后那种测试仍全绿，完全守不住 —— 已实测。

⚠️ 反向验证还有一个更隐蔽的坑（已实测踩中）：`verify_quest_panel_complete`
在源码第 23 行 `if unfinished: raise` **早退**，所以在带未完成进度的面板上，
`sealed_panel` 的各子句根本轮不到执行。把 `not unfinished` 或
`_panel_looks_sealed` 从 `sealed_panel` 里删掉，本文件原本**一条测试都不会
报红**。必须另造 `unfinished == []` 的输入（模拟 OCR 漏读进度行）才咬得住
—— 见 `PanelIdentityIsNotCompletionTest`。
"""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / 'fixtures'

# 两张真机全封印面板，裁到面板区域（x 150..1200, y 60..720）后存为 JPEG q92。
# 原始帧与筛选过程见文件末尾 Fixture selection record。
SEALED_FIXTURES = [
    'wanted_panel_all_sealed.jpg',
    'wanted_panel_all_sealed_popup.jpg',
]

# 真机**未完成**面板，原生 1280x720 无损 PNG。它们是「不误判」一侧的夹具：
# 完成判据放宽之后，最危险的回归是把未完成面板当成已完成。
UNFINISHED_FIXTURES = [
    # 12/12 与 0/8 混在同一面板 —— 局部完成不构成整体完成
    'wanted_panel_unfinished_native_a.png',
    # 带神秘妖怪卡（线索 0/12），走 mystery 忽略分支
    'wanted_panel_unfinished_native_b.png',
    # 带好友进度 10/10 与自己进度 2/12 并存
    'wanted_panel_unfinished_native_c.png',
    # 大哥 2026-10-07 提供的截图：唯一带「好友击败」协作卡（0/6 归 friend_pending），
    # 且面板标题被系统通知「欢迎进入游戏」压住 —— 身份判据缺标题时仍不得误判完成
    'wanted_panel_unfinished_user_shot.png',
]

# 裁剪偏移：夹具还原回 1280x720 全图时要补回去。
OFFSET_X, OFFSET_Y = 150, 60

PROGRESS = re.compile(r'(?<!\d)(\d{1,2})[7/](\d{1,2})(?!\d)')


def _load_panel(name):
    """读夹具并还原成 1280x720 全图坐标系的 RGB 数组。

    必须用 imdecode + BGR2RGB，与 module/atom/image.py 的 load_image 一致；
    通道顺序错了所有模板分数都会失真（见 test_courtyard_menu_calibration.py）。
    """
    import cv2
    import numpy
    from numpy import uint8, fromfile
    path = FIXTURES / name
    if not path.exists():
        return None
    image = cv2.imdecode(fromfile(str(path), dtype=uint8), -1)
    if image is None:
        return None
    if image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    else:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    canvas = numpy.zeros((720, 1280, 3), dtype=numpy.uint8)
    canvas[OFFSET_Y:720, OFFSET_X:1200] = image
    return canvas


def _load_native(name):
    """读一张原生 1280x720 夹具（不做裁剪还原）。

    未完成面板夹具直接就是整帧，不需要 _load_panel 的贴回偏移。
    通道顺序仍必须 imdecode + BGR2RGB，否则模板分数失真。
    """
    import cv2
    import numpy
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


def _install_local_matching():
    """把 RuleImage.match 绑到本地彩色模板匹配，只换掉传输层。

    真机上 `appear()` 会把 `frame_id` 交给 zerorpc 服务端去取帧；离线时
    没有服务端，`_resolve_image` 会抛 "Either frame_id or image_bytes must
    be provided"。这里用与 `module/image/runtime.py::_template_match_image`
    完全相同的算法（corp + cv2.matchTemplate + TM_CCOEFF_NORMED + 命中即回写
    roi_front）在本地算，**识别逻辑保持真实**，只把「帧从哪来」换成本地数组。

    ⚠️ 不要改成 mock `appear()` 返回 True：那样 `_update_roi_front` 不会执行，
    断言会落在初始 roi 中心而非命中点上，等于没验。
    """
    import cv2
    from module.atom.image import RuleImage

    def match(self, image, threshold=None, **kwargs):
        if threshold is None:
            threshold = self.threshold
        source = self.corp(image)
        mat = self.image
        if mat is None or mat.size == 0:
            return True
        if source.shape[0] < mat.shape[0] or source.shape[1] < mat.shape[1]:
            return False
        result = cv2.matchTemplate(source, mat, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > threshold:
            self._update_roi_front(max_loc, (mat.shape[1], mat.shape[0]))
            return True
        return False

    RuleImage.match = match


def _build_task(image):
    """造一个只缺设备交互的 ScriptTask，其余全部为真实实现。

    刻意保留真实实现的部分：
      - appear()            → RuleImage.template_match（真模板匹配）
      - _panel_unfinished_quests / _unrevealed_mystery_regions 等（真逻辑）
      - verify_quest_panel_complete 的完整循环与 stable 计数
    mock 掉的仅限无法离线驱动的设备动作：截图、点击、翻页、goto_page。
    """
    sys.path.insert(0, str(REPO))
    _install_local_matching()
    from tasks.WantedQuests.script_task import ScriptTask

    task = ScriptTask.__new__(ScriptTask)
    task.device = type('Device', (), {
        'image': image,
        'image_frame_id': 0,
        'get_image_batch_cache': lambda self, target, frame_id=None: None,
        'update_image_batch_cache': lambda self, *a, **k: None,
        'click_record_clear': lambda self, *a, **k: None,
    })()
    # 截图只是把夹具原样交回去，识别因此仍跑在真帧上。
    task.screenshot = Mock()
    task.goto_page = Mock()
    task.click = Mock(return_value=False)
    task.ui_click = Mock()
    task.ui_click_until_disappear = Mock()
    task.ui_get_reward = Mock()
    task.swipe = Mock()
    task.appear_then_click = Mock(return_value=False)
    task.wq_skipped_names = set()
    task.wq_unrevealed_skips = set()
    task.invitation_ledger = None
    task.config = Mock()
    return task


class WantedSealedPanelRecognitionTest(unittest.TestCase):
    """全封印面板必须走完 verify_quest_panel_complete 而不抛错。"""

    def _each_fixture(self):
        for name in SEALED_FIXTURES:
            image = _load_panel(name)
            self.assertIsNotNone(image, f'缺少真机夹具或无法解码: {name}')
            self.assertEqual(image.shape[:2], (720, 1280),
                             f'{name} 还原后应为 720x1280，实际 {image.shape}')
            yield name, image

    # ---- 真值前提 -------------------------------------------------------

    def test_fixtures_are_genuinely_sealed_panels(self):
        """夹具前提：确实是悬赏面板，且确实没有任何未完成进度。

        若这条红了，说明夹具选错了（游戏改版，或图不是悬赏面板），
        后面所有完成判据断言都失去意义。
        """
        from module.atom.ocr import RuleOcr
        from tasks.GlobalGame.assets import GlobalGameAssets
        from tasks.WantedQuests.assets import WantedQuestsAssets
        from tasks.WantedQuests.script_task import ScriptTask
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_completion_panel')
        for name, image in self._each_fixture():
            self.assertTrue(GlobalGameAssets.I_UI_BACK_RED.template_match(image),
                            f'{name} 上关闭按钮未命中 —— 这不是悬赏面板')
            texts = [ScriptTask._normalize_wq_list_text(r.ocr_text)
                     for r in rule.detect_and_ocr(image, logDisplay=False)]
            self.assertTrue(any('悬赏封印' in t for t in texts),
                            f'{name} 读不到面板标题: {texts}')
            self.assertTrue(any('下次刷新' in t for t in texts),
                            f'{name} 读不到刷新提示: {texts}')
            counters = [t for t in texts if PROGRESS.search(t)]
            self.assertEqual(counters, [],
                             f'{name} 竟读出未完成进度 {counters} —— '
                             f'这张图不是「全部封印」面板，不能当本测试的夹具')
            self.assertFalse(WantedQuestsAssets.I_TRACE_ENABLE.template_match(image),
                             f'{name} 上仍有一键追踪按钮 —— 存在可追踪任务')

    # ---- 核心断言 -------------------------------------------------------

    def test_sealed_panel_passes_verification(self):
        """核心：真实 verify_quest_panel_complete 在真机夹具上必须成功返回。

        这正是 2026-10-07 误报的那一步。把判据改回「要求印章 OCR」，
        本测试会立刻报红（已实测）。
        """
        from module.exception import TaskRecoveryFailed
        for name, image in self._each_fixture():
            task = _build_task(image)
            task._open_wanted_completion_panel = Mock()
            try:
                task.verify_quest_panel_complete()
            except TaskRecoveryFailed as exc:
                self.fail(f'{name} 未通过完成校验（这正是 2026-10-07 的误报）: {exc}')

    def test_verification_requires_two_stable_frames(self):
        """两帧确认的机制不能被这次修复绕过。

        完成判定仍需连续两轮 identified，防止单帧误读就报成功。
        """
        for name, image in self._each_fixture():
            task = _build_task(image)
            task._open_wanted_completion_panel = Mock()
            task.verify_quest_panel_complete()
            # 循环上限 4 轮，stable>=2 即返回 ⇒ 第 1 轮 stable=1，
            # 第 2 轮 stable=2 触发返回，故恰好 2 次循环。
            self.assertGreaterEqual(task.screenshot.call_count, 2,
                                    f'{name} 两帧确认机制疑似被绕过')

    def test_panel_identity_needs_both_title_and_refresh(self):
        """面板身份必须同时具备标题与刷新提示，缺一不可。

        防的是「在别的界面上误判为已完成」——误操作才是事故源。
        """
        from tasks.WantedQuests.script_task import ScriptTask
        self.assertTrue(ScriptTask._panel_looks_sealed(
            ['悬赏封印', '下次刷新：04:19:31']))
        self.assertFalse(ScriptTask._panel_looks_sealed(['悬赏封印']),
                         '只有标题不足以认定面板身份')
        self.assertFalse(ScriptTask._panel_looks_sealed(['下次刷新：04:19:31']),
                         '只有刷新提示不足以认定面板身份')
        self.assertFalse(ScriptTask._panel_looks_sealed(['活动', '下次刷新：04:19:31']),
                         '非悬赏界面的文字不得被当成悬赏面板')


class SealStampIsNotACompletionSignalTest(unittest.TestCase):
    """锁定「印章不能单独作为完成依据」这个语义事实。

    实测（`_label_cmp.png`，2026-10-07）：竖排标签**确实**编码状态 ——
    已封印的卡写「已封印」，未完成的卡写任务类型名（「鬼魂奇闻」「食材采集」
    「童男」「帚神」）。它不是无意义的美术边框。

    但它读不稳：同一张卡在全页 OCR 下时对时错（记封 / 记封印 / 已封 /
    已封印，重复 5 次结果一致 ⇒ 系统性首字误识），而且六张未完成真机面板
    上印章读数**一律为空** —— 也就是说它对「未完成」这一侧零信号。
    因此判据不能建立在它之上：真值信号是 x/y 进度与追踪控件。
    本测试把这条写成可执行文档，并守住旧实现不复辟。
    """

    def test_panel_identity_does_not_consult_seal_stamp(self):
        """`_panel_looks_sealed` 不得依赖印章。

        它只看面板身份（标题 + 刷新提示）。若有人又把印章写回，这里报红。
        """
        import ast
        import inspect
        import textwrap
        from tasks.WantedQuests.script_task import ScriptTask
        # 只看可执行代码：docstring 里必须写明「为什么不能信印章」，
        # 那里出现「已封印」是应该的。剥掉 docstring 与注释后再查。
        source = textwrap.dedent(inspect.getsource(ScriptTask._panel_looks_sealed))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)):
                    node.body.pop(0)
        code = ast.unparse(tree)
        for forbidden in ('已封印', '_sealed_stamp', 'SEALED_STAMP'):
            self.assertNotIn(forbidden, code,
                             f'_panel_looks_sealed 的实现里又用上了印章（{forbidden}）——'
                             f'印章虽是状态标签，但 OCR 首字系统性误识，'
                             f'且未完成面板上读数一律为空，不能当完成依据')

    def test_dead_position_stamp_ocr_is_gone(self):
        """三列死位置的窄条 OCR 已被证伪，不得复活。

        实测：列位置偏移 ±15px 就三列全失效；未完成面板在 shift−30
        时还会假阳性（把第三张卡的边框当证据）。
        """
        import inspect
        from tasks.WantedQuests.script_task import ScriptTask
        self.assertFalse(hasattr(ScriptTask, 'sealed_quest_stamp_visible'),
                         'sealed_quest_stamp_visible 应已删除：它测的是位置巧合的'
                         '窄条区域，在未完成面板上会假阳性')
        self.assertNotIn('wanted_sealed_stamp',
                         inspect.getsource(ScriptTask),
                         '窄条印章 OCR（x=250/550/850 死位置）不应复活')

    def test_sealed_panel_judgement_excludes_unfinished_counter(self):
        """完成判据必须显式要求「无未完成进度」。

        只靠「无追踪控件」不够：追踪控件状态与卡面完成度不是一回事。
        """
        import inspect
        from tasks.WantedQuests.script_task import ScriptTask
        source = inspect.getsource(ScriptTask.verify_quest_panel_complete)
        self.assertIn('not unfinished', source,
                      'sealed_panel 必须显式要求 not unfinished —— '
                      '「追踪控件消失」单独不足以证明完成')


class UnfinishedPanelIsNotCompletionTest(unittest.TestCase):
    """不误判一侧：真机未完成面板必须被拒，不得当成已完成。

    判据放宽（不再要求印章 OCR）之后，危险方向随之反转 —— 不再是「已完成
    却不认」，而是「未完成却认」。误操作的代价是跳过后续所有任务，所以这一
    侧比识别侧更需要端到端验证。

    ⚠️ 必须驱动真实 `verify_quest_panel_complete`：只测 `_panel_looks_sealed`
    守不住「误判为完成」，因为该函数只管面板身份、不管完成度。
    """

    def _each_fixture(self):
        for name in UNFINISHED_FIXTURES:
            image = _load_native(name)
            self.assertIsNotNone(image, f'缺少真机夹具或无法解码: {name}')
            self.assertEqual(image.shape[:2], (720, 1280),
                             f'{name} 应为原生 720x1280，实际 {image.shape}')
            yield name, image

    # ---- 夹具前提 ---------------------------------------------------------

    def test_fixtures_really_are_unfinished_panels(self):
        """夹具前提：确实是悬赏面板，且确实还有未完成的 x/y 进度。

        这条红了说明夹具选错了（游戏改版，或误存了已封印面板），后续
        「不误判」断言全部失去意义。
        """
        from module.atom.ocr import RuleOcr
        from tasks.GlobalGame.assets import GlobalGameAssets
        from tasks.WantedQuests.script_task import ScriptTask
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_unfinished_panel')
        for name, image in self._each_fixture():
            self.assertTrue(GlobalGameAssets.I_UI_BACK_RED.template_match(image),
                            f'{name} 上关闭按钮未命中 —— 这不是悬赏面板')
            results = rule.detect_and_ocr(image, logDisplay=False)
            texts = [ScriptTask._normalize_wq_list_text(r.ocr_text)
                     for r in results]
            self.assertTrue(any('下次刷新' in t for t in texts),
                            f'{name} 读不到刷新提示: {texts}')
            unfinished, _, _ = ScriptTask._panel_unfinished_quests(results, [])
            self.assertTrue(unfinished,
                            f'{name} 竟读不出任何未完成进度 —— '
                            f'这张图不是「未完成」面板，不能当本测试的夹具')

    # ---- 核心断言 ---------------------------------------------------------

    def test_unfinished_panel_is_reported_as_unfinished(self):
        """核心：未完成面板必须抛「remain unfinished」，而不是别的。

        三种结局里只有一种是对的：
          ✓ 抛 `Wanted quests remain unfinished` —— 保留任务稍后重试
          ✗ 抛 `could not be verified` —— 退回 2026-10-07 的误报路径
          ✗ 正常返回 —— 假性成功，后续任务全被跳过
        """
        from module.exception import TaskRecoveryFailed
        for name, image in self._each_fixture():
            task = _build_task(image)
            task._open_wanted_completion_panel = Mock()
            try:
                task.verify_quest_panel_complete()
            except TaskRecoveryFailed as exc:
                message = str(exc)
                self.assertIn('remain unfinished', message,
                              f'{name} 抛的是 {message!r}，'
                              f'不是「remain unfinished」—— 若为 '
                              f'"could not be verified" 说明完成判据'
                              f'把这张未完成面板整个认不出来')
            else:
                self.fail(f'{name} 是未完成面板，verify_quest_panel_complete '
                          f'却返回成功 —— 这是假性成功，会跳过后续所有任务')

    def test_unfinished_counters_survive_completed_cards_on_same_panel(self):
        """同一面板上混有已完成卡时，未完成计数不能被「洗掉」。

        真实样本 native_a 面板上同时有 12/12（已完成）与 0/8（未完成），
        native_c 同时有 10/10（好友已满）与 2/12。局部完成不构成整体完成。
        """
        from module.atom.ocr import RuleOcr
        from tasks.WantedQuests.script_task import ScriptTask
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_unfinished_panel')
        mixed = {'wanted_panel_unfinished_native_a.png',
                 'wanted_panel_unfinished_native_c.png'}
        for name, image in self._each_fixture():
            if name not in mixed:
                continue
            results = rule.detect_and_ocr(image, logDisplay=False)
            texts = [ScriptTask._normalize_wq_list_text(r.ocr_text)
                     for r in results]
            done = [t for t in texts
                    if PROGRESS.search(t)
                    and PROGRESS.search(t).group(1) == PROGRESS.search(t).group(2)]
            unfinished, _, _ = ScriptTask._panel_unfinished_quests(results, [])
            self.assertTrue(done, f'{name} 应含已满进度卡作对照，实际 {texts}')
            self.assertTrue(unfinished,
                            f'{name} 上有已完成卡 {done}，'
                            f'未完成计数却被吞掉了 —— 局部完成被当成了整体完成')

    def test_reached_progress_is_not_unfinished(self):
        """x/y 相等（12/12、10/10）表示该卡已打满，**不算**未完成。

        这条与上一条成对：上一条防「未完成被洗掉」，这条防「已完成被算进
        未完成」。后者会让悬赏永远判为未完成、无限重试 —— 是另一种卡死。

        ⚠️ 已知覆盖边界（实测，如实记录）：把 `_panel_unfinished_quests` 的
        `current < total` 放宽成 `current <= total`，本测试**仍全绿**。
        原因是四张未完成夹具里所有「已满卡」（12/12、10/10）前面都跟着
        「好友击败…」标签，被 `last_owner` 归入 friend_pending 或被协作
        ignore 区排除，压根没进 unfinished 列表 ⇒ 该变异无可观测差异。
        要观测它需要一张「只有已满卡、且无任何 击败 标签」的面板，日志中
        不存在（那种状态在游戏里就是「全打完待确认」，此时面板已无进度行）。
        因此本条只守住「已满卡不会**因为自身数值**被算进未完成」这一层，
        不宣称能守住整个不等式边界。
        """
        from module.atom.ocr import RuleOcr
        from tasks.WantedQuests.script_task import ScriptTask
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_reached_panel')
        mixed = {'wanted_panel_unfinished_native_a.png',
                 'wanted_panel_unfinished_native_c.png'}
        checked = 0
        for name, image in self._each_fixture():
            if name not in mixed:
                continue
            results = rule.detect_and_ocr(image, logDisplay=False)
            texts = [ScriptTask._normalize_wq_list_text(r.ocr_text)
                     for r in results]
            reached = [t for t in texts
                       if PROGRESS.search(t)
                       and PROGRESS.search(t).group(1) == PROGRESS.search(t).group(2)]
            self.assertTrue(reached,
                            f'{name} 应含已满进度卡（12/12 之类）作对照，'
                            f'实际 {texts}')
            unfinished, _, _ = ScriptTask._panel_unfinished_quests(results, [])
            for text in reached:
                self.assertNotIn(text, unfinished,
                                 f'{name}：{text} 是已打满的卡，不该算作未完成')
            checked += 1
        self.assertEqual(checked, len(mixed),
                         '两张混合作对照的夹具都要检查到')

    def test_panel_identity_is_irrelevant_to_completion(self):
        """面板身份读不到时（标题被遮挡），更不能因此判成已完成。

        大哥提供的截图上，系统通知「欢迎进入游戏」压住了标题，
        `_panel_looks_sealed` 返回 False。这恰恰是最危险的一格：
        若代码把「身份读不到」当成「不是悬赏面板 → 放弃识别」并 fall
        through 到别处，就可能误判。必须确认它仍走未完成分支。
        """
        from module.atom.ocr import RuleOcr
        from tasks.WantedQuests.script_task import ScriptTask
        name = 'wanted_panel_unfinished_user_shot.png'
        image = _load_native(name)
        self.assertIsNotNone(image)
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_occluded_panel')
        results = rule.detect_and_ocr(image, logDisplay=False)
        _, _, texts = ScriptTask._panel_unfinished_quests(results, [])
        self.assertFalse(ScriptTask._panel_looks_sealed(texts),
                         f'{name} 的标题被系统通知遮挡，'
                         f'若哪天读出标题了请同步更新本断言')
        unfinished, _, _ = ScriptTask._panel_unfinished_quests(results, [])
        self.assertTrue(unfinished,
                        f'{name} 即使身份判据为假，'
                        f'未完成进度仍必须被识别出来')

    def test_friend_progress_card_is_not_counted_as_own_unfinished(self):
        """「好友击败 … 0/6」属于协作卡，不能算成自己未完成。

        大哥截图里第一张卡是好友协作：自己 0/11 未完成，好友那条 0/6 归
        friend_pending。它必须与自己的未完成分开，否则提示会误导，
        也可能被误当成「自己还没打满」而无限重试。
        """
        from module.atom.ocr import RuleOcr
        from tasks.WantedQuests.script_task import ScriptTask
        name = 'wanted_panel_unfinished_user_shot.png'
        image = _load_native(name)
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_friend_panel')
        results = rule.detect_and_ocr(image, logDisplay=False)
        unfinished, friend_pending, _ = ScriptTask._panel_unfinished_quests(results, [])
        self.assertTrue(friend_pending,
                        f'{name} 应含好友协作进度（好友击败 … 0/6），'
                        f'实际 unfinished={unfinished} friend={friend_pending}')
        self.assertNotIn('0/6', unfinished,
                         '好友自己的进度不得算成自己未完成')


class PanelIdentityIsNotCompletionTest(unittest.TestCase):
    """锁死 `sealed_panel` 两道闸门的真实作用范围（2026-10-07 实测）。

    这组测试记录的是**方法结构的既有性质**，不是本次修复引入的行为：

      identified  只回答「这是不是悬赏面板」，不回答「是不是全做完了」；
      完成与否由 `if unfinished: raise` 在源码第 23 行独立把关，
      早于第 49 行的 `stable >= 2` 成功返回。

    推论有两条，都必须写下来，否则后人又会误读：

      1. 在带未完成进度的真机面板上，两道闸门**根本轮不到执行** ——
         `if unfinished` 先抛错。所以「把 sealed_panel 的子句删掉」这类
         改动不会被本文件任何测试发现（已实测报绿）。想验它们必须构造
         `unfinished == []` 的输入，见下面两条。
      2. 当 OCR 连 x/y 进度都读不出时（计数器被抹掉），本方法会**正常返回**。
         这不是误判完成：此时它只是「认出了这是个悬赏面板」，而这张面板是否
         真的全做完已无从证明。native_a / 大哥截图在同样处理下正确抛错
         （前者无追踪按钮、后者身份判据为假），native_c 则返回 ——
         因为它的追踪按钮处于 ENABLE 态 0.995，`trace_visible` 直接让
         `identified` 成立。**这是 OCR 失效的兜底缺口，不是判据选错。**
    """

    # B1 的守卫：删掉 sealed_panel 里的 `not unfinished` 必须被发现
    def test_sealed_panel_requires_no_unfinished_counter(self):
        """`sealed_panel` 必须显式带上 `not unfinished`。"""
        import inspect
        from tasks.WantedQuests.script_task import ScriptTask
        source = inspect.getsource(ScriptTask.verify_quest_panel_complete)
        self.assertIn('not unfinished', source,
                      'sealed_panel 必须显式要求 not unfinished')

    # B2 的守卫：删掉身份判据必须被发现，且必须靠「计数器读不出」才触达
    def test_sealed_panel_requires_panel_identity(self):
        """`sealed_panel` 必须显式带上 `_panel_looks_sealed(...)`。"""
        import inspect
        from tasks.WantedQuests.script_task import ScriptTask
        source = inspect.getsource(ScriptTask.verify_quest_panel_complete)
        self.assertIn('_panel_looks_sealed', source,
                      'sealed_panel 必须显式要求面板身份，'
                      '否则任意「有关闭按钮且无追踪控件」的界面都会被当成完成')

    def test_unfinished_guard_precedes_every_success_return(self):
        """`if unfinished: raise` 必须早于 `stable >= 2` 的成功返回。

        这是「不误判」的**唯一**有效防线，顺序颠倒就等于没有防线。
        写成源码级断言而不是靠夹具反推：夹具一旦 OCR 变化就验不到了。
        """
        import inspect
        from tasks.WantedQuests.script_task import ScriptTask
        source = inspect.getsource(ScriptTask.verify_quest_panel_complete)
        self.assertLess(source.index('if unfinished:'),
                        source.index('if stable >= 2:'),
                        '未完成守卫必须排在成功返回之前，'
                        '否则未完成面板会被记成成功')
        self.assertLess(source.index('if unfinished:'),
                        source.index("raise TaskRecoveryFailed('Wanted quest completion panel"),
                        '未完成守卫必须排在「无法校验」抛错之前')

    def test_identity_alone_cannot_complete_a_counterless_panel(self):
        """计数器读不出时，身份判据不得单独把面板推成「已完成」。

        构造 `unfinished == []`（模拟 OCR 漏读进度行）后跑真实入口：
        native_a 与大哥截图都必须仍抛错。

        ⚠️ 反向验证记录：本条在「删掉 `_panel_looks_sealed`」时报红，
        是本文件唯一能咬住 B2 的守卫 —— 因为只有走到 sealed_panel 才会
        触及身份判据。native_c 不在此列（追踪按钮 ENABLE 态让 identified
        直接成立，压根不进 sealed_panel），那是 OCR 失效的既有缺口，
        不在本次修复范围内，此处如实标注而不假装覆盖。
        """
        import re
        from module.exception import TaskRecoveryFailed
        from module.atom.ocr import RuleOcr
        progress = re.compile(r'(?<!\d)(\d{1,2})[7/](\d{1,2})(?!\d)')
        rule = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                       mode='Full', method='Default', keyword='',
                       name='wq_counterless')
        for name in ('wanted_panel_unfinished_native_a.png',
                     'wanted_panel_unfinished_user_shot.png'):
            image = _load_native(name)
            real = rule.detect_and_ocr(image, logDisplay=False)
            stripped = [r for r in real
                        if not progress.search(''.join(r.ocr_text.split()))]
            self.assertLess(len(stripped), len(real),
                            f'{name} 应当剥掉至少一行进度读数')
            task = _build_task(image)
            task._open_wanted_completion_panel = Mock()
            original = RuleOcr.detect_and_ocr
            try:
                RuleOcr.detect_and_ocr = lambda self, img, **k: stripped
                try:
                    task.verify_quest_panel_complete()
                except TaskRecoveryFailed:
                    continue
                else:
                    self.fail(f'{name} 在进度读不出的情况下被判为已完成 —— '
                              f'面板身份（标题+刷新提示）不足以证明全部做完')
            finally:
                RuleOcr.detect_and_ocr = original


# ---------------------------------------------------------------------------
# Fixture selection record (2026-10-07, reproducible)
#
# ── 已完成面板（2 张，裁剪 JPEG q92）────────────────────────────────────────
# 扫 log/ 全部真机截图 → 悬赏面板候选（底部导航条与标题栏亮度门控）→
# 标题「悬赏封印」+ 刷新提示「下次刷新」双命中。选用：
#   tomoe1_1791304842484  2026-10-07 00-40-31   三卡全封印，无弹窗
#   tomoe1_1791106609021  2026-10-04 17-36-42   三卡全封印 + 感谢好友弹窗
# 弹窗那张额外证明「感谢好友」浮层不会遮挡完成判定。
# 存储：裁到 x 150..1200 / y 60..720（保留标题、全部卡列、奖励行、刷新行、
# 追踪区、关闭按钮），JPEG q92。实测关闭按钮模板分 0.920、追踪按钮不命中，
# 均远离声明阈值 0.8，故有损存储不影响断言。
#   ⚠️ 更正：本记录早前版本写「扫 407 张、仅 tomoe1_*，日志里没有未完成面板」。
#   那是扫描范围错误 —— tomoe2_* 下有 5 张，见下。
#
# ── 未完成面板（3 张原生 + 1 张大哥截图）────────────────────────────────────
# 同一轮扫描按「close 按钮命中 + 标题/刷新双命中」筛出**全部 21 张**悬赏
# 面板，其中 5 张带未完成进度，全部来自 tomoe2_*（tomoe1_* 一张都没有）：
#   tomoe2_1791091724843  10-04 13-28-44  0/8,0/8,0/1 + 12/12,12/12  traceEnable .995
#   tomoe2_1791170809441  10-05 11-26-48  0/2,0/6,0/12              traceDisable .995
#   tomoe2_1791181078646  10-05 14-17-57  0/4,0/2,0/6,8/8           traceDisable .995
#   tomoe2_1791191888897  10-05 17-18-07  0/4,0/2,0/6,8/8           traceDisable .995
#   tomoe2_1791198000389  10-05 19-00-00  0/2,0/5,2/12 + 10/10       traceEnable .995
# 选用前三张（native_a/b/c）：原生 1280x720 无损 PNG，覆盖三种形态 ——
#   a 面板内混有已打满卡（12/12）与未完成卡
#   b 带神秘妖怪卡（线索 0/12），走 mystery 忽略分支
#   c 带好友进度 10/10 与自己进度 2/12 并存
#
# 大哥 2026-10-07 提供的截图（wanted_panel_unfinished_user_shot.png）：
#   源图 1107x565，是 1280x720 屏幕的 **1:1 平移裁剪**，非缩放。证据：真实
#   关闭按钮模板（34x38）在这张图上得 0.920 且**尺寸不变**，落点 (1163,115)
#   与真机帧声明坐标一致；贴回 x=91,y=76 后 ROI 完全落在画面内
#   （91+1107=1198 ≤ 1280，76+565=641 ≤ 720）。
#   ⚠️ 更正：本记录早前版本写「非等比缩放（关闭按钮锚点推 s=0.9229 而面板
#   边缘推 s_y=0.81/8.93），不能作坐标夹具」。**那是错的** —— 错因是拿
#   水平边条当锚点，而边条模板得分只有 0.19~0.65，匹配到的是噪声。改用
#   真实资产模板作锚点后尺度自洽。该图独有价值：
#     ① 唯一带「好友击败盗墓小鬼 0/6」协作卡 ⇒ 0/6 必须归 friend_pending；
#     ② 面板标题被系统通知「欢迎进入游戏」压住 ⇒ _panel_looks_sealed 为
#        False，验证身份判据缺标题时也不会误判完成。
#   追踪按钮在这张图上只得 0.763（<0.80 阈值）—— 按钮**确实在画面里**
#   （裁图目视确认是同一个「一键追踪」），低分是截图右边缘把按钮右下角裁掉
#   所致，非「按钮不存在」。
# ---------------------------------------------------------------------------


if __name__ == '__main__':
    unittest.main()
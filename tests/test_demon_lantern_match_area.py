"""守卫：逢魔灯笼的「点击区」与「识别区」必须分离。

背景（移植自上游 runhey 194a3eda）：
上游把灯笼点击区 C_DE_* 收缩成只包住内部图形（避免点到旁边的按钮），
但收缩后的区域放不下分类模板（I_DE_BOX / I_DE_LETTER / ...），
check_lantern 若仍拿 C_DE_* 当识别搜索区，模板匹配会全部落空 ⇒
四个灯笼一律走到最后的 else 分支，被误判成 battle。

修法是拆成两套：
  C_DE_1..4    点击区（收缩，只包内部图形）
  C_DE_MATCH_1..4  识别区（保持原大小，须容纳完整灯笼图案）

本守卫钉住三件事：
  1. C_DE_MATCH_1..4 存在，且几何等于「收缩前」的旧坐标（即与 C_DE_* 不同）
  2. check_lantern 的 match_roi 用的是 C_DE_MATCH_*，不是 C_DE_*
  3. 识别区必须能容纳分类模板：每个 MATCH 区宽高 >= 对应模板 roi_front 宽高

第3 条是真正的判据 —— 区域装不下模板就是那个 bug 的根因，
只断言「两个区不一样」会放过一批照样装不下的坐标。
"""
import ast
import re
import struct
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / 'tasks' / 'DemonEncounter' / 'assets.py'
SCRIPT = REPO / 'tasks' / 'DemonEncounter' / 'script_task.py'


def parse_rules(path):
    """从 assets.py 取 RuleImage / RuleClick 规则 -> name -> kwargs。

    必须遍历整棵子树：规则嵌在 if 块里，只扫 class.body 的直接子节点会解析到 0 条。
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        if not (isinstance(fn, ast.Name) and fn.id in ('RuleImage', 'RuleClick')):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        name = None
        for t in targets:
            name = t.attr if isinstance(t, ast.Attribute) else (t.id if isinstance(t, ast.Name) else None)
        if not name:
            continue
        kw = {}
        for k in node.value.keywords:
            try:
                kw[k.arg] = ast.literal_eval(k.value)
            except (ValueError, SyntaxError):
                pass
        if 'roi_front' in kw:
            out[name] = kw
    return out


def png_size(path: Path):
    b = path.read_bytes()
    if b[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return struct.unpack('>II', b[16:24])


class TestLanternClickAndMatchAreaSeparated(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rules = parse_rules(ASSETS)
        cls.script_src = SCRIPT.read_text(encoding='utf-8')
        # 分类模板：check_lantern 用它们在 match_roi 里判类型
        cls.templates = {
            1: 'I_DE_BOX',
            2: 'I_DE_LETTER',
            3: 'I_DE_MYSTERY',
            4: 'I_DE_REALM',
        }

    def test_parser_sane(self):
        """防假绿：解析器坏了会让后面全部空转，必须先确认真的解析到了东西。"""
        self.assertGreater(len(self.rules), 20, 'assets.py 解析结果异常少，解析器可能坏了')
        for name in ('C_DE_1', 'C_DE_2', 'C_DE_3', 'C_DE_4'):
            self.assertIn(name, self.rules)
        for name in self.templates.values():
            self.assertIn(name, self.rules, f'本库应有分类模板 {name}')

    def test_match_areas_exist(self):
        """C_DE_MATCH_1..4 必须存在，且与点击区 C_DE_* 不是同一块区域。"""
        for i in range(1, 5):
            match_name = f'C_DE_MATCH_{i}'
            self.assertIn(match_name, self.rules, f'缺少识别区 {match_name}')
            match_roi = tuple(self.rules[match_name]['roi_front'])
            click_roi = tuple(self.rules[f'C_DE_{i}']['roi_front'])
            self.assertNotEqual(
                match_roi, click_roi,
                f'{match_name} 与 C_DE_{i} 完全相同 => 点击区未收缩，'
                f'要么是没修，要么是把两套区域又合并回去了')

    def test_check_lantern_uses_match_areas(self):
        """接线守卫：check_lantern 的 match_roi 必须取 C_DE_MATCH_*。

        只断言「C_DE_MATCH_* 定义了」守不住「到底用没用到」——
        删掉实际引用，规则照样躺在文件里，测试仍然全绿。
        """
        m = re.search(r'def check_lantern\(.*?(?=\n    def |\Z)', self.script_src, re.S)
        self.assertIsNotNone(m, '找不到 check_lantern')
        body = m.group(0)
        match_roi_block = re.search(r'match_roi\s*=\s*\{.*?\}', body, re.S)
        self.assertIsNotNone(match_roi_block, 'check_lantern 里没有 match_roi 字典')
        block = match_roi_block.group(0)
        for i in range(1, 5):
            self.assertIn(f'C_DE_MATCH_{i}', block,
                          f'check_lantern 的 match_roi[{i}] 没有用 C_DE_MATCH_{i}')
            # 不能同时用点击区当识别区
        self.assertNotRegex(block, r'C_DE_[1-4]\.roi_front',
                            'check_lantern 仍在用点击区 C_DE_* 当识别区，'
                            '收缩后装不下模板会把四个灯笼全误判成 battle')

    def test_match_area_can_hold_templates(self):
        """真正的判据：识别区必须装得下对应分类模板。

        这是那个 bug 的根因。区域「和点击区不一样」还不够，
        照样可能两个都很小、模板仍然放不下 ⇒ 依然全落空。
        """
        for i, tpl_name in self.templates.items():
            match_roi = self.rules[f'C_DE_MATCH_{i}']['roi_front']
            tpl = self.rules[tpl_name]
            tpl_w, tpl_h = tpl['roi_front'][2], tpl['roi_front'][3]
            m_w, m_h = match_roi[2], match_roi[3]
            self.assertGreaterEqual(
                m_w, tpl_w,
                f'C_DE_MATCH_{i} 宽 {m_w} < 模板 {tpl_name} 宽 {tpl_w} => 模板放不进识别区')
            self.assertGreaterEqual(
                m_h, tpl_h,
                f'C_DE_MATCH_{i} 高 {m_h} < 模板 {tpl_name} 高 {tpl_h} => 模板放不进识别区')

    def test_template_png_size_matches_roi(self):
        """模板 PNG 的像素尺寸必须等于 roi_front 宽高，否则匹配直接失败。"""
        for name in self.templates.values():
            r = self.rules[name]
            f = r['file']
            p = REPO / f.lstrip('./')
            self.assertTrue(p.exists(), f'{name} 素材缺失: {f}')
            size = png_size(p)
            self.assertIsNotNone(size, f'{name} 不是 PNG: {f}')
            self.assertEqual(
                size, (r['roi_front'][2], r['roi_front'][3]),
                f'{name} 的 PNG {size[0]}x{size[1]} != roi_front '
                f'{r["roi_front"][2]}x{r["roi_front"][3]}')

    def test_box_center_template_sane(self):
        """新增的 I_DE_BOX_CENTER：规则、素材、尺寸三者齐全。"""
        self.assertIn('I_DE_BOX_CENTER', self.rules)
        r = self.rules['I_DE_BOX_CENTER']
        p = REPO / r['file'].lstrip('./')
        self.assertTrue(p.exists(), f'素材缺失: {r["file"]}')
        size = png_size(p)
        self.assertEqual(size, (r['roi_front'][2], r['roi_front'][3]))

    def test_to_real_world_kept(self):
        """回归守卫：本库自己的 I_DE_TO_REAL_WORLD 必须保留，且仍被页面导航引用。

        上游 194a3eda 把这条规则删掉换成了 I_DE_BOX_CENTER，
        但本库 tasks/DemonEncounter/page.py:13 还在用它做页面导航边
        （page_demon_encounter->page_rwt）。照抄上游会把这条边打断。

        注意引用点在 page.py 而不是 script_task.py —— 断言必须查对文件，
        否则会写出一个「查了但根本不在那儿」的假失败。
        """
        page_py = REPO / 'tasks' / 'DemonEncounter' / 'page.py'
        self.assertIn('I_DE_TO_REAL_WORLD', self.rules,
                      '本库 I_DE_TO_REAL_WORLD 被删了，但 page.py 仍在引用它')
        src = page_py.read_text(encoding='utf-8')
        self.assertIn(
            'I_DE_TO_REAL_WORLD', src,
            'page.py 应保留 I_DE_TO_REAL_WORLD 引用（page_demon_encounter->page_rwt 这条导航边）')


if __name__ == '__main__':
    unittest.main()
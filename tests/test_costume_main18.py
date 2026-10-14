"""Offline regression tests for COSTUME_MAIN_18 (玉岚狐庭 / multi-frame courtyard).

本库的 16/17 号已分别被「琦梦茨庭」「狐栖归处」占用，而上游 runhey/solana 是
16=狐栖归处、17=玉岚狐庭，编号错位一格。因此新增 18 号承载玉岚狐庭，
且必须保证 16/17 完全不动。

这里用 ast + 受控命名空间执行 production 源码，避免拉起 cv2/pydantic 等重依赖。
"""
import ast
import json
import struct
import unittest
from enum import Enum
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COSTUME_DIR = ROOT / 'tasks/Component/Costume'
MAIN18_DIR = COSTUME_DIR / 'main18'

FRAME_SUFFIXES = ('A', 'B', 'C')
# 规则名组 -> 模板文件名前缀（check_main 那组带 main18_ 前缀，其余不带）
EXPECTED_GROUPS = {
    'I_CHECK_MAIN': 'main18_check_main_18',
    'I_MAIN_GOTO_EXPLORATION': 'main18_main_goto_exploration_18',
    'I_MAIN_GOTO_SUMMON': 'main18_main_goto_summon_18',
    'I_MAIN_GOTO_TOWN': 'main18_main_goto_town_18',
    'I_PET_HOUSE': 'main18_pet_house_18',
}


class _CachedProperty:
    """复刻 module.base.decorator.cached_property 的语义（非数据描述符）。"""

    def __init__(self, func):
        self.func = func

    def __get__(self, obj, cls):
        if obj is None:
            return self
        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value


class FakeRuleImage:
    """最小 RuleImage 替身：签名与 production 一致，带 cached_property name。"""

    METHOD_TEMPLATE_MATCH = 'Template matching'
    DEFAULT_MULTI_SCALE_STEP = 0.1

    def __init__(
        self,
        roi_front,
        roi_back,
        method='Template matching',
        threshold=0.8,
        file='',
        profile=None,
    ):
        self.roi_front = list(roi_front)
        self.roi_back = tuple(roi_back)
        self.method = method
        self.threshold = threshold
        self.file = file
        self.profile = profile
        self.scale_range = None
        self.scale_step = self.DEFAULT_MULTI_SCALE_STEP
        self._image = 'loaded-template'
        self._match_init = False

    @_CachedProperty
    def name(self):
        return Path(self.file).stem.upper()

    # --- 原生 RuleImage 行为，用于验证 detach 后确实回落 ---
    def match(self, image, threshold=None, frame_id=None):
        return 'native-match'

    def coord(self):
        return ('native-coord',)

    def front_center(self):
        return ('native-center',)

    def pre_process(self, image):
        return image

    # --- 供 RuleGif.search 使用 ---
    def to_service_payload(self):
        return {'name': self.name, 'file': self.file, 'roi_front': list(self.roi_front)}

    def _apply_match_result(self, result):
        if result.get('matched'):
            self.roi_front = [int(v) for v in result['roi_front']]
            return True
        return False


class FakeImageClient:
    def __init__(self, matched_index=None):
        self.matched_index = matched_index
        self.calls = 0

    def match_many(self, rules_data, image, frame_id=None, threshold=None):
        self.calls += 1
        return [
            {
                'matched': index == self.matched_index,
                'roi_front': [100 + index, 200, 30, 40],
            }
            for index in range(len(rules_data))
        ]


def load_rulgif(client=None):
    """执行 production 的 RuleGif 类，替换掉 RPC 依赖与 RuleImage。"""
    source = (ROOT / 'module/atom/gif.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'RuleGif'
    )
    namespace = {
        'types': __import__('types'),
        'RuleImage': FakeRuleImage,
        'get_image_client': lambda: client,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), 'module/atom/gif.py', 'exec'),
        namespace,
    )
    return namespace['RuleGif']


def load_rules(relative):
    """执行 assets.py 的资产类，用替身替换 RuleImage 等原子类型。"""
    source = (ROOT / relative).read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith('Assets')
    )
    mod = ast.Module(body=[node], type_ignores=[])
    namespace = {
        'RuleImage': FakeRuleImage,
        'RuleClick': type('RuleClick', (), {}),
        'RuleScatter': type('RuleScatter', (), {}),
        'RuleLongClick': type('RuleLongClick', (), {}),
        'RuleSwipe': type('RuleSwipe', (), {}),
        'RuleOcr': type('RuleOcr', (), {}),
        'RuleList': type('RuleList', (), {}),
    }
    exec(compile(mod, relative, 'exec'), namespace)
    return namespace[node.name]


def load_main_type():
    source = (COSTUME_DIR / 'config.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainType'
    )
    namespace = {'Enum': Enum}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'config.py', 'exec'), namespace)
    return namespace['MainType']


def load_main_costume_model(main_type):
    """只执行 main_costume_model 的构造语句（跳过需要 pydantic 的部分）。"""
    source = (COSTUME_DIR / 'costume_base.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    body = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == 'main_costume_model':
                body.append(node)
            elif (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == 'main_costume_model'
            ):
                body.append(node)
    namespace = {'MainType': main_type}
    exec(
        compile(ast.Module(body=body, type_ignores=[]), 'costume_base.py', 'exec'),
        namespace,
    )
    return namespace['main_costume_model']


def png_dimensions(path):
    raw = path.read_bytes()
    assert raw[:8] == b'\x89PNG\r\n\x1a\n', f'not a png: {path}'
    return struct.unpack('>II', raw[16:24])


class MainTypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main_type = load_main_type()

    def test_main18_exists(self):
        self.assertEqual(self.main_type.COSTUME_MAIN_18.value, 'costume_main_18')

    def test_existing_16_17_are_untouched(self):
        """编号错位是本次的核心风险，16/17 的值必须保持原样。"""
        self.assertEqual(self.main_type.COSTUME_MAIN_16.value, 'costume_main_16')
        self.assertEqual(self.main_type.COSTUME_MAIN_17.value, 'costume_main_17')
        self.assertEqual(self.main_type.COSTUME_MAIN_15.value, 'costume_main_15')


class MainCostumeModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main_type = load_main_type()
        cls.model = load_main_costume_model(cls.main_type)

    def test_all_types_1_to_17_still_present(self):
        for index in range(1, 18):
            key = getattr(self.main_type, f'COSTUME_MAIN_{index}')
            self.assertIn(key, self.model, f'COSTUME_MAIN_{index} 不应被改动')

    def test_main16_and_main17_stay_single_frame(self):
        """16/17 必须仍是单帧字符串，不能被多帧改造波及。"""
        for index in (16, 17):
            key = getattr(self.main_type, f'COSTUME_MAIN_{index}')
            for name, value in self.model[key].items():
                self.assertIsInstance(
                    value, str, f'COSTUME_MAIN_{index} 的 {name} 应为单帧字符串'
                )
                self.assertTrue(value.endswith(f'_{index}'))

    def test_main18_is_three_frame_lists(self):
        model = self.model[self.main_type.COSTUME_MAIN_18]
        self.assertEqual(set(model), set(EXPECTED_GROUPS))
        for group in EXPECTED_GROUPS:
            frames = model[group]
            self.assertIsInstance(frames, list)
            self.assertEqual(
                frames, [f'{group}_{18}_{suffix}'.upper() for suffix in FRAME_SUFFIXES]
            )
            self.assertTrue(
                all(f'{group[2:]}_18' in frame for frame in frames),
                f'{group} 的帧名应指向 18 号',
            )

    def test_main18_check_rule_is_the_first_frame(self):
        """_main_check_rule 依赖 I_CHECK_MAIN 的第一帧做皮肤反查。"""
        self.assertEqual(
            self.model[self.main_type.COSTUME_MAIN_18]['I_CHECK_MAIN'][0],
            'I_CHECK_MAIN_18_A',
        )


class Main18AssetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = load_rules('tasks/Component/Costume/assets.py')
        cls.image_json = json.loads((MAIN18_DIR / 'image.json').read_text(encoding='utf-8'))

    def test_all_15_rules_exist_and_point_into_main18(self):
        for group, prefix in EXPECTED_GROUPS.items():
            for suffix in FRAME_SUFFIXES:
                name = f'{group}_{18}_{suffix}'.upper()
                self.assertTrue(hasattr(self.assets, name), f'缺少规则 {name}')
                rule = getattr(self.assets, name)
                self.assertEqual(
                    rule.file,
                    f'./tasks/Component/Costume/main18/{prefix}_{suffix.lower()}.png',
                )

    def test_every_template_exists_with_matching_roi_size(self):
        """模板像素尺寸必须等于 roi_front 的宽高，否则匹配直接崩。"""
        checked = 0
        for group, prefix in EXPECTED_GROUPS.items():
            for suffix in FRAME_SUFFIXES:
                name = f'{group}_{18}_{suffix}'.upper()
                rule = getattr(self.assets, name)
                png = MAIN18_DIR / f'{prefix}_{suffix.lower()}.png'
                self.assertTrue(png.exists(), f'缺少模板 {png.name}')
                width, height = png_dimensions(png)
                self.assertEqual(
                    (width, height),
                    (rule.roi_front[2], rule.roi_front[3]),
                    f'{png.name} 尺寸 {width}x{height} 与 roi_front '
                    f'{rule.roi_front[2]}x{rule.roi_front[3]} 不一致',
                )
                checked += 1
        self.assertEqual(checked, 15)

    def test_image_json_matches_assets_py(self):
        """image.json 是 assets.py 的生成源，两者必须逐条一致。"""
        self.assertEqual(len(self.image_json), 15)
        for item in self.image_json:
            name = f'I_{item["itemName"].upper()}'
            rule = getattr(self.assets, name)
            self.assertEqual(list(rule.roi_front), [int(v) for v in item['roiFront'].split(',')])
            self.assertEqual(list(rule.roi_back), [int(v) for v in item['roiBack'].split(',')])
            self.assertEqual(rule.threshold, item['threshold'])
            self.assertEqual(rule.method, item['method'])
            self.assertTrue(rule.file.endswith(item['imageName']))

    def test_frame_positions_follow_upstream_capture(self):
        """坐标取自上游截图，抽查三个不同位置防止被误"归一化"。"""
        self.assertEqual(self.assets.I_CHECK_MAIN_18_A.roi_front, [260, 168, 80, 54])
        self.assertEqual(self.assets.I_PET_HOUSE_18_C.roi_front, [1248, 406, 19, 59])
        self.assertEqual(
            self.assets.I_MAIN_GOTO_EXPLORATION_18_B.roi_front, [719, 216, 41, 42]
        )


class RuleGifAttachTest(unittest.TestCase):
    """attach_to / detach_from 的行为契约（多帧庭院靠它落地）。"""

    def setUp(self):
        self.client = FakeImageClient()
        self.RuleGif = load_rulgif(self.client)
        self.frames = [
            FakeRuleImage(
                roi_front=(10 * i, 20, 30, 40),
                roi_back=(0, 0, 100, 100),
                file=f'./frames/frame_{suffix}.png',
            )
            for i, suffix in enumerate(FRAME_SUFFIXES)
        ]
        self.target = FakeRuleImage(
            roi_front=(5, 6, 7, 8), roi_back=(0, 0, 100, 100), file='./frames/plain.png'
        )

    def test_attach_swaps_in_gif_behaviour_without_replacing_object(self):
        original_id = id(self.target)
        self.RuleGif.attach_to(self.target, self.frames)

        self.assertEqual(id(self.target), original_id)
        self.assertEqual(self.target.targets, self.frames)
        self.assertEqual(self.target.roi_front, [0, 0, 0, 0])
        self.assertEqual(self.target.roi_back, self.frames[0].roi_back)
        self.assertIs(self.target.appear_target, self.frames[0])
        self.assertEqual(self.target.name, self.frames[0].name)
        # 上游不写 file，本库靠它反查当前皮肤，必须同步
        self.assertEqual(self.target.file, self.frames[0].file)

    def test_match_tries_frames_in_order_and_reports_the_hit(self):
        self.client.matched_index = 2
        self.RuleGif.attach_to(self.target, self.frames)

        self.assertTrue(self.target.match(None, frame_id='f1'))
        self.assertEqual(self.client.calls, 1)
        self.assertIs(self.target.appear_target, self.frames[2])
        self.assertEqual(self.target.roi_front, [102, 200, 30, 40])

    def test_match_returns_false_when_no_frame_hits(self):
        self.client.matched_index = None
        self.RuleGif.attach_to(self.target, self.frames)
        self.assertFalse(self.target.match(None, frame_id='f1'))

    def test_detach_restores_native_behaviour(self):
        self.client.matched_index = 0
        self.RuleGif.attach_to(self.target, self.frames)
        self.assertTrue(self.target.match(None, frame_id='f1'))

        self.RuleGif.detach_from(self.target)

        self.assertEqual(self.target.match(None), 'native-match')
        self.assertEqual(self.target.coord(), ('native-coord',))
        self.assertEqual(self.target.front_center(), ('native-center',))
        self.assertNotIn('targets', self.target.__dict__)
        self.assertNotIn('appear_target', self.target.__dict__)
        self.assertIsNone(self.target._image)

    def test_detach_is_safe_on_plain_rule_image(self):
        self.RuleGif.detach_from(self.target)
        self.assertEqual(self.target.match(None), 'native-match')


class CostumeBaseWiringTest(unittest.TestCase):
    """costume_base 的接线：模块对了但没接上等于没改。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (COSTUME_DIR / 'costume_base.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def _func(self, name):
        return next(
            n
            for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        )

    def test_activate_main_costume_detaches_before_switching(self):
        """多帧切单帧时必须先摘掉 GIF 方法，否则会按旧帧继续匹配。"""
        func = self._func('_activate_main_costume')
        calls = [
            n.func.attr
            for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        self.assertIn('detach_from', calls)
        self.assertIn('attach_to', calls)

    def test_main_check_rule_handles_multi_frame(self):
        func = self._func('_main_check_rule')
        calls = [
            n.func.attr
            for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        self.assertIn('attach_to', calls)

    def test_asset_resolution_helper_exists(self):
        self.assertIsNotNone(self._func('_resolve_main_assets'))

    def test_i18n_label_present(self):
        labels = json.loads((ROOT / 'assets/i18n/zh-CN.json').read_text(encoding='utf-8'))
        self.assertEqual(labels['costume_main_18'], '玉岚狐庭')
        self.assertEqual(labels['costume_main_17'], '狐栖归处')
        self.assertEqual(labels['costume_main_16'], '琦梦茨庭')


if __name__ == '__main__':
    unittest.main()

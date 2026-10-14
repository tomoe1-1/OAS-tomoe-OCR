"""Offline regression tests for the ported random-click probability gate.

移植自 runhey/solana ``tasks/Component/GeneralBattle/battle_wait.py`` 的四道门
（开场延迟 / 每场上限 / 跨战冷却 / 触发概率）。上游的用例基于 pytest + 它自己的
Context 体系，这里按本库的 unittest 约定等价改写，并额外钉住与 production 接线。

全部离线：不需要模拟器、鼠标或网络。
"""
import ast
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'random_click_gate_subject',
    ROOT / 'tasks/Component/GeneralBattle/random_click_gate.py',
)
rcg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rcg
spec.loader.exec_module(rcg)


class FakeSelf:
    """最小替身：只记录随机动作被放行了几次。"""

    def __init__(self, gate):
        self.gate = gate
        self.calls = 0
        self.in_battle = True

    def run(self, now=None):
        if not self.gate.gate(in_battle=self.in_battle, now=now):
            return False
        self.calls += 1
        return True


def options(**overrides):
    return rcg.RandomClickGateOptions(**overrides)


def open_gate(**overrides):
    """把门控推进到「除概率门以外全过」的状态，方便单独测某一扇门。"""
    overrides.setdefault('start_delay', (0.0, 0.0))
    overrides.setdefault('cooldown', (0.0, 0.0))
    overrides.setdefault('trigger_probability', 1.0)
    gate = rcg.RandomClickGate(options=options(**overrides))
    # 显式标记「本场已解析过」，跳过首次调用的延迟解析分支
    gate.per_battle.start_delay_resolved = 0.0
    gate.per_battle.execution_limit_resolved = 5
    gate.per_battle.first_allowed_time = 1.0
    gate.last_attempt_time = -9999.0
    return gate


class RandomClickGateTest(unittest.TestCase):
    # --- 默认参数与上游一致 ---------------------------------------------------

    def test_default_options_match_upstream(self):
        o = rcg.RandomClickGateOptions()
        self.assertEqual(o.start_delay, (5.0, 10.0))
        self.assertEqual(o.cooldown, (30.0, 60.0))
        self.assertEqual(o.trigger_probability, 0.6)
        self.assertEqual(o.execution_limit, (1, 2))

    # --- 门 0：非战斗态 -------------------------------------------------------

    def test_not_in_battle_skips(self):
        gate = open_gate()
        fake = FakeSelf(gate)
        fake.in_battle = False
        self.assertFalse(fake.run(now=100.0))
        self.assertEqual(fake.calls, 0)
        self.assertEqual(gate.per_battle.execution_count, 0)
        # 非战斗态不消耗冷却
        self.assertEqual(gate.last_attempt_time, -9999.0)

    # --- 首次调用：解析开场延迟与每场上限 --------------------------------------

    def test_first_call_resolves_delay_and_limit(self):
        gate = rcg.RandomClickGate()
        self.assertEqual(gate.per_battle.first_allowed_time, 0.0)

        self.assertFalse(gate.gate(now=100.0))
        self.assertGreaterEqual(gate.per_battle.start_delay_resolved, 5.0)
        self.assertLessEqual(gate.per_battle.start_delay_resolved, 10.0)
        self.assertIn(gate.per_battle.execution_limit_resolved, (1, 2))
        # 首次调用只解析，不执行
        self.assertEqual(gate.per_battle.execution_count, 0)
        self.assertAlmostEqual(
            gate.per_battle.first_allowed_time,
            100.0 + gate.per_battle.start_delay_resolved,
            places=6,
        )

    # --- 门 1：开场延迟 -------------------------------------------------------

    def test_start_delay_blocks_until_first_allowed_time(self):
        gate = open_gate(start_delay=(100.0, 100.0))
        gate.per_battle.first_allowed_time = 1000.0
        fake = FakeSelf(gate)

        self.assertFalse(fake.run(now=999.9))
        self.assertEqual(fake.calls, 0)
        self.assertTrue(fake.run(now=1000.0))
        self.assertEqual(fake.calls, 1)

    # --- 门 2：每场上限 -------------------------------------------------------

    def test_execution_limit_blocks_after_count_reached(self):
        gate = open_gate()
        gate.per_battle.execution_limit_resolved = 1
        gate.per_battle.execution_count = 1
        fake = FakeSelf(gate)

        self.assertFalse(fake.run(now=100.0))
        self.assertEqual(fake.calls, 0)

    def test_execution_limit_allows_up_to_limit_then_blocks(self):
        gate = open_gate()
        gate.per_battle.execution_limit_resolved = 2
        fake = FakeSelf(gate)

        self.assertTrue(fake.run(now=100.0))
        self.assertTrue(fake.run(now=200.0))
        self.assertFalse(fake.run(now=300.0))
        self.assertEqual(gate.per_battle.execution_count, 2)

    # --- 门 3：跨战冷却 -------------------------------------------------------

    def test_cooldown_blocks_within_cooling_window(self):
        gate = open_gate(cooldown=(9999.0, 9999.0))
        gate.last_attempt_time = 100.0
        fake = FakeSelf(gate)

        self.assertFalse(fake.run(now=150.0))
        self.assertEqual(fake.calls, 0)

    def test_cooldown_records_attempt_even_when_probability_blocked(self):
        gate = open_gate(trigger_probability=0.0)
        fake = FakeSelf(gate)

        self.assertFalse(fake.run(now=100.0))
        self.assertEqual(fake.calls, 0)
        # 概率未通过也算一次尝试，冷却周期被刷新
        self.assertEqual(gate.last_attempt_time, 100.0)

    def test_cooldown_survives_start_battle_reset(self):
        """冷却记在跨战斗字段上：新一场开始后依然拦住，避免秒开的连战刷次数。"""
        gate = open_gate(cooldown=(9999.0, 9999.0))
        gate.last_attempt_time = 100.0

        gate.start_battle()
        gate.per_battle.execution_limit_resolved = 5
        gate.per_battle.first_allowed_time = 1.0

        fake = FakeSelf(gate)
        self.assertFalse(fake.run(now=150.0))
        self.assertEqual(fake.calls, 0)

    # --- 门 4 + 全通过 --------------------------------------------------------

    def test_all_gates_pass_executes_once_and_increments(self):
        gate = open_gate()
        fake = FakeSelf(gate)

        self.assertTrue(fake.run(now=100.0))
        self.assertEqual(fake.calls, 1)
        self.assertEqual(gate.per_battle.execution_count, 1)
        self.assertEqual(gate.last_attempt_time, 100.0)

    # --- 每场重置语义 ---------------------------------------------------------

    def test_start_battle_resets_per_battle_state_only(self):
        gate = open_gate()
        gate.per_battle.execution_count = 2
        gate.per_battle.execution_limit_resolved = 2
        gate.per_battle.start_delay_resolved = 7.0
        gate.last_attempt_time = 100.0

        gate.start_battle()

        self.assertEqual(gate.per_battle.execution_count, 0)
        self.assertEqual(gate.per_battle.execution_limit_resolved, 1)
        self.assertEqual(gate.per_battle.start_delay_resolved, 0.0)
        self.assertEqual(gate.per_battle.first_allowed_time, 0.0)
        # 冷却跨战斗保留
        self.assertEqual(gate.last_attempt_time, 100.0)

    # --- 期望次数解析模型（上游实测标定点） ------------------------------------

    def test_expected_random_clicks_matches_upstream_calibration(self):
        o = rcg.RandomClickGateOptions()
        per_battle = o.expected_random_clicks(8.75, 6.24, 1)
        self.assertAlmostEqual(per_battle, 0.125, delta=0.002)

        total = o.expected_random_clicks(8.75, 6.24, 115)
        self.assertAlmostEqual(total, 14.4, delta=0.1)

    def test_expected_random_clicks_scales_with_battles(self):
        o = rcg.RandomClickGateOptions()
        one = o.expected_random_clicks(9.0, 6.0, 1)
        ten = o.expected_random_clicks(9.0, 6.0, 10)
        self.assertAlmostEqual(ten, one * 10, places=6)

    def test_expected_random_clicks_never_exceeds_limit(self):
        """每场上限决定期望的物理天花板，概率再高也不会越界。"""
        o = rcg.RandomClickGateOptions(
            start_delay=(0.0, 0.0),
            cooldown=(1.0, 1.0),
            trigger_probability=1.0,
            execution_limit=(1, 2),
        )
        per_battle = o.expected_random_clicks(600.0, 0.0, 1)
        self.assertLessEqual(per_battle, 2.0)
        # 概率拉满时收敛到随机上限的均值 (1 + 2) / 2
        self.assertAlmostEqual(per_battle, 1.5, delta=0.05)

    def test_expected_random_clicks_rejects_degenerate_cooldown(self):
        """上游原式在 cooldown=(0, 0) 时会除零，本库显式报错而不是抛 ZeroDivisionError。"""
        o = rcg.RandomClickGateOptions(cooldown=(0.0, 0.0))
        with self.assertRaises(ValueError):
            o.expected_random_clicks(10.0, 5.0, 1)


class RandomClickGateWiringTest(unittest.TestCase):
    """钉住 general_battle 的接线：模块对了但没接上等于没改。"""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'tasks/Component/GeneralBattle/general_battle.py').read_text(
            encoding='utf-8'
        )
        cls.tree = ast.parse(cls.source)

    def _func(self, class_name, func_name):
        classes = [n for n in self.tree.body if isinstance(n, ast.ClassDef)]
        cls_node = next(n for n in classes if n.name == class_name)
        return next(
            n
            for n in cls_node.body
            if isinstance(n, ast.FunctionDef) and n.name == func_name
        )

    def test_gate_is_imported(self):
        imports = [
            n for n in ast.walk(self.tree) if isinstance(n, ast.ImportFrom)
        ]
        self.assertTrue(
            any(
                n.module == 'tasks.Component.GeneralBattle.random_click_gate'
                and any(a.name == 'RandomClickGate' for a in n.names)
                for n in imports
            ),
            'general_battle.py 必须从 random_click_gate 导入 RandomClickGate',
        )

    def test_random_click_swipt_uses_gate(self):
        func = self._func('GeneralBattle', 'random_click_swipt')
        called = [
            n
            for n in ast.walk(func)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == 'gate'
        ]
        self.assertTrue(called, 'random_click_swipt 必须走 self._random_click_gate.gate()')

    def test_legacy_dice_roll_is_gone(self):
        func = self._func('GeneralBattle', 'random_click_swipt')
        has_legacy = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == 'randint'
            and [getattr(a, 'value', None) for a in n.args] == [0, 500]
            for n in ast.walk(func)
        )
        self.assertFalse(has_legacy, '旧的 randint(0, 500) 无状态概率应当已被门控取代')

    def test_in_battle_handler_resets_gate_per_battle(self):
        func = self._func('GeneralBattle', '_handle_in_battle')
        resets = [
            n
            for n in ast.walk(func)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == 'start_battle'
        ]
        self.assertTrue(
            resets, '_handle_in_battle 必须在新一场战斗开始时调用 start_battle()'
        )

    def test_gate_instance_is_created_in_init(self):
        func = self._func('GeneralBattle', '__init__')
        self.assertIn('_random_click_gate', ast.unparse(func))


if __name__ == '__main__':
    unittest.main()

# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
"""随机点击/滑动的概率门控。

移植自 runhey/solana ``tasks/Component/GeneralBattle/battle_wait.py`` 中的
``OptionRandomclickDefault`` 与 ``randomclick_gate``（该文件的两级状态 + 12 个
hook 骨架**未**移植，这里只取门控本体，接到本库 ``GeneralBattle`` 上）。

原来本库的做法是“每次循环独立掷一次骰子”（无状态概率），由此带来三个问题：

1. 战斗刚开始就可能触发，开场瞬间的随机动作最容易被识别为脚本；
2. 单场战斗内触发次数没有上限，短战斗连击时行为密度不可控；
3. 概率不可观测，只能靠感觉调，改完不知道期望触发率是多少。

门控把它换成四道门的状态机：

===========  ==========================================  ==========================
门           含义                                        状态存放
===========  ==========================================  ==========================
开场延迟     战斗开始后 ``start_delay`` 秒内不执行        每场战斗重置
每场上限     本场最多执行 ``execution_limit`` 次          每场战斗重置
跨战冷却     距上次**尝试**不足 ``cooldown`` 秒不执行     跨战斗保留（短战斗也能续上间隔）
触发概率     到期后以 ``trigger_probability`` 概率执行    无状态
===========  ==========================================  ==========================

另附 ``expected_random_clicks()``：上游用实测数据标定过的解析模型，可在不跑
游戏的前提下算出稳态期望触发次数（上游实测 14/115 ≈ 12.2%，模型预测 12.5%）。
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field


@dataclass
class RandomClickGateOptions:
    """随机动作的四个可调参数，默认值与上游保持一致。"""

    # 战斗开始后，首次允许执行前等待多久；每场战斗重新随机一次
    start_delay: tuple[float, float] = (5.0, 10.0)
    # 两次执行之间的最小间隔；每次检查时重新随机，跨战斗累计
    cooldown: tuple[float, float] = (30.0, 60.0)
    # 到达可执行时间后，本次实际执行的概率
    trigger_probability: float = 0.6
    # 本场战斗最多执行几次；元组表示开场时随机取一个上限，每场重新随机一次
    execution_limit: tuple[int, int] = (1, 2)

    def expected_random_clicks(
        self,
        battle_seconds: float = 30.0,
        gap_seconds: float = 10.0,
        battles: int = 100,
    ) -> float:
        """估算多场战斗累计的期望随机动作执行次数（稳态解析模型）。

        上游标定数据（照抄，便于日后对照校准）：

        * 实际触发：14/115 ≈ 12.2%（swipe 8 + random_click 6，平均约每 8.2 场一次）
        * 模型预测：单场 0.125 → 115 场 ≈ 14.4 次（12.5%），
          即 ``expected_random_clicks(8.75, 6.24, 115)``
        * 实测战斗时间约 7.0~7.7s 的约 30 场、约 8.9~10.2s 的约 85 场，
          gap 平均约 6.24s，故整体周期均值 8.75 + 6.24 ≈ 15.0s

        Args:
            battle_seconds: 单场战斗的持续时间（秒）。
            gap_seconds: 战斗结束到下一轮战斗的间隔（秒），冷却在该时段内持续流逝。
            battles: 统计的战斗场数。

        Returns:
            float: 累计 ``battles`` 场的期望随机动作执行次数。
        """
        cooldown_low, cooldown_high = self.cooldown
        # 冷却每次检查都重抽，按半正态尺度近似其等效窗口；该系数沿用上游标定值
        cooldown_window_mean = cooldown_low + (math.sqrt(math.pi) / 2) * math.sqrt(
            cooldown_high - cooldown_low
        )
        if cooldown_window_mean <= 0:
            # 上游原式在 cooldown 上下界相等且为 0 时会除零，这里显式报错
            raise ValueError(
                f'cooldown={self.cooldown} 的等效窗口为 0，无法估算期望次数'
            )
        start_delay_low, start_delay_high = self.start_delay
        start_delay_mean = (start_delay_low + start_delay_high) / 2
        attempts_per_battle = (
            battle_seconds + gap_seconds - start_delay_mean
        ) / cooldown_window_mean
        attempts_can_execute = self.trigger_probability * attempts_per_battle
        attempts_can_execute_exp = math.exp(-attempts_can_execute)

        # 这里建模成泊松分布
        limit_low, limit_high = self.execution_limit
        per_battle = 0.0
        for limit in range(limit_low, limit_high + 1):
            probability_below = attempts_can_execute_exp  # P(成功次数 < k)
            expected_with_limit = 0.0
            # E[min(H, limit)] = Σ_{k=1}^{limit} P(成功次数 ≥ k)
            for k in range(1, limit + 1):
                expected_with_limit += 1 - probability_below
                probability_below += (
                    attempts_can_execute_exp
                    * attempts_can_execute ** k
                    / math.factorial(k)
                )
            per_battle += expected_with_limit
        per_battle /= limit_high - limit_low + 1
        return per_battle * battles


@dataclass
class RandomClickGateBattleState:
    """每场战斗重置的门控状态。"""

    # 本场重抽出的开场延迟
    start_delay_resolved: float = 0.0
    # 本场重抽出的执行次数上限
    execution_limit_resolved: int = 1
    # 首次允许执行的时间戳；0.0 表示本场尚未解析过
    first_allowed_time: float = 0.0
    # 本场已实际执行次数
    execution_count: int = 0


@dataclass
class RandomClickGate:
    """四道门的随机动作门控。

    ``per_battle`` 每场重置，``last_attempt_time`` 跨战斗保留，
    这样"刚打完一场秒开下一场"时冷却不会被重置掉。
    """

    options: RandomClickGateOptions = field(default_factory=RandomClickGateOptions)
    per_battle: RandomClickGateBattleState = field(
        default_factory=RandomClickGateBattleState
    )
    # 上次**尝试**时刻；概率未通过也算尝试，用于跨战斗冷却
    last_attempt_time: float = 0.0

    def start_battle(self) -> None:
        """进入新一场战斗：只重置每场状态，冷却记录跨场保留。"""

        self.per_battle = RandomClickGateBattleState()

    def gate(self, in_battle: bool = True, now: float | None = None) -> bool:
        """推进一次判定，四道门全过才放行。

        Args:
            in_battle: 当前是否处于战斗态；非战斗态直接拦截，不消耗任何状态。
            now: 当前时间戳；仅测试需要显式注入。

        Returns:
            bool: True 表示调用方可以执行随机动作，False 表示本次跳过。
        """

        if not in_battle:
            return False

        now = time.time() if now is None else now
        state = self.per_battle
        options = self.options

        # 首次调用：解析开场延迟与每场上限（每场只解析一次）
        if state.first_allowed_time == 0.0:
            state.start_delay_resolved = random.uniform(*options.start_delay)
            state.execution_limit_resolved = random.randint(*options.execution_limit)
            state.first_allowed_time = now + state.start_delay_resolved
            return False

        # 门 1：开场延迟未到
        if now < state.first_allowed_time:
            return False

        # 门 2：本场执行次数已达上限
        if state.execution_count >= state.execution_limit_resolved:
            return False

        # 门 3：冷却未到（记录跨战斗，秒掉的小战斗也能续上间隔）
        if now - self.last_attempt_time < random.uniform(*options.cooldown):
            return False

        # 记录本次尝试时间：无论概率是否通过，都算一次冷却周期
        self.last_attempt_time = now

        # 门 4：触发概率
        if random.random() >= options.trigger_probability:
            return False

        state.execution_count += 1
        return True

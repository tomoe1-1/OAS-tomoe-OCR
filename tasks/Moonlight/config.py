"""独立限时活动：月华流光。"""
from datetime import time, timedelta

from pydantic import Field

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class GeneralConfig(ConfigBase):
    challenge_limit: int = Field(default=1, ge=0, title='挑战次数')
    limit_time: Time = Field(default=Time(hour=1, minute=30), title='最长运行时间')
    random_sleep: bool = Field(default=False, title='行动前随机休息')
    active_souls_clean: bool = Field(default=False, title='结束后整理御魂')

    @property
    def limit_time_v(self) -> timedelta:
        value = self.limit_time
        if isinstance(value, time):
            return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
        return value

    def limit_for(self, action_type: str) -> int:
        return self.challenge_limit if action_type == 'moonlight' else 0


class Moonlight(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    general_config: GeneralConfig = Field(default_factory=GeneralConfig)
    moonlight_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)

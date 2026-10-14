"""Independent configuration for 拾此一瞬."""
from datetime import time, timedelta
from pydantic import Field, validator
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.ActivityShikigami.config import GeneralConfig, VersionExplorationConfig


class MomentGeneralConfig(ConfigBase):
    limit_time: Time = Field(default=Time(hour=1, minute=30),
                             title='Activity Limit Time', description='activity_limit_time_help')
    active_souls_clean: bool = Field(default=False, title='Active Souls Clean',
                                    description='active_souls_clean_help')

    @validator('limit_time', pre=True, always=True)
    def parse_limit_time(cls, value):
        return GeneralConfig.parse_limit_time(value)

    @property
    def limit_time_v(self):
        if isinstance(self.limit_time, time):
            return timedelta(hours=self.limit_time.hour, minutes=self.limit_time.minute,
                             seconds=self.limit_time.second)
        return self.limit_time


class MomentActivity(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    general_config: MomentGeneralConfig = Field(default_factory=MomentGeneralConfig)
    version_exploration_config: VersionExplorationConfig = Field(default_factory=VersionExplorationConfig)
    version_ruins_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    version_trial_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)

"""限时活动中的独立月华流光任务。"""
from cached_property import cached_property

from tasks.ActivityShikigami.base_act import BaseAct
from tasks.Moonlight.activity import MoonlightAct
from tasks.Moonlight.assets import MoonlightAssets


class ScriptTask(MoonlightAct, MoonlightAssets, BaseAct):
    def __init__(self, config, device):
        super().__init__(config, device)
        self.action_count['moonlight'] = 0

    @cached_property
    def conf(self):
        return self.config.model.moonlight

    @property
    def scheduled_task_name(self) -> str:
        return 'Moonlight'

    def battle_config(self, action_type):
        if action_type != 'rich_man':
            raise ValueError(f'Unsupported Moonlight battle config: {action_type}')
        return self.conf.moonlight_battle_conf

    def run(self):
        self.C_SAFE_RANDOM_CLICK_AREA_ACT.reset_click_focuses()
        self.run_moonlight()
        self.finish_activity_task()

"""拾此一瞬 task under the limited-time activities menu."""
from cached_property import cached_property
from tasks.ActivityShikigami.base_act import BaseAct
from tasks.ActivityShikigami.version_activity import VersionExplorationAct
from tasks.MomentActivity.config import MomentActivity


class ScriptTask(VersionExplorationAct, BaseAct):
    @cached_property
    def conf(self) -> MomentActivity:
        return self.config.model.moment_activity

    @property
    def scheduled_task_name(self):
        return 'MomentActivity'

    def run(self):
        self.before_run()
        # An empty choice must never fall back to the legacy exploration.
        if self.conf.version_exploration_config.battle_selection:
            self.run_exploration()
        self.finish_activity_task()

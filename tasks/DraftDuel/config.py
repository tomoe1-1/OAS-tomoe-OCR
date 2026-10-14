"""协同对弈每周任务配置。"""

from enum import Enum

from pydantic import Field

from tasks.Component.config_base import ConfigBase, MultiLine
from tasks.Component.config_scheduler import Scheduler


class DraftMatchMode(str, Enum):
    SOLO = '单人'
    TEAM = '组队'


class DraftRole(str, Enum):
    CAPTAIN = '队长'
    MEMBER = '队员'


class DraftDuelConfig(ConfigBase):
    match_mode: DraftMatchMode = Field(
        title='匹配模式', default=DraftMatchMode.SOLO,
        description='单人直接匹配队友；组队等待大厅内已有队友后开战。',
    )
    role: DraftRole = Field(
        title='组队角色', default=DraftRole.CAPTAIN,
        description='队长=邀请发起者，进大厅后按名单邀请跨区好友并等待队友进入；'
                    '队员=被邀请方，进大厅后等待队长邀请并点击“邀”接受。',
    )
    start_timeout_seconds: int = Field(
        title='进入选人时间（秒）', default=120, ge=30, le=300,
        description='点击“战”后等待第一轮选人出现的最长时间。',
    )
    teammate_timeout_seconds: int = Field(
        title='等待队友时间（秒）', default=180, ge=30, le=600,
        description='组队模式下等待大厅队友加入的最长时间。',
    )
    pick_timeout_seconds: int = Field(
        title='等待下一轮选人时间（秒）', default=30, ge=20, le=180,
        description='超过此时间记录等待预警；继续识别至至少90秒，避免匹配或截图延迟中断对局。',
    )
    completion_count: int = Field(
        title='完成次数', default=1, ge=1, le=20,
        description='本次任务要完成的完整对局数量，每局包含五轮选人及最终结算。',
    )
    battle_timeout_seconds: int = Field(
        title='等待整局结束时间（秒）', default=3600, ge=120, le=7200,
        description='五轮选人后继续本局各场战斗，直到最终结算。',
    )
    invite_friend_list: MultiLine = Field(
        default='', title='邀请跨区好友',
        description='每行一个跨区好友昵称；组队模式进入大厅后，点击右侧加号打开邀请面板，'
                    '切到“跨区”分类并逐个邀请。留空则不邀请。',
    )


class DraftDuel(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    draft_duel_config: DraftDuelConfig = Field(default_factory=DraftDuelConfig)

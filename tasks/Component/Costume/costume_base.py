# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

from module.atom.image import RuleImage
from module.atom.gif import RuleGif
from module.logger import logger

from tasks.Component.Costume.config import (MainType, CostumeConfig, ShikigamiType, BattleType, CourtyardAffairType)
from tasks.Component.Costume.assets import CostumeAssets
from tasks.Component.CostumeBattle.assets import CostumeBattleAssets
from tasks.Component.CostumeShikigami.assets import CostumeShikigamiAssets
from tasks.Component.CustomCourtyardAffair.assets import CustomCourtyardAffairAssets

# 庭院皮肤
# 主界面皮肤（使用字典推导式动态生成）
main_costume_model = {
    getattr(MainType, f"COSTUME_MAIN_{i}"): {
        'I_CHECK_MAIN': f'I_CHECK_MAIN_{i}',
        'I_MAIN_GOTO_EXPLORATION': f'I_MAIN_GOTO_EXPLORATION_{i}',
        'I_MAIN_GOTO_SUMMON': f'I_MAIN_GOTO_SUMMON_{i}',
        'I_MAIN_GOTO_TOWN': f'I_MAIN_GOTO_TOWN_{i}',
        'I_PET_HOUSE': f'I_PET_HOUSE_{i}',
        'I_WQ_DONE': f'I_WQ_DONE_{i}',  # 该条及以下非强制更改, 若对应庭院内容识别不到可以添加
        'I_HARVEST_SIGN': f'I_HARVEST_SIGN_{i}',
        'I_HARVEST_JADE': f'I_HARVEST_JADE_{i}',
        'I_HARVEST_MAIL': f'I_HARVEST_MAIL_{i}',
        'I_HARVEST_SOUL': f'I_HARVEST_SOUL_{i}',
        'I_HARVEST_GUILD_REWARD': f'I_HARVEST_GUILD_REWARD_{i}'
    } for i in range(1, 19)
}
# 玉岚狐庭：庭院内有动画部件，每一帧是一张模板，顺序即匹配优先级。
# 上游把这一套编在它的 COSTUME_MAIN_17 上；本库 16 号已占用（琦梦茨庭），
# 狐栖归处又已经占了 17，所以新增 18 承载它，现有 16/17 编号与素材完全不动。
main_costume_model[MainType.COSTUME_MAIN_18] = {
    'I_CHECK_MAIN': ['I_CHECK_MAIN_18_A', 'I_CHECK_MAIN_18_B', 'I_CHECK_MAIN_18_C'],
    'I_MAIN_GOTO_EXPLORATION': ['I_MAIN_GOTO_EXPLORATION_18_A', 'I_MAIN_GOTO_EXPLORATION_18_B', 'I_MAIN_GOTO_EXPLORATION_18_C'],
    'I_MAIN_GOTO_SUMMON': ['I_MAIN_GOTO_SUMMON_18_A', 'I_MAIN_GOTO_SUMMON_18_B', 'I_MAIN_GOTO_SUMMON_18_C'],
    'I_MAIN_GOTO_TOWN': ['I_MAIN_GOTO_TOWN_18_A', 'I_MAIN_GOTO_TOWN_18_B', 'I_MAIN_GOTO_TOWN_18_C'],
    'I_PET_HOUSE': ['I_PET_HOUSE_18_A', 'I_PET_HOUSE_18_B', 'I_PET_HOUSE_18_C'],
}

MAIN_COSTUME_ASSET_KEYS = {
    key
    for model in main_costume_model.values()
    for key in model
}
_default_main_assets: dict[str, RuleImage] = {}

# 战斗主题（使用循环处理常规情况 + 特例处理）
battle_theme_model = {}
for i in range(1, 16):
    entry = {
        'I_LOCAL': f'I_LOCAL_{i}',
        'I_EXIT': f'I_EXIT_{i}',
        'I_FRIENDS': f'I_FRIENDS_{i}',
        'I_BATTLE_INFO': f'I_BATTLE_INFO_{i}',
    }
    if i in [8, 12, 13, 14, 15]:  # 特殊处理
        entry.update({
            'I_WIN': f'I_WIN_{i}',
            'I_DE_WIN': f'I_DE_WIN_{i}',
            'I_FALSE': f'I_FALSE_{i}'
        })
    battle_theme_model[getattr(BattleType, f"COSTUME_BATTLE_{i}")] = entry

# 幕间主题
shikigami_costume_model = {
    getattr(ShikigamiType, f"COSTUME_SHIKIGAMI_{i}"): {
        # GameUi 进出式神录
        'I_CHECK_RECORDS': f'I_CHECK_RECORDS_{i}',
        'I_RECORD_SOUL_BACK': f'I_RECORD_SOUL_BACK_{i}',
        # SwitchSoul 相关
        'I_SOUL_PRESET': f'I_SOUL_PRESET_{i}',
        'I_SOU_CHECK_IN': f'I_SOU_CHECK_IN_{i}',
        'I_SOU_TEAM_PRESENT': f'I_SOU_TEAM_PRESENT_{i}',
        'I_SOU_CLICK_PRESENT': f'I_SOU_CLICK_PRESENT_{i}',
        'I_SOU_SWITCH_SURE': f'I_SOU_SWITCH_SURE_{i}',
        # SwitchSoul 分组相关 (1-7组)
        **{f'I_SOU_CHECK_GROUP_{g}': f'I_SOU_CHECK_GROUP_{g}_{i}' for g in range(1, 8)},
        # SwitchSoul 队伍相关 (1-4队)
        **{f'I_SOU_SWITCH_{t}': f'I_SOU_SWITCH_{t}_{i}' for t in range(1, 5)},
        # SoulsTidy 相关
        'I_ST_SOULS': f'I_ST_SOULS_{i}',
        'I_ST_REPLACE': f'I_ST_REPLACE_{i}',
    }
    for i in range(1, 13)  # 目前支持 COSTUME_SHIKIGAMI_1 到 COSTUME_SHIKIGAMI_10
}

# 庭院事务皮肤
courtyard_affair_model = {
    getattr(CourtyardAffairType, f"CUSTOM_COURTYARD_AFFAIR_{i}"): {
        'I_CHECK_COURTYARD_AFFAIRS': f'I_CHECK_COURTYARD_AFFAIRS_{i}',
        'I_ONE_COMPLETE': f'I_ONE_COMPLETE_{i}',
        'I_ENTER_DAILY': f'I_ENTER_DAILY_{i}',
        'I_CHECK_IN_DAILY': f'I_CHECK_IN_DAILY_{i}',
    } for i in range(1, 2)
}


class CostumeBase:
    def check_costume(self, config: CostumeConfig=None):
        if config is None:
            config: CostumeConfig = self.config.model.global_game.costume_config
        self.check_costume_main(config.costume_main_type)
        self.check_costume_battle(config.costume_battle_type)
        self.check_costume_shikigami(config.costume_shikigami_type)
        self.check_custom_courtyard_affair(config.custom_courtyard_affair)

    def replace_img(self,
                    asset_before: str,
                    asset_after: RuleImage,
                    rp_roi_back: bool = True):
        if not hasattr(self, asset_before):
            return
        # setattr(self, asset_before, asset_after)
        asset_before_object: RuleImage = getattr(self, asset_before)
        asset_before_object.roi_front = list(asset_after.roi_front)
        if rp_roi_back:
            asset_before_object.roi_back = tuple(asset_after.roi_back)
        asset_before_object.threshold = asset_after.threshold
        asset_before_object.method = asset_after.method
        asset_before_object.file = asset_after.file
        asset_before_object.scale_range = asset_after.scale_range
        asset_before_object.scale_step = asset_after.scale_step
        asset_before_object._image = None
        asset_before_object._kp = None
        asset_before_object._des = None
        asset_before_object._match_init = False
        asset_before_object.__dict__.pop('name', None)

    @staticmethod
    def _clone_rule_image(rule: RuleImage) -> RuleImage:
        clone = RuleImage(
            roi_front=tuple(rule.roi_front),
            roi_back=tuple(rule.roi_back),
            threshold=rule.threshold,
            method=rule.method,
            file=rule.file,
        )
        clone.scale_range = rule.scale_range
        clone.scale_step = rule.scale_step
        return clone

    def _snapshot_default_main_assets(self) -> None:
        """在首次替换前保存当前任务可见的原始庭院素材。"""
        for key in MAIN_COSTUME_ASSET_KEYS:
            if key in _default_main_assets or not hasattr(self, key):
                continue
            rule = getattr(self, key)
            if isinstance(rule, RuleImage):
                _default_main_assets[key] = self._clone_rule_image(rule)

    def _resolve_main_assets(self, value) -> list[RuleImage]:
        """把模型里的「规则名」或「规则名列表」解析成实际存在的 RuleImage 列表。

        未采集的规则会被静默跳过（沿用 `getattr(..., None)` 的既有约定），
        这样多帧皮肤缺帧时也不会让整套激活直接失败。
        """
        assets = getattr(self, '_costume_main_assets', None)
        if assets is None:
            return []
        names = value if isinstance(value, (list, tuple)) else [value]
        return [
            rule
            for rule in (getattr(assets, name, None) for name in names)
            if rule is not None
        ]

    def _main_check_rule(self, main_type: MainType) -> RuleImage | None:
        if main_type == MainType.COSTUME_MAIN:
            return _default_main_assets.get('I_CHECK_MAIN')
        rules = self._resolve_main_assets(main_costume_model[main_type]['I_CHECK_MAIN'])
        if not rules:
            return None
        if len(rules) == 1:
            return rules[0]
        # 多帧皮肤：克隆主帧后挂载多帧匹配。用克隆是为了让返回的对象仍是
        # RuleImage（`prepare_appear_cache` 有 isinstance 过滤），同时不污染
        # 共享的 CostumeAssets 规则实例。
        probe = self._clone_rule_image(rules[0])
        RuleGif.attach_to(probe, rules)
        return probe

    def _infer_active_main_type(self) -> MainType | None:
        if not hasattr(self, 'I_CHECK_MAIN'):
            return None
        active_file = str(self.I_CHECK_MAIN.file).replace('\\', '/').lower()
        for main_type in MainType:
            rule = self._main_check_rule(main_type)
            if rule is None:
                continue
            if str(rule.file).replace('\\', '/').lower() == active_file:
                return main_type
        return None

    def _activate_main_costume(self, main_type: MainType) -> None:
        """恢复默认素材后覆盖目标庭院，避免可选素材沿用上一套。"""
        for key, default_rule in _default_main_assets.items():
            self.replace_img(key, default_rule)

        # 上一套可能是多帧皮肤（被 attach_to 原地挂载过）。replace_img 只还原字段、
        # 不摘方法，残留的 GIF 方法会继续按旧的多帧目标匹配，所以这里显式摘掉。
        check_rule = getattr(self, 'I_CHECK_MAIN', None)
        if isinstance(check_rule, RuleImage):
            RuleGif.detach_from(check_rule)

        if main_type != MainType.COSTUME_MAIN:
            for key, value in main_costume_model[main_type].items():
                if not hasattr(self, key):
                    continue
                rules = self._resolve_main_assets(value)
                if not rules:
                    continue
                if len(rules) == 1:
                    self.replace_img(key, rules[0])
                else:
                    RuleGif.attach_to(getattr(self, key), rules)

        self.current_main_type = main_type
        device = getattr(self, 'device', None)
        invalidate = getattr(device, 'invalidate_image_batch_cache', None)
        if callable(invalidate):
            invalidate()
        logger.info(f'Active main costume: {main_type.value}')

    def check_costume_main(self, main_types: MainType | list[MainType]):
        self._snapshot_default_main_assets()
        self._costume_main_assets = CostumeAssets()
        if isinstance(main_types, (str, MainType)):
            main_types = [MainType(main_types)]
        else:
            main_types = [MainType(item) for item in main_types]
        self._main_costume_candidates = tuple(dict.fromkeys(main_types))

        if len(self._main_costume_candidates) == 1:
            self._activate_main_costume(self._main_costume_candidates[0])
            return

        self.current_main_type = self._infer_active_main_type()
        logger.info(
            'Enable random main costume detection: '
            + ', '.join(item.value for item in self._main_costume_candidates)
        )

    def detect_random_main_costume(self, threshold: float = None) -> bool:
        """当前庭院模板失配时批量识别候选皮肤并切换整套素材。"""
        candidates = getattr(self, '_main_costume_candidates', ())
        if len(candidates) <= 1 or not hasattr(self, 'device'):
            return False

        rules = []
        rule_types = []
        for main_type in candidates:
            if main_type == getattr(self, 'current_main_type', None):
                continue
            rule = self._main_check_rule(main_type)
            if rule is not None:
                rules.append(rule)
                rule_types.append(main_type)
        if not rules:
            return False

        if threshold is None and hasattr(self, 'prepare_appear_cache'):
            self.prepare_appear_cache(rules)

        for main_type, rule in zip(rule_types, rules):
            if threshold is None:
                cached = self.device.get_image_batch_cache(
                    rule, frame_id=self.device.image_frame_id
                )
                matched = (
                    rule._apply_match_result(cached)
                    if cached is not None
                    else rule.match(
                        self.device.image,
                        frame_id=self.device.image_frame_id,
                    )
                )
            else:
                matched = rule.match(
                    self.device.image,
                    threshold=threshold,
                    frame_id=self.device.image_frame_id,
                )
            if not matched:
                continue
            logger.info(f'Detected random main costume: {main_type.value}')
            self._activate_main_costume(main_type)
            return True
        return False

    def check_costume_battle(self, battle_type: BattleType):
        if battle_type == BattleType.COSTUME_BATTLE_DEFAULT:
            return
        logger.info(f'Switch battle theme {battle_type}')
        costume_battle_assets = CostumeBattleAssets()
        for key, value in battle_theme_model[battle_type].items():
            assert_value: RuleImage = getattr(costume_battle_assets, value)
            # 绿标的坐标点范围不变
            if key == 'I_LOCAL':
                self.replace_img(key, assert_value, rp_roi_back=False)
            else:
                self.replace_img(key, assert_value)

    def check_costume_shikigami(self, shikigami_type: ShikigamiType):
        if shikigami_type == ShikigamiType.COSTUME_SHIKIGAMI_DEFAULT:
            return
        logger.info(f'Switch shikigami theme {shikigami_type}')
        shikigami_assets = CostumeShikigamiAssets()
        model = shikigami_costume_model.get(shikigami_type, {})
        for key, value in model.items():
            if not hasattr(shikigami_assets, value):
                # 尚未采集完成的资产，跳过
                continue
            assert_value: RuleImage = getattr(shikigami_assets, value)
            # 一般不需要固定 back ROI，如确有需要可在此为特例设置 rp_roi_back=False
            self.replace_img(key, assert_value)

    def check_custom_courtyard_affair(self, courtyard_affair_type: CourtyardAffairType):
        if courtyard_affair_type == CourtyardAffairType.CUSTOM_COURTYARD_AFFAIR_DEFAULT:
            return
        logger.info(f'Switch courtyard affair {courtyard_affair_type}')
        courtyard_affair_assets = CustomCourtyardAffairAssets()
        for key, value in courtyard_affair_model[courtyard_affair_type].items():
            assert_value: RuleImage = getattr(courtyard_affair_assets, value)
            self.replace_img(key, assert_value)


if __name__ == '__main__':
    c = CostumeBase()
    c.check_costume_main(MainType.COSTUME_MAIN_2)

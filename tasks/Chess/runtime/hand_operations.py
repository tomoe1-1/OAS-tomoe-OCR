"""Chess runtime: hand, deployment, soul, cleanup, and recall operations."""

# This Python file uses the following encoding: utf-8

import random
import math
import time
from collections import Counter

import cv2
import numpy as np

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.exception import GameStuckError, TaskRecoveryFailed
from module.logger import logger
from tasks.Chess.runtime.event_actions import ChessEventActionsMixin
from tasks.Chess.runtime.emblem_equipment import ChessEmblemEquipmentMixin
from tasks.Chess.runtime.board_positions import SET_JADE_AREAS, SET_POSITIONS
from tasks.Chess.runtime.press_and_drag import Press_and_Drag
from tasks.Chess.strategy.grigri import (
    grigri_bond_name,
    grigri_category,
    resolve_grigri_name,
)
from tasks.Chess.strategy.shikigami_catalog import (
    SHIKIGAMI_BONDS_BY_ROMAJI,
)
from tasks.Chess.strategy.soul_catalog import SOUL_BY_CHINESE_NAME


class ChessHandOperationsMixin(ChessEmblemEquipmentMixin, ChessEventActionsMixin):
    """Internal mixin; use through ``ScriptTask`` only."""

    def _shikigami_attributes(
        self,
        name: str,
    ) -> tuple[str | None, str | None, str | None]:
        """返回式神本局属性：(守护之印, 御魂1, 御魂2)。"""
        attributes = getattr(self, '_board_shikigami_attributes', {})
        return attributes.get(name, (None, None, None))

    def _set_shikigami_attributes(
        self,
        name: str,
        values: tuple[str | None, str | None, str | None],
    ) -> None:
        attributes = dict(
            getattr(self, '_board_shikigami_attributes', {})
        )
        attributes[name] = values
        self._board_shikigami_attributes = attributes
        logger.debug(f'Chess shikigami attributes: {name}={values}')

    def _shikigami_name_at_set(self, set_index: int) -> str | None:
        """按本局实际位置反查指定站位的目标式神。"""
        if (
            getattr(self, '_arakawa_goldfish_current_position', None)
            == set_index
        ):
            # 荒川金鱼属于特殊单位，不能成为任何装备的目标。
            return None
        deployed_names = set(getattr(self, '_board_lineup_names', set()))
        actual_positions = getattr(self, '_board_actual_positions', {})
        return next((
            name
            for name in deployed_names
            if actual_positions.get(
                name,
                self.shikigami_deploy_positions.get(name),
            ) == set_index
        ), None)

    def _record_actual_lineup_position(
        self,
        name: str,
        set_index: int,
    ) -> None:
        positions = dict(getattr(self, '_board_actual_positions', {}))
        positions[name] = int(set_index)
        self._board_actual_positions = positions

    def _record_shikigami_soul(self, name: str, soul_name: str) -> bool:
        """写入普通御魂槽；重复御魂或两槽已满时拒绝写入。"""
        protect, soul_1, soul_2 = self._shikigami_attributes(name)
        if soul_name in (soul_1, soul_2):
            return False
        if soul_1 is None:
            soul_1 = soul_name
        elif soul_2 is None:
            soul_2 = soul_name
        else:
            return False
        self._set_shikigami_attributes(
            name,
            (protect, soul_1, soul_2),
        )
        return True

    @staticmethod
    def _rule_center(rule: RuleImage | RuleClick) -> tuple[int, int]:
        x, y, width, height = rule.roi_back
        return x + width // 2, y + height // 2

    def _set_position(self, set_index: int) -> tuple[int, int]:
        """读取独立配置中维护的 1-12 号纯站位坐标。"""
        try:
            return tuple(SET_POSITIONS[int(set_index)])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f'Chess board set position is not configured: {set_index}'
            ) from exc

    def _set_jade_area(self, set_index: int) -> tuple[int, int, int, int]:
        """读取独立配置中维护的 1-12 号勾玉占位检测区域。"""
        try:
            return tuple(SET_JADE_AREAS[int(set_index)])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f'Chess board jade area is not configured: {set_index}'
            ) from exc

    def sell_hand_card(self, source: tuple[int, int]) -> None:
        """统一拖到左侧售卖区，避免出售动作改变商店开关状态。"""
        target_rule = self.I_EXPERIENCE
        dispatched = Press_and_Drag(
            self.device,
            p1=source,
            p2=self._rule_center(target_rule),
            hold_duration=(0.2, 0.3),
            point_random=(-3, -3, 3, 3),
            swipe_duration=0.5,
            name='CHESS_SELL_UNKNOWN_HAND_CARD',
        )
        if dispatched is False:
            raise TaskRecoveryFailed('Chess drag rejected before dispatch')
        self.close_shikigami_specifics_if_open()

    def close_shikigami_specifics_if_open(self) -> bool:
        """卖卡/上卡误打开式神详情页时，点击安全区域直到关闭。"""
        if not hasattr(self, 'I_SHIKIGAMI_SPECIFICS'):
            return False
        if not self.appear(self.I_SHIKIGAMI_SPECIFICS):
            return False

        logger.warning(
            'Chess shikigami specifics opened unexpectedly, close it'
        )
        for attempt in range(1, self.ACTION_ICON_MAX_ATTEMPTS + 1):
            self.device.click_record_remove(self.C_CLICK_CLOSE_SPECIFICS_AREA)
            self.click(self.C_CLICK_CLOSE_SPECIFICS_AREA)
            if self._wait_chess_action_state(
                lambda: not self.appear(self.I_SHIKIGAMI_SPECIFICS)
            ):
                logger.debug(
                    'Chess shikigami specifics closed: '
                    f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}'
                )
                return True
            logger.warning(
                'Chess shikigami specifics still visible after close click: '
                f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}, '
                f'timeout={self.ACTION_ICON_WAIT_TIMEOUT:.0f}s'
            )
        raise GameStuckError(
            'Chess: failed to close shikigami specifics after 3 attempts'
        )

    def sell_one_lineup_hand_card(self) -> dict | None:
        """紧急清理后仍然手满时，出售最右侧的已识别阵容式神。"""
        hand_cards = self._hand_card_detections()
        if not hand_cards:
            logger.warning(
                'Chess hand is full but no hand-card anchor was detected'
            )
            return None

        lineup_names = set(self.shikigami_deploy_positions)
        candidates = []
        for card in hand_cards:
            identity = self.classify_hand_card(card['roi'])
            if (
                identity['type'] == 'shikigami'
                and identity['name'] in lineup_names
            ):
                candidates.append(identity)
        if not candidates:
            logger.warning(
                'Chess hand remains full but no recognized lineup card '
                'is available for emergency sale'
            )
            return None

        identity = max(
            candidates,
            key=lambda item: item['position'][0],
        )
        position = identity['position']
        sale_key = (identity['type'], identity['name'])
        count_before = self._hand_card_identity_count(*sale_key)
        logger.info(
            f'Sell Chess lineup card after hand remains full: '
            f'name={self._shikigami_display_name(identity["name"])}, '
            f'position={position}'
        )
        self.sell_hand_card(position)
        time.sleep(self.ACTION_SETTLE_INTERVAL)
        self.screenshot()
        self.close_shikigami_specifics_if_open()
        self.screenshot()
        count_after = self._hand_card_identity_count(*sale_key)
        if count_after >= count_before:
            logger.warning(
                'Emergency Chess lineup-card sale not confirmed; '
                f'count={count_before}->{count_after}'
            )
            return None
        logger.info(
            'Emergency Chess lineup-card sale confirmed: '
            f'count={count_before}->{count_after}'
        )
        return {
            'type': 'lineup',
            'name': identity['name'],
            'position': position,
        }

    def _hakuzosu_protect_target_position(
        self,
        verified_names: set[str] | None = None,
    ) -> int | None:
        """从阵容级配置读取守护之印目标位置。"""
        strategy = self.get_lineup_strategy()
        if self.HAKUZOSU_NAME not in strategy['shikigami']:
            return None
        position = strategy.get('hakuzosu_protect_position')
        if position is not None:
            position = int(position)
            target_name = next((
                name
                for name, config in strategy['shikigami'].items()
                if int(config['position']) == position
            ), None)
            if (
                target_name is not None
                and (
                    verified_names is None
                    or target_name in verified_names
                )
            ):
                return position

        # 兼容尚未迁移的第三方四元组阵容配置。
        for name, config in strategy['shikigami'].items():
            if verified_names is not None and name not in verified_names:
                continue
            if config.get('equip_hakuzosu_protect', False):
                return int(config['position'])
        return None

    def _arakawa_goldfish_target_position(self) -> int | None:
        """返回当前阵容配置的荒川金鱼目标格。"""
        position = self.get_lineup_strategy().get(
            'arakawa_goldfish_position'
        )
        return None if position is None else int(position)

    def _lineup_arakawa_names(self) -> set[str]:
        """返回当前阵容中带有荒川羁绊的式神。"""
        return {
            name
            for name in self.get_lineup_strategy()['shikigami']
            if self.ARAKAWA_BOND_NAME
            in SHIKIGAMI_BONDS_BY_ROMAJI.get(name, ())
        }

    def _record_arakawa_goldfish_position(self, set_index: int) -> None:
        """登记本局金鱼状态；金鱼不占人口且不能佩戴任何装备。"""
        set_index = int(set_index)
        self._arakawa_goldfish_current_position = set_index
        units = dict(getattr(self, '_board_special_units', {}))
        units['arakawa_goldfish'] = {
            'display_name': '金鱼',
            'position': set_index,
            'counts_toward_capacity': False,
            'can_equip_soul': False,
            'can_equip_emblem': False,
            'can_equip_hakuzosu_protect': False,
        }
        self._board_special_units = units
        logger.debug(
            'Chess special board unit state: '
            f'arakawa_goldfish={units["arakawa_goldfish"]}'
        )

    def _move_failed_action_goldfish(self, source_position: int) -> bool:
        """将失败目标中已确认的金鱼拖到阵容配置位置，不再二次确认。"""
        target_position = self._arakawa_goldfish_target_position()
        if target_position is None:
            return False
        source_position = int(source_position)
        protected_before = set(
            getattr(self, '_player_deployed_positions', set())
        )
        if source_position == target_position:
            protected_positions = set(protected_before)
            protected_positions.add(target_position)
            self._player_deployed_positions = protected_positions
            self._record_arakawa_goldfish_position(target_position)
            logger.info(
                'Chess failed-action target is goldfish and already at '
                f'assigned set {target_position}'
            )
            return True
        dispatched = Press_and_Drag(
            self.device,
            p1=self._set_position(source_position),
            p2=self._set_position(target_position),
            hold_duration=(0.2, 0.3),
            point_random=(-2, -2, 2, 2),
            swipe_duration=0.5,
            name=(
                f'CHESS_MOVE_ARAKAWA_GOLDFISH_SET_{source_position}'
                f'_TO_{target_position}'
            ),
        )
        if dispatched is False:
            raise TaskRecoveryFailed('Chess drag rejected before dispatch')
        time.sleep(self.FAST_OPERATION_INTERVAL)
        self._record_arakawa_goldfish_position(target_position)
        protected_positions = set(protected_before)
        protected_positions.discard(source_position)
        protected_positions.add(target_position)
        # 若目标格原来是脚本上阵式神，拖动会交换双方位置；源格仍需保护。
        if target_position in protected_before:
            protected_positions.add(source_position)
        self._player_deployed_positions = protected_positions
        logger.info(
            'Move Chess Arakawa goldfish after board action failure: '
            f'{source_position} -> {target_position}'
        )
        return True

    def _handle_goldfish_after_board_action_failure(
        self,
        set_index: int,
        action: str,
    ) -> bool:
        """仅在下阵失败后点击该格详情，被动识别并移动金鱼。"""
        if not self._lineup_arakawa_names():
            return False
        target_position = self._arakawa_goldfish_target_position()
        if target_position is None or not self._is_preparation_mode():
            return False
        self.close_shikigami_specifics_if_open()
        x, y = self._set_position(set_index)
        inspect_rule = RuleClick(
            roi_front=(x - 8, y - 8, 16, 16),
            roi_back=(x - 8, y - 8, 16, 16),
            name=f'chess_inspect_failed_{action}_set_{set_index}',
        )
        logger.info(
            'Inspect failed Chess board action target for Arakawa goldfish: '
            f'action={action}, set={set_index}'
        )
        for attempt in range(1, self.ACTION_ICON_MAX_ATTEMPTS + 1):
            self.device.click_record_remove(inspect_rule)
            self.click(inspect_rule)
            if not self._wait_chess_action_state(
                lambda: self.appear(self.I_SHIKIGAMI_SPECIFICS)
            ):
                logger.warning(
                    'Chess failed-action target specifics did not open: '
                    f'action={action}, set={set_index}, '
                    f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}'
                )
                continue
            is_goldfish = self._wait_chess_action_state(
                lambda: self.appear(self.I_CHECK_GOLDFISH)
            )
            logger.info(
                'Chess failed-action target goldfish identification: '
                f'action={action}, set={set_index}, '
                f'is_goldfish={is_goldfish}'
            )
            self.close_shikigami_specifics_if_open()
            if not is_goldfish:
                return False
            return self._move_failed_action_goldfish(set_index)
        return False

    def _find_hakuzosu_protect_hand_card(self) -> dict | None:
        """定位手牌中的守护之印。"""
        if self._hakuzosu_protect_target_position() is None:
            return None
        matches = self.hakuzosu_protect_rule.match_all_any(
            self.device.image,
            roi=list(self.HAND_AREA),
            threshold=self.hakuzosu_protect_rule.threshold,
            nms_threshold=0.3,
            frame_id=self.device.image_frame_id,
        )
        if not matches:
            return None
        score, x, y, width, height = max(matches, key=lambda item: item[0])
        return {
            'name': self.HAKUZOSU_PROTECT_NAME,
            'display_name': self.HAKUZOSU_PROTECT_DISPLAY_NAME,
            'score': score,
            'position': (x + width // 2, y + height // 2),
        }

    def _hakuzosu_protect_hand_count(self) -> int:
        """统计完整手牌区中的守护之印数量。"""
        if self._hakuzosu_protect_target_position() is None:
            return 0
        return len(self.hakuzosu_protect_rule.match_all_any(
            self.device.image,
            roi=list(self.HAND_AREA),
            threshold=self.hakuzosu_protect_rule.threshold,
            nms_threshold=0.3,
            frame_id=self.device.image_frame_id,
        ))

    def _hakuzosu_protect_match_in_card(
        self,
        card_roi: tuple[int, int, int, int],
    ) -> dict | None:
        """确认指定手牌框是否为守护之印，用于卖卡保护。"""
        if self._hakuzosu_protect_target_position() is None:
            return None
        matches = self.hakuzosu_protect_rule.match_all_any(
            self.device.image,
            roi=list(card_roi),
            threshold=self.hakuzosu_protect_rule.threshold,
            nms_threshold=0.3,
            frame_id=self.device.image_frame_id,
        )
        if not matches:
            return None
        score, x, y, width, height = max(matches, key=lambda item: item[0])
        return {
            'name': self.HAKUZOSU_PROTECT_NAME,
            'text': self.HAKUZOSU_PROTECT_DISPLAY_NAME,
            'score': score,
            'position': (x + width // 2, y + height // 2),
        }

    def equip_hakuzosu_protect_after_deploy(
        self,
        verified_names: set[str] | None = None,
    ) -> bool:
        """按阵容专属御魂配置尝试装备守护之印。"""
        target_position = self._hakuzosu_protect_target_position(
            verified_names
        )
        if target_position is None:
            return False
        target_name = self._shikigami_name_at_set(target_position)
        if target_name is None:
            return False
        protect, soul_1, soul_2 = self._shikigami_attributes(target_name)
        if protect is not None:
            return False
        if not self._is_preparation_mode():
            return False
        card = self._find_hakuzosu_protect_hand_card()
        if card is None:
            logger.debug(
                'Chess Hakuzosu protect card is not in hand after '
                'Byakuzou deployment'
            )
            return False
        logger.info(
            f'检测到{card["display_name"]}(功能)，'
            f'移动到{target_position}号位'
        )
        protect_before = self._hakuzosu_protect_hand_count()
        if not self._equip_soul_card(
            source=card['position'],
            set_index=int(target_position),
            operation_name=self.HAKUZOSU_PROTECT_NAME.upper(),
        ):
            return False
        time.sleep(self.ACTION_SETTLE_INTERVAL)
        self.screenshot()
        protect_after = self._hakuzosu_protect_hand_count()
        if protect_after >= protect_before:
            logger.warning(
                'Chess Hakuzosu protect equip not confirmed: '
                f'{target_name} at set {target_position}, '
                f'hand_count={protect_before}->{protect_after}'
            )
            return False
        self._set_shikigami_attributes(
            target_name,
            (self.HAKUZOSU_PROTECT_DISPLAY_NAME, soul_1, soul_2),
        )
        logger.info(
            'Chess Hakuzosu protect equip confirmed: '
            f'{target_name} at set {target_position}'
        )
        return True

    def _equip_soul_card(
        self,
        source: tuple[int, int],
        set_index: int,
        operation_name: str,
        soul_name: str | None = None,
        pre_drag_guard=None,
    ) -> bool:
        """御魂及阵容特殊手牌共用入口；拖动前必须关闭商店。"""
        equip_round = self._read_round_number()
        equip_generation = getattr(self.device, '_escape_generation', 0)
        if equip_round is None or not self._is_preparation_mode():
            return False
        if not self._ensure_shop_closed():
            logger.warning(
                f'Abort Chess soul equipment: shop could not be closed, '
                f'operation={operation_name}'
            )
            return False
        if (self._read_round_number() != equip_round
                or not self._is_preparation_mode()
                or getattr(self.device, '_escape_generation', 0) != equip_generation):
            return False
        if soul_name is not None:
            fresh = next((card for card in self._soul_hand_cards(required_names=(soul_name,))
                          if card['name'] == soul_name), None)
            if (fresh is None or not self._is_preparation_mode()
                    or self._read_round_number() != equip_round
                    or getattr(self.device, '_escape_generation', 0) != equip_generation
                    or not self._board_set_has_shikigami_visual(set_index)):
                return False
            source = fresh['position']
        budget = getattr(self, '_preparation_work_deadline', None)
        if budget is not None and time.monotonic() + 2 >= budget:
            return False
        target = self._soul_target_position(set_index)
        logger.debug(
            f'Equip Chess soul-type card {operation_name} to set '
            f'{set_index}: source={source}, target={target}'
        )
        dispatched = Press_and_Drag(
            self.device,
            p1=source,
            p2=target,
            pre_dispatch=pre_drag_guard,
            hold_duration=(0.2, 0.3),
            point_random=(-3, -3, 3, 3),
            swipe_duration=0.5,
            name=f'CHESS_EQUIP_SOUL_{operation_name}_SET_{set_index}',
        )
        return dispatched is not False

    def deploy_shikigami_hand_card(
        self,
        name: str,
        source: tuple[int, int],
    ) -> bool:
        """按当前策略站位拖动式神，并交叉确认是否上阵。"""
        set_index = self.shikigami_deploy_positions.get(name)
        if set_index is None:
            logger.warning(f'Chess shikigami has no deploy position: {name}')
            return False

        self.screenshot()
        deploy_round = self._read_round_number()
        deploy_generation = getattr(self.device, '_escape_generation', 0)
        if deploy_round is None or not self._is_preparation_mode():
            return False
        if not self._ensure_shop_closed():
            logger.warning(
                f'Abort Chess shikigami deployment: shop is not closed '
                f'before dragging {name}'
            )
            return False
        self.screenshot()
        if (self._read_round_number() != deploy_round
                or not self._is_preparation_mode()):
            return False
        fresh = self._find_best_shikigami_hand_card(
            excluded_names=set(self.shikigami_deploy_positions) - {name},
        )
        if fresh is None or fresh['name'] != name:
            return False
        if (self._read_round_number() != deploy_round
                or not self._is_preparation_mode()
                or getattr(self.device, '_escape_generation', 0) != deploy_generation
                or self._observe_shop_visibility() is not False):
            return False
        source = fresh['position']
        count_before = self._read_shikigami_count()
        hand_count_before = self._lineup_hand_card_match_count(name)
        target_occupied_before = self._board_set_has_shikigami(set_index)
        target_position = self._set_position(set_index)
        logger.debug(
            f'Deploy Chess shikigami {name} to set {set_index}, '
            f'source={source}, target={target_position}'
        )
        dispatched = Press_and_Drag(
            self.device,
            p1=source,
            p2=target_position,
            hold_duration=(0.2, 0.3),
            point_random=(-3, -3, 3, 3),
            swipe_duration=0.5,
            name=f'CHESS_DEPLOY_{name.upper()}_SET_{set_index}',
        )
        if dispatched is False:
            raise TaskRecoveryFailed('Chess drag rejected before dispatch')
        self.close_shikigami_specifics_if_open()
        time.sleep(self.ACTION_SETTLE_INTERVAL)
        self.screenshot()
        if (self._read_round_number() != deploy_round
                or not self._is_preparation_mode()
                or getattr(self.device, '_escape_generation', 0) != deploy_generation):
            return False
        count_after = self._read_shikigami_count()
        hand_count_after = self._lineup_hand_card_match_count(name)
        target_occupied_after = self._board_set_has_shikigami(set_index)

        count_increased = False
        if count_before is not None and count_after is not None:
            count_increased = (
                count_after['current'] > count_before['current']
            )
        hand_count_decreased = hand_count_after < hand_count_before
        target_became_occupied = (
            not target_occupied_before and target_occupied_after
        )

        # 与御魂装备采用同一确认逻辑：比较完整手牌区中同名卡数量，
        # 数量减少即确认本次上阵成功。人数和目标格只保留为诊断信息。
        succeeded = hand_count_decreased
        if succeeded:
            logger.debug(
                f'Chess shikigami deploy confirmed: {name} -> set {set_index}, '
                f'board_count={count_before["current"] if count_before else "?"}'
                f'->{count_after["current"] if count_after else "?"}, '
                f'hand_count={hand_count_before}->{hand_count_after}, '
                f'target_occupied={target_occupied_before}'
                f'->{target_occupied_after}'
            )
            return True

        logger.warning(
            f'Chess shikigami deploy not confirmed: {name} -> set {set_index}, '
            f'board_count={count_before["current"] if count_before else "?"}'
            f'->{count_after["current"] if count_after else "?"}, '
            f'hand_count={hand_count_before}->{hand_count_after}, '
            f'target_occupied={target_occupied_before}'
            f'->{target_occupied_after}'
        )
        return False

    def _lineup_hand_card_match_count(self, name: str) -> int:
        """统计手牌区内指定阵容式神的实际卡位数量。"""
        candidates = []
        for rule_name, rule in self.lineup_shikigami_hand_rules:
            if rule_name != name:
                continue
            matches = rule.match_all_any(
                self.device.image,
                roi=list(self.HAND_AREA),
                threshold=self.HAND_DEPLOY_TEMPLATE_THRESHOLD,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            )
            candidates.extend(
                (score, x + width // 2, y + height // 2)
                for score, x, y, width, height in matches
            )

        # 同一个式神若存在多个模板，每张实际手牌可能产生多个重叠命中。
        # 以人物中心聚类，只计算一次。
        distinct = []
        for candidate in sorted(candidates, reverse=True):
            _, center_x, center_y = candidate
            if any(
                abs(center_x - kept_x) <= 32
                and abs(center_y - kept_y) <= 40
                for _, kept_x, kept_y in distinct
            ):
                continue
            distinct.append(candidate)
        return len(distinct)

    def _find_best_shikigami_hand_card(
        self,
        excluded_names: set[str] | None = None,
    ) -> dict | None:
        """让阵容头像模板直接扫描整个手牌区，返回最左侧命中卡。"""
        excluded_names = excluded_names or set()
        frame_candidates = []
        for frame_index in range(1, self.HAND_DEPLOY_CONFIRM_FRAMES + 1):
            if frame_index > 1:
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
            frame_candidates.append(
                self._scan_lineup_hand_card_candidates_once(excluded_names)
            )

        merged = {}
        for candidates in frame_candidates:
            for candidate in candidates:
                key = (
                    candidate['name'],
                    round(candidate['position'][0] / 12),
                )
                item = merged.setdefault(key, {
                    **candidate,
                    'scores': [],
                    'frames': 0,
                })
                item['scores'].append(candidate['score'])
                item['frames'] += 1

        candidates = []
        for item in merged.values():
            if item['frames'] < self.HAND_DEPLOY_CONFIRM_FRAMES:
                logger.debug(
                    'Skip Chess lineup hand card candidate: '
                    f'name={item["name"]}, frames={item["frames"]}/'
                    f'{self.HAND_DEPLOY_CONFIRM_FRAMES}'
                )
                continue
            avg_score = sum(item['scores']) / len(item['scores'])
            item['score'] = avg_score
            if avg_score < self.HAND_DEPLOY_TEMPLATE_THRESHOLD:
                logger.debug(
                    'Skip Chess lineup hand card candidate below threshold: '
                    f'name={item["name"]}, avg_score={avg_score:.3f}, '
                    f'threshold={self.HAND_DEPLOY_TEMPLATE_THRESHOLD}'
                )
                continue
            candidates.append(item)
            logger.debug(
                'Chess lineup hand card candidate confirmed: '
                f'name={item["name"]}, '
                f'avg_score={avg_score:.3f}, '
                f'position={item["position"]}'
            )

        if not candidates:
            logger.debug('No deployable Chess lineup hand card detected')
            return None

        # 同名多张时先保留最左侧。不同式神先按阵容配置的上阵权重
        # 排序（数值越低越优先），同权重再保持原有的从左到右顺序。
        selected_by_name = {}
        for candidate in sorted(
            candidates,
            key=lambda item: (item['position'][0], -item['score']),
        ):
            selected_by_name.setdefault(candidate['name'], candidate)
        strategy_shikigami = self.get_lineup_strategy()['shikigami']
        selected = min(
            selected_by_name.values(),
            key=lambda item: (
                int(
                    strategy_shikigami[item['name']].get(
                        'deploy_weight', 1
                    )
                ),
                item['position'][0],
            ),
        )
        selected['deploy_weight'] = int(
            strategy_shikigami[selected['name']].get('deploy_weight', 1)
        )
        return selected

    def _scan_lineup_hand_card_candidates_once(
        self,
        excluded_names: set[str],
    ) -> list[dict]:
        """单帧：阵容头像模板直接在完整手牌区域内匹配。"""
        candidates = []
        for name, rule in self.lineup_shikigami_hand_rules:
            if name in excluded_names:
                continue
            matches = rule.match_all_any(
                self.device.image,
                roi=list(self.HAND_AREA),
                threshold=self.HAND_DEPLOY_TEMPLATE_THRESHOLD,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            )
            for score, x, y, width, height in matches:
                candidate = {
                    'name': name,
                    'score': score,
                    'position': (x + width // 2, y + height // 2),
                }
                logger.info(
                    'Chess deploy hand scan: '
                    f'best={self._shikigami_display_name(name)}, '
                    f'score={score:.3f}, position={candidate["position"]}'
                )
                candidates.append(candidate)

        # 不同角色模板可能在同一张卡上同时得到较低分命中。按人物中心
        # 聚类，每个实际卡位只保留最高分结果，避免同一卡被认成两个人。
        deduplicated = []
        for candidate in sorted(
            candidates,
            key=lambda item: item['score'],
            reverse=True,
        ):
            if any(
                abs(candidate['position'][0] - kept['position'][0]) <= 32
                and abs(candidate['position'][1] - kept['position'][1]) <= 40
                for kept in deduplicated
            ):
                continue
            deduplicated.append(candidate)
        return sorted(
            deduplicated,
            key=lambda item: item['position'][0],
        )

    def _soul_category(self, name: str) -> str | None:
        if name in self.ATTACK_SOUL_NAMES:
            return 'attack'
        if name in self.FUNCTIONAL_SOUL_NAMES:
            return 'functional'
        return None

    def _soul_target_position(self, set_index: int) -> tuple[int, int]:
        """返回御魂类卡牌的投放位置；奇数位统一向北偏移 5 像素。"""
        x, y = self._set_position(set_index)
        if set_index % 2 == 1:
            y += self.SOUL_ODD_SET_Y_OFFSET
        return x, y

    def _soul_targets(
        self, soul_name: str, category: str, verified_names: set[str],
    ) -> list[tuple[int, tuple[int, int]]]:
        """保留专属和攻/功能偏好，空槽允许回退到其他已确认棋子。"""
        strategy = self.get_lineup_strategy()['shikigami']
        positions = getattr(self, '_board_actual_positions', {})
        candidates = []
        wanted_parity = 0 if category == 'attack' else 1
        for name in verified_names:
            if name in getattr(self, "_chess_emblem_uncertain_targets", set()):
                continue
            if name not in self.shikigami_deploy_positions:
                continue
            _, soul_1, soul_2 = self._shikigami_attributes(name)
            if soul_name in (soul_1, soul_2) or (soul_1 is not None and soul_2 is not None):
                continue
            position = positions.get(name, self.shikigami_deploy_positions[name])
            if (self._shikigami_name_at_set(position) != name
                    or not self._board_set_has_shikigami_visual(position)):
                continue
            preferred = soul_name in strategy[name].get('preferred_souls', ())
            candidates.append((not preferred, position % 2 != wanted_parity, position))
        return [(position, self._soul_target_position(position))
                for _, _, position in sorted(candidates)]


    def _is_lineup_preferred_soul(self, soul_name: str) -> bool:
        """判断御魂是否被当前阵容任一式神列为专属御魂。"""
        return any(
            soul_name in config.get('preferred_souls', ())
            for config in self.get_lineup_strategy()['shikigami'].values()
        )

    def _template_soul_hand_cards(self, names=None) -> list[dict]:
        """在手牌区对 soul 模板执行多尺度匹配。"""
        candidates = []
        roi_x, roi_y, roi_width, roi_height = self.HAND_AREA
        source = self.device.image[
            roi_y:roi_y + roi_height,
            roi_x:roi_x + roi_width,
        ]
        for name, rule in self.soul_hand_rules:
            if names is not None and name not in names:
                continue
            category = self._soul_category(name)
            if category is None:
                continue
            matches = []
            template = rule.image
            for scale in self.SOUL_TEMPLATE_SCALES:
                width = max(1, int(template.shape[1] * scale))
                height = max(1, int(template.shape[0] * scale))
                if width > source.shape[1] or height > source.shape[0]:
                    continue
                scaled = cv2.resize(
                    template,
                    (width, height),
                    interpolation=(
                        cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
                    ),
                )
                result = cv2.matchTemplate(
                    source,
                    scaled,
                    cv2.TM_CCOEFF_NORMED,
                )
                locations = np.where(
                    result >= self.SOUL_TEMPLATE_THRESHOLD
                )
                for point_x, point_y in zip(*locations[::-1]):
                    matches.append((
                        float(result[point_y, point_x]),
                        roi_x + int(point_x),
                        roi_y + int(point_y),
                        width,
                        height,
                    ))
            if matches:
                boxes = [list(match[1:]) for match in matches]
                scores = [match[0] for match in matches]
                indices = cv2.dnn.NMSBoxes(
                    boxes,
                    scores,
                    score_threshold=self.SOUL_TEMPLATE_THRESHOLD,
                    nms_threshold=0.3,
                )
                matches = [
                    matches[int(index)]
                    for index in np.array(indices).reshape(-1).tolist()
                ] if len(indices) else []
            for score, x, y, width, height in matches:
                candidates.append({
                    'name': name,
                    'text': self.SOUL_DISPLAY_NAMES[name],
                    'position': (x + width // 2, y + height // 2),
                    'score': score,
                    'source': 'template',
                    'category': category,
                    'roi': (x, y, width, height),
                })
                logger.debug(
                    f'Chess soul image matched: {self.SOUL_DISPLAY_NAMES[name]}, '
                    f'score={score:.3f}, box={(x, y, width, height)}'
                )
        return candidates

    def _soul_hand_cards(self, required_names=()) -> list[dict]:
        """仅使用 soul 文件夹图片识别御魂，并按手牌横坐标去重。"""
        key = (getattr(self.device, 'image_frame_id', None), id(self.device.image),
               getattr(getattr(self, 'chess_state', None), 'game_number', 0),
               getattr(self.device, '_escape_generation', 0),
               getattr(self.device, '_accepted_invitation_generation', 0))
        cached = getattr(self, '_chess_soul_hand_cache', None)
        required_names = set(required_names)
        seen = getattr(self, '_chess_soul_inventory_seen', None)
        if seen and seen[0] == key[2]:
            required_names.update(seen[1])
        same_frame = key[0] is not None and cached and cached[0] == key
        if same_frame and (cached[2] is None or required_names <= cached[2]):
            return [dict(card) for card in cached[1]]
        merged = []
        # Exact readable labels narrow the expensive multi-scale search.
        # OCR never authorizes input: the actual soul artwork must still match.
        names = set(required_names)
        if same_frame:
            names.update(cached[2])
            candidates = [dict(card) for card in cached[1]]
            candidates.extend(self._template_soul_hand_cards(required_names - cached[2]))
        else:
            for item in self.O_BADGE_AREA.detect_and_ocr(self.device.image):
                text = self._normalize_ocr_text(item.ocr_text).strip('()（）[]【】')
                score = float(item.score)
                entry = SOUL_BY_CHINESE_NAME.get(text)
                if entry is not None and math.isfinite(score) and score >= .90:
                    names.add(entry.romaji)
            candidates = self._template_soul_hand_cards(names or None)
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item['position'][0],
                -item['score'],
            ),
        ):
            existing = next((
                item
                for item in merged
                if abs(item['position'][0] - candidate['position'][0]) <= 24
            ), None)
            if existing is None:
                merged.append(candidate)
            elif candidate['score'] > existing['score']:
                merged.remove(existing)
                merged.append(candidate)
        result = sorted(merged, key=lambda item: item['position'][0])
        if key[0] is not None:
            self._chess_soul_hand_cache = (key, tuple(dict(card) for card in result), names or None)
        return result

    def _soul_slot_context(self):
        return (getattr(getattr(self, 'chess_state', None), 'game_number', 0),
                self._read_round_number(), getattr(self.device, '_escape_generation', 0),
                getattr(self.device, '_accepted_invitation_generation', 0))

    def _soul_slot_valid(self, context):
        return (context[0] > 0 and context[1] is not None
                and context[1] not in self.BOSS_CHALLENGE_ROUNDS
                and self._soul_slot_context() == context
                and getattr(self.device.image, 'shape', ()) == (720, 1280, 3)
                and not getattr(self.device, '_handling_invitation', False)
                and self._is_preparation_mode()
                and not self._discover_choices() and not self.appear(self.I_USE_SOUL)
                and self._observe_shop_visibility() is False)

    def _untracked_soul_slots(self, context):
        """Two fresh occupied frames; never invent a unit's name."""
        previous = getattr(self.device, 'image_frame_id', None)
        common = None
        counts = []
        self._chess_untracked_soul_counts = None
        excluded = set(getattr(self, '_board_actual_positions', {}).values())
        excluded.update(self.shikigami_deploy_positions.get(name)
                        for name in getattr(self, '_board_lineup_names', set()))
        excluded.update(unit.get('position') for unit in
                        getattr(self, '_board_special_units', {}).values())
        excluded.add(getattr(self, '_arakawa_goldfish_current_position', None))
        for _ in range(2):
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == previous or not self._soul_slot_valid(context):
                return ()
            previous = frame
            occupied = {i for i in SET_POSITIONS if i not in excluded
                        and self._board_set_has_shikigami_visual(i)}
            common = occupied if common is None else common & occupied
            required = tuple(getattr(self, '_chess_untracked_soul_ledger', {}).get('unconfirmed', {}))
            counts.append(Counter(c['name'] for c in self._soul_hand_cards(required_names=required)))
        self._chess_untracked_soul_counts = counts
        return tuple(sorted(common)) if self._soul_slot_valid(context) else ()

    def _soul_final_drag(self, context, soul_name, position, target_name=None):
        previous = getattr(self.device, 'image_frame_id', None)
        self.screenshot()
        frame = getattr(self.device, 'image_frame_id', None)
        image_id = id(self.device.image)
        observed = time.monotonic()
        if (frame is None or frame == previous or not self._soul_slot_valid(context)
                or not self._board_set_has_shikigami_visual(position)
                or (target_name is not None and self._shikigami_name_at_set(position) != target_name)
                ):
            return None
        remaining = self._read_remaining_time()
        if remaining is None or remaining < 3:
            return None
        deadline = observed + remaining - 1
        work = getattr(self, '_preparation_work_deadline', None)
        if work is not None:
            deadline = min(deadline, work)
        card = next((c for c in self._soul_hand_cards(required_names=(soul_name,)) if c['name'] == soul_name), None)
        if (card is None or not self._soul_slot_valid(context)
                or getattr(self.device, 'image_frame_id', None) != frame
                or id(self.device.image) != image_id or time.monotonic() + 2 >= deadline):
            return None
        return card['position'], self._soul_target_position(position)

    def _equip_untracked_souls(self, max_actions):
        """Ordinary souls may equip system units; emblems/protection need identity."""
        if not self._ensure_shop_closed():
            return []
        context = self._soul_slot_context()
        ledger = getattr(self, '_chess_untracked_soul_ledger', None)
        if not ledger or ledger['game'] != context[0]:
            ledger = dict(game=context[0], attempts={}, equipped={}, unconfirmed={}, failures={})
            self._chess_untracked_soul_ledger = ledger
        equipped = []
        for _ in range(min(self.SOUL_EQUIP_SAFETY_LIMIT, max_actions)):
            slots = self._untracked_soul_slots(context)
            cards = self._soul_hand_cards() if slots else []
            selected = None
            for card in cards:
                if ledger['failures'].get(card['name'], 0) >= 2:
                    continue
                wanted_parity = 0 if card['category'] == 'attack' else 1
                for position in sorted(slots, key=lambda i: (i % 2 != wanted_parity, i)):
                    gear = ledger['equipped'].get((context[1], position), set())
                    key = (card['name'], position)
                    if len(gear) >= 2 or card['name'] in gear or ledger['attempts'].get(key, 0) >= 2:
                        continue
                    pending = ledger['unconfirmed'].get(card['name'])
                    counts = getattr(self, '_chess_untracked_soul_counts', None)
                    if pending is not None and (not counts or any(c[card['name']] != pending for c in counts)):
                        continue
                    selected = (card, position, key)
                    break
                if selected:
                    break
            if not selected or not self._soul_slot_valid(context):
                break
            card, position, key = selected
            before = sum(c['name'] == card['name'] for c in cards)
            # A second attempt requires this invocation's two new occupied
            # frames and a positively reidentified complete soul card.
            ledger['attempts'][key] = ledger['attempts'].get(key, 0) + 1
            ledger['unconfirmed'][card['name']] = before
            if not self._equip_soul_card(card['position'], position, card['name'].upper(),
                    soul_name=card['name'], pre_drag_guard=lambda:
                    self._soul_final_drag(context, card['name'], position)):
                ledger['attempts'][key] -= 1  # Guard rejection dispatched nothing.
                break
            if not self._confirm_soul_equipped(card['name'], before, context[1], context[2], position, context=context):
                ledger['failures'][card['name']] = ledger['failures'].get(card['name'], 0) + 1
                logger.warning(f'Chess system-unit soul unconfirmed: {card["text"]}, set={position}')
                continue
            ledger['equipped'].setdefault((context[1], position), set()).add(card['name'])
            ledger['unconfirmed'].pop(card['name'], None)
            ledger['failures'].pop(card['name'], None)
            equipped.append(card['name'])
            logger.info(f'Chess system-unit soul consumed: {card["text"]}, set={position}; unit name unknown')
        return equipped

    def _soul_match_in_card(
        self,
        card_roi: tuple[int, int, int, int],
        soul_cards: list[dict] | None = None,
    ) -> dict | None:
        """返回落在指定手牌框内的最佳御魂图片匹配。"""
        x, _, width, _ = card_roi
        soul_cards = (
            self._soul_hand_cards() if soul_cards is None else soul_cards
        )
        matches = [
            item
            for item in soul_cards
            if x - 8 <= item['position'][0] <= x + width + 8
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: item['score'])

    def _discover_named_hand_cards(
        self,
        expected_text: str,
        allow_fuzzy: bool,
    ) -> list[dict]:
        """使用完整卡名在手牌文字区定位发现类特殊卡。"""
        results = self.O_BADGE_AREA.detect_and_ocr(self.device.image)
        roi_x, roi_y = self.O_BADGE_AREA.roi[:2]
        cards = []
        for result in results:
            text = self._normalize_ocr_text(result.ocr_text).strip(
                '()（）[]【】'
            )
            if not text:
                continue
            if text == expected_text:
                similarity = 1.0
                matched = True
            elif allow_fuzzy:
                matched, similarity, _ = self._fuzzy_text_match(
                    expected_text,
                    text,
                )
            else:
                matched = False
                similarity = 0.0
            if not matched:
                continue

            points = result.box
            left = min(int(point[0]) for point in points)
            right = max(int(point[0]) for point in points)
            top = min(int(point[1]) for point in points)
            bottom = max(int(point[1]) for point in points)
            cards.append({
                'kind': expected_text,
                'text': text,
                'similarity': similarity,
                'score': float(result.score),
                'position': (
                    roi_x + (left + right) // 2,
                    roi_y + (top + bottom) // 2,
                ),
            })
        return sorted(cards, key=lambda item: item['position'][0])

    def _discover_soul_hand_cards(self) -> list[dict]:
        """定位“发现御魂”；保留原有 OCR 编辑距离兜底。"""
        return self._discover_named_hand_cards('发现御魂', allow_fuzzy=True)

    def _discover_badge_hand_cards(self) -> list[dict]:
        """严格按完整四字定位“发现纹章”，禁止仅匹配“纹章”。"""
        return self._discover_named_hand_cards('发现纹章', allow_fuzzy=False)

    def _wait_for_discover_soul_choices(self) -> list[RuleImage]:
        """等待发现御魂三选一界面，并返回本帧实际出现的选项。"""
        deadline = time.monotonic() + self.DISCOVER_SOUL_UI_TIMEOUT
        rules = (
            self.I_SELECT_SOUL_1,
            self.I_SELECT_SOUL_2,
            self.I_SELECT_SOUL_3,
        )
        while time.monotonic() < deadline:
            self.screenshot()
            options = [rule for rule in rules if self.appear(rule)]
            if options:
                return options
            time.sleep(self.SCREENSHOT_INTERVAL)
        return []

    def discover_souls_from_hand(self) -> int:
        """Use discovery cards through the persistent single-input event ledger."""
        return self._discover_cards_guarded()

    def _confirm_soul_equipped(
        self, soul_name: str, count_before: int, round_no: int,
        generation: int, set_index: int, context=None,
    ) -> bool:
        deadline = time.monotonic() + 8
        last_frame = None
        confirmations = 0
        for _ in range(4):
            if time.monotonic() >= deadline:
                break
            time.sleep(self.ACTION_SETTLE_INTERVAL)
            self.screenshot()
            if (not self._is_preparation_mode()
                    or self._read_round_number() != round_no
                    or getattr(self.device, '_escape_generation', 0) != generation
                    or (context is not None and self._soul_slot_context() != context)
                    or self._observe_shop_visibility() is not False
                    or not self._board_set_has_shikigami_visual(set_index)):
                return False
            count_after = sum(card['name'] == soul_name for card in self._soul_hand_cards(required_names=(soul_name,)))
            frame = self.device.image_frame_id
            if frame != last_frame:
                confirmations = confirmations + 1 if count_after < count_before else 0
                last_frame = frame
            if confirmations >= 2:
                return True
        return False


    def equip_souls_from_hand(
        self,
        verified_names: set[str] | None = None,
        max_actions: int | None = None,
    ) -> list[str]:
        """给已确认式神装备御魂，并用完整手牌同名数量减少确认。"""
        if not self._is_preparation_mode():
            logger.debug(
                'Skip equipping Chess souls: preparation was interrupted or '
                'has ended'
            )
            return []
        # “发现御魂”会生成普通御魂，因此必须先全部处理，再扫描并装配
        # soul 文件夹中的御魂卡。
        if max_actions is None:
            self.discover_souls_from_hand()
        # Once per preparation round, recover cards whose labels OCR missed.
        # Later fresh frames search these artwork names even if labels vanish.
        inventory_key = self._soul_slot_context()
        remaining = self._read_remaining_time()
        work = getattr(self, '_preparation_work_deadline', None)
        if (getattr(self, '_chess_soul_inventory_round', None) != inventory_key
                and remaining is not None and remaining >= 8
                and (work is None or time.monotonic() + 6 < work)
                and self._ensure_shop_closed()):
            self._chess_soul_inventory_round = inventory_key
            all_names = tuple(entry.romaji for entry in SOUL_BY_CHINESE_NAME.values())
            cards = self._soul_hand_cards(required_names=all_names)
            previous = getattr(self, '_chess_soul_inventory_seen', None)
            seen_names = set(previous[1]) if previous and previous[0] == inventory_key[0] else set()
            seen_names.update(card['name'] for card in cards)
            self._chess_soul_inventory_seen = (inventory_key[0], seen_names)
        verified_names = set(verified_names or set())
        if not verified_names:
            return self._equip_untracked_souls(2 if max_actions is None else max(0, max_actions))

        equipped = []
        if max_actions is None:
            equipped.extend(self.equip_emblems_from_hand(verified_names))
        # 守护之印与普通御魂共享阵容优先目标；它使用独立图片模板，
        # 因此在普通 soul 文件夹扫描前单独处理。每次备阶段都会重试，
        # 不再局限于梦山白藏主刚上阵的瞬间。
        if max_actions is None and self.equip_hakuzosu_protect_after_deploy(verified_names):
            equipped.append(self.HAKUZOSU_PROTECT_NAME)
        repeated_attempts = {}
        target_failures = Counter()
        limit = self.SOUL_EQUIP_SAFETY_LIMIT if max_actions is None else min(self.SOUL_EQUIP_SAFETY_LIMIT, max(0, max_actions))
        for _ in range(limit):
            if not self._is_preparation_mode():
                logger.debug(
                    'Stop equipping Chess souls: '
                    'mode is no longer preparation'
                )
                break

            if not self._ensure_shop_closed():
                break
            self.screenshot()
            if not self._is_preparation_mode():
                break
            selected = None
            selected_target = None
            soul_candidates = sorted(
                self._soul_hand_cards(),
                key=lambda candidate: (
                    not self._is_lineup_preferred_soul(candidate['name']),
                    candidate['position'][0],
                ),
            )
            for candidate in soul_candidates:
                for target in self._soul_targets(
                    candidate['name'],
                    candidate['category'],
                    verified_names,
                ):
                    attempt_key = (
                        candidate['name'],
                        candidate['position'][0] // 20,
                        target[0],
                    )
                    if repeated_attempts.get(attempt_key, 0) >= 2:
                        continue
                    selected = candidate
                    selected_target = target
                    repeated_attempts[attempt_key] = (
                        repeated_attempts.get(attempt_key, 0) + 1
                    )
                    break
                if selected is not None:
                    break

            if selected is None or selected_target is None:
                break

            set_index, _ = selected_target
            target_name = self._shikigami_name_at_set(set_index)
            if target_name is None:
                break
            hand_counts_before = Counter(
                card['name'] for card in self._soul_hand_cards(required_names=(selected['name'],))
            )
            logger.info(
                f'检测到{selected["text"]}'
                f'({self._soul_category_display(selected["category"])})，'
                f'移动到{set_index}号位'
            )
            equip_round = self._read_round_number()
            equip_generation = getattr(self.device, '_escape_generation', 0)
            equip_context = self._soul_slot_context()
            if not self._equip_soul_card(
                source=selected['position'],
                set_index=set_index,
                operation_name=selected['name'].upper(),
                soul_name=selected['name'],
                pre_drag_guard=lambda: self._soul_final_drag(
                    equip_context, selected['name'], set_index, target_name),
            ):
                break
            confirmed = self._confirm_soul_equipped(
                selected['name'], hand_counts_before[selected['name']],
                equip_round, equip_generation, set_index, context=equip_context,
            )
            hand_counts_after = Counter(card['name'] for card in self._soul_hand_cards(required_names=(selected['name'],)))
            if not confirmed:
                attempts = repeated_attempts[
                    (
                        selected['name'],
                        selected['position'][0] // 20,
                        set_index,
                    )
                ]
                logger.warning(
                    f'Chess soul equip not confirmed: '
                    f'{selected["text"]} -> set {set_index}, '
                    f'hand_count={hand_counts_before[selected["name"]]}'
                    f'->{hand_counts_after[selected["name"]]}, '
                    f'attempt={attempts}/2'
                )
                target_failures[target_name] += 1
                # 装备未确认不能证明棋子移位；保留真实上阵记录。
                continue

            target_failures.pop(target_name, None)
            if not self._record_shikigami_soul(
                target_name,
                selected['name'],
            ):
                logger.warning(
                    'Chess soul disappeared from hand but target attributes '
                    f'rejected it: target={target_name}, '
                    f'soul={selected["text"]}'
                )
                continue
            equipped.append(selected['name'])
            logger.debug(
                f'Chess soul equip confirmed: '
                f'{selected["text"]} -> {target_name} at set {set_index}, '
                f'hand_count={hand_counts_before[selected["name"]]}'
                f'->{hand_counts_after[selected["name"]]}'
            )
        else:
            logger.warning(
                'Stop equipping Chess souls at safety limit '
                f'{self.SOUL_EQUIP_SAFETY_LIMIT}'
            )

        logger.debug(f'Chess soul equipment complete, equipped={equipped}')
        return equipped


    def deploy_shikigami_from_hand(self) -> list[str]:
        """关闭商店后上阵手牌式神，并以当前阶数限制场上人数。"""
        # 场上人数 OCR 只有在商店完全收起后才可见。把约束放在上卡
        # 方法内部，避免其他调用入口绕过外层准备流程后无效拖卡。
        if not self._is_preparation_mode():
            logger.debug(
                'Skip Chess shikigami deployment: preparation was interrupted '
                'or has ended'
            )
            return []
        if not self._ensure_shop_closed():
            logger.warning(
                'Skip Chess shikigami deployment: shop could not be closed'
            )
            return []
        if self._is_shop_open():
            logger.warning(
                'Skip Chess shikigami deployment: shop is still visible '
                'after close confirmation'
            )
            return []

        deployed = []
        deployed_names = set(getattr(self, '_board_lineup_names', set()))
        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )

        # 已确认上阵的阵容式神在本局内保持登记。战斗动画、遮挡或勾玉
        # 模板单帧漏识别不能证明式神已经离场；若据此清除名称，会同时
        # 导致重复上阵和专属御魂失去目标。
        failed_attempts = {}
        for _ in range(self.HAND_DEPLOY_SAFETY_LIMIT):
            if not self._is_preparation_mode():
                logger.debug(
                    'Stop deploying Chess hand cards: '
                    'mode is no longer preparation'
                )
                break
            if not self._ensure_shop_closed():
                logger.warning(
                    'Stop deploying Chess hand cards: shop reopened or '
                    'could not be confirmed closed before capacity check'
                )
                break

            capacity = self._read_lineup_capacity_status()
            if capacity is None:
                logger.warning(
                    'Stop deploying Chess hand cards: lineup capacity '
                    'could not be confirmed'
                )
                break
            candidate = self._find_best_shikigami_hand_card(
                excluded_names=(
                    deployed_names
                    | {
                        name
                        for name, attempts in failed_attempts.items()
                        if attempts >= 2
                    }
                ),
            )
            if candidate is None:
                logger.info(
                    'Stop deploying Chess hand cards: no deployable lineup '
                    'hand card candidate'
                )
                break

            logger.info(
                f'Chess deploy candidate: '
                f'{self._shikigami_display_name(candidate["name"])}, '
                f'weight={candidate.get("deploy_weight", 1)}, '
                f'score={candidate["score"]:.3f}, '
                f'position={candidate["position"]}'
            )
            set_index = self.shikigami_deploy_positions[candidate['name']]
            # 每次上阵前都检查系统自动上阵区域。若存在未由脚本登记的
            # 单位，先将其中一个下阵；只有系统位为空时才以动态人数
            # 判断是否还能直接上阵。
            recalled_set = self._recall_one_system_board_card(
                preferred_set_index=set_index,
            )
            if recalled_set is not None:
                # 下阵卡进入手牌后会让整排手牌重新布局。下阵前保存的
                # candidate.position 已失效，必须基于新截图重新定位同名卡。
                self.screenshot()
                candidate_name = candidate['name']
                candidate = self._find_best_shikigami_hand_card(
                    excluded_names=(
                        set(self.shikigami_deploy_positions)
                        - {candidate_name}
                    ),
                )
                if candidate is None or candidate['name'] != candidate_name:
                    logger.warning(
                        'Stop current Chess deployment after system recall: '
                        f'{self._shikigami_display_name(candidate_name)} '
                        'could not be relocated in the reflowed hand'
                    )
                    failed_attempts[candidate_name] = (
                        failed_attempts.get(candidate_name, 0) + 1
                    )
                    continue
                set_index = self.shikigami_deploy_positions[candidate_name]
                logger.info(
                    f'Recall Chess system card at set {recalled_set}, then '
                    f'deploy {self._shikigami_display_name(candidate["name"])} '
                    f'to set {set_index}, refreshed_position='
                    f'{candidate["position"]}'
                )
            elif capacity['full']:
                logger.warning(
                    'Stop deploying Chess hand cards: runtime lineup is full '
                    'and no removable system-deployed card was found at '
                    f'{self.BOARD_RECALL_POSITIONS}'
                )
                break

            if not self.deploy_shikigami_hand_card(
                candidate['name'],
                candidate['position'],
            ):
                failed_attempts[candidate['name']] = (
                    failed_attempts.get(candidate['name'], 0) + 1
                )
                logger.warning(
                    f'Retry Chess shikigami deployment later: '
                    f'{candidate["name"]}, '
                    f'attempt={failed_attempts[candidate["name"]]}/2'
                )
                continue

            deployed.append(candidate['name'])
            deployed_names.add(candidate['name'])
            self._board_lineup_names = deployed_names
            self._record_actual_lineup_position(
                candidate['name'],
                set_index,
            )
            if candidate['name'] not in getattr(
                self,
                '_board_shikigami_attributes',
                {},
            ):
                self._set_shikigami_attributes(
                    candidate['name'],
                    (None, None, None),
                )
            player_positions = set(
                getattr(self, '_player_deployed_positions', set())
            )
            player_positions.add(set_index)
            self._player_deployed_positions = player_positions
            logger.debug(
                'Mark Chess player-deployed position: '
                f'set={set_index}, name={candidate["name"]}'
            )
            if candidate['name'] == self.HAKUZOSU_NAME:
                self.equip_hakuzosu_protect_after_deploy(deployed_names)
        else:
            logger.warning(
                'Stop deploying Chess hand cards at safety limit '
                f'{self.HAND_DEPLOY_SAFETY_LIMIT}'
            )

        logger.debug(f'Chess hand deployment complete, deployed={deployed}')
        return deployed

    def _hand_card_detections(self) -> list[dict]:
        """直接用式神/御魂素材定位已收录手牌，不依赖羁绊 OCR。"""
        template_rules = [
            rule
            for _, rule in (
                list(self.shikigami_hand_rules)
                + list(self.soul_hand_rules)
            )
        ]
        template_rules.append(self.hakuzosu_protect_rule)
        candidates = []
        for rule in template_rules:
            matches = rule.match_all_any(
                self.device.image,
                roi=list(self.HAND_AREA),
                threshold=rule.threshold,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            )
            for score, x, y, width, height in matches:
                candidates.append((score, x, y, width, height))

        # 多个素材可能在同一张卡上产生近邻命中；每个实际卡位只保留
        # 最高分结果。这里保留素材自身矩形，拖动时使用其中心即可。
        card_matches = []
        for candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
            _, x, y, width, height = candidate
            center_x = x + width // 2
            center_y = y + height // 2
            if any(
                abs(center_x - (kept[1] + kept[3] // 2)) <= 28
                and abs(center_y - (kept[2] + kept[4] // 2)) <= 40
                for kept in card_matches
            ):
                continue
            card_matches.append(candidate)

        detections = []
        for score, x, y, width, height in sorted(
            card_matches,
            key=lambda item: item[1],
        ):
            if width <= 0 or height <= 0:
                continue
            detection = {
                'roi': (x, y, width, height),
                'score': score,
            }
            detections.append(detection)
            logger.debug(
                f'Chess hand card template detected: score={score:.3f}, '
                f'roi={detection["roi"]}'
            )
        if not detections:
            logger.debug('Chess hand card templates found no cards')
        return detections

    def _hand_card_rois(self) -> list[tuple[int, int, int, int]]:
        """兼容只需要卡框的分类流程。"""
        return [item['roi'] for item in self._hand_card_detections()]

    def _hand_card_identity_count(
        self,
        card_type: str,
        name: str | None,
    ) -> int:
        """统计手牌中指定素材的命中张数，用于确认出售确实生效。"""
        if card_type == 'shikigami' and name:
            rules = [
                rule
                for rule_name, rule in self.shikigami_hand_rules
                if rule_name == name
            ]
        elif card_type == 'soul' and name:
            rules = [
                rule
                for rule_name, rule in self.soul_hand_rules
                if rule_name == name
            ]
        else:
            return len(self._hand_card_detections())

        matches = []
        for rule in rules:
            matches.extend(rule.match_all_any(
                self.device.image,
                roi=list(self.HAND_AREA),
                threshold=rule.threshold,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            ))

        centers = []
        for score, x, y, width, height in sorted(
            matches,
            key=lambda item: item[0],
            reverse=True,
        ):
            center = (x + width // 2, y + height // 2)
            if any(
                abs(center[0] - kept[0]) <= 28
                and abs(center[1] - kept[1]) <= 40
                for kept in centers
            ):
                continue
            centers.append(center)
        return len(centers)

    def cleanup_non_lineup_hand_cards(
        self,
        allowed_modes: tuple[str, ...] = ('战',),
        emergency: bool = False,
    ) -> list[tuple[int, int]]:
        """独立卖卡环节：保留纹章与未知卡，出售已识别非阵容卡。"""
        if not emergency and self._is_early_round_layout():
            logger.debug(
                'Skip Chess hand cleanup: '
                'alternate layout means round 1-3'
            )
            return []

        sold = []
        failed_sale_attempts = {}
        clean_confirm_frames = 0
        strategy = self.get_lineup_strategy()
        logger.debug(
            'Chess hand cleanup lineup protection: '
            f'lineup={strategy["key"]}, '
            f'names={list(strategy["shikigami"].keys())}'
        )
        for cleanup_pass in range(1, self.HAND_CLEANUP_SAFETY_LIMIT + 1):
            self.close_shikigami_specifics_if_open()
            mode = self._read_chess_mode()
            if not self._is_hand_cleanup_allowed(allowed_modes):
                logger.debug(
                    'Stop cleaning Chess hand cards: '
                    f'mode={mode} is outside {allowed_modes}'
                )
                break

            # Keep emblems for the next preparation equipment pass.
            emblem_positions = self._emblem_hand_positions()

            # 御魂图片可能与星级卡框同时命中。先独立识别御魂，避免
            # classify_hand_card 的固定尺寸模板漏检后将它当作 unknown 出售。
            soul_cards = self._soul_hand_cards()
            discover_cards = (
                self._discover_soul_hand_cards()
                + self._discover_badge_hand_cards()
            )
            sell_target = None
            for card_roi in self._hand_card_rois():
                card_x, _, card_width, _ = card_roi
                if any(card_x - 8 <= point[0] <= card_x + card_width + 8
                       for point in emblem_positions):
                    continue
                discover_card = next((
                    item
                    for item in discover_cards
                    if (
                        card_x - 8
                        <= item['position'][0]
                        <= card_x + card_width + 8
                    )
                ), None)
                if discover_card is not None:
                    logger.debug(
                        'Keep unused Chess discover card during cleanup: '
                        f'text={discover_card["text"]}, '
                        f'position={discover_card["position"]}'
                    )
                    continue
                soul = self._soul_match_in_card(card_roi, soul_cards)
                if soul is not None:
                    logger.debug(
                        'Keep Chess soul hand card during cleanup: '
                        f'name={soul["text"]}, score={soul["score"]:.3f}, '
                        f'position={soul["position"]}'
                    )
                    continue
                protect = self._hakuzosu_protect_match_in_card(card_roi)
                if protect is not None:
                    logger.debug(
                        'Keep Chess Hakuzosu protect card during cleanup: '
                        f'score={protect["score"]:.3f}, '
                        f'position={protect["position"]}'
                    )
                    continue
                result = self.classify_hand_card(card_roi)
                keep = (
                    result['type'] == 'soul'
                    or (
                        result['type'] == 'shikigami'
                        and result['name'] in self.shikigami_deploy_positions
                    )
                )
                if keep:
                    continue

                if result['type'] == 'unknown':
                    # Unknown cards may be unread emblems: never sell them.
                    continue
                sale_key = (result['type'], result['name'])
                if failed_sale_attempts.get(sale_key, 0) >= 2:
                    logger.debug(
                        'Skip repeatedly failed Chess sale target in this '
                        f'cleanup pass: type={result["type"]}, '
                        f'name={result["name"]}'
                    )
                    continue
                sell_target = result
                break
            if sell_target is None:
                clean_confirm_frames += 1
                if (
                    clean_confirm_frames
                    >= self.HAND_CLEANUP_CLEAN_CONFIRM_FRAMES
                ):
                    logger.debug(
                        'Chess hand cleanup confirmed clean: '
                        f'frames={clean_confirm_frames}'
                    )
                    break
                logger.debug(
                    'No sellable Chess hand card in current scan, '
                    'wait for layout and verify again: '
                    f'frame={clean_confirm_frames}/'
                    f'{self.HAND_CLEANUP_CLEAN_CONFIRM_FRAMES}'
                )
                time.sleep(self.SLOW_POLL_INTERVAL)
                self.screenshot()
                continue

            logger.info(
                f'Sell Chess card: '
                f'type={sell_target["type"]}, '
                f'name={self._shikigami_display_name(sell_target["name"])}, '
                f'position={sell_target["position"]}'
            )
            sale_key = (sell_target['type'], sell_target['name'])
            count_before = self._hand_card_identity_count(*sale_key)
            self.sell_hand_card(sell_target['position'])
            time.sleep(self.SLOW_POLL_INTERVAL)
            self.screenshot()
            self.close_shikigami_specifics_if_open()
            self.screenshot()
            count_after = self._hand_card_identity_count(*sale_key)
            if count_after < count_before:
                sold.append(sell_target['position'])
                failed_sale_attempts.pop(sale_key, None)
                clean_confirm_frames = 0
                logger.info(
                    'Chess card sale confirmed: '
                    f'type={sell_target["type"]}, '
                    f'name={self._shikigami_display_name(sell_target["name"])}, '
                    f'count={count_before}->{count_after}'
                )
                continue

            failed_sale_attempts[sale_key] = (
                failed_sale_attempts.get(sale_key, 0) + 1
            )
            clean_confirm_frames = 0
            logger.warning(
                'Chess card sale not confirmed; do not register as sold: '
                f'type={sell_target["type"]}, '
                f'name={self._shikigami_display_name(sell_target["name"])}, '
                f'count={count_before}->{count_after}, '
                f'attempt={failed_sale_attempts[sale_key]}/2'
            )
        else:
            logger.warning(
                'Stop cleaning Chess hand cards at safety limit '
                f'{self.HAND_CLEANUP_SAFETY_LIMIT}'
            )

        logger.debug(f'Chess non-lineup hand cleanup complete, sold={sold}')
        return sold

    def _is_hand_cleanup_allowed(
        self,
        allowed_modes: tuple[str, ...] = ('战',),
    ) -> bool:
        """卖卡只在调用方指定阶段执行，阶段变化后立刻停止。"""
        return self._read_chess_mode() in allowed_modes

    def emergency_cleanup_hand(self) -> dict | None:
        """手牌满时依次装配御魂、出售非阵容卡，然后交还原操作重试。"""
        mode = self._read_chess_mode()
        if mode not in ('备', '战'):
            logger.warning(
                f'Cannot run emergency Chess hand cleanup in mode={mode}'
            )
            return None
        if not self._ensure_shop_closed():
            logger.warning(
                'Cannot run emergency Chess hand cleanup: '
                'shop could not be closed'
            )
            return None

        self.screenshot()
        equipped = []
        soul_cards = self._soul_hand_cards()
        if soul_cards:
            verified_board_names = {
                name
                for name in getattr(self, '_board_lineup_names', set())
                if name in self.shikigami_deploy_positions
            }
            logger.info(
                'Emergency Chess hand cleanup found souls; equip first: '
                f'count={len(soul_cards)}'
            )
            equipped = self.equip_souls_from_hand(verified_board_names)
            self.screenshot()

        sold = self.cleanup_non_lineup_hand_cards(
            allowed_modes=(mode,),
            emergency=True,
        )
        logger.info(
            'Emergency Chess hand cleanup complete: '
            f'equipped={equipped}, sold_non_lineup={sold}'
        )
        return {
            'type': 'emergency_cleanup',
            'equipped': equipped,
            'sold_non_lineup': sold,
        }

    def _free_one_hand_slot_for_purchase(
        self,
        sell_lineup: bool = False,
    ) -> dict | None:
        """购买受阻时统一清理；清理后重试仍手满才出售阵容卡。"""
        if sell_lineup:
            if not self._ensure_shop_closed():
                return None
            self.screenshot()
            result = self.sell_one_lineup_hand_card()
            if result is None:
                return None
        else:
            result = self.emergency_cleanup_hand()
            if result is None:
                return None

        # 本方法只会在购买失败的恢复路径调用；清理后必须恢复“商店开”
        # 这一购买前置状态，至于卖卡过程本身不主动切换商店。
        if not self._ensure_shop_open():
            logger.warning(
                'Emergency Chess hand cleanup succeeded, but shop could not '
                'be reopened'
            )
            return None
        self.screenshot()
        return result

    def _recognized_emblem_hand_cards(self) -> list[dict]:
        """OCR 识别手牌中的具体纹章，并映射到符咒图鉴标准名称。"""
        results = self.O_BADGE_AREA.detect_and_ocr(self.device.image)
        roi_x, roi_y = self.O_BADGE_AREA.roi[:2]
        cards = []
        for result in results:
            text = self._normalize_ocr_text(result.ocr_text).strip(
                '()（）[]【】'
            )
            if not text or text == '发现纹章':
                continue
            name, similarity = resolve_grigri_name(text)
            if name is None or grigri_category(name) != 'emblem':
                continue
            points = result.box
            left = min(int(point[0]) for point in points)
            right = max(int(point[0]) for point in points)
            top = min(int(point[1]) for point in points)
            bottom = max(int(point[1]) for point in points)
            cards.append({
                'name': name,
                'text': text,
                'similarity': similarity,
                'score': float(result.score),
                'position': (
                    roi_x + (left + right) // 2,
                    roi_y + (top + bottom) // 2,
                ),
            })
        return sorted(cards, key=lambda item: item['position'][0])

    def _emblem_targets(self, emblem_name, verified_names):
        return self._guarded_emblem_targets(emblem_name, verified_names)

    def equip_emblems_from_hand(self, verified_names, max_actions=None):
        """Retain unconfirmed emblems; one guarded drag per card."""
        return self._equip_emblems_guarded(verified_names, max_actions)

    def _find_badge_hand_card(self) -> dict | None:
        """返回 badge_area 内最左侧“纹章”文字的屏幕坐标。"""
        results = self.O_BADGE_AREA.detect_and_ocr(self.device.image)
        matches = []
        roi_x, roi_y = self.O_BADGE_AREA.roi[:2]
        for result in results:
            text = self._normalize_ocr_text(result.ocr_text).strip(
                '()（）[]【】'
            )
            # “发现纹章”必须由专用完整卡名流程处理；若这里只按
            # “纹章”子串判断，会在使用前被当作普通纹章卖掉并卡死。
            if text == '发现纹章':
                continue
            if '纹章' not in text:
                continue

            # detect_and_ocr 返回的是相对于 OCR 裁剪区的四点框。
            points = result.box
            left = min(int(point[0]) for point in points)
            right = max(int(point[0]) for point in points)
            top = min(int(point[1]) for point in points)
            bottom = max(int(point[1]) for point in points)
            position = (
                roi_x + (left + right) // 2,
                roi_y + (top + bottom) // 2,
            )
            matches.append({
                'text': text,
                'position': position,
                'score': float(result.score),
            })

        if not matches:
            return None
        matches.sort(key=lambda item: item['position'][0])
        return matches[0]

    def _recall_one_system_board_card(
        self,
        preferred_set_index: int | None = None,
    ) -> int | None:
        """满员上卡时只下阵一个系统卡位，成功则返回对应站位。"""
        self.screenshot()
        recall_round = self._read_round_number()
        recall_generation = getattr(self.device, '_escape_generation', 0)
        if recall_round is None or not self._is_preparation_mode():
            return None
        if self._is_boss_challenge_round():
            logger.debug(
                'Skip Chess system-card recall in boss round: fixed jade '
                f'areas are unavailable, round={self._current_round_no}'
            )
            return None
        if not self._ensure_shop_closed():
            logger.warning(
                'Cannot free Chess lineup slot: shop could not be closed'
            )
            return None

        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )
        positions = list(self.BOARD_RECALL_POSITIONS)
        if preferred_set_index in positions:
            positions.remove(preferred_set_index)
            positions.insert(0, preferred_set_index)

        hand_target = self._rule_center(
            RuleClick(
                roi_front=self.HAND_AREA,
                roi_back=self.HAND_AREA,
                name='chess_hand_area',
            )
        )
        time.sleep(self.ACTION_SETTLE_INTERVAL)
        for set_index in positions:
            if set_index in player_positions:
                continue
            if not self._board_set_has_shikigami(set_index):
                continue

            source = self._set_position(set_index)
            goldfish_kept_at_target = False
            for attempt in range(1, self.ACTION_ICON_MAX_ATTEMPTS + 1):
                drag_source = (
                    source
                    if attempt == 1
                    else (source[0], source[1] - 14)
                )
                self.screenshot()
                if (self._read_round_number() != recall_round
                        or not self._is_preparation_mode()
                        or getattr(self.device, '_escape_generation', 0) != recall_generation
                        or self._observe_shop_visibility() is not False):
                    return None
                dispatched = Press_and_Drag(
                    self.device,
                    p1=drag_source,
                    p2=hand_target,
                    hold_duration=(0.2, 0.3),
                    point_random=(-2, -2, 2, 2),
                    swipe_duration=0.5,
                    name=(
                        f'CHESS_FREE_SYSTEM_SET_{set_index}'
                        f'_ATTEMPT_{attempt}'
                    ),
                )
                if dispatched is False:
                    raise TaskRecoveryFailed('Chess drag rejected before dispatch')
                recalled = self._wait_chess_action_state(
                    lambda: not self._board_set_has_shikigami(set_index)
                )
                if (self._read_round_number() != recall_round
                        or not self._is_preparation_mode()
                        or getattr(self.device, '_escape_generation', 0) != recall_generation):
                    return None
                if recalled:
                    tracked_names = set(
                        getattr(self, '_board_lineup_names', set())
                    )
                    self._board_lineup_names = {
                        name
                        for name in tracked_names
                        if getattr(
                            self,
                            '_board_actual_positions',
                            {},
                        ).get(
                            name,
                            self.shikigami_deploy_positions.get(name),
                        )
                        != set_index
                    }
                    logger.debug(
                        f'Chess system card recalled for deployment: '
                        f'set={set_index}, attempt={attempt}'
                    )
                    return set_index
                if self._handle_goldfish_after_board_action_failure(
                    set_index,
                    action='recall_for_deploy',
                ):
                    self.screenshot()
                    if (self._read_round_number() != recall_round
                            or not self._is_preparation_mode()
                            or getattr(self.device, '_escape_generation', 0) != recall_generation):
                        return None
                    source_still_occupied = self._board_set_has_shikigami(
                        set_index
                    )
                    if not source_still_occupied:
                        logger.info(
                            'Chess failed recall source cleared after moving '
                            f'goldfish: set={set_index}'
                        )
                        return set_index
                    if set_index == self._arakawa_goldfish_target_position():
                        goldfish_kept_at_target = True
                        logger.info(
                            'Keep Chess goldfish at assigned position during '
                            f'system recall: set={set_index}'
                        )
                        break
                    logger.info(
                        'Chess failed recall source remains occupied after '
                        'goldfish swap; continue original recall: '
                        f'set={set_index}'
                    )
                    continue
                logger.warning(
                    'Chess system card recall did not clear jade marker: '
                    f'set={set_index}, '
                    f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}, '
                    f'timeout={self.ACTION_ICON_WAIT_TIMEOUT:.0f}s'
                )
            else:
                raise GameStuckError(
                    'Chess: failed to recall occupied system card at '
                    f'set {set_index} after 3 attempts'
                )
            if goldfish_kept_at_target:
                continue
        return None

    def recall_all_board_cards(self) -> bool:
        """按系统自动上阵顺序，快速回收棋盘右侧四个候选位置。"""
        if self._is_boss_challenge_round():
            logger.debug(
                'Defer Chess board recall in boss round: fixed jade areas '
                f'are unavailable, round={self._current_round_no}'
            )
            return False
        if not self._ensure_shop_closed():
            logger.warning(
                'Abort Chess board recall: shop could not be closed'
            )
            return False
        hand_target = self._rule_center(
            RuleClick(
                roi_front=self.HAND_AREA,
                roi_back=self.HAND_AREA,
                name='chess_hand_area',
            )
        )

        count = self._read_shikigami_count()
        if count is not None and count['current'] == 0:
            logger.debug('Chess board is already empty; skip recall')
            self._board_lineup_names = set()
            self._player_deployed_positions = set()
            self._board_actual_positions = {}
            return True

        tracked_names = set(getattr(self, '_board_lineup_names', set()))
        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )
        recall_positions = tuple(
            set_index
            for set_index in self.BOARD_RECALL_POSITIONS
            if set_index not in player_positions
        )
        protected_recall_positions = sorted(
            set(self.BOARD_RECALL_POSITIONS) & player_positions
        )
        if protected_recall_positions:
            logger.debug(
                'Keep Chess system-set positions during recall: '
                f'they were deployed by script, positions='
                f'{protected_recall_positions}'
            )
        logger.debug(
            f'Chess board recall order: {recall_positions}, '
            f'current_count={None if count is None else count["current"]}'
        )
        if not self._is_preparation_mode():
            logger.debug(
                'Stop recalling Chess board cards: '
                'mode is no longer preparation'
            )
            return False

        # 商店图层消失后，棋盘的触控层仍有一小段收起动画。日志显示此前
        # 11 号位在关闭判定后立即拖动，手势已下发但没有成功下阵。
        time.sleep(self.ACTION_SETTLE_INTERVAL)

        # 每个已占用候选格均以勾玉图标消失确认；单次最多等待3秒，
        # 超时后上移到模型主体重拖，连续三次失败则直接报错。
        for set_index in recall_positions:
            if not self._board_set_has_shikigami(set_index):
                logger.debug(
                    f'Skip Chess recall set {set_index}: '
                    'jade marker is not detected'
                )
                continue
            source = self._set_position(set_index)
            goldfish_handled = False
            for attempt in range(1, self.ACTION_ICON_MAX_ATTEMPTS + 1):
                drag_source = (
                    source
                    if attempt == 1
                    else (source[0], source[1] - 14)
                )
                dispatched = Press_and_Drag(
                    self.device,
                    p1=drag_source,
                    p2=hand_target,
                    hold_duration=(0.2, 0.3),
                    point_random=(-3, -3, 3, 3),
                    swipe_duration=0.5 if attempt > 1 else 0.45,
                    name=(
                        f'CHESS_RECALL_SET_{set_index}'
                        f'_ATTEMPT_{attempt}'
                    ),
                )
                if dispatched is False:
                    raise TaskRecoveryFailed('Chess drag rejected before dispatch')
                if self._wait_chess_action_state(
                    lambda: not self._board_set_has_shikigami(set_index)
                ):
                    logger.debug(
                        'Chess board recall confirmed by jade marker: '
                        f'set={set_index}, '
                        f'attempt={attempt}/'
                        f'{self.ACTION_ICON_MAX_ATTEMPTS}'
                    )
                    count = self._read_shikigami_count()
                    break
                if self._handle_goldfish_after_board_action_failure(
                    set_index,
                    action='recall_all',
                ):
                    self.screenshot()
                    source_still_occupied = self._board_set_has_shikigami(
                        set_index
                    )
                    if (
                        not source_still_occupied
                        or set_index
                        == self._arakawa_goldfish_target_position()
                    ):
                        goldfish_handled = True
                        logger.info(
                            'Continue Chess board recall after handling '
                            f'goldfish: set={set_index}, '
                            f'source_occupied={source_still_occupied}'
                        )
                        break
                    logger.info(
                        'Chess board recall source remains occupied after '
                        'goldfish swap; retry original recall: '
                        f'set={set_index}'
                    )
                    continue
                logger.warning(
                    'Chess board recall jade marker still visible: '
                    f'set={set_index}, '
                    f'attempt={attempt}/{self.ACTION_ICON_MAX_ATTEMPTS}, '
                    f'timeout={self.ACTION_ICON_WAIT_TIMEOUT:.0f}s'
                )
            else:
                raise GameStuckError(
                    'Chess: board recall failed at '
                    f'set {set_index} after 3 attempts'
                )
            if goldfish_handled:
                count = self._read_shikigami_count()

        self.screenshot()
        count = self._read_shikigami_count()
        # 只清除脚本记录中确实位于本次回收区域的式神。若 9 号位是脚本
        # 上阵的卡，或场上仍有 1-8 号位的式神，则保留对应记录。
        self._board_lineup_names = {
            name
            for name in tracked_names
            if getattr(self, '_board_actual_positions', {}).get(
                name,
                self.shikigami_deploy_positions.get(name),
            )
            not in recall_positions
        }
        self._board_actual_positions = {
            name: set_index
            for name, set_index in getattr(
                self,
                '_board_actual_positions',
                {},
            ).items()
            if set_index not in recall_positions
        }
        special_positions = {
            int(unit['position'])
            for unit in getattr(self, '_board_special_units', {}).values()
            if unit.get('position') is not None
        }
        self._player_deployed_positions = (
            (player_positions - set(recall_positions))
            | special_positions
        )
        if count is not None and count['current'] == 0:
            self._board_lineup_names = set()
            self._board_actual_positions = {}
            self._player_deployed_positions = special_positions
        if count is None:
            logger.debug('Chess board recall completed; count is unavailable')
        else:
            logger.debug(
                'Chess board recall completed at positions '
                f'{self.BOARD_RECALL_POSITIONS}: '
                f'{count["current"]}/{count["total"]}'
            )
        return True

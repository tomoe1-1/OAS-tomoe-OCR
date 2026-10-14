"""Chess runtime: shop access, purchasing, and atomic economy operations."""

# This Python file uses the following encoding: utf-8

import re
import time

import cv2
import numpy as np

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.logger import logger
from module.device.click_verification import ClickResponseUnchanged, before_click, after_click, signature
from tasks.Chess.strategy.shikigami_catalog import (
    KNOWN_BONDS,
    NON_SHOP_SHIKIGAMI,
    SHIKIGAMI_ENTRIES,
    SHIKIGAMI_BY_ROMAJI,
    STORE_SHIKIGAMI_BY_SIGNATURE,
    bond_key,
    normalize_bond_ocr_text,
)


class ChessEconomyMixin:
    """Internal mixin; use through ``ScriptTask`` only."""

    # I_MARKET remains the cloth-edge recognition asset, never the tap point.
    # Round-4 real click evidence puts this compact region inside the circle.
    C_MARKET_TOGGLE = RuleClick(
        roi_front=(1190, 642, 30, 28), roi_back=(1190, 642, 30, 28),
        name='CHESS_MARKET_TOGGLE', profile='High')
    C_EXPERIENCE_CENTER = RuleClick(
        roi_front=(75, 625, 20, 20), roi_back=(75, 625, 20, 20),
        name='CHESS_EXPERIENCE_CENTER', profile='High')

    def _market_toggle_context(self, opened):
        return (getattr(getattr(self, 'chess_state', None), 'game_number', 0),
                self._read_round_number(), self._read_chess_mode(), opened,
                getattr(self.device, '_escape_generation', 0),
                getattr(self.device, '_accepted_invitation_generation', 0))

    def _click_market_toggle_guarded(self, key, opened, deadline):
        rule = self.C_MARKET_TOGGLE
        name = str(rule)
        if (getattr(getattr(self.device, 'image', None), 'shape', ()) != (720, 1280, 3)
                or getattr(self.device, '_handling_invitation', False)
                or time.monotonic() >= deadline
                or self.device.handle_control_check(name) is False):
            return False
        if getattr(getattr(self.device, 'image', None), 'shape', ()) != (720, 1280, 3):
            return False
        point = rule.coord()
        before_click(self.device, *point, name)
        previous = getattr(self.device, 'image_frame_id', None)
        self.screenshot()
        frame = getattr(self.device, 'image_frame_id', None)
        if (getattr(getattr(self.device, 'image', None), 'shape', ()) != (720, 1280, 3)
                or frame is None or frame == previous or self._market_toggle_context(opened) != key
                or getattr(self.device, '_handling_invitation', False)
                or self.appear(self.I_SELECT_GRIGRI)
                or (key[2] == '备' and not self._is_preparation_mode())
                or self._observe_shop_visibility() is not (not opened)
                or self._market_toggle_context(opened) != key
                or getattr(self.device, 'image_frame_id', None) != frame
                or time.monotonic() >= deadline):
            return False
        # Device recovery and repeat checks have run; this is their final
        # same-context semantic frame, with coordinates in the circle.
        point = rule.coord()
        response_before = signature(self.device.image, *point)
        result = self.device.click(*point, control_check=False, control_name=name)
        if result is False:
            return False
        after_click(self.device, *point, name, response_before)
        return True  # Dispatch only; caller still requires two destination frames.

    def _shop_slots(self) -> list[tuple[int, RuleClick]]:
        """返回商店五个卡面点击区；编号按原资源定义从右向左。"""
        return [
            (
                index,
                getattr(self, f'C_SHIKIGAMI_{index}'),
            )
            for index in range(1, 6)
        ]

    def _is_shop_open(self) -> bool:
        """使用刷新按钮判断商店是否展开；禁止在此读取价格 OCR。

        ``check_market`` 是开店确认的辅助标志，不能用于“商店仍展开”
        的通用判断，否则商店关闭后仍可能命中该图标，导致上卡阶段被
        误判为商店未关闭。
        """
        visible = self._shop_refresh_marker_visible()
        if visible:
            self._shop_assumed_open = True
            return True
        # 战斗技能会遮住右侧刷新按钮。经济续跑期间以脚本自己记录的
        # 商店状态为准，避免因“看不见刷新”而反复开关商店。
        return bool(
            getattr(self, '_economy_battle_mode', False)
            and getattr(self, '_shop_assumed_open', False)
        )

    def _shop_refresh_marker_visible(self) -> bool:
        """刷新/金币不足刷新任一出现，表示商店明确展开。"""
        return self.appear(self.I_REFRESH) or self.appear(self.I_REFRESH_NOT_GOLD)

    def _shop_open_confirm_marker_visible(self) -> bool:
        """开店确认：非战阶段只看 refresh；战阶段追加 check_market。"""
        if self._shop_refresh_marker_visible():
            return True
        return self._read_chess_mode() == '战' and self.appear(
            self.I_CHECK_MARKET
        )

    def _is_preparation_mode(self) -> bool:
        """只有无符咒弹窗的“备”可继续操作；弹窗出现立即中断。"""
        if self._read_chess_mode() != '备':
            return False
        deadline = getattr(self, '_preparation_work_deadline', None)
        if deadline is not None and (
                time.monotonic() >= deadline
                or self._read_round_number() != self._preparation_work_round):
            return False
        if self.appear(self.I_SELECT_GRIGRI):
            logger.debug(
                'Interrupt Chess preparation immediately: grigri selection '
                'panel detected'
            )
            return False
        return True

    def _is_purchase_allowed(self) -> bool:
        """商店动作遇到“鬼”必停；其余阶段由外层状态机调度。"""
        mode = self._read_chess_mode()
        if mode == '备':
            budget = getattr(self, '_preparation_economy_deadline', None)
            return self._is_preparation_mode() and (
                budget is None or time.monotonic() + 8 < budget
            )
        return mode == '战'

    def _read_shop_gold(self) -> int | None:
        """读取当前金币；OCR 异常时返回 None，避免误停购买流程。"""
        value = self.O_GOLD.ocr(self.device.image)
        if not isinstance(value, int) or value < 0:
            logger.warning(f'Chess gold OCR invalid: [{value}]')
            return None
        return value

    @staticmethod
    def _parse_coin_text(raw_text: str) -> dict | None:
        """解析鼬乐币 m/600，并恢复斜杠丢失或误识别为 1/I 的结果。"""
        raw = ''.join(str(raw_text or '').split())
        if not raw:
            return None

        # 标准斜杠以及被识别为 I/l/竖线的分隔符。
        explicit = re.search(r'(\d{1,3})[/／Iil|](600)$', raw)
        recovered = False
        if explicit is not None:
            current = int(explicit.group(1))
            recovered = '/' not in raw and '／' not in raw
        else:
            digits = ''.join(re.findall(r'\d', raw))
            if not digits.endswith('600'):
                return None
            prefix = digits[:-3]
            # 3441600 表示 344/600，其中额外的 1 是斜杠误识别。
            if len(prefix) == 4 and prefix.endswith('1'):
                prefix = prefix[:-1]
                recovered = True
            elif prefix:
                recovered = True
            if not prefix:
                return None
            current = int(prefix)

        if not 0 <= current <= 600:
            return None
        return {
            'current': current,
            'remaining': 600 - current,
            'total': 600,
            'raw': raw,
            'recovered': recovered,
        }

    def _read_coin(self) -> dict | None:
        """读取棋局大厅鼬乐币，优先消费 DigitCounter 三元组。"""
        result = self.O_COIN.ocr(self.device.image)
        coin = None
        if isinstance(result, (tuple, list)) and len(result) == 3:
            current, remaining, total = result
            if (
                all(isinstance(value, int) for value in result)
                and current >= 0
                and remaining >= 0
                and total == 600
                and current + remaining == total
            ):
                coin = {
                    'current': current,
                    'remaining': remaining,
                    'total': total,
                    'raw': f'{current}/{total}',
                    'recovered': False,
                }
        else:
            # 兼容尚未重新生成资源、仍返回文本的旧版 OCR 配置。
            raw = self._normalize_ocr_text(result)
            coin = self._parse_coin_text(raw)

        if coin is None:
            logger.warning(f'Chess coin OCR invalid: [{result}]')
            return None
        if coin['recovered']:
            logger.debug(
                f'Chess coin OCR recovered: [{coin["raw"]}] -> '
                f'{coin["current"]}/{coin["total"]}'
            )
        else:
            logger.debug(
                f'Chess coin: {coin["current"]}/{coin["total"]}'
            )
        return coin

    def _coin_is_full(self) -> bool:
        """最多复查三帧，仅 600/600 才视为鼬乐币已满。"""
        for attempt in range(1, 4):
            coin = self._read_coin()
            if coin is not None:
                full = coin['current'] == coin['total'] == 600
                if full:
                    logger.info('Chess coin is full: 600/600')
                return full
            if attempt < 3:
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
        return False

    def _can_afford_shop_shikigami(
        self,
        slot_index: int,
        known_price: int | None = None,
    ) -> bool:
        """判断购买能力；身份已确定时优先使用目录费用。"""
        if slot_index not in range(1, 6):
            logger.warning(f'Invalid Chess shop slot index: {slot_index}')
            return False

        gold = self._read_shop_gold()
        raw_price = ''
        price = known_price
        if price is None:
            price, raw_price = self._read_shop_slot_price(slot_index)
        if gold is None or price is None:
            logger.warning(
                'Skip Chess shop purchase because affordability OCR is '
                f'unavailable: slot={slot_index}, gold={gold}, '
                f'price_raw=[{raw_price}]'
            )
            return False

        affordable = gold >= price
        logger.debug(
            'Chess shop affordability: '
            f'slot={slot_index}, gold={gold}, price={price}, '
            f'affordable={affordable}'
        )
        return affordable

    def _read_shop_slot_price(self, slot_index: int) -> tuple[int | None, str]:
        """定位金币图标并读取其右侧费用数字。"""
        if slot_index not in range(1, 6):
            return None, ''
        price_rule = getattr(self, f'O_SHIKIGAMI_GOLD_{slot_index}')
        price_roi = tuple(price_rule.roi)
        matches = self.store_gold_rule.match_all_any(
            self.device.image,
            roi=list(price_roi),
            threshold=self.SHOP_GOLD_ICON_THRESHOLD,
            nms_threshold=0.3,
            frame_id=self.device.image_frame_id,
        )
        if not matches:
            logger.debug(
                'Chess shop price icon unavailable: '
                f'slot={slot_index}, roi={price_roi}'
            )
            return None, ''

        _, icon_x, _, icon_width, _ = max(
            matches,
            key=lambda item: item[0],
        )
        roi_x, roi_y, roi_width, roi_height = price_roi
        price_x = icon_x + icon_width
        price_right = roi_x + roi_width
        price_width = price_right - price_x
        if price_width <= 0:
            return None, ''

        digit_rule = RuleOcr(
            roi=(price_x, roi_y, price_width, roi_height),
            area=(price_x, roi_y, price_width, roi_height),
            mode='Digit',
            method='Default',
            keyword='',
            name=f'shikigami_gold_value_{slot_index}',
        )
        price = digit_rule.ocr(self.device.image)
        raw = str(price)
        if not isinstance(price, int):
            return None, raw
        if price not in range(1, 6):
            return None, raw
        return price, raw

    def _observe_shop_visibility(self) -> bool | None:
        """组合商店内容与按钮证据；单个刷新标志消失不等于已关闭。"""
        if self._shop_refresh_marker_visible():
            return True
        if self._read_chess_mode() == '战' and self.appear(self.I_CHECK_MARKET):
            return True
        # 刷新按钮会被战斗技能遮住，卡牌价格图标仍能证明商店展开。
        for index in range(1, 6):
            roi = getattr(self, f'O_SHIKIGAMI_GOLD_{index}').roi
            if self.store_gold_rule.match_all_any(
                self.device.image, roi=list(roi),
                threshold=self.SHOP_GOLD_ICON_THRESHOLD,
                nms_threshold=0.3, frame_id=self.device.image_frame_id,
            ):
                return True
        if self.appear(self.I_MARKET):
            return False
        return None

    def _ensure_shop_visibility(
        self, opened: bool, allowed_modes: tuple[str, ...],
    ) -> bool:
        """同回目阶段两新帧确认；未确认的切换只派发一次。"""
        self.screenshot()
        round_no = self._read_round_number()
        mode = self._read_chess_mode()
        generation = getattr(self.device, '_escape_generation', 0)
        if round_no is None or mode not in allowed_modes:
            return False
        invitation_generation = getattr(self.device, '_accepted_invitation_generation', 0)
        key = (getattr(getattr(self, 'chess_state', None), 'game_number', 0),
               round_no, mode, opened, generation, invitation_generation)
        timeout = self.SHOP_OPEN_TIMEOUT if opened else self.SHOP_CLOSE_TIMEOUT
        deadline = time.monotonic() + timeout
        candidate = None
        consecutive = 0
        last_frame = None
        pending = getattr(self, '_shop_toggle_pending', None)
        attempted = bool(pending and pending['key'] == key)
        while time.monotonic() < deadline:
            if (self._read_round_number() != round_no
                    or self._read_chess_mode() != mode
                    or getattr(getattr(self, 'chess_state', None), 'game_number', 0) != key[0]
                    or self.appear(self.I_SELECT_GRIGRI)
                    or getattr(self.device, '_escape_generation', 0) != generation
                    or getattr(self.device, '_accepted_invitation_generation', 0) != invitation_generation
                    or getattr(self.device, '_handling_invitation', False)):
                logger.debug('Pause Chess shop toggle: round, mode or recovery changed')
                return False
            if mode == '备' and not self._is_preparation_mode():
                return False
            frame = self.device.image_frame_id
            visible = self._observe_shop_visibility()
            if frame != last_frame:
                consecutive = consecutive + 1 if visible is candidate and visible is not None else 1
                candidate = visible
                last_frame = frame
            if visible is not None and consecutive >= 2:
                if visible is opened:
                    if (getattr(getattr(self.device, 'image', None), 'shape', ()) != (720, 1280, 3)
                            or self._market_toggle_context(opened) != key
                            or getattr(self.device, 'image_frame_id', None) != frame
                            or getattr(self.device, '_handling_invitation', False)
                            or time.monotonic() >= deadline):
                        return False
                    self._shop_assumed_open = opened
                    # 目的状态有独立语义回执，按钮附近没有变化不再代表失败。
                    # 只释放商店按钮这一条回执，其他输入的保护保持原样。
                    receipt = getattr(self.device, '_verified_click', None)
                    if (pending and pending['key'] == key
                            and pending['receipt'] is receipt and receipt
                            and receipt['name'] == str(self.C_MARKET_TOGGLE)):
                        self.device._verified_click = dict(receipt, changed=True)
                    self._shop_toggle_pending = None
                    return True
                if not attempted:
                    if opened and not self._is_purchase_allowed():
                        return False
                    attempted = True
                    pending = {'key': key, 'receipt': None}
                    self._shop_toggle_pending = pending
                    self.device.click_record_remove(self.C_MARKET_TOGGLE)
                    prior_receipt = getattr(self.device, '_verified_click', None)
                    try:
                        self._click_market_toggle_guarded(key, opened, deadline)
                    except ClickResponseUnchanged:
                        logger.warning('Pause Chess shop toggle: repeated market input fenced')
                        return False
                    receipt = getattr(self.device, '_verified_click', None)
                    if receipt is not prior_receipt:
                        pending['receipt'] = receipt
                    # click 内部截图不能代替本方法的两新帧目的状态确认。
                    candidate = None
                    consecutive = 0
                    last_frame = None
            time.sleep(self.SCREENSHOT_INTERVAL)
            self.screenshot()
        logger.warning('Pause Chess shop toggle: destination could not be confirmed')
        return False

    def _ensure_shop_open(self) -> bool:
        return self._ensure_shop_visibility(True, ('备', '战'))

    def _ensure_battle_economy_shop_open(self) -> bool:
        return self._ensure_shop_visibility(True, ('战',))

    def _ensure_shop_closed(
        self,
        allowed_modes: tuple[str, ...] = ('备',),
    ) -> bool:
        return self._ensure_shop_visibility(False, allowed_modes)

    def _clear_economy_click_history(self) -> None:
        """豁免合法的经验/刷新循环，保留其他按钮的全局防重复点击保护。"""
        removed_experience = self.device.click_record_remove(self.I_EXPERIENCE)
        removed_refresh = self.device.click_record_remove(self.I_REFRESH)
        if removed_experience or removed_refresh:
            logger.debug(
                'Clear Chess economy click history before legal loop: '
                f'experience={removed_experience}, refresh={removed_refresh}'
            )

    def _match_shop_shikigami_avatar(
        self,
        click_rule: RuleClick,
        expected_name: str | None = None,
        rules: list[tuple[str, RuleImage]] | None = None,
    ) -> dict | None:
        """只在一个商店点击框内匹配 ``*_m`` 式神头像。"""
        best = None
        rules = self.shikigami_shop_rules if rules is None else rules
        for name, rule in rules:
            if expected_name is not None and name != expected_name:
                continue
            matches = rule.match_all_any(
                self.device.image,
                roi=list(click_rule.roi_back),
                threshold=rule.threshold,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            )
            if not matches:
                continue
            score, x, y, width, height = max(matches, key=lambda item: item[0])
            if best is None or score > best['score']:
                best = {
                    'name': name,
                    'score': float(score),
                    'position': (x + width // 2, y + height // 2),
                }
        if best is not None:
            return best

        fallback = self._match_shop_shikigami_avatar_glow_fallback(
            click_rule,
            expected_name=expected_name,
            rules=rules,
        )
        if fallback is not None:
            logger.debug(
                'Chess shop avatar matched by glow fallback: '
                f'name={fallback["name"]}, score={fallback["score"]:.3f}, '
                f'threshold={self.SHOP_GLOW_TEMPLATE_THRESHOLD}'
            )
        return fallback

    def _recognize_shop_shikigami_by_name(
        self,
        slot_index: int,
        click_rule: RuleClick,
    ) -> dict | None:
        """以式神名字 OCR 作为商店身份识别的最高优先级。"""
        if slot_index not in range(1, 6):
            return None
        name_rule = getattr(self, f'O_SHIKIGAMI_NAME_{slot_index}')
        raw = self._normalize_ocr_text(name_rule.ocr(self.device.image))
        if not raw:
            return None

        entries = tuple(
            entry
            for entry in SHIKIGAMI_ENTRIES
            if entry.romaji not in NON_SHOP_SHIKIGAMI
        )
        exact = [
            entry
            for entry in entries
            if entry.chinese_name in raw
        ]
        if exact:
            entry = max(exact, key=lambda item: len(item.chinese_name))
            score = 1.0
        else:
            candidates = []
            for entry in entries:
                matched, similarity, _ = self._fuzzy_text_match(
                    entry.chinese_name,
                    raw,
                )
                if matched:
                    candidates.append((similarity, entry))
            if not candidates:
                logger.debug(
                    'Chess shop name OCR unavailable: '
                    f'slot={slot_index}, raw=[{raw}]'
                )
                return None
            score, entry = max(candidates, key=lambda item: item[0])

        x, y, width, height = click_rule.roi_back
        logger.debug(
            'Chess shop name identity: '
            f'slot={slot_index}, raw=[{raw}], score={score:.3f} '
            f'-> {entry.romaji}'
        )
        return {
            'name': entry.romaji,
            'score': float(score),
            'position': (x + width // 2, y + height // 2),
            'source': 'ocr_name',
            'price': entry.cost,
            'bonds': entry.bonds,
            'raw_name': raw,
        }

    def _recognize_shop_shikigami_by_ocr(
        self,
        slot_index: int,
        click_rule: RuleClick,
    ) -> dict | None:
        """羁绊优先；有歧义时读取费用，仍失败则由外层图片兜底。"""
        if slot_index not in range(1, 6):
            return None

        skill_rule = getattr(self, f'O_SHIKIGAMI_SKILL_{slot_index}')
        raw_skill = self._normalize_ocr_text(
            skill_rule.ocr(self.device.image)
        )
        skill_text = normalize_bond_ocr_text(raw_skill)
        if not skill_text:
            logger.debug(
                'Chess shop OCR identity unavailable: '
                f'slot={slot_index}, bonds=[{raw_skill}]'
            )
            return None

        observed_bonds = tuple(
            bond
            for bond in KNOWN_BONDS
            if bond in skill_text
        )
        observed_key = bond_key(observed_bonds)
        if not observed_key:
            logger.debug(
                'Chess shop bonds are unavailable; use image fallback: '
                f'slot={slot_index}, raw=[{raw_skill}]'
            )
            return None

        all_names = tuple(STORE_SHIKIGAMI_BY_SIGNATURE.values())
        candidates = [
            name
            for name in all_names
            if observed_key.issubset(
                bond_key(SHIKIGAMI_BY_ROMAJI[name].bonds)
            )
        ]

        price = None
        raw_price = ''
        source = 'ocr_bonds'
        if len(candidates) != 1:
            price, raw_price = self._read_shop_slot_price(slot_index)
            if price is None:
                logger.debug(
                    'Chess shop bond candidates need price but price OCR '
                    f'failed: slot={slot_index}, bonds={observed_bonds}, '
                    f'candidates={candidates}, price_raw=[{raw_price}]'
                )
                return None
            candidates = [
                name
                for name in candidates
                if SHIKIGAMI_BY_ROMAJI[name].cost == price
            ]
            source = 'ocr_bonds_price'

        if len(candidates) != 1:
            logger.debug(
                'Chess shop identity remains ambiguous; use image fallback: '
                f'slot={slot_index}, bonds={observed_bonds}, price={price}, '
                f'candidates={candidates}'
            )
            return None

        name = candidates[0]
        entry = SHIKIGAMI_BY_ROMAJI[name]
        x, y, width, height = click_rule.roi_back
        logger.debug(
            'Chess shop bond identity: '
            f'slot={slot_index}, bonds={observed_bonds}, '
            f'price={price}, source={source} -> {name}'
        )
        return {
            'name': name,
            'score': 1.0,
            'position': (x + width // 2, y + height // 2),
            'source': source,
            # 羁绊已唯一时直接采用目录费用，购买能力判断无需再次 OCR。
            'price': entry.cost,
            'bonds': entry.bonds,
            'raw_bonds': raw_skill,
        }

    def _recognize_shop_slot(
        self,
        slot_index: int,
        click_rule: RuleClick,
        expected_name: str | None = None,
        fallback_rules: list[tuple[str, RuleImage]] | None = None,
    ) -> dict | None:
        """商店统一识别入口：名字→羁绊及费用→图片兜底。"""
        recognized = self._recognize_shop_shikigami_by_name(
            slot_index,
            click_rule,
        )
        if recognized is None:
            recognized = self._recognize_shop_shikigami_by_ocr(
                slot_index,
                click_rule,
            )
        if recognized is not None:
            if expected_name is None or recognized['name'] == expected_name:
                return recognized
            # 购买复检时若 OCR 突然识别成另一张卡，再用原目标头像复核
            # 一次，避免单帧 OCR 误识别造成“已经购买成功”的假结论。
            fallback = self._match_shop_shikigami_avatar(
                click_rule,
                expected_name=expected_name,
                rules=fallback_rules,
            )
            if fallback is not None:
                fallback['source'] = 'avatar_after_ocr_mismatch'
            return fallback

        fallback = self._match_shop_shikigami_avatar(
            click_rule,
            expected_name=expected_name,
            rules=fallback_rules,
        )
        if fallback is not None:
            fallback['source'] = 'avatar'
        return fallback

    @staticmethod
    def _template_gray(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return image
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def _match_shop_shikigami_avatar_glow_fallback(
        self,
        click_rule: RuleClick,
        expected_name: str | None = None,
        rules: list[tuple[str, RuleImage]] | None = None,
    ) -> dict | None:
        """发光商店卡兜底：用灰度归一化匹配降低光效影响。"""
        x, y, width, height = click_rule.roi_back
        source = self.device.image[y:y + height, x:x + width]
        if source.size == 0:
            return None
        source_gray = self._template_gray(source)
        best = None
        rules = self.shikigami_shop_rules if rules is None else rules
        for name, rule in rules:
            if expected_name is not None and name != expected_name:
                continue
            template = rule.image
            if template is None or template.size == 0:
                continue
            if (
                template.shape[0] > source.shape[0]
                or template.shape[1] > source.shape[1]
            ):
                continue
            template_gray = self._template_gray(template)
            result = cv2.matchTemplate(
                source_gray,
                template_gray,
                cv2.TM_CCOEFF_NORMED,
            )
            _, score, _, location = cv2.minMaxLoc(result)
            if best is None or score > best['score']:
                best = {
                    'name': name,
                    'score': float(score),
                    'position': (
                        x + location[0] + template.shape[1] // 2,
                        y + location[1] + template.shape[0] // 2,
                    ),
                }
        if best is None:
            return None
        if best['score'] < self.SHOP_GLOW_TEMPLATE_THRESHOLD:
            logger.debug(
                'Chess shop glow fallback best candidate below threshold: '
                f'name={best["name"]}, score={best["score"]:.3f}, '
                f'threshold={self.SHOP_GLOW_TEMPLATE_THRESHOLD}'
            )
            return None
        return best

    def _buy_shop_slot(
        self,
        slot_index: int,
        click_rule: RuleClick,
        matched_name: str,
    ) -> bool | None:
        """True 为购买回执；False 为未购买；None 为已派发但结果未知。"""
        known_price = SHIKIGAMI_BY_ROMAJI[matched_name].cost
        deadline = time.monotonic() + self.SHOP_BUY_TIMEOUT
        origin_round = self._read_round_number()
        if origin_round is None:
            return None
        attempts = 0
        emergency_cleanup_done = False
        lineup_sale_done = False

        while time.monotonic() < deadline:
            # 扫描与动作之间可能经过换店或动画；只使用当前新帧派发。
            self.screenshot()
            if (
                self._read_round_number() != origin_round
                or not self._is_purchase_allowed()
                or self.appear(self.I_SELECT_GRIGRI)
                or not self._shop_refresh_marker_visible()
            ):
                return None
            current_match = self._recognize_shop_slot(
                slot_index, click_rule, expected_name=matched_name,
            )
            if current_match is None:
                # 扫描目标过期不构成购买；上层重新观察，禁止记成功。
                return None if attempts else False
            gold_before = self._read_shop_gold()
            if gold_before is None:
                return None
            if gold_before < known_price:
                return False
            before_frame = self.device.image_frame_id
            attempts += 1
            logger.info(
                f'Buy Chess card: {self._shikigami_display_name(matched_name)} '
                f'(slot={slot_index}, attempt={attempts})'
            )
            # BaseTask.click 的 bool 不是派发结果，不以它判购买。
            self.click(click_rule)
            confirmed_frames = set()
            missing_frames = 0
            present_frames = 0
            gold_changed_seen = False
            gold_after = None
            current_match = None
            for _ in range(3):
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
                if (
                    self._read_round_number() != origin_round
                    or not self._is_purchase_allowed()
                    or self.appear(self.I_SELECT_GRIGRI)
                    or not self._shop_refresh_marker_visible()
                ):
                    return None
                frame = self.device.image_frame_id
                if frame == before_frame or frame in confirmed_frames:
                    continue
                confirmed_frames.add(frame)
                gold_after = self._read_shop_gold()
                if gold_after is None:
                    return None
                gold_changed_seen = gold_changed_seen or gold_after != gold_before
                current_match = self._recognize_shop_slot(
                    slot_index, click_rule, expected_name=matched_name,
                )
                if current_match is None:
                    present_frames = 0
                    missing_frames += 1
                    if missing_frames >= 2:
                        if gold_after <= gold_before - known_price:
                            logger.debug(
                                'Chess purchase confirmed by two fresh frames '
                                f'and gold: {gold_before} -> {gold_after}'
                            )
                            return True
                        # 收入可能抵消扣款；不确定时不卖牌或刷新。
                        return None
                else:
                    missing_frames = 0
                    present_frames += 1
                    if gold_changed_seen:
                        return None
            if present_frames < 2 or current_match is None:
                return None
            # 两新帧目标仍在、金币未动，保留原有有界手牌腾位路径。
            if emergency_cleanup_done and lineup_sale_done:
                return False
            sell_lineup = emergency_cleanup_done
            recovery = self._free_one_hand_slot_for_purchase(
                sell_lineup=sell_lineup,
            )
            if recovery is None:
                return False
            if sell_lineup:
                lineup_sale_done = True
            else:
                emergency_cleanup_done = True
        logger.warning(f'Chess purchase confirmation timed out: {matched_name}')
        return None if attempts else False

    def buy_lineup_shikigami_from_shop(self) -> list[str] | None:
        """先以费用＋羁绊记录商店目标，再按记录购买所有阵容式神。"""
        if not self._is_purchase_allowed():
            logger.debug('Stop Chess shop purchase: Hyakki mode detected')
            return None
        if not self._ensure_shop_open():
            return None

        logger.debug('Scan all Chess shop slots before purchasing')
        targets = []
        recognized_slots = {}

        for slot_index, click_rule in self._shop_slots():
            if not self._is_purchase_allowed():
                logger.debug('Stop Chess shop purchase: Hyakki mode detected')
                return None
            matched = self._recognize_shop_slot(
                slot_index,
                click_rule,
            )
            if matched is None:
                recognized_slots[slot_index] = '未识别'
                logger.debug(
                    f'Chess shop slot {slot_index}: identity not recognized'
                )
                continue

            recognized_slots[slot_index] = self._shikigami_display_name(
                matched['name']
            )

            if matched['name'] not in self.shikigami_deploy_positions:
                logger.debug(
                    f'Chess shop slot {slot_index}: recognized non-lineup '
                    f'card {matched["name"]}'
                )
                continue

            logger.debug(
                f'Chess shop slot {slot_index}: {matched["source"]} -> '
                f'{matched["name"]}, score={matched["score"]:.3f}'
            )
            targets.append({
                'slot_index': slot_index,
                'click_rule': click_rule,
                'matched_name': matched['name'],
                'known_price': SHIKIGAMI_BY_ROMAJI[matched['name']].cost,
            })

        # 资源编号从右向左为 1→5；日志按玩家看到的左→右输出 5→1。
        logger.info(
            'Chess shop recognized (left->right): '
            + ' | '.join(
                recognized_slots.get(slot_index, '未识别')
                for slot_index in range(5, 0, -1)
            )
        )
        logger.debug(
            'Chess shop target scan complete: '
            f'{[(item["slot_index"], item["matched_name"]) for item in targets]}'
        )
        purchased = []
        for target in targets:
            if not self._is_purchase_allowed():
                logger.debug('Stop Chess shop purchase: Hyakki mode detected')
                return None
            if not self._can_afford_shop_shikigami(
                target['slot_index'],
                known_price=target['known_price'],
            ):
                logger.debug(
                    'Skip unaffordable Chess shop target: '
                    f'slot={target["slot_index"]}, '
                    f'name={target["matched_name"]}'
                )
                continue
            result = self._buy_shop_slot(
                slot_index=target['slot_index'],
                click_rule=target['click_rule'],
                matched_name=target['matched_name'],
            )
            if result is None:
                logger.warning('Pause Chess economy: purchase outcome unknown')
                return None
            if result is True:
                purchased.append(target['matched_name'])
                # 第一张卡购买/升星动画会短暂覆盖其他商店格。等待稳定并
                # 刷新截图后再处理目标列表中的下一格，重复卡也逐格购买。
                time.sleep(self.ACTION_SETTLE_INTERVAL)
                self.screenshot()
            elif not self._is_purchase_allowed():
                return None
            elif not self._can_afford_shop_shikigami(
                target['slot_index'],
                known_price=target['known_price'],
            ):
                logger.debug(
                    'Chess target became unaffordable; skip it and continue: '
                    f'slot={target["slot_index"]}, '
                    f'name={target["matched_name"]}'
                )
                continue
            else:
                logger.warning(
                    'Chess target purchase was not confirmed by avatar '
                    'disappearance; '
                    f'slot={target["slot_index"]}, '
                    f'name={target["matched_name"]}; '
                    'stop this shop cycle before any refresh'
                )
                return None

        logger.debug(f'Chess shop check complete, purchased={purchased}')
        return purchased

    def purchase_lineup_cards_once(self) -> list[str] | None:
        """确保商店打开后扫描并购买，不负责关闭商店。"""
        return self.buy_lineup_shikigami_from_shop()

    def _reset_economy_state(self) -> None:
        self._experience_pending = None
        self._refresh_pending = None
        self._preparation_work_round = None
        self._preparation_work_deadline = None
        self._preparation_economy_deadline = None
        """重置单局可暂停的回目结束任务和商店状态。"""
        self._economy_pending = False
        self._economy_step_state = 'idle'
        self._economy_sequence_level = None
        self._economy_sequence_index = 0
        self._formation_pending = False
        self._economy_battle_mode = False
        self._shop_assumed_open = False

    def _schedule_economy_cycle(self) -> None:
        """登记一次经济任务；已有未完成原子动作时保持其精确进度。"""
        if getattr(self, '_economy_pending', False):
            logger.debug(
                'Chess economy is already pending: '
                f'state={self._economy_step_state}'
            )
            return
        self._economy_pending = True
        self._economy_step_state = 'ready'
        logger.debug('Schedule Chess economy upgrade/refresh cycle')

    def _schedule_round_end_actions(self, round_no: int) -> None:
        """登记回目结束任务；第四回目起额外登记系统站位整理。"""
        if round_no > 3:
            if not getattr(self, '_formation_pending', False):
                logger.debug(
                    f'Schedule Chess formation recovery after round {round_no}'
                )
            self._formation_pending = True
        else:
            logger.debug(
                f'Chess round {round_no} uses alternate early layout; '
                'skip formation recovery scheduling'
            )
        self._schedule_economy_cycle()

    def _finish_economy_cycle(self, reason: str) -> None:
        self._economy_pending = False
        self._economy_step_state = 'idle'
        logger.debug(f'Chess economy cycle complete: {reason}')

    def _read_experience_counter(self) -> tuple[int, int] | None:
        rule = getattr(self, '_experience_counter_rule', None)
        if rule is None:
            rule = RuleOcr(roi=(55, 544, 80, 42), area=(55, 544, 80, 42),
                           mode='Single', method='Default', keyword='',
                           name='chess_experience_counter')
            self._experience_counter_rule = rule
        raw = str(rule.ocr(self.device.image))
        match = re.search(r'(\d{1,3})\s*[/／]\s*(\d{1,3})', raw)
        if match is None:
            return None
        current, total = map(int, match.groups())
        return (current, total) if 0 <= current <= total and total > 0 else None

    def _experience_context(self):
        return (getattr(getattr(self, 'chess_state', None), 'game_number', 0),
                self._read_round_number(), self._read_chess_mode(),
                getattr(self.device, '_escape_generation', 0),
                getattr(self.device, '_accepted_invitation_generation', 0))

    def _dispatch_experience_guarded(self, pending, button):
        """Recovery/session checks first, then a fresh semantic hardware guard."""
        rule = self.C_EXPERIENCE_CENTER
        name = str(rule)
        if (time.monotonic() >= pending['deadline']
                or self.device.handle_control_check(name) is False):
            return False
        point = rule.coord()
        before_click(self.device, *point, name)
        previous = getattr(self.device, 'image_frame_id', None)
        self.screenshot()
        frame = getattr(self.device, 'image_frame_id', None)
        if (frame is None or frame == previous
                or getattr(self.device.image, 'shape', ()) != (720, 1280, 3)
                or self._experience_context() != pending['key']
                or getattr(self.device, '_handling_invitation', False)
                or self.appear(self.I_SELECT_GRIGRI)
                or self._discover_choices() or self.appear(self.I_USE_SOUL)
                or not self.appear(button)
                or (pending['key'][2] == '备' and not self._is_preparation_mode())):
            return False
        remaining = self._read_remaining_time()
        if remaining is None or remaining < 8:
            return False
        state = (self._read_level(), self._read_shop_gold(), self._read_experience_counter())
        if (state != (pending['level'], pending['gold'], pending['experience'])
                or self._experience_context() != pending['key']
                or getattr(self.device, 'image_frame_id', None) != frame
                or time.monotonic() >= pending['deadline']):
            return False
        point = rule.coord()
        response_before = signature(self.device.image, *point)
        pending['sent'] = True  # Unknown hardware exceptions cannot authorize replay.
        result = self.device.click(*point, control_check=False, control_name=name)
        if result is False:
            pending['sent'] = False
            return False
        after_click(self.device, *point, name, response_before)
        pending['receipt'] = getattr(self.device, '_verified_click', None)
        return True

    def _click_economy_button_and_confirm_gold(
        self, button: RuleImage, expected_cost: int, label: str,
        allow_hidden: bool,
    ) -> str:
        """单次经验输入，以同回目两新帧的阶数、经验或扣款确认。"""
        self.screenshot()
        round_no = self._read_round_number()
        mode = self._read_chess_mode()
        generation = getattr(self.device, '_escape_generation', 0)
        gold_before = self._read_shop_gold()
        level_before = self._read_level()
        experience_before = self._read_experience_counter()
        key = self._experience_context()
        pending = getattr(self, '_experience_pending', None)
        continuing = bool(pending and pending['key'] == key)
        if (round_no is None or mode not in ('备', '战')
                or self.appear(self.I_SELECT_GRIGRI)
                or (mode == '备' and not self._is_preparation_mode())
                or not self.appear(button)):
            return 'no_progress'
        if continuing:
            # Retry at most once only after two actual fresh observations
            # positively prove level/gold/complete experience all unchanged.
            if (pending.get('sent') and pending.get('attempts', 1) < 2
                    and pending.get('unchanged_frames', 0) >= 2
                    and experience_before is not None
                    and (level_before, gold_before, experience_before)
                    == (pending['level'], pending['gold'], pending['experience'])):
                pending['sent'] = False
                pending['receipt'] = None
                pending['unchanged_frames'] = 0
                pending['attempts'] += 1
                pending['deadline'] = time.monotonic() + 8
                work = getattr(self, '_preparation_work_deadline', None)
                if mode == '备' and work is not None:
                    pending['deadline'] = min(pending['deadline'], work)
            gold_before = pending['gold']
            level_before = pending['level']
            experience_before = pending['experience']
        else:
            budget = getattr(self, '_preparation_economy_deadline', None)
            if (gold_before is None or level_before is None
                    or gold_before - expected_cost < 50
                    or level_before >= self._lineup_final_level()
                    or (mode == '备' and budget is not None
                        and time.monotonic() + 8 >= budget)):
                return 'no_progress'
            pending = {'key': key, 'gold': gold_before, 'level': level_before,
                       'experience': experience_before, 'receipt': None,
                       'sent': False, 'attempts': 1, 'unchanged_frames': 0,
                       'deadline': time.monotonic() + 8}
            work = getattr(self, '_preparation_work_deadline', None)
            if mode == '备' and work is not None:
                pending['deadline'] = min(pending['deadline'], work)
            self._experience_pending = pending
        if not pending.get('sent'):
            self._clear_economy_click_history()
            try:
                dispatched = self._dispatch_experience_guarded(pending, button)
            except ClickResponseUnchanged:
                logger.warning('Pause Chess upgrade: experience input fenced')
                return 'unknown'
            if not dispatched:
                if not pending.get('sent'):
                    self._experience_pending = None  # No hardware input occurred.
                return 'no_progress'
        receipt = pending['receipt']
        if receipt is None or receipt['name'] != str(self.C_EXPERIENCE_CENTER):
            return 'unknown'
        last_frame = None
        confirmations = 0
        deadline = pending['deadline']
        for _ in range(4):
            if time.monotonic() >= deadline:
                break
            time.sleep(self.ACTION_SETTLE_INTERVAL)
            self.screenshot()
            if (self._experience_context() != key
                    or self.appear(self.I_SELECT_GRIGRI)
                    or getattr(self.device, '_handling_invitation', False)
                    or (mode == '备' and not self._is_preparation_mode())):
                return 'unknown'
            frame = self.device.image_frame_id
            level_after = self._read_level()
            experience_after = self._read_experience_counter()
            gold_after = self._read_shop_gold()
            progressed = (
                level_after == level_before + 1
            ) or (
                level_after == level_before and experience_before is not None
                and experience_after is not None
                and experience_after[1] == experience_before[1]
                and experience_after[0] > experience_before[0]
            ) or (
                (experience_before is None or experience_after is None)
                and level_after == level_before
                and gold_after is not None and gold_after <= gold_before - expected_cost
            )
            if frame != last_frame:
                confirmations = confirmations + 1 if progressed else 0
                unchanged = (experience_before is not None
                             and experience_after == experience_before
                             and level_after == level_before and gold_after == gold_before)
                pending['unchanged_frames'] = pending['unchanged_frames'] + 1 if unchanged else 0
                last_frame = frame
            if confirmations >= 2:
                if (receipt is getattr(self.device, '_verified_click', None)
                        and receipt['name'] == str(self.C_EXPERIENCE_CENTER)):
                    self.device._verified_click = dict(receipt, changed=True)
                self._experience_pending = None
                logger.info(f'Chess upgrade confirmed: level={level_before}->{level_after}, '
                            f'experience={experience_before}->{experience_after}, '
                            f'gold={gold_before}->{gold_after}')
                return 'success'
        logger.warning('Pause Chess upgrade: single input outcome unconfirmed')
        return 'unknown'

    def _shop_slot_refresh_snapshot(self) -> dict[int, dict]:
        """保存五个商店槽位的身份和低分辨率图像，用于确认刷新。"""
        snapshot = {}
        image_height, image_width = self.device.image.shape[:2]
        for slot_index, click_rule in self._shop_slots():
            matched = self._recognize_shop_slot(
                slot_index,
                click_rule,
                fallback_rules=self.all_shikigami_shop_rules,
            )
            x, y, width, height = click_rule.roi_back
            x = max(0, int(x))
            y = max(0, int(y))
            width = min(int(width), image_width - x)
            height = min(int(height), image_height - y)
            crop = self.device.image[y:y + height, x:x + width]
            if crop.size:
                gray = self._template_gray(crop)
                signature = cv2.resize(
                    gray,
                    (32, 32),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                signature = None
            snapshot[slot_index] = {
                'name': None if matched is None else matched['name'],
                'image': signature,
            }
        return snapshot

    def _shop_refresh_changed_slots(
        self,
        before: dict[int, dict],
        after: dict[int, dict],
    ) -> tuple[list[int], dict[int, float]]:
        """比较对应槽位；身份可用时比较身份，否则以卡面图像差异兜底。"""
        changed = []
        image_differences = {}
        for slot_index in range(1, 6):
            previous = before[slot_index]
            current = after[slot_index]
            previous_name = previous['name']
            current_name = current['name']
            previous_image = previous['image']
            current_image = current['image']
            difference = 0.0
            if previous_image is not None and current_image is not None:
                difference = float(np.mean(cv2.absdiff(
                    previous_image,
                    current_image,
                )))
            image_differences[slot_index] = difference
            if previous_name is not None and current_name is not None:
                slot_changed = previous_name != current_name
            else:
                slot_changed = (
                    difference
                    >= self.SHOP_REFRESH_SLOT_IMAGE_DIFF_THRESHOLD
                )
            if slot_changed:
                changed.append(slot_index)
        return changed, image_differences

    def _click_shop_refresh_and_confirm_slots(self, allow_hidden: bool) -> str:
        """单次刷新，未知结果在同阶段仅观察，禁止连续花费。"""
        self.screenshot()
        round_no = self._read_round_number()
        mode = self._read_chess_mode()
        generation = getattr(self.device, '_escape_generation', 0)
        game = getattr(getattr(self, 'chess_state', None), 'game_number', 0)
        key = (game, round_no, mode, generation)
        if (round_no is None or not self._is_purchase_allowed()
                or self.appear(self.I_SELECT_GRIGRI) or not self.appear(self.I_REFRESH)):
            return 'no_progress'
        pending = getattr(self, '_refresh_pending', None)
        if pending and pending['key'] == key:
            before = pending['snapshot']
            receipt = pending['receipt']
        else:
            before = self._shop_slot_refresh_snapshot()
            # 槽位OCR期间可能经过阶段边界；花费前重新取新帧和余额。
            self.screenshot()
            gold = self._read_shop_gold()
            if (self._read_round_number() != round_no or self._read_chess_mode() != mode
                    or getattr(self.device, '_escape_generation', 0) != generation
                    or not self._is_purchase_allowed() or gold is None
                    or gold - self.SHOP_REFRESH_COST < 50
                    or self.appear(self.I_SELECT_GRIGRI) or not self.appear(self.I_REFRESH)):
                return 'no_progress'
            prior = getattr(self.device, '_verified_click', None)
            pending = {'key': key, 'snapshot': before, 'receipt': None}
            self._refresh_pending = pending
            self._clear_economy_click_history()
            try:
                self.click(self.I_REFRESH)
            except ClickResponseUnchanged:
                return 'no_progress'
            receipt = getattr(self.device, '_verified_click', None)
            if receipt is None or receipt is prior or receipt['name'] != str(self.I_REFRESH):
                return 'no_progress'
            pending['receipt'] = receipt
        if receipt is None:
            return 'no_progress'
        frames = set()
        confirmations = 0
        deadline = time.monotonic() + 8
        for _ in range(4):
            if time.monotonic() >= deadline:
                break
            time.sleep(self.ACTION_SETTLE_INTERVAL)
            self.screenshot()
            if (self._read_round_number() != round_no or self._read_chess_mode() != mode
                    or getattr(self.device, '_escape_generation', 0) != generation
                    or getattr(getattr(self, 'chess_state', None), 'game_number', 0) != game
                    or self.appear(self.I_SELECT_GRIGRI)
                    or (mode == '备' and not self._is_preparation_mode())):
                return 'no_progress'
            frame = self.device.image_frame_id
            if frame in frames:
                continue
            frames.add(frame)
            after = self._shop_slot_refresh_snapshot()
            changed, _ = self._shop_refresh_changed_slots(before, after)
            confirmations = confirmations + 1 if len(changed) >= self.SHOP_REFRESH_CHANGED_SLOT_MINIMUM else 0
            if confirmations >= 2:
                if receipt is getattr(self.device, '_verified_click', None):
                    self.device._verified_click = dict(receipt, changed=True)
                self._refresh_pending = None
                return 'success'
        logger.warning('Pause Chess refresh: single input outcome unconfirmed')
        return 'no_progress'

    def _economy_sequence_for_level(self, level: int) -> tuple[str, ...]:
        return ('experience',) if level < self._lineup_final_level() else ('refresh',)

    def _economy_reserve_for_level(self, level: int) -> int:
        return 50

    def _reset_economy_sequence_if_level_changed(self, level: int) -> None:
        if self._economy_sequence_level == level:
            return
        logger.debug(
            'Reset Chess economy operation counter: '
            f'{self._economy_sequence_level} -> {level}'
        )
        self._economy_sequence_level = level
        self._economy_sequence_index = 0

    def _next_economy_operation(self, level: int) -> str:
        self._reset_economy_sequence_if_level_changed(level)
        sequence = self._economy_sequence_for_level(level)
        index = self._economy_sequence_index % len(sequence)
        operation = sequence[index]
        logger.debug(
            'Chess economy next operation: '
            f'level={level}, sequence={sequence}, index={index}, '
            f'operation={operation}'
        )
        return operation

    def _advance_economy_operation_counter(self, level: int) -> None:
        self._reset_economy_sequence_if_level_changed(level)
        sequence = self._economy_sequence_for_level(level)
        self._economy_sequence_index = (
            self._economy_sequence_index + 1
        ) % len(sequence)
        logger.debug(
            'Advance Chess economy operation counter: '
            f'level={level}, next_index={self._economy_sequence_index}'
        )

    def _can_execute_economy_operation(
        self,
        level: int,
        gold: int,
        operation: str,
    ) -> bool:
        reserve = self._economy_reserve_for_level(level)
        cost = (
            self.EXPERIENCE_COST
            if operation == 'experience'
            else self.SHOP_REFRESH_COST
        )
        if level < self._lineup_final_level() and gold <= reserve:
            return False
        return gold >= reserve + cost

    def _run_economy_atomic_batch(self, battle_mode: bool = False) -> str:
        self._economy_purchase_attempted = False
        """执行一个由计数器决定的升级/刷新原子动作。"""
        if not getattr(self, '_economy_pending', False):
            return 'complete'
        if not self._is_purchase_allowed():
            logger.debug('Pause Chess economy: Hyakki mode detected')
            return 'blocked'

        previous_battle_mode = getattr(self, '_economy_battle_mode', False)
        self._economy_battle_mode = battle_mode
        try:
            level = self._read_level()
            gold = self._read_shop_gold()
            if level is None or gold is None:
                logger.warning(
                    'Pause Chess economy: level or gold OCR unavailable'
                )
                return 'blocked'

            operation = self._next_economy_operation(level)
            if not self._can_execute_economy_operation(level, gold, operation):
                self._finish_economy_cycle(
                    f'budget limit reached, level={level}, gold={gold}, '
                    f'operation={operation}, '
                    f'reserve={self._economy_reserve_for_level(level)}, '
                    f'final_level={self._lineup_final_level()}'
                )
                return 'complete'

            if operation == 'experience':
                result = self._click_economy_button_and_confirm_gold(
                    self.I_EXPERIENCE,
                    self.EXPERIENCE_COST,
                    'buy experience',
                    allow_hidden=battle_mode,
                )
                if result != 'success':
                    return 'blocked'
                self._advance_economy_operation_counter(level)
            else:
                # 只有刷新动作要求商店打开；购买经验不改变商店状态。
                if not self._ensure_shop_open():
                    return 'blocked'
                result = self._click_shop_refresh_and_confirm_slots(
                    allow_hidden=battle_mode,
                )
                if result == 'no_progress':
                    return 'blocked'
                logger.info('Refresh Chess shop completed')
                self._advance_economy_operation_counter(level)
                # 槽位变化确认时商店已完成换牌；仍额外等待稳定帧，
                # 再读取费用和羁绊，避免残余动画影响购买识别。
                time.sleep(self.ACTION_SETTLE_INTERVAL)
                self.screenshot()
                self._economy_purchase_attempted = True
                purchased = self.purchase_lineup_cards_once()
                if purchased is None:
                    logger.warning(
                        'Pause Chess economy after refresh: purchase not '
                        'confirmed'
                    )
                    return 'blocked'
            self._economy_step_state = 'ready'

            logger.debug(
                'Chess economy atomic batch finished: '
                f'operation={operation}, battle_mode={battle_mode}'
            )

            # 只判断是否还有下一批；真正执行留到外层重新截图、检查回目
            # 后，确保新回目的备阶段能够抢占长时间经济循环。
            level = self._read_level()
            gold = self._read_shop_gold()
            if level is None or gold is None:
                return 'pending'
            next_operation = self._next_economy_operation(level)
            if self._can_execute_economy_operation(
                level,
                gold,
                next_operation,
            ):
                return 'pending'
            self._finish_economy_cycle(
                f'budget limit reached after batch, level={level}, '
                f'gold={gold}, next_operation={next_operation}, '
                f'reserve={self._economy_reserve_for_level(level)}, '
                f'final_level={self._lineup_final_level()}'
            )
            return 'complete'
        finally:
            self._economy_battle_mode = previous_battle_mode

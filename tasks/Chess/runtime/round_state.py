"""Chess runtime: round OCR, board occupancy, and state readers."""

# This Python file uses the following encoding: utf-8

import random
import re
import time
from functools import cached_property
from pathlib import Path

import cv2
from module.logger import logger
from module.atom.ocr import RuleOcr
from module.exception import TaskRecoveryFailed
from tasks.Chess.strategy.grigri import (
    grigri_category,
    grigri_file,
    grigri_keep_names,
    grigri_names_for_quality,
    grigri_score,
    grigri_selection_key,
    resolve_grigri_name,
)


class ChessRoundStateMixin:
    """Internal mixin; use through ``ScriptTask`` only."""

    @cached_property
    def grigri_icon_templates(self) -> dict[str, object]:
        """按中文名缓存全部符咒图标，供 OCR 失败时进行图像兜底。"""
        badge_dir = Path(__file__).resolve().parents[1] / 'badge'
        templates = {}
        for name in grigri_names_for_quality(None):
            file = badge_dir / grigri_file(name)
            image = cv2.imread(str(file), cv2.IMREAD_COLOR)
            if image is None:
                logger.warning(f'Chess grigri icon is missing: {file}')
                continue
            templates[name] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        logger.debug(f'Loaded {len(templates)} Chess grigri icon templates')
        return templates

    def _detect_grigri_quality(self) -> str | None:
        for quality, rule in (
            ('gold', self.I_GRIGRI_GOLD),
            ('silver', self.I_GRIGRI_SILVER),
            ('copper', self.I_GRIGRI_COPPER),
        ):
            if self.appear(rule):
                self._grigri_quality_cache = quality
                return quality
        cached_quality = getattr(self, '_grigri_quality_cache', None)
        if cached_quality is not None:
            logger.warning(
                'Chess grigri quality was not recognized after refresh; '
                f'reuse current selection quality={cached_quality}'
            )
            return cached_quality
        logger.warning('Chess grigri quality was not recognized')
        return None

    def _match_grigri_icon(
        self,
        option_rule,
        quality: str | None,
    ) -> tuple[str | None, float]:
        x, y, width, height = option_rule.roi_front
        image_height, image_width = self.device.image.shape[:2]
        # 点击框略窄于 94px 图标，匹配区向左右各扩 24px。
        left = max(0, x - 24)
        top = max(0, y - 8)
        right = min(image_width, x + width + 24)
        bottom = min(image_height, y + height)
        source = self.device.image[top:bottom, left:right]
        best_name, best_score = None, 0.0
        for name in grigri_names_for_quality(quality):
            template = self.grigri_icon_templates.get(name)
            if (
                template is None
                or template.shape[0] > source.shape[0]
                or template.shape[1] > source.shape[1]
            ):
                continue
            result = cv2.matchTemplate(
                source,
                template,
                cv2.TM_CCOEFF_NORMED,
            )
            _, score, _, _ = cv2.minMaxLoc(result)
            if score > best_score:
                best_name, best_score = name, float(score)
        if best_score < self.GRIGRI_ICON_THRESHOLD:
            return None, best_score
        return best_name, best_score

    def _recognize_grigri_options(self) -> list[dict]:
        quality = self._detect_grigri_quality()
        lineup_strategy = self.get_lineup_strategy()
        option_rules = (
            self.C_GRIGRI_OPTION_1,
            self.C_GRIGRI_OPTION_2,
            self.C_GRIGRI_OPTION_3,
        )
        ocr_rules = (
            self.O_GRIGRI_OPTION_NAME_1,
            self.O_GRIGRI_OPTION_NAME_2,
            self.O_GRIGRI_OPTION_NAME_3,
        )
        result = []
        for index, (option_rule, ocr_rule) in enumerate(
            zip(option_rules, ocr_rules),
            start=1,
        ):
            raw = self._normalize_ocr_text(ocr_rule.ocr(self.device.image))
            ocr_name, text_score = resolve_grigri_name(raw, quality)
            icon_name, icon_score = self._match_grigri_icon(
                option_rule,
                quality,
            )
            # OCR 已达到图鉴映射阈值时优先采用文字；图像负责校验和兜底。
            name = ocr_name or icon_name
            if ocr_name and icon_name and ocr_name != icon_name:
                logger.warning(
                    'Chess grigri OCR/icon conflict: '
                    f'option={index}, ocr={ocr_name}({text_score:.3f}), '
                    f'icon={icon_name}({icon_score:.3f})'
                )
            category = grigri_category(name)
            score = grigri_score(name, lineup_strategy)
            selection_key = grigri_selection_key(name, lineup_strategy)
            item = {
                'index': index,
                'quality': quality,
                'raw': raw,
                'name': name,
                'category': category,
                'score': score,
                'selection_key': selection_key,
                'text_score': text_score,
                'icon_name': icon_name,
                'icon_score': icon_score,
                'rule': option_rule,
            }
            result.append(item)
            logger.info(
                'Chess grigri option: '
                f'option={index}, quality={quality or "unknown"}, '
                f'ocr=[{raw}], name={name or "未知"}, '
                f'category={category}, benefit={score:g}, '
                f'icon={icon_name or "未命中"}({icon_score:.3f})'
            )
        return result

    @staticmethod
    def _best_grigri_option(options: list[dict]) -> dict:
        best_key = max(item['selection_key'] for item in options)
        return random.choice([
            item for item in options if item['selection_key'] == best_key
        ])

    def _grigri_context(self):
        return (
            getattr(getattr(self, 'chess_state', None), 'game_number', 0),
            self._read_round_number(),
            getattr(self.device, '_escape_generation', 0),
        )

    def _grigri_context_valid(self, context):
        session = getattr(self, '_grigri_session', None)
        if session and session['context'] == context and time.monotonic() >= session['deadline']:
            raise TaskRecoveryFailed('Chess grigri event budget exhausted')
        return (
            context[1] is not None
            and self._grigri_context() == context
            and self._read_chess_mode() == '备'
            and self.appear(self.I_SELECT_GRIGRI)
        )

    @staticmethod
    def _grigri_options_key(options):
        return tuple((o['index'], o['quality'], o['name'], o['raw']) for o in options)

    def _read_grigri_refresh_count(self, index):
        rule = getattr(self, f'C_GRIGRI_REFRESH_{index}')
        x, y, width, height = rule.roi_front
        # Single preserves an empty OCR result; Digit turns it into zero.
        roi = (x + width // 2, y, width - width // 2, height)
        raw = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                      keyword='', name=f'grigri_refresh_count_{index}').ocr(self.device.image)
        raw = self._normalize_ocr_text(raw)
        return int(raw) if raw in ('0', '1') else None

    def _settle_grigri_refresh(self, session):
        pending = session.get('refresh_pending')
        if pending is None:
            return True
        deadline = pending['deadline']
        last_frame = getattr(self.device, 'image_frame_id', None)
        stable_key, previous_count, confirmations = None, None, 0
        while time.monotonic() < deadline:
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == last_frame:
                time.sleep(self.SCREENSHOT_INTERVAL)
                continue
            last_frame = frame
            if not self._grigri_context_valid(session['context']):
                return False
            options = self._recognize_grigri_options()
            item = options[pending['index'] - 1]
            key = self._grigri_options_key(options)
            count = self._read_grigri_refresh_count(pending['index'])
            # A known new card or the explicit 1 -> 0 counter confirms refresh.
            changed_card = bool(pending['name'] and item['name'] and item['name'] != pending['name'])
            confirmed = changed_card or (pending['count'] == 1 and count == 0)
            previous_key, last_count = stable_key, previous_count
            previous_count = count
            confirmations = confirmations + 1 if confirmed and key == stable_key else int(confirmed)
            stable_key = key
            if (not confirmed and key == previous_key and last_count == count == 1
                    and item['name'] == pending['name'] and item['name']
                    and self._grigri_context_valid(session['context'])
                    and time.monotonic() < deadline):
                # The same explicit remaining refresh count in two new frames
                # proves this refresh was not consumed. Keep its reserved count.
                x, y = getattr(self, f'C_GRIGRI_REFRESH_{pending["index"]}').coord()
                self.device.click(x=x, y=y,
                    control_name=f'grigri_refresh_{pending["index"]}')
                stable_key = None
                continue
            if confirmations >= 2:
                session['refresh_pending'] = None
                logger.info(f"Chess grigri single refresh confirmed: option={pending['index']}")
                return True
            time.sleep(self.SCREENSHOT_INTERVAL)
        raise TaskRecoveryFailed('Chess grigri refresh confirmation budget exhausted')

    def _refresh_grigri_option(self, option: dict) -> bool:
        session = self._grigri_session
        index = option['index']
        if session.get('selection_sent') or session.get('refresh_pending'):
            return False
        last_frame = getattr(self.device, 'image_frame_id', None)
        self.screenshot()
        frame = getattr(self.device, 'image_frame_id', None)
        if frame is None or frame == last_frame:
            return False
        if not self._grigri_context_valid(session['context']):
            return False
        options = self._recognize_grigri_options()
        if self._grigri_options_key(options) != session['options_key']:
            return False
        count = self._read_grigri_refresh_count(index)
        if not self._grigri_context_valid(session['context']):
            return False
        if count != 1 or self._grigri_refresh_remaining[index - 1] <= 0:
            self._grigri_refresh_remaining[index - 1] = 0
            return True
        # Reserve the consumption count before dispatch; retry only on two explicit unconsumed frames.
        self._grigri_refresh_remaining[index - 1] = 0
        session['refresh_pending'] = dict(index=index, name=options[index - 1]['name'], count=count,
                                         deadline=time.monotonic() + self.GRIGRI_SELECT_TIMEOUT)
        click_x, click_y = getattr(self, f'C_GRIGRI_REFRESH_{index}').coord()
        if self.device.click(x=click_x, y=click_y,
                             control_name=f'grigri_refresh_{index}') is False:
            return False
        return self._settle_grigri_refresh(session)

    def _continue_grigri_refresh_below_nine(self) -> bool:
        """读取是否启用“刷新收益排名非前40%选项”。"""
        chess_task_config = getattr(self.config, 'chess', None)
        chess_config = getattr(chess_task_config, 'chess_config', None)
        return bool(getattr(
            chess_config,
            'continue_grigri_refresh_below_nine',
            True,
        ))

    def _next_refreshable_grigri_option(
        self,
        options: list[dict],
    ) -> dict | None:
        """选择下一项要刷新的位置；优先刷新当前评分最低项。"""
        candidates = [
            item
            for item in options
            if self._grigri_refresh_remaining[item['index'] - 1] > 0
        ]
        if not candidates:
            return None
        if not self._continue_grigri_refresh_below_nine():
            return None
        if any(item['quality'] is None for item in candidates):
            logger.warning(
                'Skip Chess grigri refresh: quality is unknown, '
                'top-40-percent ranking cannot be calculated safely'
            )
            return None

        lineup_strategy = self.get_lineup_strategy()
        keep_names = {
            quality: grigri_keep_names(
                quality,
                lineup_strategy,
            )
            for quality in {item['quality'] for item in candidates}
        }
        logger.debug(
            'Chess grigri top-40-percent keep counts: '
            f'{ {quality: len(names) for quality, names in keep_names.items()} }'
        )
        # 以当前品质的图鉴总数为基数，严格按数量保留前40%；
        # 同收益只在最终选取时随机，排行榜边界使用固定图鉴顺序。
        candidates = [
            item
            for item in candidates
            if item['name'] not in keep_names[item['quality']]
        ]
        if not candidates:
            return None

        # 刷新标准只看实际收益，不受符咒类别影响。
        lowest_score = min(item['score'] for item in candidates)
        return random.choice([
            item
            for item in candidates
            if item['score'] == lowest_score
        ])

    def _grigri_new_event_stable(self, context):
        """A later event needs two new real panel frames before a new budget."""
        deadline = time.monotonic() + 6
        previous = getattr(self.device, 'image_frame_id', None)
        previous_options = None
        for _ in range(2):
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if (frame is None or frame == previous or self._grigri_context() != context
                    or self._read_chess_mode() != '备'
                    or not self.appear(self.I_SELECT_GRIGRI)
                    or time.monotonic() >= deadline):
                return False
            options = self._recognize_grigri_options()
            key = self._grigri_options_key(options)
            if (not any(o['name'] for o in options)
                    or (previous_options is not None and key != previous_options)
                    or time.monotonic() >= deadline):
                return False
            previous_options = key
            previous = frame
        return self._grigri_context() == context and time.monotonic() < deadline

    def select_grigri(self) -> bool:
        """Fresh semantic confirmation; retry visible unconsumed actions within budget."""
        self.screenshot()
        if not self.appear(self.I_SELECT_GRIGRI):
            return False
        context = self._grigri_context()
        session = getattr(self, '_grigri_session', None)
        if context[1] is None:
            return False
        if (session and session['context'][0] == context[0]
                and (session['context'][1] is None or context[1] < session['context'][1])):
            return False  # Unknown/backward observations cannot refill an event.
        if (session and session['context'][0] == context[0]
                and context[1] > session['context'][1]):
            if not self._grigri_new_event_stable(context):
                return False
            if not session.get('completed'):
                self._grigri_last_unconfirmed_event = dict(
                    context=session['context'], passed=True, confirmed=False,
                    selection_sent=bool(session.get('selection_sent')),
                    refresh_pending=bool(session.get('refresh_pending')))
        if (session and session.get('completed') and session['context'][0] == context[0]
                and (context[1] is None or context[1] <= session['context'][1])):
            raise TaskRecoveryFailed('Chess grigri completed event cannot be replayed')
        if session and session['context'][0::2] == context[0::2]:
            if context[1] is None:
                context = session['context']
            elif session['context'][1] is None:
                session['context'] = context
        if session is None or session['context'] != context:
            if (session and not session.get('completed')
                    and context[1] is not None
                    and session['context'][:2] == context[:2]):
                # Recovery changes generation, not the event's consumption
                # budget. Fresh phase observations below authorize any retry.
                session['context'] = context
            else:
                session = dict(context=context, remaining=[1, 1, 1],
                               refresh_pending=None, selection_sent=False,
                               deadline=time.monotonic() + 40.0)
                self._grigri_session = session
                self._grigri_quality_cache = None
        if session.get('completed'):
            raise TaskRecoveryFailed('Chess grigri completed event unexpectedly reopened')
        if not self._grigri_context_valid(context):
            return False
        self._grigri_refresh_remaining = session['remaining']
        if not self._settle_grigri_refresh(session):
            return False

        if not session['selection_sent']:
            options = self._recognize_grigri_options()
            for _ in range(self.GRIGRI_REFRESH_MAXIMUM):
                session['options_key'] = self._grigri_options_key(options)
                target = self._next_refreshable_grigri_option(options)
                if target is None:
                    break
                if not self._refresh_grigri_option(target):
                    return False
                options = self._recognize_grigri_options()
            key = self._grigri_options_key(options)
            last_frame = getattr(self.device, 'image_frame_id', None)
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if frame is None or frame == last_frame or not self._grigri_context_valid(context):
                return False
            fresh_options = self._recognize_grigri_options()
            if self._grigri_options_key(fresh_options) != key:
                return False
            if not any(item['name'] for item in fresh_options):
                return False
            if not self._grigri_context_valid(context):
                return False
            selected = self._best_grigri_option([item for item in fresh_options if item['name']])
            session['selection_sent'] = True
            session['selected'] = selected['index']
            session['selected_name'] = selected['name']
            session['selection_deadline'] = time.monotonic() + self.GRIGRI_SELECT_TIMEOUT
            x, y = selected['rule'].coord()
            if self.device.click(x=x, y=y, control_name=selected['rule'].name) is False:
                return False

        deadline = session['selection_deadline']
        last_frame = getattr(self.device, 'image_frame_id', None)
        confirmations = 0
        destination = None
        while time.monotonic() < deadline:
            self.screenshot()
            frame = getattr(self.device, 'image_frame_id', None)
            if getattr(self.device, '_escape_generation', 0) != context[2]:
                raise TaskRecoveryFailed('Chess grigri selection interrupted by recovery')
            if getattr(getattr(self, 'chess_state', None), 'game_number', 0) != context[0]:
                raise TaskRecoveryFailed('Chess grigri selection crossed game boundary')
            if frame is None or frame == last_frame:
                time.sleep(self.SCREENSHOT_INTERVAL)
                continue
            last_frame = frame
            round_no, mode = self._read_round_number(), self._read_chess_mode()
            valid = (not self.appear(self.I_SELECT_GRIGRI)
                     and round_no is not None and round_no >= context[1]
                     and mode in ('备', '战', '鬼', '待'))
            current_destination = (round_no, mode)
            confirmations = confirmations + 1 if valid and destination == current_destination else int(valid)
            same_panel = (self.appear(self.I_SELECT_GRIGRI)
                          and self._grigri_context() == context)
            if same_panel:
                options = self._recognize_grigri_options()
                selected_now = next((o for o in options if o['index'] == session['selected']), None)
                key = self._grigri_options_key(options)
                prior_key = session.get('selection_retry_key')
                session['selection_retry_key'] = key
                if (selected_now is not None and selected_now['name'] == session.get('selected_name')
                        and selected_now['name'] and prior_key == key and self._grigri_context_valid(context)
                        and time.monotonic() < deadline):
                    x, y = selected_now['rule'].coord()
                    self.device.click(x=x, y=y, control_name=selected_now['rule'].name)
                    session['selection_retry_key'] = None
            else:
                session['selection_retry_key'] = None
            destination = current_destination
            if confirmations >= 2:
                session['completed'] = True
                logger.info(f"Chess grigri panel closed in two fresh frames: option={session['selected']}")
                return True
            time.sleep(self.SCREENSHOT_INTERVAL)
        raise TaskRecoveryFailed('Chess grigri selection confirmation budget exhausted')

    @staticmethod
    def _normalize_ocr_text(value) -> str:
        if value is None:
            return ''
        return ''.join(str(value).split())

    @classmethod
    def _fuzzy_text_match(
        cls,
        expected,
        current,
    ) -> tuple[bool, float, float]:
        """Chess 内部 OCR 编辑距离兜底，避免修改公共 RuleOcr。"""
        expected = cls._normalize_ocr_text(expected)
        current = cls._normalize_ocr_text(current)
        threshold = 0.75 if len(expected) <= 2 else 0.65
        if not expected or not current:
            return False, 0.0, threshold
        if expected == current:
            return True, 1.0, threshold

        # 仅保留较短字符串长度的一行动态规划状态。
        if len(expected) < len(current):
            expected, current = current, expected
        previous = list(range(len(current) + 1))
        for expected_index, expected_char in enumerate(expected, start=1):
            row = [expected_index]
            for current_index, current_char in enumerate(current, start=1):
                row.append(min(
                    row[current_index - 1] + 1,
                    previous[current_index] + 1,
                    previous[current_index - 1]
                    + (expected_char != current_char),
                ))
            previous = row
        similarity = 1.0 - previous[-1] / max(len(expected), len(current))
        return similarity >= threshold, similarity, threshold

    def _parse_round_number(self, ocr_rule) -> tuple[int | None, str]:
        raw = self._normalize_ocr_text(ocr_rule.ocr(self.device.image))
        matched = re.search(r'(\d+)(?=回)', raw)
        if matched is None:
            matched = re.search(r'\d+', raw)
        if matched is None:
            return None, raw
        value = int(matched.group(0))
        return (value if value > 0 else None), raw

    def _read_primary_round_layout(self) -> tuple[str, bool]:
        """以主 chess_mode 是否读到数字判断是否启用前三回合布局。"""
        frame_id = getattr(self.device, 'image_frame_id', None)
        cache = getattr(self, '_primary_round_layout_cache', None)
        if frame_id is not None and cache is not None and cache[0] == frame_id:
            return cache[1]

        raw = self._normalize_ocr_text(
            self.O_CHESS_MODE.ocr(self.device.image)
        )
        if raw:
            use_alternate_layout = bool(re.search(r'\d', raw))
            self._last_alternate_round_layout = use_alternate_layout
        else:
            # 动画帧偶发空识别时沿用上一帧，避免在两套区域间抖动。
            use_alternate_layout = getattr(
                self,
                '_last_alternate_round_layout',
                False,
            )
        result = raw, use_alternate_layout
        if frame_id is not None:
            self._primary_round_layout_cache = frame_id, result
        return result

    def _is_early_round_layout(self) -> bool:
        """第二套 round/mode 布局即前三回合，此阶段禁止任何卖卡。"""
        _, use_alternate_layout = self._read_primary_round_layout()
        return use_alternate_layout

    def _read_round_number(self) -> int | None:
        """主 chess_mode 读到数字时使用前三回合专用 round_2。"""
        mode_raw, use_alternate_layout = self._read_primary_round_layout()
        ocr_rule = self.O_ROUND_2 if use_alternate_layout else self.O_ROUND
        value, round_raw = self._parse_round_number(ocr_rule)
        if use_alternate_layout:
            logger.debug(
                f'Chess mode primary is numeric [{mode_raw}]; '
                f'use round_2 [{round_raw}] -> {value}'
            )
        return value

    def _read_chess_mode(self) -> str | None:
        """返回模式文字；只有 OCR 真正无文本时才返回 None。"""
        primary_raw, use_alternate_layout = self._read_primary_round_layout()
        raw = (
            self._normalize_ocr_text(
                self.O_CHESS_MODE_2.ocr(self.device.image)
            )
            if use_alternate_layout
            else primary_raw
        )
        for mode in ('备', '战', '鬼'):
            if mode in raw:
                return mode
        # “待”等过渡文字虽然不触发阶段动作，但必须保留为有文本状态，
        # 不能和真正的 OCR 空结果混为一谈，否则会误累计结算空帧。
        return raw or None

    def _is_boss_challenge_round(self) -> bool:
        """当前是否为固定勾玉框会整体失效的首领挑战回合。"""
        return getattr(self, '_current_round_no', None) in (
            self.BOSS_CHALLENGE_ROUNDS
        )

    def _tracked_board_positions(self) -> set[int]:
        """汇总无需依赖勾玉模板即可确认的本局站位。"""
        positions = {
            int(position)
            for position in getattr(
                self,
                '_player_deployed_positions',
                set(),
            )
        }
        actual_positions = getattr(self, '_board_actual_positions', {})
        for name in getattr(self, '_board_lineup_names', set()):
            position = actual_positions.get(
                name,
                self.shikigami_deploy_positions.get(name),
            )
            if position is not None:
                positions.add(int(position))
        for unit in getattr(self, '_board_special_units', {}).values():
            position = unit.get('position')
            if position is not None:
                positions.add(int(position))
        return positions

    def _board_set_has_shikigami(self, set_index: int) -> bool:
        """判断站位是否有人；首领回合仅采用本局动态站位记录。"""
        if self._is_boss_challenge_round():
            occupied = int(set_index) in self._tracked_board_positions()
            logger.debug(
                'Chess boss-round board occupancy by runtime state: '
                f'round={self._current_round_no}, set={set_index}, '
                f'occupied={occupied}'
            )
            return occupied

        return self._board_set_has_shikigami_visual(set_index)

    def _board_set_has_shikigami_visual(self, set_index: int) -> bool:
        """仅按当前截图中的勾玉标志判断指定站位是否有人。"""

        x, y, width, height = self._set_jade_area(set_index)
        image_height, image_width = self.device.image.shape[:2]
        if (
            x < 0
            or y < 0
            or x >= image_width
            or y >= image_height
        ):
            logger.warning(
                f'Chess set jade area is outside screenshot: '
                f'set={set_index}, area={(x, y, width, height)}'
            )
            return False
        width = min(width, image_width - x)
        height = min(height, image_height - y)
        if width <= 0 or height <= 0:
            logger.warning(
                f'Chess set jade area is empty after clipping: '
                f'set={set_index}, area={(x, y, width, height)}'
            )
            return False

        roi = [x, y, width, height]
        return any(
            bool(rule.match_all_any(
                self.device.image,
                roi=roi,
                threshold=self.BOARD_OCCUPANCY_TEMPLATE_THRESHOLD,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            ))
            for rule in self.board_occupancy_rules
        )

    def _reconcile_lineup_after_hyakki_round(
        self,
        next_round_no: int | None = None,
    ) -> list[str]:
        """百鬼夜行结束后移除已不在原站位上的式神上阵记录。"""
        if next_round_no in self.BOSS_CHALLENGE_ROUNDS:
            logger.info(
                'Skip post-Hyakki lineup check during boss round: '
                f'round={next_round_no}'
            )
            return []
        deployed_names = set(
            getattr(self, '_board_lineup_names', set())
        )
        actual_positions = dict(
            getattr(self, '_board_actual_positions', {})
        )
        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )
        removed = []
        for name in sorted(deployed_names):
            set_index = actual_positions.get(
                name,
                self.shikigami_deploy_positions.get(name),
            )
            if set_index is None:
                continue
            if self._board_set_has_shikigami_visual(int(set_index)):
                continue
            deployed_names.discard(name)
            actual_positions.pop(name, None)
            player_positions.discard(int(set_index))
            removed.append(name)
            logger.warning(
                'Chess shikigami missing after Hyakki round; '
                f'mark as undeployed: name={name}, set={set_index}'
            )

        self._board_lineup_names = deployed_names
        self._board_actual_positions = actual_positions
        self._player_deployed_positions = player_positions
        logger.info(
            'Chess post-Hyakki lineup check: '
            f'removed={removed}, deployed={sorted(deployed_names)}'
        )
        return removed

    def _read_board_position_count(self) -> dict:
        """统计 12 个站位勾玉区域中检测到图标的位置数量。"""
        if self._is_boss_challenge_round():
            occupied_positions = sorted(self._tracked_board_positions())
            count = len(occupied_positions)
            raw = ','.join(str(index) for index in occupied_positions)
            logger.debug(
                'Chess boss-round board position count by runtime state: '
                f'round={self._current_round_no}, count={count}/12, '
                f'occupied={occupied_positions}'
            )
            return {
                'current': count,
                'total': 12,
                'raw': raw,
                'occupied_positions': occupied_positions,
            }

        occupied_positions = [
            set_index
            for set_index in range(1, 13)
            if self._board_set_has_shikigami(set_index)
        ]
        count = len(occupied_positions)
        raw = ','.join(str(index) for index in occupied_positions)
        logger.debug(
            'Chess board position count by jade: '
            f'{count}/12, occupied={occupied_positions}'
        )
        return {
            'current': count,
            'total': 12,
            'raw': raw,
            'occupied_positions': occupied_positions,
        }

    def _read_shikigami_count(self) -> dict | None:
        """读取场上人数：统计 12 个站位勾玉区域是否有式神。"""
        return self._read_board_position_count()

    def _read_lineup_capacity_status(self) -> dict | None:
        """以本局已确认上阵名单统计人口；金鱼等特殊单位不计人口。"""
        level = self._read_level()
        if level is None:
            logger.warning(
                'Chess lineup capacity unavailable: '
                f'level={level}'
            )
            return None

        deployed_names = {
            name
            for name in getattr(self, '_board_lineup_names', set())
            if name in self.shikigami_deploy_positions
        }
        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )
        current = len(deployed_names)
        capacity = level
        full = current >= capacity
        logger.debug(
            'Chess lineup capacity by runtime state: '
            f'population={current}, '
            f'capacity={capacity}, level={level}, '
            f'deployed={sorted(deployed_names)}, '
            f'player_positions={sorted(player_positions)}, '
            f'arakawa_goldfish='
            f'{getattr(self, "_arakawa_goldfish_current_position", None)}, '
            f'full={full}'
        )
        return {
            'current': current,
            'capacity': capacity,
            'level': level,
            'deployed_names': deployed_names,
            'player_positions': player_positions,
            'arakawa_goldfish': (
                getattr(self, '_arakawa_goldfish_current_position', None)
                is not None
            ),
            'full': full,
        }

    def _read_level(self) -> int | None:
        """读取“一阶”至“九阶”，同时兼容阿拉伯数字显示。"""
        raw = self._normalize_ocr_text(self.O_LEVEL.ocr(self.device.image))
        digit = re.search(r'([1-9])', raw)
        if digit is not None:
            level = int(digit.group(1))
            logger.debug(f'Chess level: [{raw}] -> {level}')
            return level

        chinese_digits = {
            '一': 1,
            '二': 2,
            '三': 3,
            '四': 4,
            '五': 5,
            '六': 6,
            '七': 7,
            '八': 8,
            '九': 9,
        }
        for character, level in chinese_digits.items():
            if character in raw:
                logger.debug(f'Chess level: [{raw}] -> {level}')
                return level

        logger.warning(f'Chess level OCR invalid: [{raw}]')
        return None

    def _read_remaining_time(self) -> int | None:
        """读取剩余时间；第二套布局复用第一套阶段框中的数字。"""
        primary_raw, use_alternate_layout = (
            self._read_primary_round_layout()
        )
        raw = (
            primary_raw
            if use_alternate_layout
            else self._normalize_ocr_text(
                self.O_NOW_TIME.ocr(self.device.image)
            )
        )
        matched = re.search(r'\d+', raw)
        if matched is None:
            logger.warning(f'Chess remaining time OCR invalid: [{raw}]')
            return None
        remaining = int(matched.group(0))
        logger.debug(
            f'Chess remaining time: [{raw}] -> {remaining}, '
            f'alternate_layout={use_alternate_layout}'
        )
        return remaining

    def _read_game_rank(self) -> tuple[int | None, str]:
        """读取结算页“第几名”，兼容阿拉伯数字和中文数字。"""
        raw = self._normalize_ocr_text(self.O_RANK.ocr(self.device.image))
        matched = re.search(r'第?([1-8])名?', raw)
        if matched is not None:
            return int(matched.group(1)), raw

        chinese_digits = {
            '一': 1,
            '二': 2,
            '三': 3,
            '四': 4,
            '五': 5,
            '六': 6,
            '七': 7,
            '八': 8,
        }
        for character, rank in chinese_digits.items():
            if character in raw:
                return rank, raw
        return None, raw

    def _try_last_seconds_deploy(self) -> bool:
        """第一套布局备阶段倒计时不超过 10 秒时，空闲补做一次上阵。

        Returns:
            bool: 是否已经进入倒计时补位判定。OCR 未到阈值或不可用时
                返回 False，允许后续截图继续检查；一旦到达阈值，无论
                阵容是否已满都返回 True，确保每回目至多触发一次。
        """
        if self._is_early_round_layout():
            return False
        remaining = self._read_remaining_time()
        if remaining is None or remaining > 10:
            return False

        logger.debug(
            'Chess last-seconds lineup check: '
            f'remaining={remaining}'
        )
        if self._read_chess_mode() != '备':
            logger.debug('Skip last-seconds deploy: mode is no longer 备')
            return True
        if not self._ensure_shop_closed():
            logger.warning(
                'Skip last-seconds deploy: shop could not be closed'
            )
            return True

        self.screenshot()
        if self._is_early_round_layout() or not self._is_preparation_mode():
            logger.debug(
                'Skip last-seconds deploy: preparation/layout changed '
                'while closing shop'
            )
            return True

        capacity = self._read_lineup_capacity_status()
        if capacity is None:
            logger.warning(
                'Skip last-seconds deploy: lineup capacity is unavailable'
            )
            return True
        if capacity['full']:
            logger.debug(
                'Skip last-seconds deploy: lineup is already full '
                f'({capacity["current"]}/{capacity["capacity"]})'
            )
            return True

        logger.debug(
            'Run last-seconds deploy: '
            f'{capacity["current"]}/{capacity["capacity"]}'
        )
        deployed = self.deploy_shikigami_from_hand()
        logger.debug(
            'Chess last-seconds deploy complete: '
            f'deployed={deployed}'
        )
        return True

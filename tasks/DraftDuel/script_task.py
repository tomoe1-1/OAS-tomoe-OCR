"""协同对弈：每人初选五人，再从己方卡牌中安排三人上场。"""

import difflib
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from module.exception import GameStuckError, TaskEnd
from module.atom.click import RuleClick
from module.logger import logger
from tasks.DraftDuel.assets import DraftDuelAssets
from tasks.DraftDuel.config import DraftMatchMode, DraftRole
from tasks.DraftDuel.field_drag import drag_to_field
from tasks.DraftDuel.strategy import (CATALOG, choose_pick, is_fire_supplier, normalize_name,
                                       plan_lineup, plan_duel_lineup)
from tasks.DraftDuel.visual_cards import (locate_card, portrait_above_name, full_portrait_above_name,
                                         confirmed_draft_portrait, card_drag_start, face_above_name)
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_town
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets


DUEL_SHARE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'duel_share')


class ScriptTask(GameUi, DraftDuelAssets):
    TEAM_SIZE = 5
    _CROSS_SOURCE = cv2.imdecode(
        np.fromfile(str(Path(__file__).with_name('selected_cross.png')), np.uint8),
        cv2.IMREAD_COLOR)
    CROSS_TEMPLATE = ((_CROSS_SOURCE.min(axis=2) > 190) &
                      (_CROSS_SOURCE.max(axis=2) - _CROSS_SOURCE.min(axis=2) < 65)
                      ).astype(np.uint8) * 255
    CROSS_TEMPLATES = (CROSS_TEMPLATE, cv2.resize(CROSS_TEMPLATE, None, fx=0.9, fy=0.9,
                                                interpolation=cv2.INTER_NEAREST))
    @staticmethod
    def _duel_share_reset():
        """每局开始清空两个号的共享名单，避免读到上一局残留。"""
        try:
            for role in ('captain', 'member'):
                path = os.path.join(DUEL_SHARE_DIR, f'draft_share_{role}.json')
                if os.path.exists(path):
                    os.remove(path)
        except Exception as exc:
            logger.warning(f'DraftDuel share reset failed: {exc}')

    def _duel_share_write(self, names):
        """选完人、大厅查看阵容后，把本方 5 人名单写入共享文件。"""
        role = getattr(self, '_lobby_role', None)
        if role not in ('captain', 'member') or not names:
            return
        try:
            os.makedirs(DUEL_SHARE_DIR, exist_ok=True)
            payload = {'role': role, 'names': list(names), 'ts': time.time(), 'state': 'ready'}
            with open(os.path.join(DUEL_SHARE_DIR, f'draft_share_{role}.json'),
                      'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            logger.info(f'DraftDuel share written ({role}): {list(names)}')
        except Exception as exc:
            logger.warning(f'DraftDuel share write failed: {exc}')

    def _duel_share_read_mate(self, timeout=6.0):
        """读取队友 5 人名单；队友尚未写入时轮询等待，超时返回 None。"""
        role = getattr(self, '_lobby_role', None)
        if role not in ('captain', 'member'):
            return None
        mate = 'member' if role == 'captain' else 'captain'
        path = os.path.join(DUEL_SHARE_DIR, f'draft_share_{mate}.json')
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if (data.get('state') == 'ready'
                        and isinstance(data.get('names'), list)
                        and len(data['names']) >= 3):
                    names = [normalize_name(raw) for raw in data['names']]
                    names = [name for name in names if name]
                    if len(names) >= 3:
                        logger.info(f'DraftDuel mate share read ({mate}): {names}')
                        return names
            except Exception:
                pass
            time.sleep(0.5)
        return None

    @staticmethod
    def _cross_mask(image):
        return ((image.min(axis=2) > 190) &
                (image.max(axis=2) - image.min(axis=2) < 65)).astype(np.uint8) * 255

    @staticmethod
    def _activity_open(now):
        return 10 <= now.hour < 23

    def _require_open_for_match(self):
        now = datetime.now()
        if self._activity_open(now):
            return
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        logger.info(f'DraftDuel closed; next attempt after {target}')
        self.set_next_run(task='DraftDuel', success=None, finish=False,
                          server=False, target=target)
        raise TaskEnd('协同对弈当前未开放')

    @classmethod
    def _selected_cross(cls, image):
        """返回整排卡牌中叉号的屏幕横坐标与匹配分数。"""
        roi = cls._cross_mask(image[500:550, 240:850])
        best = (0, 0.0)
        for template in cls.CROSS_TEMPLATES:
            _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(
                roi, template, cv2.TM_CCOEFF_NORMED))
            if score > best[1]:
                best = (240 + point[0] + template.shape[1] // 2, float(score))
        return best

    @classmethod
    def _selected_cards(cls, image):
        """检测卡面上的白色交叉标记；已灰化卡没有此标记。"""
        result = set()
        for index, x in enumerate((320, 435, 550, 665, 780)):
            roi = cls._cross_mask(image[502:547, x-26:x+26])
            score = max(float(cv2.matchTemplate(
                roi, template, cv2.TM_CCOEFF_NORMED).max())
                        for template in cls.CROSS_TEMPLATES)
            if score >= 0.6:
                result.add(index)
        return result

    def _draft_invite_panel_open(self):
        """邀请面板已弹出：任一好友分类标签 OCR 到文字即视为面板存在。"""
        for rule in (self.O_F_LIST_1, self.O_F_LIST_2, self.O_F_LIST_3, self.O_F_LIST_4):
            if str(rule.ocr(self.device.image) or '').strip():
                return True
        return False

    def _open_draft_invite_panel(self, timeout=8):
        """点击大厅右侧空队友位加号打开邀请面板。"""
        started = time.monotonic()
        clicked = False
        while time.monotonic() - started < timeout:
            self.device.stuck_record_clear()
            self.screenshot()
            if clicked:
                if self._draft_invite_panel_open():
                    return True
            elif self.appear(self.I_EMPTY_TEAMMATE):
                self.click(self.I_EMPTY_TEAMMATE, interval=0.5)
                clicked = True
        logger.warning('DraftDuel invite panel did not open')
        return False

    def _click_draft_friend_class(self, target):
        """在邀请面板上识别好友分类标签（好友/最近/寮友/跨区）并点击目标分类。"""
        for rule in (self.O_F_LIST_1, self.O_F_LIST_2, self.O_F_LIST_3, self.O_F_LIST_4):
            results = rule.detect_and_ocr(self.device.image)
            for item in results:
                text = str(item.ocr_text or '').replace(' ', '').replace('、', '').strip()
                if text == target:
                    x, y = item.box.mean(axis=0)
                    x += rule.roi[0]
                    y += rule.roi[1]
                    self.device.click(x=int(x), y=int(y), control_name=rule.name)
                    logger.info(f'DraftDuel switched friend class {target} at ({x:.0f},{y:.0f})')
                    return True
        return False

    def _select_draft_friend(self, name):
        """在好友列表左/右两列中精确识别指定好友并点击。"""
        for rule in (self.O_FRIEND_NAME_1, self.O_FRIEND_NAME_2):
            results = rule.detect_and_ocr(self.device.image)
            for item in results:
                text = str(item.ocr_text or '').replace(' ', '').replace('　', '').strip()
                if text == name:
                    x, y = item.box.mean(axis=0)
                    x += rule.roi[0]
                    y += rule.roi[1]
                    self.device.click(x=int(x), y=int(y), control_name=rule.name)
                    logger.info(f'DraftDuel selected friend {name} at ({x:.0f},{y:.0f})')
                    return True
        return False

    def _draft_invite_click(self, rule):
        """RuleClick 没有 match/appear，直接按坐标点击邀请面板底部的按钮。"""
        x, y = rule.coord()
        self.device.click(x=int(x), y=int(y), control_name=rule.name)
        logger.info(f'DraftDuel clicked {rule.name} at ({x:.0f},{y:.0f})')

    def _invite_cross_server_friends(self, friend_list):
        """组队大厅：点加号→切“跨区”→按昵称选人→点邀请发送（借鉴 GeneralInvite 流程）。"""
        logger.hr('DraftDuel invite cross-server friends', 2)
        if not self._open_draft_invite_panel():
            return False
        time.sleep(0.5)
        self.screenshot()
        if not self._click_draft_friend_class('跨区'):
            logger.warning('DraftDuel no 跨区 tab, cancel invite')
            self._draft_invite_click(self.I_INVITE_CANCEL)
            return False
        time.sleep(1.0)
        self.screenshot()
        selected = 0
        for name in friend_list:
            for _ in range(2):
                if self._select_draft_friend(name):
                    selected += 1
                    break
                self.screenshot()
        if selected == 0:
            logger.warning(f'DraftDuel none of {friend_list} found in 跨区, cancel invite')
            self._draft_invite_click(self.I_INVITE_CANCEL)
            return False
        time.sleep(0.5)
        # 点“邀请”发送邀请（图1：好友列表下方邀请按钮中心 ≈ (758,572)）
        self._draft_invite_click(self.I_INVITE_ENSURE)
        # 等邀请面板关闭回到大厅
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.screenshot()
            if not self._draft_invite_panel_open():
                logger.info(f'DraftDuel invited {friend_list} successfully')
                return True
            time.sleep(0.3)
        return True

    def _wait_draft_invite_and_accept(self, timeout):
        """队员端：等待队长发来的协同对弈邀请弹窗，点绿色√接受（≈132,255）。"""
        deadline = time.monotonic() + timeout
        attempts = 0
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()
            text = str(self.O_INVITE_ACCEPT.ocr(self.device.image) or '').strip()
            if '邀' in text:
                attempts += 1
                self.device.click(x=132, y=255, control_name='draft_invite_accept')
                logger.info(f'DraftDuel member clicked green √ accept (attempt {attempts})')
                # 验证弹窗关闭（“邀”按钮消失）
                close_deadline = time.monotonic() + 5
                closed = False
                while time.monotonic() < close_deadline:
                    self.device.stuck_record_clear()
                    self.screenshot()
                    if '邀' not in str(self.O_INVITE_ACCEPT.ocr(self.device.image) or '').strip():
                        closed = True
                        break
                    time.sleep(0.5)
                if closed:
                    logger.info('DraftDuel member accepted invite, panel closed')
                    return True
                logger.warning('DraftDuel √ click did not close panel, retry')
            time.sleep(0.8)
        logger.warning('DraftDuel member invite accept timeout')
        return False

    def _enter_first_round(self, settings):
        self._require_open_for_match()
        self.goto_page(page_town)
        self.screenshot()
        self.click(self.I_TOWN_GOTO_DRAFT_DUEL, interval=0.6)
        self.screenshot()
        if settings.match_mode == DraftMatchMode.TEAM:
            if settings.role == DraftRole.CAPTAIN:
                names = [line.strip() for line in settings.invite_friend_list.split('\n') if line.strip()]
                if names:
                    self._invite_cross_server_friends(names)
                deadline = time.monotonic() + settings.teammate_timeout_seconds
                teammate_seen = 0
                while time.monotonic() < deadline:
                    self.device.stuck_record_clear()
                    self.screenshot()
                    if not self.appear(self.I_EMPTY_TEAMMATE):
                        teammate_seen += 1
                        if teammate_seen >= 3:
                            break
                    else:
                        teammate_seen = 0
                    time.sleep(0.8)
                else:
                    raise GameStuckError('协同对弈组队模式等待队友超时')
            else:
                # 队员：等待队长的邀请弹窗并点“邀”接受；不点“战”，由队长开战
                if not self._wait_draft_invite_and_accept(settings.teammate_timeout_seconds):
                    raise GameStuckError('协同对弈组队模式等待邀请超时')
                return self._wait_round(1, settings.start_timeout_seconds)
        logger.info(f'DraftDuel matchmaking mode: {settings.match_mode.value}')
        self.click(self.C_START, interval=0.5)
        return self._wait_round(1, settings.start_timeout_seconds)

    def _round_number(self):
        text = str(self.O_ROUND.ocr(self.device.image) or '')
        match = re.search(r'第\s*([1-5])\s*名\s*式神', text)
        return int(match.group(1)) if match else None

    def _wait_round(self, expected, timeout):
        # 选人横幅只在我方回合出现。匹配动画与对方回合可能超过设置的
        # 30 秒，所以持续读取横幅；宽限期内不重启正在进行的对局。
        start = time.monotonic()
        expected = max(expected, getattr(self, '_verified_pick_round', 0))
        deadline = start + max(timeout + 60, 90)
        warned = False
        previous = None
        stable_frames = 0
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()
            number = self._round_number()
            if number is not None and number >= expected:
                stable_frames = stable_frames + 1 if number == previous else 1
                if stable_frames >= 2:
                    if number > expected:
                        logger.warning(f'DraftDuel missed pick {expected}; resume at pick {number}')
                    self._accept_pick_round(number)
                    return number
            else:
                stable_frames = 0
            previous = number
            if not warned and time.monotonic() - start >= timeout:
                logger.warning(f'DraftDuel pick {expected} still pending after {timeout}s; keep OCR polling')
                warned = True
            time.sleep(0.25)
        raise GameStuckError(f'协同对弈等待第{expected}轮超时')

    def _accept_pick_round(self, number):
        """Only a verified new round clears that phase's button history."""
        previous = getattr(self, '_verified_pick_round', 0)
        if number <= previous:
            return
        self._verified_pick_round = number
        self.device.confirm_control_progress(self.C_SELECT)

    def _pick_and_wait(self, position, round_number, timeout):
        """A rejected action never becomes a selected member."""
        emitted = self.click(self.C_SELECT[position], interval=0.5)
        if emitted is False:
            logger.warning(f'DraftDuel pick {round_number} was blocked; keep team unconfirmed')
        if round_number == self.TEAM_SIZE:
            self._wait_final_pick_accepted(timeout)
            return emitted is not False, self.TEAM_SIZE + 1
        observed = self._wait_round(round_number + 1, timeout)
        return emitted is not False and observed == round_number + 1, observed

    def _offers(self):
        for attempt in range(4):
            raw = [str(rule.ocr(self.device.image) or '') for rule in self.O_NAMES]
            names = [normalize_name(value) for value in raw]
            logger.info(f'DraftDuel offers: raw={raw}, names={names}')
            if len([name for name in names if name]) >= 2:
                return names
            if attempt < 3:
                # OCR 抖动常见于描边/动效帧，换新帧重试，避免单轮抖动直接退出。
                self.screenshot()
                time.sleep(0.3)
        raise GameStuckError('协同对弈至少两张候选卡名称未能识别')

    def _wait_final_pick_accepted(self, timeout):
        deadline = time.monotonic() + timeout
        absent_since = None
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()
            if self._round_number() != self.TEAM_SIZE:
                absent_since = absent_since or time.monotonic()
                if time.monotonic() - absent_since >= 1.2:
                    return
            else:
                absent_since = None
            time.sleep(0.5)
        raise GameStuckError('协同对弈第五次选择后未离开选人界面')

    def _screen_results(self):
        image = self.device.image
        cached = getattr(self, '_screen_ocr_cache', None)
        if cached is not None and cached[0] is image:
            return cached[1]
        height, width = image.shape[:2]
        self.O_SCREEN.roi = [0, 0, width, height]
        results = self.O_SCREEN.detect_and_ocr(image, logDisplay=False)
        self._screen_ocr_cache = (image, results)
        return results

    def _screen_text(self):
        return ' '.join(item.ocr_text for item in self._screen_results())

    def _battle_ready(self):
        height, width = self.device.image.shape[:2]
        for item in self._screen_results():
            if item.ocr_text.strip() not in ('确定', '確定'):
                continue
            x, y = item.box.min(axis=0)
            right, bottom = item.box.max(axis=0)
            if x < width * 0.7 or y < height * 0.6:
                continue
            # 点击 OCR 检测文字中心偏左 35px：鼓形"确定"按钮上文字靠右,
            # 点文字中心会落在按钮右缘导致偶发点击失效(confirmation not observed)。
            cx = int(x + (right - x) / 2) - 35
            cy = int(y + (bottom - y) / 2)
            area = (cx - 25, cy - 25, 50, 50)
            self.C_BATTLE_CONFIRM = RuleClick(roi_front=area, roi_back=area, name='battle_confirm')
            return True
        return False

    def _click_auto_form(self):
        """OCR 定位「自动上阵」按钮（右下角、文字竖排两行），返回是否找到。"""
        for item in self._screen_results():
            ocr_text = item.ocr_text or ''
            if '自动' in ocr_text and '上阵' in ocr_text:
                x, y = item.box.min(axis=0)
                right, bottom = item.box.max(axis=0)
                cx = int((x + right) / 2)
                cy = int((y + bottom) / 2)
                self.C_AUTO_FORM = RuleClick(
                    roi_front=(cx - 25, cy - 25, 50, 50),
                    roi_back=(cx - 25, cy - 25, 50, 50),
                    name='auto_form')
                return True
        return False

    def _card_locations(self, names):
        bank = getattr(self, '_portrait_bank', {})
        locations = {name: locate_card(self.device.image, bank.get(name, [])) for name in names}
        found = {name: value for name, value in locations.items() if value is not None}
        # 不允许两个人名被匹配到同一张卡。
        for name, value in list(found.items()):
            if any(other != name and abs(value['point'][0] - match['point'][0]) < 45
                   for other, match in found.items()):
                locations[name] = None
        return locations

    def _detail_name_boxes(self):
        height, width = self.device.image.shape[:2]
        found = []
        for item in self._screen_results():
            name = normalize_name(item.ocr_text)
            x, y = item.box.mean(axis=0)
            if name and width * 0.16 < x < width * 0.92 and height * 0.22 < y < height * 0.32:
                found.append((name, item))
        found.sort(key=lambda value: value[1].box[:, 0].mean())
        return found

    def _post_draft_lobby_role(self):
        """用选秀完成的阵容标记定位大厅，胜场 OCR 不作为开战前提。"""
        if not self.appear(self.I_POST_DRAFT_ROSTER):
            return None
        # NCC 模板在整体变暗时仍可命中；结算弹层下的大厅不能开战。
        x, y, width, height = self.I_POST_DRAFT_ROSTER.roi_front
        visible = self.device.image[y:y + height, x:x + width, :3]
        if visible.size == 0:
            return None
        brightness = np.percentile(visible.mean(axis=2), 90)
        reference = np.percentile(self.I_POST_DRAFT_ROSTER.image.mean(axis=2), 90)
        if brightness < reference * 0.8:
            return None
        score = str(self.O_LOBBY_SCORE.ocr(self.device.image) or '')
        wins = re.search(r'([0-6])\s*番\s*胜', score)
        if wins and int(wins.group(1)) >= 6:
            return 'finished'
        if self.appear(self.I_LOBBY_BATTLE):
            return 'captain'
        button = str(self.O_LOBBY_BUTTON.ocr(self.device.image) or '')
        return 'member' if '等待' in button else 'waiting'

    def _advance_post_draft_lobby(self, role):
        """队长稳定看到“战”才点击；队员等待，并允许队长身份变化。"""
        previous = getattr(self, '_lobby_role', None)
        if role != previous:
            self._lobby_role = role
            self._lobby_stable_frames = 1
            logger.info(f'DraftDuel post-draft lobby role: {role}')
        else:
            self._lobby_stable_frames += 1
        if role != 'captain' or self._lobby_stable_frames < 2:
            return
        now = time.monotonic()
        if now < getattr(self, '_lobby_start_retry_at', 0):
            return
        attempts = getattr(self, '_lobby_start_attempts', 0)
        if attempts >= 3:
            raise GameStuckError('协同对弈队长点击战后仍未进入战斗')
        self._require_open_for_match()
        self._lobby_start_retry_at = now + 5
        previous_timing = getattr(self.device, '_time_critical_clicks', False)
        self.device._time_critical_clicks = True
        try:
            clicked = self.click(self.I_LOBBY_BATTLE, interval=1.0)
        finally:
            self.device._time_critical_clicks = previous_timing
        if clicked is not False:
            self._lobby_start_attempts = attempts + 1
            self._lobby_start_pending = True
            logger.info('DraftDuel captain clicked 战; await battle preparation')

    def _resume_post_draft_match(self, settings):
        if (self._post_draft_lobby_role() is None and not self._battle_ready() and
                not self.appear(GeneralBattleAssets.I_BATTLE_INFO)):
            return False
        logger.info('DraftDuel resume existing post-draft match without starting a new draft')
        self._wait_battle_return(settings.battle_timeout_seconds, [])
        return True

    @staticmethod
    def _resolve_lineup_names(raw_names, drafted):
        clean_names = [re.sub(r'\d+$', '', raw) for raw in raw_names]
        names = [normalize_name(raw) for raw in clean_names]
        # 只依据本局已选名单补全 OCR，避免把近形字认成其他候选。
        for index, name in enumerate(names):
            if name and name not in drafted:
                matches = [candidate for candidate in drafted
                           if name in candidate and candidate not in names]
                if len(matches) == 1:
                    names[index] = matches[0]
            if names[index] is None and len(clean_names[index]) >= 3:
                choices = [candidate for candidate in drafted if candidate not in names]
                ranked = sorted(((difflib.SequenceMatcher(
                    None, clean_names[index], candidate).ratio(), candidate)
                    for candidate in choices), reverse=True)
                if (ranked and ranked[0][0] >= 0.7 and
                        (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15)):
                    names[index] = ranked[0][1]
        missing = set(drafted) - set(name for name in names if name)
        if len(missing) == 1 and names.count(None) == 1:
            names[names.index(None)] = missing.pop()
        return names

    @staticmethod
    def _victory_screen(image):
        """“获得大胜”字样经过特效处理，OCR 不稳定，改看右上金色大字。"""
        area = cv2.cvtColor(image[20:255, 950:1240], cv2.COLOR_RGB2HSV)
        gold = ((area[:, :, 0] >= 8) & (area[:, :, 0] <= 38) &
                (area[:, :, 1] > 80) & (area[:, :, 2] > 120))
        return int(gold.sum()) >= 15000

    def _read_battle_lineup(self, drafted):
        """阵容详情依当前卡牌顺序列出五人；用初选名单核对 OCR。"""
        cached = getattr(self, '_match_lineup_detail', None)
        if cached is not None:
            return cached
        now = time.monotonic()
        if now < getattr(self, '_lineup_detail_retry_at', 0):
            return None
        attempts = getattr(self, '_lineup_detail_attempts', 0)
        if attempts >= 3:
            return None
        self._lineup_detail_attempts = attempts + 1
        self._lineup_detail_retry_at = now + 10
        candidates = []
        for index, x in enumerate((115, 190, 265, 335, 405, 480)):
            roi = self.device.image[40:90, x-24:x+24]
            saturation = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)[:, :, 1].mean()
            candidates.append((saturation >= 120, -float(roi.std()), index))
        opened = False
        for _, _, index in sorted(candidates)[:2]:
            if self.click(self.C_DETAIL_CARDS[index], interval=0.15) is False:
                return None
            time.sleep(0.3)
            self.screenshot()
            if '阵容详情' in str(self.O_DETAIL_TITLE.ocr(self.device.image) or ''):
                opened = True
                break
        if not opened:
            logger.warning('DraftDuel lineup detail did not open')
            return None
        try:
            if self.click(self.C_MINE_TAB, interval=0.3) is False:
                return None
            time.sleep(0.4)
            previous_names = None
            stable = False
            for reading in range(3):
                self.screenshot()
                name_boxes = self._detail_name_boxes()
                raw_names = [item.ocr_text for _, item in name_boxes]
                # 只读实际名单，不从选秀偏好补猜；等待“我的”切页完成。
                names = self._resolve_lineup_names(raw_names, ())
                valid = len(names) == 5 and not any(name is None for name in names) and len(set(names)) == 5
                if valid and names == previous_names:
                    stable = True
                    break
                previous_names = names if valid else None
                if reading < 2:
                    time.sleep(0.25)
            if not stable:
                logger.warning(f'DraftDuel battle lineup OCR unstable: raw={raw_names}, names={names}; '
                               f'detail attempt {attempts + 1}/3')
                return None
            self._actual_drafted = list(names)
            bank = getattr(self, '_portrait_bank', {})
            for name, (_, item) in zip(names, name_boxes):
                portrait = portrait_above_name(self.device.image, item.box)
                if portrait.size:
                    # 详情是已核对的本局名单；替换之前选秀阶段的临时参照。
                    bank[name] = [portrait, full_portrait_above_name(self.device.image, item.box),
                                  face_above_name(self.device.image, item.box)]
            self._portrait_bank = bank
            if drafted and set(drafted) != set(names):
                logger.warning(f'DraftDuel actual lineup differs from pick preferences: {names}')
            speed_by_name = {}
            for index, name in enumerate(names):
                label = CATALOG[name].tier
                if '速' in label or '220' in label or '170' in label:
                    raw = str(self.O_LINEUP_SPEEDS[index].ocr(self.device.image) or '')
                    match = re.search(r'\d{2,3}', raw)
                    if match:
                        speed_by_name[name] = int(match.group())
            defense_by_name = {}
            for index, name in enumerate(names):
                if name == '云外镜':
                    raw = str(self.O_LINEUP_DEFENSES[index].ocr(self.device.image) or '')
                    match = re.search(r'\d{3,4}', raw)
                    if match:
                        defense_by_name[name] = int(match.group())
            logger.info(f'DraftDuel battle lineup: {names}, '
                        f'speed={speed_by_name}, defense={defense_by_name}')
            self._match_lineup_detail = (names, speed_by_name, defense_by_name)
            return self._match_lineup_detail
        finally:
            self.click(self.C_DETAIL_CLOSE, interval=0.2)

    @staticmethod
    def _card_saturation(image, index):
        x = (320, 435, 550, 665, 780)[index]
        hsv = cv2.cvtColor(image[548:620, x-35:x+35], cv2.COLOR_BGR2HSV)
        return float(hsv[:, :, 1].mean())

    @staticmethod
    def _spotlight_x(image, role='captain'):
        """寻找当前候选式神上方的蓝色聚光灯；灯位随上场轮次移动。
        返回灯位中心 (x, y)。队员(role=member)的战场格子在右上方,
        扩展搜索到右半(80-700, 避开敌方半场), 无蓝光时 fallback 到右上。"""
        if role == 'member':
            # 队员格子偏右上方: 搜索区 y 100-340, x 80-700(我方半场右缘, 敌方半场更靠右以 700 为界)
            area = image[100:340, 80:700].astype(np.float32)
            y0, x0 = 100, 80
            fallback = (600, 280)
        else:
            # 敌方半场也有蓝色特效；队长只在我方半场(左侧)寻找上场聚光灯。
            area = image[130:300, 80:590].astype(np.float32)
            y0, x0 = 130, 80
            fallback = (110, 320)
        blue = (area[:, :, 2] > 130) & (area[:, :, 2] > area[:, :, 0] * 1.15)
        columns = blue.sum(axis=0).astype(np.float32)
        if columns.max() <= 0:
            # 没有搜到蓝光聚光灯(队员半场可能无灯): 用右上方/原 fallback, 不要拖到左下角
            return fallback
        window = np.convolve(columns, np.ones(60, dtype=np.float32), mode='valid')
        x = int(x0 + np.argmax(window) + 30)
        rows = blue.sum(axis=1).astype(np.float32)
        y = int(y0 + int(np.argmax(rows)))
        return x, y

    def _select_battle_lineup(self, drafted):
        previous = getattr(self.device, '_time_critical_clicks', False)
        self.device._time_critical_clicks = True
        try:
            return self._select_battle_lineup_timed(drafted)
        finally:
            self.device._time_critical_clicks = previous

    def _wait_drag_selection(self, target, timeout=4):
        """松手后卡牌会重排；连续两帧确认同一画像和叉号才算成功。"""
        deadline = time.monotonic() + timeout
        stable = 0
        previous_x = None
        time.sleep(0.3)
        while time.monotonic() < deadline:
            self.screenshot()
            cross_x, cross_score = self._selected_cross(self.device.image)
            location = self._card_locations([target]).get(target)
            selected = (cross_score >= 0.6 and location is not None and
                        abs(cross_x - location['point'][0]) <= 45)
            if selected:
                x = location['point'][0]
                stable = stable + 1 if previous_x is not None and abs(x-previous_x) <= 15 else 1
                previous_x = x
                if stable >= 2:
                    return True
            else:
                stable, previous_x = 0, None
            time.sleep(0.2)
        return False

    def _select_battle_lineup_timed(self, drafted):
        # 自动上阵优先：检测到「自动上阵」按钮则直接点击（系统自动配置），
        # 等「确定」可点后确认，免手动拖动。第一场战斗结束后回到准备界面即有此按钮。
        if self._click_auto_form():
            logger.info('DraftDuel auto-form detected; clicking 自动上阵')
            if self.click(self.C_AUTO_FORM, interval=0.3) is False:
                return
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.screenshot()
                if self._battle_ready():
                    if self.click(self.C_BATTLE_CONFIRM, interval=0.3) is False:
                        return
                    return
                time.sleep(0.5)
            logger.warning('DraftDuel auto-form clicked but confirm not ready')
            return
        detail = getattr(self, '_battle_detail', None) or getattr(self, '_match_lineup_detail', None)
        verified = getattr(self, '_verified_draft_names', [])
        if detail is None and len(verified) == 5:
            locations = self._card_locations(verified)
            if all(locations.values()):
                detail = (list(verified), {}, {})
                self._match_lineup_detail = detail
                logger.info(f'DraftDuel lineup verified by draft portraits: {verified}')
        # 对局内不再打开阵容详情面板（准备阶段倒计时短，且反复打开会触发
        # 点击防抖暂停 60s）；阵容信息只来自大厅阶段的缓存或本局选人名单。
        if detail is None:
            return
        names, speeds, defenses = detail
        locked = getattr(self, '_battle_locked', [])
        for turn in range(3):
            self.screenshot()
            if not self._battle_ready():
                return
            locations = self._card_locations(names)
            for name, match in locations.items():
                if match and match.get('grey') and name not in locked:
                    locked.append(name)
                    self._battle_locked = locked
                    logger.info(f'DraftDuel already fielded card recognized: {name}')
            available = [name for name in names if name in locked or
                         (locations.get(name) and not locations[name].get('grey'))]
            if not available:
                logger.warning(f'DraftDuel not enough active cards: {available}')
                return
            fire_locked = [name for name in locked if is_fire_supplier(name)]
            # 总共 2 火机即可（两个号合计）；本方允许上 2 只，超过 2 只才过滤。
            if len(fire_locked) >= 2:
                available = [name for name in available
                             if name in locked or not is_fire_supplier(name)]
            preferred = getattr(self, '_battle_plan', None)
            if preferred is not None and sum(is_fire_supplier(name)
                    for name in set(locked) | set(preferred)) > 2:
                logger.info('DraftDuel discard lineup with too many fire suppliers')
                preferred = None
                self._battle_plan = None
            if preferred is None:
                try:
                    mate_names = getattr(self, '_battle_mate_names', None)
                    if mate_names and len(mate_names) >= 3:
                        try:
                            own_pick, mate_pick = plan_duel_lineup(
                                available, mate_names, locked=locked,
                                team_size=min(3, len(available)))
                            preferred = own_pick
                            logger.info('DraftDuel joint lineup (2火+2输出+1保护+1控制/增伤): '
                                        f'own={own_pick} mate={mate_pick}')
                        except ValueError as exc:
                            logger.warning(f'DraftDuel joint planning failed, fallback single: {exc}')
                            preferred = plan_lineup(available, team_size=min(3, len(available)), locked=locked,
                                                    speeds=speeds, defenses=defenses, max_fire=1)
                    else:
                        preferred = plan_lineup(available, team_size=min(3, len(available)), locked=locked,
                                                speeds=speeds, defenses=defenses, max_fire=1)
                except ValueError as exc:
                    logger.warning(f'DraftDuel lineup planning failed: {exc}')
                    return
                self._battle_plan = preferred
            if len(locked) >= len(preferred):
                return
            targets = [name for name in preferred if name not in locked]
            target = targets[0]
            location = locations.get(target)
            if location is None:
                logger.warning(f'DraftDuel target portrait not confidently found: {target}')
                return
            if getattr(self, '_battle_drag_failures', {}).get(target, 0) >= 2:
                return
            source = card_drag_start(self.device.image, location)
            if len(fire_locked) >= 1 and is_fire_supplier(target) and target not in locked:
                logger.info(f'DraftDuel skip extra fire supplier: {target}; already={fire_locked}')
                self._battle_plan = None
                return
            # 默认高亮只代表候选被选中，不能替代上阵拖动。
            before = self.device.image.copy()
            before_x, _ = self._selected_cross(before)
            spotlight = self._spotlight_x(before,
                role=getattr(self, '_lobby_role', None) or 'captain')
            spotlight_x, spotlight_y = spotlight
            logger.info(f'DraftDuel drag target {target}: start={source}, '
                        f'portrait={location["point"]}, score={location["score"]:.3f}, '
                        f'spotlight={spotlight}')
            if drag_to_field(self.device,
                p1=source,
                p2=(spotlight_x, spotlight_y)) is False:
                return
            cross_confirmed = self._wait_drag_selection(target)
            cross_x, cross_score = self._selected_cross(self.device.image)
            portrait_change = float(np.abs(
                before[35:95, 85:510].astype(np.int16) -
                self.device.image[35:95, 85:510].astype(np.int16)).mean())
            if not cross_confirmed:
                failures = getattr(self, '_battle_drag_failures', {})
                failures[target] = failures.get(target, 0) + 1
                self._battle_drag_failures = failures
                logger.warning(
                    f'DraftDuel drag not confirmed; do not click confirm: {target}; '
                    f'spotlight={spotlight_x}, cross={before_x}->{cross_x} '
                    f'({cross_score:.2f}), portrait_change={portrait_change:.1f}')
                return
            logger.info(f'DraftDuel field {len(locked) + 1}/3: {target}, '
                        f'planned={preferred}')
            if len(fire_locked) >= 1 and is_fire_supplier(target) and target not in locked:
                logger.info(f'DraftDuel do not confirm extra fire supplier: {target}')
                self._battle_plan = None
                return
            if not self._battle_ready():
                return
            if self.click(self.C_BATTLE_CONFIRM, interval=0.3) is False:
                return
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                self.screenshot()
                advanced = not self._battle_ready()
                if not advanced:
                    current_location = self._card_locations([target]).get(target)
                    current_x, current_score = self._selected_cross(self.device.image)
                    advanced = (current_location is not None and current_score >= 0.6
                                and abs(current_x-current_location['point'][0]) > 45)
                if advanced:
                    locked.append(target)
                    self._battle_locked = locked
                    self.device.confirm_control_progress(
                        [self.C_BATTLE_CONFIRM, 'draft_battle_field'])
                    break
                time.sleep(0.3)
            else:
                logger.warning(f'DraftDuel confirmation not observed: {target}')
                return

    def _wait_battle_return(self, timeout, drafted):
        previous = getattr(self.device, '_time_critical_clicks', False)
        self.device._time_critical_clicks = False
        try:
            return self._wait_battle_return_impl(timeout, drafted)
        finally:
            self.device._time_critical_clicks = previous

    def _wait_battle_return_impl(self, timeout, drafted):
        deadline = time.monotonic() + timeout
        preparation_missing_since = None
        last_preparation_attempt = 0
        terminal_seen = 0
        while time.monotonic() < deadline:
            self.device.stuck_record_clear()
            self.screenshot()
            role = self._post_draft_lobby_role()
            if role is not None:
                if role == 'finished':
                    terminal_seen += 1
                    if terminal_seen >= 2:
                        return
                else:
                    terminal_seen = 0
                    self._advance_post_draft_lobby(role)
                time.sleep(0.5)
                continue
            # 跨页面后重新累计稳定帧，防止前一页的队长判断被复用。
            self._lobby_role = None
            self._lobby_stable_frames = 0
            text = self._screen_text()
            if '登录游戏中心' in text or '签到赚积分' in text:
                # 登录游戏中心推广弹窗：模态弹窗按返回键即关闭；
                # 不点“退出游戏/查看更多”，避免退游或开外部页面。
                logger.info('DraftDuel closing login-game-center popup via BACK')
                self.device.press_escape()
                time.sleep(1.0)
                continue
            if '队友已离开战斗' in text and '放弃本场战斗' in text:
                logger.info('DraftDuel teammate left; confirming this battle ends')
                self.click(self.C_TEAMMATE_LEAVE_CONFIRM, interval=0.5)
                continue
            if '拔得头筹' in text:
                # MVP 结算：右上金色大字被 _victory_screen 误判为普通胜利结算，
                # 但画面无「确定/继续」按钮，点 settlement_dismiss 无效会卡 60s；
                # 按 BACK 直接关闭（已验证 ESC 后回到上阵界面）。
                logger.info('DraftDuel MVP settlement detected; closing via BACK')
                self.device.press_escape()
                time.sleep(1.0)
                continue
            if (not any(word in text for word in ('选择中', '确定', '协同对弈', '阵容详情'))
                    and self._victory_screen(self.device.image)):
                logger.info('DraftDuel victory settlement detected')
                self.click(self.C_SETTLEMENT_DISMISS, interval=0.5)
                continue
            if any(item.ocr_text.strip() == '失败' for item in self._screen_results()):
                logger.info('DraftDuel defeat settlement detected')
                self.click(self.C_SETTLEMENT_DISMISS, interval=0.5)
                continue
            if self._battle_ready():
                if getattr(self, '_lobby_start_pending', False):
                    self.screenshot()
                    if not self._battle_ready():
                        continue
                    self.device.confirm_control_progress([self.I_LOBBY_BATTLE])
                    self._lobby_start_pending = False
                    self._lobby_start_attempts = 0
                    self._lobby_start_retry_at = 0
                preparation_missing_since = None
                if time.monotonic() - last_preparation_attempt >= 1.5:
                    last_preparation_attempt = time.monotonic()
                    self._select_battle_lineup(drafted)
                time.sleep(0.5)
                continue
            preparation_missing_since = preparation_missing_since or time.monotonic()
            plan = getattr(self, '_battle_plan', None)
            if ((plan and len(getattr(self, '_battle_locked', [])) >= len(plan)) or
                    time.monotonic() - preparation_missing_since >= 25):
                for attribute in ('_battle_locked', '_battle_plan', '_battle_detail',
                                  '_battle_drag_failures'):
                    if hasattr(self, attribute):
                        delattr(self, attribute)
            if any(item.ocr_text.strip() == '拒绝' for item in self._screen_results()):
                self.click(self.C_BOUNTY_REJECT, interval=0.3)
                continue
            if ('匹配' not in text and ('好友' in text or '邀请' in text) and
                    any(item.ocr_text.strip() == '取消' for item in self._screen_results())):
                self.click(self.C_FRIEND_CANCEL, interval=0.3)
                continue
            if '番胜' in text and '终' in text:
                self.click(self.C_SETTLEMENT_DISMISS, interval=0.4)
                continue
            if re.search(r'[0-6]\s*番\s*胜', text) and '协同对弈' not in text:
                logger.info(f'DraftDuel final banner: {text}')
                self.click(self.C_SETTLEMENT_DISMISS, interval=0.4)
                continue
            if '点击屏幕继续' in text:
                self.click(self.C_RESULT_CONTINUE, interval=0.4)
                continue
            if self.appear(self.I_CHECK_TOWN) or self.appear(self.I_CHECK_MAIN):
                return
            score = text
            match = re.search(r'([0-6])\s*番\s*胜', score)
            if '未开启' in score or match:
                if '未开启' in score:
                    terminal_seen += 1
                    if terminal_seen >= 2:
                        return
                    continue
                if match and int(match.group(1)) >= 6:
                    terminal_seen += 1
                    if terminal_seen >= 2:
                        return
                    continue
                terminal_seen = 0
                if match:
                    # 大厅开战已由上方阵容标记和“战”模板处理。
                    # 胜场文字本身不能证明这里存在可点击的开战按钮。
                    logger.debug(f'DraftDuel score visible outside post-draft lobby: {score}')
            else:
                terminal_seen = 0
            time.sleep(2)
        raise GameStuckError('协同对弈对局结束后未返回可识别页面')

    def run(self):
        previous = getattr(self.device, '_time_critical_clicks', False)
        self.device._time_critical_clicks = True
        try:
            return self._run_matches()
        finally:
            self.device._time_critical_clicks = previous

    def _run_matches(self):
        settings = self.config.draft_duel.draft_duel_config
        timeout = settings.pick_timeout_seconds
        for match_number in range(1, settings.completion_count + 1):
            self._duel_share_reset()
            for attribute in ('_battle_detail', '_battle_locked', '_battle_plan',
                              '_card_color_baseline', '_battle_drag_failures',
                              '_verified_pick_round', '_actual_drafted',
                              '_lobby_role', '_lobby_stable_frames',
                              '_lobby_start_attempts', '_lobby_start_retry_at',
                              '_lobby_start_pending', '_match_lineup_detail',
                              '_lineup_detail_retry_at', '_lineup_detail_attempts',
                              '_portrait_bank', '_verified_draft_names', '_screen_ocr_cache',
                              '_battle_mate_names'):
                if hasattr(self, attribute):
                    delattr(self, attribute)
            self.screenshot()
            round_number = self._round_number()
            if round_number is None:
                if self._resume_post_draft_match(settings):
                    logger.info(f'DraftDuel completed resumed match {match_number}/{settings.completion_count}')
                    continue
                round_number = self._enter_first_round(settings)
            elif round_number > 1:
                logger.warning(f'DraftDuel resumed during pick {round_number}; preserve current match')
            round_number = self._wait_round(round_number, timeout)
            # Preferences help choose subsequent offers; only lineup detail
            # establishes actual team membership before fielding cards.
            team = []
            while round_number <= self.TEAM_SIZE:
                # 对游戏内的十几秒倒计时只重试一次 OCR，避免耗尽选人时间。
                names = self._offers()
                decision = choose_pick(
                    [name for name in names if name], team, team_size=self.TEAM_SIZE)
                position = names.index(decision.name)
                logger.info(
                    f'DraftDuel match {match_number}/{settings.completion_count}, '
                    f'pick {round_number}/{self.TEAM_SIZE}: '
                    f'{decision.name}, score={decision.score}, '
                    f'reasons={decision.reasons}, souls={decision.suggested_souls}'
                )
                accepted, next_round = self._pick_and_wait(position, round_number, timeout)
                if accepted:
                    team.append(decision.name)
                    portrait = confirmed_draft_portrait(self.device.image, self._screen_results(), round_number)
                    if portrait is not None:
                        bank = getattr(self, '_portrait_bank', {})
                        bank.setdefault(decision.name, []).append(portrait)
                        self._portrait_bank = bank
                        self._verified_draft_names = list(team)
                round_number = next_round
            logger.info(f'DraftDuel pick preferences awaiting actual lineup: {team}')
            # 选完式神后、点“战”之前先在大厅查看阵容详情（式神配置），
            # 战斗准备阶段倒计时短，来不及查看。
            for _lobby_attempt in range(3):
                detail = self._read_battle_lineup(team)
                if detail is not None:
                    logger.info('DraftDuel lineup detail captured in post-draft lobby')
                    self._duel_share_write(team)
                    mate_names = self._duel_share_read_mate()
                    self._battle_mate_names = mate_names
                    break
                # 大厅阶段立即重试（清掉方法内的冷却/次数限制），
                # 若大厅界面不支持则重置计数，留给战斗准备阶段兜底。
                for attr in ('_lineup_detail_retry_at', '_lineup_detail_attempts'):
                    if hasattr(self, attr):
                        delattr(self, attr)
                time.sleep(2.0)
            self._wait_battle_return(settings.battle_timeout_seconds, team)
            logger.info(f'DraftDuel completed match {match_number}/{settings.completion_count}')
        self.set_next_run(task='DraftDuel', success=True, finish=True)
        raise TaskEnd('DraftDuel')

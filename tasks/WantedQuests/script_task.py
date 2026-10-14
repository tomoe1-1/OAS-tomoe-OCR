# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
from pathlib import Path
from cached_property import cached_property
from datetime import timedelta, time, datetime
from time import sleep

from tasks.GameUi.default_pages import page_battle_prepare, page_battle
from tasks.GameUi.matcher import any_of
from typing import List, Callable, Optional

from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import TaskEnd, TaskRecoveryFailed
from module.image.recipes import match_highlight_rule
from module.logger import logger
from tasks.Component.Costume.config import MainType
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.GameUi.page import page_main, page_exploration, page_shikigami_records
from tasks.Secret.script_task import ScriptTask as SecretScriptTask
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType, CooperationSelectMask, WQInfo, WQType, WantedQuestsConfig
from tasks.WantedQuests.explore import WQExplore, ExploreWantedBoss
from tasks.WantedQuests.invitation import (InvitationLedger, is_provisional,
                                            precise_key, provisional_key, quest_key)


class ScriptTask(WQExplore, SecretScriptTask, WantedQuestsAssets):
    WQ_LIST_MAX_SCROLLS = 4
    WQ_LIST_EDGE_MARGIN = 80

    @cached_property
    def invitation_ledger(self):
        profile = re.sub(r'[^\w.-]', '_', self.config.config_name)
        return InvitationLedger(Path('log/wanted_invitation') / f'{profile}.json')

    # 追踪界面(显示"前往"按钮的界面,左上角位置,神秘任务不好使)显示以下名称时,任务不再执行
    unwanted_boss_name_list: list = []
    # 已经执行过的悬赏封印(仅记录执行过的, 不一定成功运行)
    wq_executed_set: set[WQInfo] = set()

    def run(self):
        con = self.config.model.wanted_quests
        unwanted_boss_names = con.wanted_quests_config.unwanted_boss_names
        # Per-run state: class-level sets otherwise skip the same strategy tomorrow.
        self.wq_executed_set = set()
        self.unwanted_boss_name_list = [
            name.strip() for name in re.split(r"[，,]", unwanted_boss_names or '')
            if name.strip()
        ]
        self.wq_skipped_names = set()
        self.wq_unrevealed_skips = set()

        # 悬赏会使用探索和秘闻战斗，御魂直接复用这两个任务的预设。
        self.switch_mission_souls()

        preSuc = False
        if (self.get_config()).cooperation_only:
            preSuc = self.pre_work_cooperation_only()
        else:
            preSuc = self.pre_work()
        if not preSuc:
            # 无法完成预处理 很有可能你已经完成了悬赏任务
            logger.warning('Cannot pre-work')
            logger.warning('You may have completed the reward task')
            self.verify_quest_panel_complete()
            self.next_run()
            raise TaskEnd('WantedQuests')

        error_count = 0
        while 1:
            self.screenshot()
            if not self.is_wq_remained():
                # Completed visible cards cannot prove that lower cards are done.
                # Let find_wq and the bounded scroll loop inspect the whole list.
                logger.info("Visible wanted cards complete; scan lower cards before final verification")
            if self.appear(self.I_WQ_BOX):
                logger.info("get reward")
                self.ui_get_reward(self.I_WQ_BOX)
                continue
            if self.appear(self.I_E_REWARD_BOX_BIG):
                logger.info("get treasure")
                self.ui_get_reward(self.I_E_REWARD_BOX_BIG)
                continue
            current, found, total, self.O_WQ_TEXT_ALL.area = self.find_wq(
                self.device.image,
                allow_edge_fallback=(
                    error_count >= self.WQ_LIST_MAX_SCROLLS
                ),
            )
            if found == -1:
                if error_count >= self.WQ_LIST_MAX_SCROLLS:
                    logger.warning(
                        'No unfinished wanted quest after scrolling '
                        f'{self.WQ_LIST_MAX_SCROLLS} times, exit'
                    )
                    break
                error_count += 1
                # 当前画面没有可靠候选，保留原逻辑：手指上滑以继续浏览列表下方。
                self.swipe(self.S_WQ_LIST_UP, interval=1)
                sleep(1)
                continue
            if not self.open_wq_info():
                error_count += 1
                if error_count >= self.WQ_LIST_MAX_SCROLLS:
                    logger.warning('Wanted details remained unavailable; verify the full quest panel')
                    break
                self.swipe(self.S_WQ_LIST_UP, interval=1)
                sleep(1)
                continue
            if self.execute_mission(total - current) is False:
                # Untracing normally exposes the next quest. If the same
                # unusable counter remains visible, do not reopen forever.
                error_count += 1
                if error_count >= self.WQ_LIST_MAX_SCROLLS:
                    logger.warning('Wanted routes remained unusable; verify the full quest panel')
                    break
            else:
                error_count = 0
            sleep(1.5)
        self.verify_quest_panel_complete()
        self.next_run()
        raise TaskEnd('WantedQuests')

    def _open_wanted_completion_panel(self):
        self.screenshot()
        # The courtyard entry changes to a checked seal after quest completion.
        # This only opens the panel; completion still requires the full-panel check.
        entry = self.I_WQ_DONE if self.appear(self.I_WQ_DONE) else self.I_WQ_SEAL
        self.ui_click(entry, self.I_UI_BACK_RED)

    def verify_quest_panel_complete(self):
        """Verify every quest, allowing this run's positively unrevealed mystery cards."""
        self.goto_page(page_main)
        self._open_wanted_completion_panel()
        panel_ocr = RuleOcr(roi=(100, 60, 1150, 580), area=(100, 60, 1150, 580),
                            mode='Full', method='Default', keyword='',
                            name='wq_completion_panel')
        stable = 0
        previous_mystery = None
        for _ in range(4):
            self.screenshot()
            result = panel_ocr.detect_and_ocr(self.device.image, logDisplay=False)
            ignored = list(self._completed_invitation_regions(result))
            cooperation_reader = getattr(self, '_completed_cooperation_regions', None)
            completed_cooperation = cooperation_reader(result) if cooperation_reader is not None else []
            ignored.extend(completed_cooperation)
            # A black mystery portrait still has a counter, but no revealed
            # target. Require its title, clue and counter on the same card.
            mystery_reader = getattr(self, '_unrevealed_mystery_regions', None)
            mystery = mystery_reader(result) if mystery_reader is not None else {}
            ignored.extend(mystery.values())
            unfinished, friend_pending, panel_texts = self._panel_unfinished_quests(result, ignored)
            if unfinished:
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                raise TaskRecoveryFailed(f'Wanted quests remain unfinished: {unfinished}; skipped={sorted(self.wq_skipped_names)}')
            if friend_pending:
                logger.info(f'Invitation quests completed by own progress; friend progress pending: {friend_pending}')
            mystery_unknown_reader = getattr(self, '_unverified_mystery_visible', None)
            mystery_unknown = (mystery_unknown_reader(result, mystery)
                               if mystery_unknown_reader is not None else False)
            # A panel with tracing controls positively identifies the quest panel.
            trace_visible = self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE)
            # Once every quest is sealed, tracing controls disappear entirely
            # and no unfinished x/y counter remains. Both are measured, not
            # inferred from artwork.
            sealed_panel = (not trace_visible and self.appear(self.I_UI_BACK_RED)
                            and not unfinished
                            and self._panel_looks_sealed(panel_texts))
            cooperation_panel = bool(completed_cooperation) and self.appear(self.I_UI_BACK_RED)
            identified = (trace_visible or self.appear(self.I_WQ_DONE) or sealed_panel
                          or cooperation_panel) and not mystery_unknown
            identities = set(mystery)
            same_mystery = (previous_mystery is None or
                            (identities == set(previous_mystery)
                             and all(max(abs(a - b) for a, b in zip(region, previous_mystery[key])) <= 12
                                     for key, region in mystery.items())))
            stable = (stable + 1 if same_mystery else 1) if identified else 0
            previous_mystery = mystery
            if stable >= 2:
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                if mystery:
                    self.wq_unrevealed_skips = identities
                    descriptions = [f'神秘妖怪(线索:{clue},目标:{total})'
                                    for clue, total in sorted(identities)]
                    self.wq_skipped_names.update(descriptions)
                    self.config.task_runtime_outcome = dict(
                        task='WantedQuests', status='skipped_unrevealed_mystery',
                        quests=descriptions)
                    logger.info(f'Unrevealed mystery quests skipped for this run: {descriptions}')
                logger.info('Full wanted quest panel checked after processing')
                return
        raise TaskRecoveryFailed('Wanted quest completion panel could not be verified')

    @classmethod
    def _unrevealed_mystery_regions(cls, results):
        """Return only unique, positively unrevealed mystery counters to skip."""
        items = []
        for item in results:
            box = getattr(item, 'box', None)
            if box is None:
                continue
            area = cls._wq_box_xywh(box)
            if area[2] <= 0 or area[3] <= 0:
                continue
            items.append((cls._normalize_wq_list_text(item.ocr_text).replace('：', ':'), area))
        candidates = {}
        for title, title_area in items:
            if title != '神秘妖怪':
                continue
            title_center = title_area[0] + title_area[2] / 2
            clues = [(text[3:], area) for text, area in items
                     if text.startswith('线索:') and text[3:]
                     and not any(char in text[3:] for char in '?？')
                     and 0 <= area[1] - title_area[1] <= 400
                     and abs(area[0] + area[2] / 2 - title_center) <= 165]
            if len(clues) != 1:
                continue
            clue, clue_area = clues[0]
            clue_center = clue_area[0] + clue_area[2] / 2
            counters = []
            for text, area in items:
                match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', text)
                if not match:
                    continue
                current, total = map(int, match.groups())
                center = area[0] + area[2] / 2
                if (0 <= current < total <= 14
                        and 0 <= area[1] - clue_area[1] <= 55
                        and abs(center - clue_center) <= 100):
                    counters.append((total, area))
            if len(counters) != 1:
                continue
            total, counter_area = counters[0]
            center = counter_area[0] + counter_area[2] / 2
            # A revealed or cooperative target must use its normal completion
            # rules, even if an old clue/title remains visible during animation.
            if any('击败' in text
                   and abs(area[0] + area[2] / 2 - center) <= 145
                   for text, area in items):
                continue
            key = (clue, total)
            candidates.setdefault(key, []).append(counter_area)
        # Identical mystery cards cannot safely identify which counter was skipped.
        return {key: (area[0] - 1, area[1] - 1, area[2] + 2, area[3] + 2)
                for key, areas in candidates.items() if len(areas) == 1
                for area in areas}

    @classmethod
    def _unverified_mystery_visible(cls, results, confirmed):
        """An incomplete reading of a visible mystery card cannot prove completion."""
        titles = []
        targets = []
        for result in results:
            text = cls._normalize_wq_list_text(result.ocr_text)
            if text != '神秘妖怪' and '击败' not in text:
                continue
            box = getattr(result, 'box', None)
            if box is None:
                if text == '神秘妖怪':
                    return True
                continue
            area = cls._wq_box_xywh(box)
            if text == '神秘妖怪':
                titles.append(area)
            else:
                targets.append(area)
        unrevealed = 0
        for title in titles:
            center = title[0] + title[2] / 2
            # A revealed card follows the regular own/friend progress checks.
            if any(0 <= target[1] - title[1] <= 400
                   and abs(target[0] + target[2] / 2 - center) <= 165
                   for target in targets):
                continue
            unrevealed += 1
        return unrevealed != len(confirmed)

    @classmethod
    def _panel_looks_sealed(cls, texts) -> bool:
        """Identity of a wanted panel, judged without reading the seal stamp.

        Completion itself is decided by the caller: tracing controls gone and no
        unfinished x/y counter left. The vertical stamp is deliberately not
        consulted. It does carry meaning — a sealed card reads 「已封印」 while
        an unfinished one reads its quest type (「鬼魂奇闻」「食材采集」「雷男」
        …) — but OCR mangles its first glyph on the same card frame to frame
        (记封/ 记封印 / 已封 / 已封印), so requiring it made a genuinely finished
        panel fail verification every round. The x/y counter is the stable
        signal; the stamp is decoration that happens to encode state.
        """
        normalized = [cls._normalize_wq_list_text(text) for text in texts]
        has_title = any('悬赏封印' in text for text in normalized)
        has_refresh = any('下次刷新' in text for text in normalized)
        return has_title and has_refresh

    @classmethod
    def _panel_unfinished_quests(cls, results, ignored_regions=()):
        """Only the player's own counter determines an invitation quest's completion."""
        items = []
        for item in results:
            text = cls._normalize_wq_list_text(item.ocr_text)
            box = getattr(item, 'box', None)
            area = cls._wq_box_xywh(box) if box is not None else None
            items.append((text, area))

        unfinished, friend_pending = [], []
        last_owner = None
        for text, area in items:
            if '好友击败' in text:
                last_owner = 'friend'
            elif '自己击败' in text:
                last_owner = 'self'
            match = re.search(r'(?<!\d)(\d{1,2})[7/](\d{1,2})(?!\d)', text)
            if not match:
                continue
            current, total = int(match.group(1)), int(match.group(2))
            if not 0 <= current < total <= 14:
                continue
            if area is not None and any(
                x <= area[0] + area[2] / 2 <= x + width
                and y <= area[1] + area[3] / 2 <= y + height
                for x, y, width, height in ignored_regions
            ):
                continue
            owner = last_owner
            if area is not None:
                labels = []
                for label, label_area in items:
                    if label_area is None or not ('好友击败' in label or '自己击败' in label):
                        continue
                    dy = area[1] - label_area[1]
                    dx = abs((area[0] + area[2] / 2) - (label_area[0] + label_area[2] / 2))
                    if 0 <= dy <= 55 and dx <= 170:
                        labels.append((dy, dx, label))
                owner = ('friend' if '好友击败' in min(labels)[2] else 'self') if labels else None
            (friend_pending if owner == 'friend' else unfinished).append(text)
        return unfinished, friend_pending, [text for text, _ in items]

    @classmethod
    def _completed_cooperation_regions(cls, results):
        """Either a reached own quota OR a same-card claim label proves completion.

        Only counters belonging to that cooperation card are excluded from the
        unfinished check. Never associate adjacent cards by OCR reading order.
        """
        items = [(cls._normalize_wq_list_text(r.ocr_text), cls._wq_box_xywh(r.box))
                 for r in results if getattr(r, 'box', None) is not None]
        regions = []
        for label, friend_area in items:
            if '好友击败' not in label:
                continue
            center = friend_area[0] + friend_area[2] / 2
            nearby = [(text, area) for text, area in items
                      if abs(area[0] + area[2] / 2 - center) <= 100]
            friends = [(text, area) for text, area in nearby
                       if '好友击败' in text and abs(area[1] - friend_area[1]) <= 100]
            if len(friends) != 1:
                continue
            friend_top = friend_area[1]
            # The claim stamp may cover "自己击败" (the supplied image OCR
            # reads only "自"). Anchor its counter above the intact friend row.
            own_top = friend_top - 70
            counters = []
            for text, area in nearby:
                match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', text)
                if match and own_top <= area[1] < friend_top:
                    counters.append(tuple(map(int, match.groups())))
            quota_reached = (len(counters) == 1 and
                             0 < counters[0][1] <= 14 and
                             counters[0][0] >= counters[0][1])
            claims = [area for text, area in nearby if text == '领取'
                      and -170 <= area[1] - friend_top <= 0]
            if quota_reached or len(claims) == 1:
                regions.append((center - 100, own_top, 200,
                                friend_top + 55 - own_top))
        return regions

    def _completed_invitation_regions(self, results):
        ledger = getattr(self, 'invitation_ledger', None)
        if ledger is None:
            return []
        labels = [(self._normalize_wq_list_text(r.ocr_text), self._wq_box_xywh(r.box))
                  for r in results if getattr(r, 'box', None) is not None]
        matches = {}
        for own, own_area in labels:
            if '自己击败' not in own:
                continue
            center = own_area[0] + own_area[2] / 2
            nearby = [(friend, area) for friend, area in labels
                      if '好友击败' in friend and 0 <= area[1] - own_area[1] <= 100
                      and abs(area[0] + area[2] / 2 - center) <= 170]
            if len(nearby) != 1:
                continue
            # Only the precise owner-label key exists on this side; the
            # invitation flow re-files its provisional slot entry under exactly
            # this key once the card expanded (InvitationLedger.renames), so
            # both ends name the same quest here.
            key = precise_key(own, nearby[0][0])
            if key and ledger.complete(key):
                matches.setdefault(key, []).append((center - 145, 0, 290, 580))
        # Ambiguous duplicate cards must not mask another unfinished quest.
        return [areas[0] for areas in matches.values() if len(areas) == 1]

    def switch_mission_souls(self) -> None:
        """为悬赏装配探索与秘闻任务已配置的御魂预设。"""
        soul_configs = (
            ('Exploration', self.config.model.exploration.switch_soul_config),
            ('Secret', self.config.model.secret.switch_soul),
        )
        for task_name, soul_config in soul_configs:
            if soul_config.enable:
                logger.info(f'Wanted quests use {task_name} soul preset')
                self.goto_page(page_shikigami_records)
                self.run_switch_soul(soul_config.switch_group_team)
            if soul_config.enable_switch_by_name:
                logger.info(f'Wanted quests use {task_name} named soul preset')
                self.goto_page(page_shikigami_records)
                self.run_switch_soul_by_name(
                    soul_config.group_name,
                    soul_config.team_name,
                )

    def open_wq_info(self) -> bool:
        """打开对应怪物悬赏详情界面"""
        timeout_timer = Timer(5).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_TRACE_TRUE):
                return True
            self.click(self.O_WQ_TEXT_ALL, interval=1.2)
        return False

    def next_run(self):
        before_end: time = self.get_config().before_end
        if before_end == time(hour=0, minute=0, second=0):
            self.set_next_run(task='WantedQuests', success=True, finish=True)
            return
        time_delta = timedelta(hours=-before_end.hour, minutes=-before_end.minute, seconds=-before_end.second)
        now_datetime = datetime.now()
        now_time = now_datetime.time()
        if time(hour=5) <= now_time < time(hour=18):
            # 如果是在5点到18点之间，那就设定下一次运行的时间为第二天的5点 + before_end
            next_run_datetime = datetime.combine(now_datetime.date() + timedelta(days=1), time(hour=5))
            next_run_datetime = next_run_datetime + time_delta
        elif time(hour=18) <= now_time < time(hour=23, minute=59, second=59):
            # 如果是在18点到23点59分59秒之间，那就设定下一次运行的时间为第二天的18点 + before_end
            next_run_datetime = datetime.combine(now_datetime.date() + timedelta(days=1), time(hour=18))
            next_run_datetime = next_run_datetime + time_delta
        else:
            # 如果是在0点到5点之间，那就设定下一次运行的时间为今天的18点 + before_end
            next_run_datetime = datetime.combine(now_datetime.date(), time(hour=18))
            next_run_datetime = next_run_datetime + time_delta
        self.set_next_run(task='WantedQuests', target=next_run_datetime)

    def pre_work(self):
        """
        前置工作，
        :return:
        """
        self.goto_page(page_main)
        done_timer = Timer(5)
        while 1:
            self.screenshot()
            if self.appear(self.I_TRACE_DISABLE):
                break
            if self.appear_then_click(self.I_WQ_SEAL, interval=1):
                continue
            if self.appear_then_click(self.I_WQ_DONE, interval=1):
                continue
            if self.appear_then_click(self.I_TRACE_ENABLE, interval=1):
                continue
            if self.special_main and self.click(self.C_SPECIAL_MAIN, interval=3):
                logger.info('Click special main left to find wanted quests')
                continue
            if self.appear(self.I_UI_BACK_RED):
                if not done_timer.started():
                    done_timer.start()
            if done_timer.started() and done_timer.reached():
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                return False
        # 已追踪所有任务
        logger.info('All wanted quests are traced')

        # 存在协作任务则邀请
        self.screenshot()
        if self.appear(self.I_WQ_INVITE_1) or self.appear(self.I_WQ_INVITE_2) or self.appear(self.I_WQ_INVITE_3):
            if self.need_invite_vip():
                self.all_cooperation_invite()
            else:
                self.invite_five()
        self.ui_click_until_disappear(self.I_UI_BACK_RED)
        self.goto_page(page_exploration)
        return True

    def pre_work_cooperation_only(self):
        if self.get_current_page() != page_main:
            self.goto_page(page_main)
        # 打开悬赏封印 界面
        done_timer = Timer(5)
        while 1:
            self.screenshot()
            if self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE):
                break
            if self.appear_then_click(self.I_WQ_SEAL, interval=1):
                continue
            if self.appear_then_click(self.I_WQ_DONE, interval=1):
                continue
            if self.special_main and self.click(self.C_SPECIAL_MAIN, interval=3):
                logger.info('Click special main left to find wanted quests')
                continue
            if self.appear(self.I_UI_BACK_RED):
                if not done_timer.started():
                    done_timer.start()
            if done_timer.started() and done_timer.reached():
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                return False

        if not (self.appear(self.I_WQ_INVITE_1) or self.appear(self.I_WQ_INVITE_2) or self.appear(self.I_WQ_INVITE_3)):
            logger.info("there is no cooperation quest")
            return False

        # 追踪任务 并邀请
        self.all_cooperation_invite()

        self.ui_click_until_disappear(self.I_UI_BACK_RED)
        self.goto_page(page_exploration)
        return True

    def trace_one(self, btn: RuleImage):
        """
            参数必须为邀请按钮(I_WQ_INVITE_n ),特定场景,就不做通用的函数了,怪麻烦的,若还有什么奇葩需求,再扩展吧
        @param btn: 邀请按钮,
        @type btn:
        """
        self.screenshot()
        if not self.appear(btn):
            return
        while 1:
            self.screenshot()
            # 追踪成功  或  是现世任务 不需要追踪
            if self.appear(self.I_WQ_TRACE_ONE_ENABLE) or self.appear(self.I_WQ_TRACE_ONE_REALWORLD):
                break
            if self.appear(self.I_WQ_TRACE_ONE_DISABLE):
                self.click(self.I_WQ_TRACE_ONE_DISABLE, interval=1.5)
                continue
            # 根据邀请按钮位置生成 对应的点击位置 打开追踪界面
            # NOTE magic Number

            self.device.click(btn.roi_front[0], btn.roi_front[1] - 40, control_name=str(btn) + ' y-40')
            # 防止点击后界面来不及刷新
            sleep(1.5)
        # 关闭单个任务的追踪界面
        self.ui_click_until_smt_disappear(self.C_WQ_TRACE_ONE_CLOSE, stop=self.I_WQ_TRACE_ONE_CHECK_OPENED,
                                          interval=1.5)

    def execute_mission(self, num_want: int):
        """
        执行对应的悬赏任务
        :param num_want: 一共要打败的怪物数量
        :return:
        """
        logger.hr('Start wanted quests')
        if not any(self.appear(rule) for rule in
                   (self.I_GOTO_1, self.I_GOTO_2, self.I_GOTO_3, self.I_GOTO_4)):
            # Button absence alone is not completion. The full panel later
            # verifies an unrevealed mystery card or preserves unfinished work.
            logger.info('No recognized wanted route; verify the full quest panel')
            self.ui_click(self.I_TRACE_TRUE, self.I_TRACE_FALSE)
            return False
        # 跳过不想打的
        monster_name = self.O_WQ_MONSTER_TYPE.detect_text(self.device.image)
        self.wanted_boss_only = monster_name.strip() in {'酒吞', '酒吞童子', '阎魔'}
        if monster_name in self.unwanted_boss_name_list:
            logger.warning(f'unwanted {monster_name}')
            self.wq_skipped_names.add(monster_name)
            self.ui_click(self.I_TRACE_TRUE, self.I_TRACE_FALSE)
            return False
        # 获取排序后的悬赏信息列表
        ordered_wq_infos = self.get_ordered_wq_infos(num_want)
        if not ordered_wq_infos:
            logger.info('Current wanted quest skipped all, cancel it')
            self.ui_click(self.I_TRACE_TRUE, self.I_TRACE_FALSE)
            return False
        wq_call_dict: dict[WQType, Callable] = {
            WQType.CHALLENGE: self.challenge,
            WQType.EXPLORE: self.explore,
            WQType.SECRET: self.secret
        }
        # 获取当前需要执行的悬赏策略
        wq_info = self.get_need_exec_wq(ordered_wq_infos)
        if not wq_info:
            logger.info('Current wanted quests can not be executed')
            self.ui_click(self.I_TRACE_TRUE, self.I_TRACE_FALSE)
            return False
        try:
            logger.info(f'Choose wq: {wq_info}')
            wq_call_dict[wq_info.type](wq_info.goto_btn, wq_info.do_num)
        except ExploreWantedBoss:
            logger.warning('Maybe only need attack boss')
        finally:
            self.wq_executed_set.add(wq_info)
            # The narration band can still be covering the screen here; leaving
            # with it open makes the page unrecognisable and recovery gives up.
            self.secret_after_battle()
            self.goto_page(page_exploration)

    def get_need_exec_wq(self, ordered_wq_infos: List[WQInfo]) -> Optional[WQInfo]:
        """获取需要执行的悬赏(按优先级选择最靠前的)"""
        for wq_info in ordered_wq_infos:
            if WQType.CHALLENGE == wq_info.type:
                number_challenge = self.O_WQ_NUMBER.ocr(self.device.image)
                if number_challenge < 5:  # 挑战卷5张都没有了, 省着点吧试试别的
                    logger.warning("Challenge ticket num < 5, skip")
                    continue
            return wq_info
        return None

    def get_ordered_wq_infos(self, num_want: int) -> List[WQInfo]:
        """获取当前悬赏妖怪页面排好序的信息列表, 会自动排除无法执行的悬赏"""
        wq_type_ocr = [self.O_WQ_TYPE_1, self.O_WQ_TYPE_2, self.O_WQ_TYPE_3, self.O_WQ_TYPE_4]
        wq_info_ocr = [self.O_WQ_INFO_1, self.O_WQ_INFO_2, self.O_WQ_INFO_3, self.O_WQ_INFO_4]
        goto_btn_list = [self.I_GOTO_1, self.I_GOTO_2, self.I_GOTO_3, self.I_GOTO_4]
        wq_info_list = []
        for i in range(4):
            wq_info = self.build_wq_info(wq_type_ocr[i], wq_info_ocr[i], goto_btn_list[i], num_want)
            if not wq_info:
                continue
            if not self.appear(wq_info.goto_btn):
                logger.info('Wanted route has no recognized available Goto button; skip this route')
                continue
            # 跳过高层秘闻
            if wq_info.dest[-1] in {"捌", "玖", "拾", "番外"}:
                logger.warning('This secret layer is too high, skip')
                continue
            # 跳过已经执行过的(例:都是探索第5层4只怪, 上次计算需要打2次但是这次还是打2次, 肯定出问题了也不需要执行了)
            if wq_info in self.wq_executed_set:
                logger.warning('This wanted quest has been executed, skip')
                continue
            wq_info_list.append(wq_info)
        # 排序
        type_ordered_list = self.get_config()._wq_type_ordered_list
        wq_info_list.sort(key=lambda x: type_ordered_list.index(x.type))
        return wq_info_list

    def build_wq_info(self, type_rule: RuleOcr, info_rule: RuleOcr, goto_btn_rule: RuleImage, num_want: int) -> Optional[WQInfo]:
        """
        构建单条悬赏信息
        :param type_rule: 悬赏类型
        :param info_rule: 悬赏信息
        :param goto_btn_rule: 前往按钮
        :param num_want: 想要攻击的数量
        :return: WQInfo
        """
        wq_type_txt = type_rule.ocr(self.device.image)
        # 适配老逻辑, 将式神碎片改为挑战
        wq_type_txt = '挑战' if wq_type_txt == '式神' else wq_type_txt
        if wq_type_txt == '' or not WQType.contains(wq_type_txt):
            logger.warning(f'Unknown wq type: {wq_type_txt}')
            return None
        wq_type = WQType(wq_type_txt)
        type_ordered_list = self.get_config()._wq_type_ordered_list
        if wq_type not in type_ordered_list:
            logger.warning(f'{wq_type.value} is not in the order list')
            return None
        wq_info_txt = info_rule.ocr(self.device.image)
        wq_info_txt = wq_info_txt.replace('：', ':').replace('（', '(').replace('）', ')')
        match = re.search(r"(.+?)\s*\(?[数教]量[:：]\s*(\d+)", wq_info_txt)
        if not match:
            logger.warning(f'Unknown wq info: {wq_info_txt}')
            return None
        wq_dest, wq_number = match.group(1).strip(), int(match.group(2))
        do_num = num_want // wq_number + (num_want % wq_number > 0)
        return WQInfo(wq_type, wq_dest, wq_number, goto_btn_rule, do_num)

    def challenge(self, goto_btn, num):
        self.ui_click(goto_btn, self.I_WQC_FIRE)
        self.ui_click(self.I_WQC_UNLOCK, self.I_WQC_LOCK)
        self.ui_click_until_disappear(self.I_WQC_FIRE)
        # 锁定阵容进入战斗
        wq_config = GeneralBattleConfig(lock_team_enable=True)
        self.run_general_battle(config=wq_config, exit_matcher=self.I_WQC_FIRE)

    def secret_chat_skip(self, budget=6) -> int:
        """Advance the secret-mission dialogue with one bounded burst of clicks.

        The secret chat is a black narration band that opens both BEFORE the
        battle and AFTER it finished. The pre-battle loop used to clear it
        inline; the post-battle half was never implemented, so ``goto_page``
        ran while the band still covered the screen, the page could not be
        recognised, and recovery deadlocked on an unidentified page.

        The band's identity is the narration itself: while it is up the secret
        screen's own 挑战 button is absent.  So the exit condition is
        "challenge button visible" rather than any chat-specific artwork.

        :param budget: maximum clicks in one burst; the chat is known to be
            arbitrarily long, so the caller must re-check and call again.
        :return: clicks actually issued
        """
        spent = 0
        while spent < budget:
            self.screenshot()
            # Challenge button back means the narration is done.
            if self.appear(self.I_WQSE_FIRE) or self.appear(self.I_WQSE_SPECIAL_FIRE):
                if spent:
                    logger.info(f'Secret mission chat cleared by {spent} clicks')
                return spent
            if not self.appear(self.I_UI_BACK_RED, threshold=0.7):
                # Neither chat nor its follow-up screen: nothing to advance.
                return spent
            self.click(self.C_SECRET_CHAT, interval=0.8)
            spent += 1
        logger.warning('Secret mission chat still open after one burst, re-check')
        return spent

    def secret(self, goto, num=1):
        self.ui_click(goto, self.I_WQSE_FIRE)
        for i in range(num):
            self.screenshot()
            # 若是当周特殊秘闻则禁止连续进攻, 战斗结束之后直接退到探索页面重新进入挑战(避免当周秘闻没打结果跳转到第一层)
            if self.appear(self.I_WQSE_SPECIAL_FIRE):
                logger.warning('Current is special secret, exit and retry')
                return
            # 又臭又长的对话针的是服了这个网易
            while 1:
                self.screenshot()
                if self.get_current_page() in [page_battle_prepare, page_battle]:
                    self.run_general_battle(self.battle_config, exit_matcher=any_of(self.I_UI_BACK_RED, self.I_WQSE_SPECIAL_FIRE))
                    break
                if self.appear_then_click(self.I_WQSE_FIRE, interval=1):
                    continue
                if self.appear(self.I_UI_BACK_RED, threshold=0.7) and not self.appear(self.I_WQSE_FIRE):
                    self.secret_chat_skip()
        logger.info('Secret mission finished')

    def secret_after_battle(self, rounds=3):
        """Clear the post-battle narration before leaving the secret screen.

        ``goto_page`` used to run straight after the battle, so a still-open
        narration band made the page unrecognisable and recovery gave up after
        60s.  Each round is a bounded click burst; a round that clears nothing
        ends the loop instead of spinning.
        """
        for _ in range(rounds):
            if not self.secret_chat_skip():
                return True
        logger.warning('Post-battle secret chat did not close, leaving anyway')
        self.device.click_record_clear()
        return False

    def invite_random(self, add_button: RuleImage):
        self.screenshot()
        if not self.appear(add_button):
            return False
        self.ui_click(add_button, self.I_WQ_INVITE_ENSURE, interval=2.5)
        logger.info('enter invite form')
        for index in range(1, 6):
            self.screenshot()
            before = len(self.I_WQ_INVITE_SELECTED.match_all_any(
                self.device.image, frame_id=self.device.image_frame_id))
            rule = getattr(self, f'I_WQ_FRIEND_{index}')
            if self.click(rule):
                self._ui_verify(lambda: len(self.I_WQ_INVITE_SELECTED.match_all_any(
                    self.device.image, frame_id=self.device.image_frame_id)) > before,
                    [rule], timeout=2, fail=False)
        self.screenshot()
        if not self.appear(self.I_WQ_INVITE_SELECTED):
            logger.warning('No friend selected')
            self.ui_click_until_disappear(self.I_WQ_INVITE_CANCEL)
            return False
        self.ui_click_until_disappear(self.I_WQ_INVITE_ENSURE)
        self._verify_invitation_panel()
        return True

    def invite_five(self):
        """
        邀请好友，默认点五个
        :return:
        """

        logger.hr('Invite friends')
        return self.all_cooperation_invite(name_all='')

    def all_cooperation_invite(self, name_all: str = None):
        """
            所有的协作任务依次邀请
            如果配置了只完成协作任务 还会将该任务设置为追踪
        @return:

        """
        self.screenshot()
        ret = self.get_cooperation_info()
        if len(ret) == 0:
            logger.info("no Cooperation found")
            return False
        typeMask = 15
        typeMask = CooperationSelectMask[(self.get_config()).cooperation_type.value]
        for item in ret:
            # 该任务是需要邀请的任务类型
            if not (item['type'] & typeMask):
                # BUG 存在多个协作任务时,邀请完第一个协作任务对方接受后,未邀请的任务位置无法确定(缺少信息)
                # 例如 按顺序存在 abc 3个协作任务,邀请完a,好友接受后,这三个任务在界面上的顺序变化,abc 还是bca
                # 如果顺序不变 则应该没有问题
                logger.info("cooperationType %s But needed Type %s ,Skipped", item['type'], typeMask)
                continue
            item['inviteResult'] = False
            if name_all is None:
                name = self.get_invite_vip_name(item['type'])
            else:
                name = name_all
            logger.warning("find cooperationType %s ,start invite %s", item['type'], name)
            item['inviteResult'] = self._invite_cooperation_twice(item['inviteBtn'], name,
                                                                  item['type'])
            # 邀请追踪一起吧,只有邀请成功才追踪
            if item['inviteResult']:
                self.invite_success_callback(item['type'], name)
                if (self.get_config()).cooperation_only:
                    logger.info("start trace_one")
                    self.trace_one(item['inviteBtn'])
        return ret

    def _verify_invitation_panel(self):
        return self._ui_verify(lambda: self.appear(self.I_UI_BACK_RED)
            and not self.appear(self.I_WQ_INVITE_ENSURE)
            and (self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE)), [])

    def _invitation_card_identity(self, btn, ctype=None):
        """Identify a cooperation card for the invitation ledger.

        The ledger exists so repeated task runs cannot spam the same quest with
        invitations, so the key must be stable across retries but must NOT be
        read off card artwork that appears only after inviting.

        On a real device a cooperation card shows 「与好友共享任务」 plus its
        objective and an x/y counter — the 「自己击败 … / 好友击败 …」 pair only
        shows up once the invitation has been placed and the card expands. So
        reading those labels BEFORE inviting asks for something that cannot be
        there yet, and did: six consecutive failures between 2026-10-05 and
        2026-10-07 all came from this line.

        Template-derived identity first: get_cooperation_info already located the
        card by its I_WQ_INVITE_n template, and the type icon beside it. That
        slot+type pair survives collapses, neighbours expanding and re-layouts.
        Card text is only a refinement, never a requirement.
        """
        x = max(0, int(btn.roi_front[0]) - 25)
        rule = RuleOcr(roi=(x, 130, 300, 470), area=(x, 130, 300, 470),
                       mode='Full', method='Default', keyword='', name='wq_invitation_identity')
        texts = [r.ocr_text for r in rule.detect_and_ocr(self.device.image, logDisplay=False)]
        slot = getattr(btn, 'name', '')
        precise = quest_key(texts)
        if precise:
            return precise
        if not slot or ctype is None:
            # Neither template position nor cooperation type: refuse rather than
            # invent an identity that could collide with another quest's entry.
            raise TaskRecoveryFailed('Invitation quest identity could not be verified')
        key = provisional_key(slot, ctype)
        logger.info('Cooperation card %s shows no owner label before inviting; '
                    'using its slot identity until the card expands', key)
        return key

    def _invitation_precise_key(self, btn):
        """The owner-label key for this card, or None while it is still collapsed."""
        x = max(0, int(btn.roi_front[0]) - 25)
        rule = RuleOcr(roi=(x, 130, 300, 470), area=(x, 130, 300, 470),
                       mode='Full', method='Default', keyword='', name='wq_invitation_identity')
        texts = [r.ocr_text for r in rule.detect_and_ocr(self.device.image, logDisplay=False)]
        return quest_key(texts)

    def _wait_invitation_accepted(self, btn, key, ctype=None):
        # A sent request is not an accepted request. Wait up to 20 seconds,
        # requiring a partner name and the disappearing add button in two frames.
        # Returns (accepted, key): the key can change here, because a card that
        # showed no owner labels before inviting may show them afterwards, and
        # the ledger entry is re-filed under that precise identity.
        timer = Timer(20).start()
        stable = 0
        panel_stable = 0
        x, y = int(btn.roi_front[0]), int(btn.roi_front[1])
        partner = RuleOcr(roi=(x + 40, y - 5, 210, 50), area=(x + 40, y - 5, 210, 50),
                          mode='Full', method='Default', keyword='', name='wq_invitation_partner')
        while not timer.reached():
            self.screenshot()
            precise = self._invitation_precise_key(btn)
            if precise is not None and precise != key:
                if is_provisional(key):
                    # The card expanded after the invitation and now shows who
                    # is doing what. Re-file the ledger entry under that precise
                    # identity; the panel completion check only sees these labels.
                    key = self.invitation_ledger.renames(key, precise)
                else:
                    # We already knew this card's owners and they changed: the
                    # card we are waiting on is no longer the one we invited.
                    raise TaskRecoveryFailed(
                        'Invitation quest identity changed; do not reuse its old position')
            present = self.appear(btn)
            panel_stable = panel_stable + 1 if present else 0
            names = [] if present else [r.ocr_text.strip() for r in
                partner.detect_and_ocr(self.device.image, logDisplay=False)]
            accepted = any(t and re.search(r'[\w\u4e00-\u9fff]', t)
                           and not any(word in t for word in ('邀请', '好友击败', '自己击败', '追踪'))
                           for t in names)
            stable = stable + 1 if accepted and not present else 0
            if stable >= 2:
                return True, key
        if panel_stable < 2:
            raise TaskRecoveryFailed('Invitation acceptance state could not be verified')
        return False, key

    def _invite_cooperation_twice(self, btn, name, ctype=None):
        self.screenshot()
        key = self._invitation_card_identity(btn, ctype)
        ledger = self.invitation_ledger
        for _ in range(InvitationLedger.LIMIT):
            state = ledger.state(key)
            if state['accepted']:
                return True
            if ledger.complete(key):
                break
            if state['checked'] < state['sent']:
                accepted, key = self._wait_invitation_accepted(btn, key, ctype)
            else:
                sent = self.cooperation_invite(btn, name) if name else self.invite_random(btn)
                if not sent:
                    # No selected recipient / unsuccessful send is not evidence
                    # that a friend declined two actual invitations.
                    continue
                ledger.sent(key)
                accepted, key = self._wait_invitation_accepted(btn, key, ctype)
            ledger.checked(key, accepted)
            if accepted:
                return True
        if ledger.complete(key):
            logger.info(f'Wanted invitation unanswered twice; consider this quest complete: {key}')
            self._untrace_invitation_quest(btn)
        return False

    def _untrace_invitation_quest(self, btn):
        self.screenshot()
        self.device.click(int(btn.roi_front[0]), int(btn.roi_front[1]) - 40,
                          control_name='WQ_INVITATION_COMPLETED_DETAILS')
        self._ui_verify(lambda: self.appear(self.I_WQ_TRACE_ONE_ENABLE)
            or self.appear(self.I_WQ_TRACE_ONE_DISABLE) or self.appear(self.I_WQ_TRACE_ONE_REALWORLD), [])
        if self.appear(self.I_WQ_TRACE_ONE_ENABLE):
            self.ui_click(self.I_WQ_TRACE_ONE_ENABLE, self.I_WQ_TRACE_ONE_DISABLE)
        self.ui_click_until_smt_disappear(self.C_WQ_TRACE_ONE_CLOSE, self.I_WQ_TRACE_ONE_CHECK_OPENED)
        self._verify_invitation_panel()

    def cooperation_invite(self, btn: RuleImage, name: str):
        """
            单个协作任务邀请
        @param btn:
        @param name:
        @return:
        """
        self.ui_click(btn, stop=self.I_WQ_INVITE_ENSURE, interval=2.5)

        # 选人
        self.O_WQ_INVITE_COLUMN_1.keyword = name
        self.O_WQ_INVITE_COLUMN_2.keyword = name

        find = False
        for i in range(2):
            self.wait_until_appear(self.I_WQ_INVITE_FRIEND_LIST_APPEAR, wait_time=4)
            self.screenshot()
            in_col_1 = self.ocr_appear_click(self.O_WQ_INVITE_COLUMN_1)
            in_col_2 = False if in_col_1 else self.ocr_appear_click(self.O_WQ_INVITE_COLUMN_2)
            find = in_col_2 or in_col_1
            if find:
                self.wait_until_appear(self.I_WQ_INVITE_SELECTED, wait_time=2)
                self.screenshot()
                if self.appear(self.I_WQ_INVITE_SELECTED):
                    logger.info("friend found and selected")
                    break
                find = False
                # TODO OCR识别到文字 但是没有选中 尝试重新选择  (选择好友时,弹出协作邀请导致选择好友失败)
            # 检测跨服好友按钮是否高亮
            self.screenshot()
            if not self.appear_highlight(self.I_WQ_INVITE_DIFF_SVR_HIGHLIGHT):
                if not self.click(self.I_WQ_INVITE_DIFF_SVR):
                    raise TaskRecoveryFailed('Cross-server friend tab was not actionable')
                self._ui_verify(lambda: self.appear_highlight(self.I_WQ_INVITE_DIFF_SVR_HIGHLIGHT),
                                [self.I_WQ_INVITE_DIFF_SVR])
            # 等待好友列表加载
            self.wait_until_appear(self.I_WQ_INVITE_DIFF_SVR_HIGHLIGHT, wait_time=4)
        # 没有找到需要邀请的人,点击取消 返回悬赏封印界面
        if not find:
            self.screenshot()
            self.ui_click_until_disappear(self.I_WQ_INVITE_CANCEL, interval=1.5)
            return False
        #
        self.ui_click_until_disappear(self.I_WQ_INVITE_ENSURE, interval=1)
        self._verify_invitation_panel()
        return True

    def get_cooperation_info(self) -> List:
        """
            获取协作任务详情
        @return: 协作任务类型与邀请按钮
        """
        self.screenshot()
        retList = []
        i = 0
        for index in range(3):
            btn = self.__getattribute__("I_WQ_INVITE_" + str(index + 1))
            if not self.appear(btn):
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_JADE_" + str(index + 1))):
                retList.append({'type': CooperationType.Jade, 'inviteBtn': btn})
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_DOG_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_CAT_FOOD_" + str(index + 1))):
                retList.append({'type': CooperationType.Food, 'inviteBtn': btn})
                continue
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_SUSHI_" + str(index + 1))):
                retList.append({'type': CooperationType.Sushi, 'inviteBtn': btn})
                continue
            # NOTE 因为食物协作里面也有金币奖励 ,所以判断金币协作放在最后面
            if self.appear(self.__getattribute__("I_WQ_COOPERATION_TYPE_GOLD_" + str(index + 1))):
                retList.append({'type': CooperationType.Gold, 'inviteBtn': btn})
                continue
        logger.info(f"get cooperation size {len(retList)}")
        return retList

    # 使用平均亮度检测是否一致
    def appear_highlight(self, rule_image: RuleImage):
        return match_highlight_rule(rule_image, self.device.image, frame_id=self.device.image_frame_id)

    @property
    def special_main(self) -> bool:
        # 特殊的庭院需要点一下，左边然后才能找到图标
        main_type = getattr(self, 'current_main_type', None)
        if main_type is None:
            configured = self.config.global_game.costume_config.costume_main_type
            if len(configured) == 1:
                main_type = configured[0]
        return main_type == MainType.COSTUME_MAIN_3

    def get_config(self) -> WantedQuestsConfig:
        return self.config.wanted_quests.wanted_quests_config

    def need_invite_vip(self):
        return bool(self.get_config().invite_friend_name)

    def get_invite_vip_name(self, ctype: CooperationType):
        return self.get_config().invite_friend_name

    def invite_success_callback(self, ctype: CooperationType, name):
        """
           邀请成功回调
        @param ctype:
        @type ctype:
        @param name:
        @type name:
        """

        return True

    def process_ocr(self, txt):
        def detect_spliter(txt):
            index = txt.find('/')
            if index != -1:
                return index
            # 由于斜杠'/'经常被误识别为'7',且悬赏封印悬赏怪物总数没有与‘7’相关的数字
            reg = re.compile(r'^(\d+)([7/])(\d+)$')
            match = reg.match(txt)
            if match:
                return match.start(2)
            return -1

        index = detect_spliter(txt)
        if index < 0:
            return 0, 0, 0
        return int(txt[:index]), 1, int(txt[index + 1:])

    def txt_ocr_appear(self, ocr_item: RuleOcr, reg, img):
        res = ocr_item.ocr(img)
        regex = re.compile(reg)
        ismatch = regex.match(res)
        return ismatch is not None

    @staticmethod
    def _wq_box_xywh(box) -> List[int]:
        """将 OCR 的四点坐标转换为相对 OCR 截图的 xywh。"""
        xs = [int(point[0]) for point in box]
        ys = [int(point[1]) for point in box]
        x1, x2 = min(xs), max(xs)
        y1, y2 = min(ys), max(ys)
        return [x1, y1, x2 - x1, y2 - y1]

    @staticmethod
    def _normalize_wq_list_text(text: str) -> str:
        """清理列表 OCR 文本，保留进度分隔符的容错字符 7。"""
        return ''.join(str(text).split()).replace('／', '/')

    @staticmethod
    def _has_wq_label_hint(text: str) -> bool:
        """完整或被底部裁切的“悬赏封印”都可作为局部归属证据。"""
        if '悬赏' in text or '封印' in text:
            return True
        return re.search(r'[封野][印卬]', text) is not None

    def find_wq(
        self,
        img,
        *,
        allow_edge_fallback: bool = False,
    ) -> tuple[int, int, int, List[int]]:
        """
        在探索页面定位未完成的悬赏卡位。

        新逻辑先找进度，再用同一卡位附近的“悬赏/封印”文字确认归属，
        避免将真八岐大蛇等其他左侧入口误当成悬赏。靠近底部的卡位先
        通过外层翻页移到完整区域；连续翻页仍无法完整显示时才启用边缘保底。

        :param img: 当前截图
        :param allow_edge_fallback: 已连续翻页到下方时，允许点击文字裁切的底部卡位
        :return: (已完成数量, 有效标记, 总数量, 进度文字点击 roi)
        """
        res_list = self.O_WQ_TEXT_ALL.detect_and_ocr(img)
        progress_pattern = re.compile(
            r'(?<!\d)(\d{1,2})([7/])(\d{1,2})(?!\d)'
        )
        roi_x, roi_y, _, roi_height = self.O_WQ_TEXT_ALL.roi
        safe_progress_bottom = (
            roi_y + roi_height - self.WQ_LIST_EDGE_MARGIN
        )

        ocr_items = []
        for result in res_list:
            relative_area = self._wq_box_xywh(result.box)
            absolute_area = [
                relative_area[0] + roi_x,
                relative_area[1] + roi_y,
                relative_area[2],
                relative_area[3],
            ]
            ocr_items.append((
                self._normalize_wq_list_text(result.ocr_text),
                absolute_area,
            ))

        confirmed_candidates = []
        edge_candidates = []
        for text, progress_area in ocr_items:
            match = progress_pattern.search(text)
            if not match:
                continue
            current, total = int(match.group(1)), int(match.group(3))
            if total <= 0 or total > 14 or current > total:
                logger.warning(
                    'Ignore abnormal wanted quest progress: '
                    f'{text}'
                )
                continue
            if current == total:
                continue

            progress_top = progress_area[1]
            progress_bottom = progress_top + progress_area[3]
            label_confirmed = self._has_wq_label_hint(text)
            if not label_confirmed:
                for nearby_text, nearby_area in ocr_items:
                    if not self._has_wq_label_hint(nearby_text):
                        continue
                    nearby_top = nearby_area[1]
                    nearby_bottom = nearby_top + nearby_area[3]
                    if (
                        nearby_bottom >= progress_top - 12
                        and nearby_top <= progress_bottom + 82
                    ):
                        label_confirmed = True
                        break

            candidate = (
                progress_top,
                current,
                total,
                progress_area,
                text,
            )
            if progress_top >= safe_progress_bottom:
                # 最底一项即使识别到局部文字，也先翻页将其移入完整区域。
                edge_candidates.append(candidate)
            elif label_confirmed:
                confirmed_candidates.append(candidate)

        candidates = confirmed_candidates
        if not candidates and allow_edge_fallback:
            candidates = edge_candidates
            if candidates:
                logger.warning(
                    'Use cropped bottom wanted quest as fallback after '
                    'list scrolling'
                )
        if candidates:
            _, current, total, click_area, raw_text = min(
                candidates,
                key=lambda item: item[0],
            )
            logger.info(
                f'Find wanted quest progress {raw_text}: '
                f'{current}/{total}, click={click_area}'
            )
            return current, 1, total, click_area
        return -1, -1, -1, [0, 0, 0, 0]

    def is_wq_remained(self):
        """检测是否仍有悬赏；兼容列表底部仅剩裁切卡位。"""
        progress_pattern = re.compile(
            r'(?<!\d)(\d{1,2})([7/])(\d{1,2})(?!\d)'
        )
        completed = 0
        for result in self.O_WQ_TEXT_ALL.detect_and_ocr(
            self.device.image,
            logDisplay=False,
        ):
            text = self._normalize_wq_list_text(result.ocr_text)
            match = progress_pattern.search(text)
            if not match:
                continue
            current, total = int(match.group(1)), int(match.group(3))
            if 0 <= current < total <= 14:
                logger.info(
                    'Wanted quest remains by cropped progress fallback: '
                    f'{current}/{total}'
                )
                return True
            if 0 < total <= 14 and current == total:
                completed += 1
        if completed:
            logger.info('All visible tracked wanted quest counters are complete; verify the full quest panel')
            return False
        return bool(self.appear(self.O_WQ_LIST_CHECK))


if __name__ == '__main__':
    # 原始版本
    strict_pattern = re.compile(r"^(.+?)\(?[数教]量:\s*(\d+)\)?$")
    # OCR容错版本
    ocr_pattern = re.compile(r"(.+?)\s*\(?[数教]量[:：]\s*(\d+)")
    test_cases = [
        "第十章(数量:2)",
        '第十章(数量:2)"',
        '第十章(数量:2""',
        "第十章 数量:2",
        "第十章 数量：2",
        "第十章(数量：2)",
        "第十章 (数量: 2)",
        "第十章(教量:2)",
        "第十章(数量:2abc)",
        "第十章(数量:20)",
        "第十章数量:2",
        "第十章",
        "",
    ]
    test_cases.extend([
        "第十章（数量：2）",  # 中文括号
        "第十章(数最:2)",  # OCR错字
        "第十章(数量:Z)",  # OCR数字识别失败
        "第十章(数量:2 )",
        "第十章(数量: 2)",
        "第十章(数量:02)",
        "第十章(数量:2.”)",
        "第十章(数量:2。)",
    ])

    def test(pattern, text):
        match = pattern.search(text)
        if not match:
            return "❌ 不匹配"
        return (
            f"✅ 匹配 "
            f"dest=[{match.group(1).strip()}], "
            f"number=[{int(match.group(2))}]"
        )

    for text in test_cases:
        print("=" * 60)
        print(f"输入: {repr(text)}")
        print("\n【严格模式】")
        print(test(strict_pattern, text))
        print("\n【OCR容错模式】")
        print(test(ocr_pattern, text))
        print()

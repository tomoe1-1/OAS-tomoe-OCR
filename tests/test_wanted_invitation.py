"""Two unanswered sent invitations complete only their matching quest."""
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import TaskRecoveryFailed
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationType
from tasks.WantedQuests.invitation import (InvitationLedger, is_provisional,
                                            precise_key, provisional_key,
                                            quest_key, refresh_cycle)
from tasks.WantedQuests.script_task import ScriptTask


KEY = '自己击败帚神|好友击败唐纸伞妖'
# RuleImage.name is derived from the asset file name, so it is NOT 'I_WQ_INVITE_1'.
SLOT = 'INVITE_WQ_INVITE_1'
PROVISIONAL = provisional_key(SLOT, CooperationType.Jade)


class WantedInvitationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profile.json'
        self.now = datetime(2026, 9, 30, 18, 30)
        self.ledger = InvitationLedger(self.path, self.now)

    def task(self, accepted=(False, False), sent=True):
        task = ScriptTask.__new__(ScriptTask)
        task.invitation_ledger = self.ledger
        task.screenshot = Mock()
        task._invitation_card_identity = Mock(return_value=KEY)
        task.cooperation_invite = Mock(return_value=sent)
        task.invite_random = Mock(return_value=sent)
        # _wait_invitation_accepted returns (accepted, key): the key can be
        # re-filed mid-wait once the card expands and shows its owner labels.
        task._wait_invitation_accepted = Mock(
            side_effect=[(flag, KEY) for flag in accepted])
        task._untrace_invitation_quest = Mock()
        return task

    def test_two_unanswered_sends_complete_and_untrace_one_quest(self):
        task = self.task()
        self.assertFalse(task._invite_cooperation_twice('button', 'friend'))
        self.assertEqual(task.cooperation_invite.call_count, 2)
        self.assertEqual(task._wait_invitation_accepted.call_count, 2)
        self.assertTrue(self.ledger.complete(KEY))
        task._untrace_invitation_quest.assert_called_once_with('button')

    def test_random_invites_use_the_same_two_attempt_rule(self):
        task = self.task()
        task._invite_cooperation_twice('button', '')
        self.assertEqual(task.invite_random.call_count, 2)
        task.cooperation_invite.assert_not_called()
        self.assertTrue(self.ledger.complete(KEY))

    def test_first_acceptance_stops_further_invites_and_preserves_quest(self):
        task = self.task(accepted=(True,))
        self.assertTrue(task._invite_cooperation_twice('button', 'friend'))
        task.cooperation_invite.assert_called_once()
        task._untrace_invitation_quest.assert_not_called()
        self.assertFalse(self.ledger.complete(KEY))

    def test_second_acceptance_preserves_quest(self):
        task = self.task(accepted=(False, True))
        self.assertTrue(task._invite_cooperation_twice('button', 'friend'))
        self.assertEqual(task.cooperation_invite.call_count, 2)
        task._untrace_invitation_quest.assert_not_called()
        self.assertFalse(self.ledger.complete(KEY))

    def test_send_failures_are_not_unanswered_invitations(self):
        task = self.task(accepted=(), sent=False)
        task._invite_cooperation_twice('button', 'friend')
        task._wait_invitation_accepted.assert_not_called()
        task._untrace_invitation_quest.assert_not_called()
        self.assertFalse(self.ledger.complete(KEY))

    def test_completion_survives_restart_and_does_not_send_a_third_time(self):
        task = self.task()
        task._invite_cooperation_twice('button', '')
        task = self.task(accepted=())
        task.invitation_ledger = InvitationLedger(self.path, self.now)
        task._invite_cooperation_twice('button', '')
        task.invite_random.assert_not_called()
        task._wait_invitation_accepted.assert_not_called()
        task._untrace_invitation_quest.assert_called_once()

    def test_pending_send_resumes_wait_before_sending_again(self):
        self.ledger.sent(KEY)
        task = self.task()
        task._invite_cooperation_twice('button', 'friend')
        task.cooperation_invite.assert_called_once()
        self.assertEqual(task._wait_invitation_accepted.call_count, 2)
        self.assertTrue(self.ledger.complete(KEY))

    def test_unknown_acceptance_state_retains_pending_send(self):
        task = self.task()
        task._wait_invitation_accepted.side_effect = TaskRecoveryFailed('unknown screen')
        with self.assertRaises(TaskRecoveryFailed):
            task._invite_cooperation_twice('button', '')
        self.assertEqual(self.ledger.state(KEY)['sent'], 1)
        self.assertFalse(self.ledger.complete(KEY))
        task._untrace_invitation_quest.assert_not_called()

    def test_new_refresh_resets_old_invitation_counts(self):
        for _ in range(2):
            self.ledger.sent(KEY)
            self.ledger.checked(KEY, False)
        next_cycle = InvitationLedger(self.path, datetime(2026, 10, 1, 5))
        self.assertFalse(next_cycle.complete(KEY))
        self.assertEqual(next_cycle.state(KEY)['sent'], 0)

    def test_refresh_boundaries_follow_five_and_eighteen(self):
        self.assertEqual(refresh_cycle(datetime(2026, 10, 1, 4, 59)), '2026-09-30:18')
        self.assertEqual(refresh_cycle(datetime(2026, 10, 1, 5)), '2026-10-01:05')
        self.assertEqual(refresh_cycle(datetime(2026, 10, 1, 18)), '2026-10-01:18')

    def test_key_ignores_progress_but_requires_both_targets(self):
        self.assertEqual(quest_key(['自己击败帚神 0/8', '好友击败唐纸伞妖 0/6']), KEY)
        self.assertEqual(quest_key(['自己击败帚神 8/8', '好友击败唐纸伞妖 2/6']), KEY)
        self.assertIsNone(quest_key(['自己击败帚神 0/8']))

    def panel(self):
        def result(text, x, y):
            return SimpleNamespace(ocr_text=text, box=[[x,y],[x+100,y],[x+100,y+20],[x,y+20]])
        return [result('自己击败帚神', 70, 300), result('0/8', 90, 325),
                result('好友击败唐纸伞妖', 70, 350), result('0/6', 90, 375),
                result('击败2个酒吞童子', 400, 300), result('0/2', 420, 325)]

    def test_completed_invitation_does_not_hide_regular_unfinished_card(self):
        for _ in range(2):
            self.ledger.sent(KEY)
            self.ledger.checked(KEY, False)
        task = self.task()
        results = self.panel()
        regions = task._completed_invitation_regions(results)
        unfinished, friends, _ = task._panel_unfinished_quests(results, regions)
        self.assertEqual(unfinished, ['0/2'])
        self.assertEqual(friends, [])

    def test_one_unanswered_invitation_does_not_complete_own_counter(self):
        self.ledger.sent(KEY)
        self.ledger.checked(KEY, False)
        task = self.task()
        results = self.panel()
        regions = task._completed_invitation_regions(results)
        self.assertEqual(regions, [])
        unfinished, _, _ = task._panel_unfinished_quests(results, regions)
        self.assertEqual(unfinished, ['0/8', '0/2'])

    def test_acceptance_requires_partner_name_and_two_fresh_frames(self):
        task = self.task()
        task._wait_invitation_accepted = ScriptTask._wait_invitation_accepted.__get__(task, ScriptTask)
        task.device = SimpleNamespace(image=None)
        task.appear = Mock(return_value=False)
        btn = SimpleNamespace(roi_front=[137, 361, 39, 47], name=SLOT)
        timer = SimpleNamespace(start=lambda: timer, reached=Mock(side_effect=[False, False]))
        partner = SimpleNamespace(detect_and_ocr=Mock(return_value=[SimpleNamespace(ocr_text='心有猛虎')]))
        with patch('tasks.WantedQuests.script_task.Timer', return_value=timer), \
                patch('tasks.WantedQuests.script_task.RuleOcr', return_value=partner):
            accepted, key = task._wait_invitation_accepted(btn, KEY, CooperationType.Jade)
        self.assertTrue(accepted)
        self.assertEqual(key, KEY)
        self.assertEqual(task.screenshot.call_count, 2)

    def test_unanswered_requires_visible_invite_control_at_timeout(self):
        task = self.task()
        task._wait_invitation_accepted = ScriptTask._wait_invitation_accepted.__get__(task, ScriptTask)
        task.device = SimpleNamespace(image=None)
        task.appear = Mock(return_value=True)
        btn = SimpleNamespace(roi_front=[137, 361, 39, 47], name=SLOT)
        timer = SimpleNamespace(start=lambda: timer, reached=Mock(side_effect=[False, False, True]))
        with patch('tasks.WantedQuests.script_task.Timer', return_value=timer), \
                patch('tasks.WantedQuests.script_task.RuleOcr'):
            accepted, key = task._wait_invitation_accepted(btn, KEY, CooperationType.Jade)
        self.assertFalse(accepted)
        self.assertEqual(key, KEY)

    def _wait_task(self, reached):
        task = self.task()
        task._wait_invitation_accepted = ScriptTask._wait_invitation_accepted.__get__(task, ScriptTask)
        task.device = SimpleNamespace(image=None)
        # The invite button stays visible, so the loop reaches its timeout and
        # returns 'unanswered'. These tests are about the identity branch, which
        # runs before the acceptance verdict.
        task.appear = Mock(return_value=True)
        timer = SimpleNamespace(start=lambda: timer, reached=Mock(side_effect=reached))
        btn = SimpleNamespace(roi_front=[137, 361, 39, 47], name=SLOT)
        return task, btn, timer

    def test_collapsed_card_mid_wait_does_not_trip_the_identity_guard(self):
        """等待中卡片从展开收回（标签消失）不得被判成「换了任务」。

        收起/展开是邀请过程中的正常瞬态；早期版本拿完整 key 比重，
        一旦卡收回（读不到标签 → 回落临时身份）就误报位移。
        """
        task, btn, timer = self._wait_task([False, False, True])
        task._invitation_precise_key = Mock(side_effect=[KEY, None])
        with patch('tasks.WantedQuests.script_task.Timer', return_value=timer), \
                patch('tasks.WantedQuests.script_task.RuleOcr'), \
                patch.object(task, '_invitation_card_identity', return_value=PROVISIONAL):
            self.assertFalse(task._wait_invitation_accepted(btn, PROVISIONAL, CooperationType.Jade)[0])

    def test_card_expanding_mid_wait_promotes_the_slot_key(self):
        """邀请成功后标签出现：临时 key 必须被改写成精确 key。"""
        task, btn, timer = self._wait_task([False, False, True])
        task._invitation_precise_key = Mock(side_effect=[None, KEY])
        with patch('tasks.WantedQuests.script_task.Timer', return_value=timer), \
                patch('tasks.WantedQuests.script_task.RuleOcr'):
            accepted, key = task._wait_invitation_accepted(btn, PROVISIONAL, CooperationType.Jade)
        self.assertFalse(accepted)
        self.assertEqual(key, KEY, '标签出现后台账条目应改用精确身份')
        self.assertNotIn(PROVISIONAL, task.invitation_ledger.quests)

    def test_identity_changing_to_a_different_quest_is_refused(self):
        """已知是这条悬赏，标签却换成了别的目标：必须拒绝，不能等下去。"""
        other = '自己击败鬼王酒|好友击败召唤兽'
        task, btn, timer = self._wait_task([False])
        task._invitation_precise_key = Mock(return_value=other)
        with patch('tasks.WantedQuests.script_task.Timer', return_value=timer), \
                patch('tasks.WantedQuests.script_task.RuleOcr'):
            with self.assertRaises(TaskRecoveryFailed):
                task._wait_invitation_accepted(btn, KEY, CooperationType.Jade)


class CollapsedCardIdentityTest(unittest.TestCase):
    """守卫：收起态协作卡（邀请前）不得再因读不到击败标签而抛错。

    背景（2026-10-07）：悬赏协作邀请连续 6 次抛
    `TaskRecoveryFailed: Invitation quest identity could not be verified`
    （10-05 三次、10-07 三次）。根因是 `_invitation_card_key` 用 `quest_key`
    要求卡面 OCR 读出「自己击败 / 好友击败」，而真机收起态协作卡上只有
    「与好友共享任务」+ 目标 + x/y 进度 —— 击败标签是**邀请之后**才出现的。
    代码在邀请前就要求读出它，等于要求一个当时不存在的字段。

    该功能自 2026-09-30（8a764d5a）引入后从未成功工作过，不是回归。

    ⚠️ 本类必须驱动**真实的** `_invitation_card_identity`（含真实 RuleOcr 与
    真实模板匹配），只 mock 设备传输层。把 OCR 结果 mock 掉等于把要验的东西
    一起 mock 掉了 —— 之前诊断脚本正是这么误判过一次。

    反向验证（7 个变异，全部报红；还原后全绿）：

      M1 邀请前重新强制要求击败标签（原 bug）   -> 1 error
      M2 renames() 退化成空操作（拆掉台账桥）   -> 3 failures
      M3 面板侧改用 own 去查（对不上邀请侧）      -> 2 failures
      M4 身份冲突时不抛错                        -> 1 error
      M5 provisional_key 忽略协作类型            -> 1 failure
      M6 等待期不 promote（临时 key 永不升级）    -> 1 failure
      M7 is_provisional() 恒为 False             -> 1 failure + 2 errors
    """

    FIXTURES = ['wanted_coop_jade_collapsed.png', 'wanted_coop_jade_collapsed2.png']

    def _each_fixture(self):
        from tests.test_wanted_sealed_panel import _install_local_matching, _load_native
        _install_local_matching()
        for name in self.FIXTURES:
            image = _load_native(name)
            self.assertIsNotNone(image, f'缺少真机夹具或无法解码: {name}')
            self.assertEqual(image.shape[:2], (720, 1280),
                             f'{name} 应为原生 720x1280，实际 {image.shape}')
            yield name, image

    def _task(self, image):
        from tasks.WantedQuests.assets import WantedQuestsAssets
        task = ScriptTask.__new__(ScriptTask)
        for index in (1, 2, 3):
            setattr(task, 'I_WQ_INVITE_%d' % index,
                    getattr(WantedQuestsAssets, 'I_WQ_INVITE_%d' % index))
        task.device = type('Device', (), {
            'image': image, 'image_frame_id': 0,
            'get_image_batch_cache': lambda s, t, frame_id=None: None,
            'update_image_batch_cache': lambda s, *a, **k: None,
            'click_record_clear': lambda s, *a, **k: None,
        })()
        return task

    def test_collapsed_card_does_not_raise_and_yields_slot_identity(self):
        for name, image in self._each_fixture():
            task = self._task(image)
            btn = WantedQuestsAssets.I_WQ_INVITE_1
            key = task._invitation_card_identity(btn, CooperationType.Jade)
            self.assertEqual(key, PROVISIONAL, f'{name} 应回落到模板卡位身份')

    def test_collapsed_card_really_has_no_owner_labels(self):
        """夹具前提：若哪天收起态真带击败标签，本文件就失去意义，须换夹具。"""
        for name, image in self._each_fixture():
            task = self._task(image)
            btn = WantedQuestsAssets.I_WQ_INVITE_1
            self.assertIsNone(task._invitation_precise_key(btn),
                              f'{name} 收起态竟读出了击败标签，夹具前提需重估')

    def test_invite_slot_template_is_found_on_the_real_frame(self):
        for name, image in self._each_fixture():
            task = self._task(image)
            btn = WantedQuestsAssets.I_WQ_INVITE_1
            self.assertTrue(task.appear(btn), f'{name} 应命中 I_WQ_INVITE_1')
            self.assertEqual(btn.name, SLOT)

    def test_slot_identity_is_distinct_per_slot_and_type(self):
        self.assertNotEqual(provisional_key(SLOT, CooperationType.Jade),
                            provisional_key('INVITE_WQ_INVITE_2', CooperationType.Jade))
        self.assertNotEqual(provisional_key(SLOT, CooperationType.Jade),
                            provisional_key(SLOT, CooperationType.Gold))

    def test_provisional_and_precise_key_shapes_stay_disjoint(self):
        """两种 key 形态必须不会互相冒充，否则等待期的分支判断就不可靠。"""
        self.assertTrue(is_provisional(PROVISIONAL))
        self.assertFalse(is_provisional(KEY))
        self.assertFalse(is_provisional(''))
        self.assertEqual(precise_key('自己击败帚神 0/8', '好友击败唐纸伞妖 3/6'), KEY)

    def test_identity_without_slot_nor_type_refuses_rather_than_invents(self):
        """既无模板卡位又无协作类型时必须拒绝，不能凭空造一个可能撞车的身份。"""
        task = self._task(_placeholder_image())
        btn = SimpleNamespace(roi_front=[137, 361, 39, 47], name='')
        with self.assertRaises(TaskRecoveryFailed):
            task._invitation_card_identity(btn, None)


def _placeholder_image():
    import numpy
    return numpy.zeros((720, 1280, 3), dtype=numpy.uint8)


class ProvisionalLedgerBridgeTest(unittest.TestCase):
    """守卫：临时卡位身份必须能在标签出现后被精确身份接管。

    邀请侧只能写出 'S|<slot>|<type>'（邀请前无标签），面板侧只能写出
    '<自己击败>|<好友击败>'（面板上只有标签）。两端若对不上名字，一次
    已完成的协作会被忘光，下次重试又对同一条悬赏重复邀请 —— 正是台账
    要防的事。`renames()` 就是这座桥。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profile.json'
        self.now = datetime(2026, 9, 30, 18, 30)
        self.ledger = InvitationLedger(self.path, self.now)

    def test_two_unanswered_sends_under_slot_key_are_visible_to_the_panel(self):
        for _ in range(2):
            self.ledger.sent(PROVISIONAL)
            self.ledger.checked(PROVISIONAL, False)
        self.assertTrue(self.ledger.complete(PROVISIONAL))
        # 面板侧只带标签来查，必须先经桥接才查得到
        self.assertFalse(self.ledger.complete(KEY))
        self.assertEqual(self.ledger.renames(PROVISIONAL, KEY), KEY)
        self.assertTrue(self.ledger.complete(KEY))
        self.assertNotIn(PROVISIONAL, self.ledger.quests, '旧条目应被改名而非复制')

    def test_rename_does_not_clobber_an_existing_precise_entry(self):
        for _ in range(2):
            self.ledger.sent(KEY)
            self.ledger.checked(KEY, False)
        self.ledger.sent(PROVISIONAL)
        self.assertEqual(self.ledger.renames(PROVISIONAL, KEY), KEY)
        self.assertEqual(self.ledger.state(KEY)['sent'], 2, '已有精确条目不得被覆盖')

    def test_rename_of_unknown_or_identical_key_is_a_no_op(self):
        self.assertEqual(self.ledger.renames(KEY, '其他|目标'), '其他|目标')
        self.assertEqual(self.ledger.renames('缺失|条目', '缺失|条目'), '缺失|条目')

    def test_rename_survives_reload_from_disk(self):
        self.ledger.sent(PROVISIONAL)
        self.ledger.renames(PROVISIONAL, KEY)
        self.assertEqual(sorted(InvitationLedger(self.path, self.now).quests), [KEY])

    def test_panel_side_region_lookup_uses_the_renamed_entry(self):
        for _ in range(2):
            self.ledger.sent(PROVISIONAL)
            self.ledger.checked(PROVISIONAL, False)
        task = ScriptTask.__new__(ScriptTask)
        task.invitation_ledger = self.ledger

        def result(text, x, y):
            return SimpleNamespace(ocr_text=text, box=[[x, y], [x + 100, y], [x + 100, y + 20], [x, y + 20]])
        results = [result('自己击败帚神', 70, 300), result('0/8', 90, 325),
                   result('好友击败唐纸伞妖', 70, 350), result('0/6', 90, 375)]

        def normalize(text):
            return text
        task._normalize_wq_list_text = normalize
        task._wq_box_xywh = lambda box: (box[0][0], box[0][1],
                                         box[1][0] - box[0][0], box[1][1] - box[0][1])
        self.assertEqual(task._completed_invitation_regions(results), [],
                         '未桥接时面板侧不该认这张卡')
        self.ledger.renames(PROVISIONAL, KEY)
        regions = task._completed_invitation_regions(results)
        self.assertEqual(len(regions), 1, '桥接后面板侧应认出这张卡已完成')


if __name__ == '__main__':
    unittest.main()

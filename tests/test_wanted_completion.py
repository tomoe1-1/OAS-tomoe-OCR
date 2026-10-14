import ast,re,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from module.exception import TaskRecoveryFailed
ROOT=Path(__file__).resolve().parents[1]

def subject(texts, stamp=''):
 tree=ast.parse((ROOT/'tasks/WantedQuests/script_task.py').read_text(encoding='utf-8'))
 cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
 methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in {'run','verify_quest_panel_complete','_open_wanted_completion_panel','_panel_looks_sealed','_normalize_wq_list_text','_wq_box_xywh','_panel_unfinished_quests','_completed_invitation_regions','_completed_cooperation_regions','is_wq_remained'}]
 mini=ast.ClassDef(name='Subject',bases=[],keywords=[],body=methods,decorator_list=[])
 rules=[]
 def ocr(**kwargs):
  rules.append(kwargs)
  return SimpleNamespace(ocr=lambda *a,**kw:stamp,detect_and_ocr=lambda *a,**kw:[SimpleNamespace(ocr_text=t[0],box=t[1]) if isinstance(t,tuple) else SimpleNamespace(ocr_text=t) for t in texts])
 ns=dict(re=re,logger=Mock(),RuleOcr=ocr,TaskRecoveryFailed=TaskRecoveryFailed,TaskEnd=RuntimeError,page_main='main',List=list)
 exec(compile(ast.fix_missing_locations(ast.Module(body=[mini],type_ignores=[])),'wanted','exec'),ns)
 obj=ns['Subject']()
 for name in ['I_WQ_SEAL','I_UI_BACK_RED','I_TRACE_ENABLE','I_TRACE_DISABLE','I_WQ_DONE']:
  setattr(obj,name,name)
 obj.device=SimpleNamespace(image=None);obj.screenshot=Mock();obj.goto_page=Mock();obj.ui_click=Mock();obj.ui_click_until_disappear=Mock()
 obj.appear=lambda rule:rule=='I_TRACE_DISABLE';obj.wq_skipped_names=set()
 return obj,rules

class WantedCompletionTests(unittest.TestCase):
 def claim_card(self, own='12/12', friend='8/8', claim='领取', claim_x=100):
  box=lambda x,y:[[x,y],[x+90,y],[x+90,y+20],[x,y+20]]
  texts=[(claim,box(claim_x,230)),('自己击败赤舌',box(100,280)),
         (own,box(100,305)),('好友击败管狐',box(100,330)),(friend,box(100,355))]
  obj,_=subject(texts)
  obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  return obj,texts

 def test_claimable_cooperation_without_trace_or_seal_completes(self):
  obj,_=self.claim_card()
  obj.verify_quest_panel_complete()
  self.assertEqual(obj.screenshot.call_count,3)

 def test_claim_alone_completes_even_with_stale_incomplete_counter(self):
  obj,_=self.claim_card(own='11/12')
  obj.verify_quest_panel_complete()

 def test_reached_quota_alone_completes(self):
  obj,_=self.claim_card(claim='奖励')
  obj.verify_quest_panel_complete()

 def test_claim_on_another_card_does_not_identify_completed_cooperation(self):
  obj,_=self.claim_card(own='11/12',claim_x=450)
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_claimable_cooperation_does_not_hide_other_unfinished_quest(self):
  _,texts=self.claim_card()
  texts.append(('0/2',[[500,305],[550,305],[550,325],[500,325]]))
  obj,_=subject(texts);obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_claimable_cooperation_preserves_own_only_completion_rule(self):
  obj,_=self.claim_card(friend='0/8')
  obj.verify_quest_panel_complete()

 def test_claim_with_unreadable_own_counter_completes(self):
  obj,_=self.claim_card(own='?/?')
  obj.verify_quest_panel_complete()

 def test_friend_quota_alone_does_not_complete_own_unfinished_card(self):
  obj,_=self.claim_card(own='11/12',claim='奖励')
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_reached_quota_with_occluded_own_label_and_no_claim_completes(self):
  _,texts=self.claim_card(claim='奖励')
  texts[1]=('自',texts[1][1])
  obj,_=subject(list(reversed(texts)));obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  obj.verify_quest_panel_complete()

 def test_unreadable_seal_stamp_still_completes(self):
  """印章读不出也必须判为完成。

  2026-10-07 的误报根因：旧判据要求 OCR 读出竖排「已封印」，而真机把
  同一张卡读成「记封」/「记封印」/「已封」（同一张卡时对时错），于是每轮
  identified=False、4 轮后抛 TaskRecoveryFailed。现判据只看
  「面板身份 + 无追踪控件 + 无未完成进度」，与印章无关。

  「感谢好友」浮层只是协作卡领奖提示，不构成未完成证据，故仍应通过。
  """
  obj,rules=subject(['悬赏封印','下次刷新：07:56:38','感谢好友'],stamp='')
  obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  obj.verify_quest_panel_complete()
  self.assertEqual(obj.screenshot.call_count,3)
  # 不应再有任何窄条印章 OCR（x=250/550/850 死位置已被删除）
  self.assertFalse([r for r in rules if r.get('name')=='wanted_sealed_stamp'])

 def test_sealed_cards_do_not_hide_another_unfinished_card(self):
  obj,_=subject(['悬赏封印','下次刷新：07:56:38','感谢好友','0/2'],stamp='已封印')
  obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_thanks_friend_popup_with_another_unfinished_card_still_fails(self):
  """「感谢好友」浮层不得掩盖另一张卡上的未完成进度。

  意图不变（协作弹窗不该成为「全部完成」的挡箭牌），但门槛换了：
  完成判据不再要求印章 OCR，改由「无任何 x/y 未完成进度」决定。
  所以这里必须放一张**确实有进度**的卡进去 —— 否则它与
  test_unreadable_seal_stamp_still_completes 是同一条断言。
  真正的风险面在协作卡自身：若它的进度被 _completed_cooperation_regions
  误排除，unfinished 就会漏掉它，那才是事故。
  """
  obj,_=subject(['悬赏封印','下次刷新：07:56:38','感谢好友','3/10'])
  obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_untracked_unfinished_quest_is_not_success(self):
  obj,rules=subject(['击败2个酒吞童子','0/2'])
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()
  self.assertEqual(rules[0]['roi'],(100,60,1150,580))
  obj.ui_click.assert_called_once();obj.ui_click_until_disappear.assert_called_once()

 def test_completed_panel_checked_in_two_frames(self):
  obj,rules=subject(['2/2'])
  obj.verify_quest_panel_complete()
  self.assertEqual(obj.screenshot.call_count,3)

 def test_invitation_uses_own_counter_only(self):
  obj,_=subject(['自己击败武士之灵','12/12','好友击败饿鬼','0/8'])
  obj.verify_quest_panel_complete()
  self.assertEqual(obj.screenshot.call_count,3)

 def test_invitation_own_counter_unfinished(self):
  obj,_=subject(['自己击败武士之灵','11/12','好友击败饿鬼','0/8'])
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_friend_label_on_other_card_does_not_mask_regular_quest(self):
  box=lambda x,y:[[x,y],[x+50,y],[x+50,y+20],[x,y+20]]
  obj,_=subject([('好友击败饿鬼',box(100,300)),('0/8',box(500,322))])
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_completed_tracked_card_goes_directly_to_panel_verification(self):
  obj,_=subject(['12/12','悬赏封印'])
  obj.O_WQ_TEXT_ALL=SimpleNamespace(detect_and_ocr=lambda *a,**kw:[SimpleNamespace(ocr_text='12/12')])
  obj.O_WQ_LIST_CHECK='list'
  obj.appear=lambda rule:True
  self.assertFalse(obj.is_wq_remained())

 def test_normalized_progress(self):
  obj,rules=subject(['0 ／ 2'])
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_unidentified_panel_cannot_report_success(self):
  obj,rules=subject([]);obj.appear=lambda rule:False
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_all_sealed_panel_without_trace_controls(self):
  obj,_=subject(['悬赏封印','已封印','下次刷新：06:01:43'])
  obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  obj.verify_quest_panel_complete()
  self.assertEqual(obj.screenshot.call_count,3)

 def test_sealed_card_does_not_hide_an_unfinished_quest(self):
  obj,_=subject(['悬赏封印','已封印','下次刷新：06:01:43','0/2'])
  obj.appear=lambda rule:rule=='I_UI_BACK_RED'
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_panel_identity_requires_title_and_refresh(self):
  """面板身份必须两者兼具，缺一不可（防在别的界面误判为完成）。"""
  for texts in (['悬赏封印'],['下次刷新：06:01:43'],['活动','下次刷新：06:01:43']):
   obj,_=subject(texts)
   obj.appear=lambda rule:rule=='I_UI_BACK_RED'
   with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_missing_close_button_is_not_completion(self):
  """关闭按钮不在 ⇒ 这不是悬赏面板，即使标题与刷新提示都读到了。"""
  obj,_=subject(['悬赏封印','下次刷新：06:01:43'])
  obj.appear=lambda rule:False
  with self.assertRaises(TaskRecoveryFailed):obj.verify_quest_panel_complete()

 def test_empty_filter_and_strategy_history_reset_each_run(self):
  obj,rules=subject([])
  obj.config=SimpleNamespace(model=SimpleNamespace(wanted_quests=SimpleNamespace(wanted_quests_config=SimpleNamespace(unwanted_boss_names=''))))
  obj.wq_executed_set={'old'};obj.unwanted_boss_name_list=['old']
  obj.get_config=lambda:SimpleNamespace(cooperation_only=False)
  obj.switch_mission_souls=Mock();obj.pre_work=lambda:False;obj.next_run=Mock();obj.verify_quest_panel_complete=Mock()
  with self.assertRaises(RuntimeError):obj.run()
  self.assertEqual(obj.wq_executed_set,set());self.assertEqual(obj.unwanted_boss_name_list,[])
  obj.verify_quest_panel_complete.assert_called_once()

if __name__=='__main__':unittest.main()



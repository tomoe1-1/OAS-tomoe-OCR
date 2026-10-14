from pathlib import Path
from types import SimpleNamespace
import copy, unittest
import cv2,numpy as np
from tasks.GameUi.default_pages import page_secret_chapter,page_secret_zones,page_reward
from tasks.WantedQuests.assets import WantedQuestsAssets as W
from tasks.GlobalGame.assets import GlobalGameAssets as B
class SecretChapterTests(unittest.TestCase):
 def test_error_frame_identifies_chapter_and_safe_return_edge(self):
  p=Path(__file__).parent/'fixtures'/'secret_chapter.png'
  image=cv2.cvtColor(cv2.imdecode(np.fromfile(str(p),np.uint8),1),cv2.COLOR_BGR2RGB)
  task=SimpleNamespace(appear=lambda rule:copy.deepcopy(rule).template_match(image))
  self.assertTrue(page_secret_chapter.recognizer.evaluate(task))
  edge=page_secret_chapter.transitions[0]
  self.assertIs(edge.destination,page_secret_zones)
  self.assertIs(edge.action,B.I_UI_BACK_RED)
  self.assertGreater(page_secret_chapter.priority,page_reward.priority)
 def test_close_icon_without_challenge_does_not_identify_chapter(self):
  task=SimpleNamespace(appear=lambda rule:rule is B.I_UI_BACK_RED)
  self.assertFalse(page_secret_chapter.recognizer.evaluate(task))
 def test_challenge_without_close_does_not_identify_chapter(self):
  task=SimpleNamespace(appear=lambda rule:rule is W.I_WQSE_FIRE)
  self.assertFalse(page_secret_chapter.recognizer.evaluate(task))
if __name__=='__main__': unittest.main()

"""RealmRaid: the reskinned "是否再次挑战?" confirm dialog must be answered.

Reproduction of the 2026-10-10 failure (17:30 and again 17:39):
  Click RES_FIRE_AGAIN -> a modal "是否再次挑战?" opens -> the script re-clicks
  RES_FIRE_AGAIN, the modal swallows the input, `changed=False`, and the
  repeat-click guard raises ClickResponseUnchanged.

These tests drive the real `fire_again()` entry point and the real templates
against real device screenshots; only the transport (device actions) is faked.
Matching a template against the very image it was cropped from is the positive
direction only, so the negative direction carries its own weight: the battle
exit dialog reuses the same gold button, and the old textless template must be
shown dead on the new button.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
from numpy import fromfile, uint8

from tasks.RealmRaid.script_task import ScriptTask


def load_fixture(name):
    """Read a real device screenshot exactly like module/atom/image.py does.

    `load_image` stores templates as RGB, so the screenshot must be converted
    the same way or the colour channels disagree and every score collapses.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    image = cv2.imdecode(fromfile(os.path.join(here, 'fixtures', name), dtype=uint8), -1)
    assert image is not None, f'failed to read fixture {name}'
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class FireAgainConfirmDialogTests(unittest.TestCase):
    def subject(self, image):
        task = ScriptTask.__new__(ScriptTask)
        task.device = SimpleNamespace(image=image, image_frame_id='frame')
        task.screenshot = Mock()
        task.click = Mock(return_value=True)
        task.appear_then_click = Mock(return_value=False)
        task._ui_verify = Mock(return_value=True)
        return task

    # ---- recognition ------------------------------------------------------

    def test_new_dialog_is_recognised_on_real_screenshot(self):
        task = self.subject(load_fixture('realm_raid_fire_again_confirm.png'))
        self.assertTrue(task.I_RAID_CONFIRM_Q.match(task.device.image, frame_id='f'))
        self.assertTrue(task.I_RAID_CONFIRM_ENSURE.match(task.device.image, frame_id='f'))
        self.assertTrue(task.fire_again_confirm_visible())

    def test_battle_exit_dialog_is_not_mistaken_for_the_fire_again_one(self):
        # Same gold button, different question. This is why both must be required.
        task = self.subject(load_fixture('battle_exit_confirm_dialog.png'))
        self.assertTrue(task.I_RAID_CONFIRM_ENSURE.match(task.device.image, frame_id='f'))
        self.assertFalse(task.I_RAID_CONFIRM_Q.match(task.device.image, frame_id='f'))
        self.assertFalse(task.fire_again_confirm_visible())

    def test_old_textless_button_template_is_dead_on_the_new_button(self):
        # Guards the root cause: the pre-reskin template scored 0.05.
        task = self.subject(load_fixture('realm_raid_fire_again_confirm.png'))
        self.assertFalse(task.I_FRESH_ENSURE.match(task.device.image, frame_id='f'))

    # ---- the actual fix ---------------------------------------------------

    def test_confirm_clicks_ensure_and_verifies_the_dialog_left(self):
        task = self.subject(load_fixture('realm_raid_fire_again_confirm.png'))
        seen = []
        task.fire_again_confirm_visible = Mock(side_effect=[True, True, False])
        task.click.side_effect = lambda *a, **k: seen.append(a[0].name) or True
        task._ui_verify.side_effect = lambda *a, **k: seen.append(('verify', k.get('timeout'))) or True

        self.assertTrue(task.confirm_fire_again())
        self.assertEqual(seen, [('verify', 5), 'raid_confirm_ensure', ('verify', 15)])

    def test_confirm_is_a_no_op_when_the_dialog_is_absent(self):
        task = self.subject(load_fixture('battle_exit_confirm_dialog.png'))
        self.assertFalse(task.confirm_fire_again())
        task.click.assert_not_called()

    def test_fire_again_answers_the_dialog_instead_of_reclicking_fire_again(self):
        # The regression: FIRE_AGAIN was clicked again while the modal was up,
        # the input was swallowed, and the repeat guard raised.
        # Frame sequence: modal up -> answered -> battle screen reached.
        task = self.subject(load_fixture('realm_raid_fire_again_confirm.png'))
        task.wait_until_appear = Mock()
        dialog = [True, False]
        task.fire_again_confirm_visible = Mock(side_effect=lambda: dialog[0])
        in_battle = [False]

        def fake_appear(target, *a, **k):
            if target is task.I_EXIT:
                return in_battle[0]
            return False

        task.appear = Mock(side_effect=fake_appear)

        def fake_confirm():
            dialog[0] = False   # answering the modal dismisses it
            in_battle[0] = True  # and the re-attack proceeds
            return True

        task.confirm_fire_again = Mock(side_effect=fake_confirm)

        self.assertTrue(task.fire_again())
        task.confirm_fire_again.assert_called_once()
        task.appear_then_click.assert_not_called()

    def test_fire_again_still_uses_the_legacy_path_without_the_dialog(self):
        task = self.subject(load_fixture('battle_exit_confirm_dialog.png'))
        task.wait_until_appear = Mock()
        task.fire_again_confirm_visible = Mock(return_value=False)
        task.appear = Mock(side_effect=[False, False, True])
        task.appear_then_click = Mock(return_value=False)

        self.assertTrue(task.fire_again())
        self.assertTrue(task.appear_then_click.called)


if __name__ == '__main__':
    unittest.main()
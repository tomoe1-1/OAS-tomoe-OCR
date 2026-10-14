"""The courtyard fortune card, the collection receipt and the six-star soul
selector are three different layers. Each must be identified by its own
artwork, each layer may take exactly one input, and the selector may only be
touched while a claim is actually being completed.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from module.exception import TaskRecoveryFailed
from tasks.DailyTrifles.courtyard_claim import (close_courtyard_claim_popups,
                                               courtyard_claim_popup,
                                               courtyard_claim_succeeded)


ASSETS = Path(__file__).resolve().parents[1] / 'tasks/DailyTrifles/dt'

# Declared search windows, copied from courtyard_claim_popup.
TITLE_XY = (420, 110)
TITLE_ROI = (415, 100, 440, 80)
STRIP_TOP = 420
STRIP_BOTTOM = 500
BUTTON_SLOTS = (180, 550, 920)
SLOT_WIDTH = 200


def template(name):
    bgr = cv2.imdecode(np.frombuffer((ASSETS / name).read_bytes(), np.uint8),
                       cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def blank():
    return np.zeros((720, 1280, 3), np.uint8)


def paste(frame, patch, x, y):
    frame[y:y + patch.shape[0], x:x + patch.shape[1]] = patch


def soul_selector_frame(button_dx=0, drop_slot=None):
    """A frame carrying the six-star selector title and its three buttons."""
    frame = blank()
    title = template('courtyard_soul_choice_title.png')
    button = template('courtyard_soul_choice_button.png')
    paste(frame, title, TITLE_XY[0], TITLE_XY[1])
    for index, x in enumerate(BUTTON_SLOTS):
        if index == drop_slot:
            continue
        paste(frame, button, x + button_dx, STRIP_TOP)
    return frame


def receipt_frame():
    frame = blank()
    paste(frame, template('courtyard_claim_success.png'), 280, 15)
    return frame


def fortune_frame():
    frame = blank()
    paste(frame, template('courtyard_sign_total.png'), 455, 480)
    paste(frame, template('courtyard_sign_explain.png'), 840, 430)
    paste(frame, template('courtyard_sign_close.png'), 830, 70)
    return frame


class Clock:
    """A monotonic stub that always advances, so bounded loops terminate."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 0.2
        return self.now


def task(images):
    """A task stub whose screenshots walk a fixed image timeline.

    The last frame repeats forever: a stable end state is what lets a bounded
    loop observe the same state twice and finish.
    """
    pending = [list(images)]

    class Device:
        _escape_generation = 0
        _accepted_invitation_generation = 0
        image_frame_id = 0
        image = None

        def __init__(self):
            self.performance = SimpleNamespace(timeout=lambda seconds: seconds)
            self.clicks = []
            self.confirmed = []

        def click(self, x, y, control_name=None):
            self.clicks.append((x, y, control_name))
            return True

        def confirm_control_progress(self, controls):
            self.confirmed.append(list(controls))

    obj = SimpleNamespace(device=Device())

    def screenshot():
        # The current frame is consumed first, then the timeline advances.
        obj.device.image = pending[0][0]
        if len(pending[0]) > 1:
            pending[0].pop(0)
        obj.device.image_frame_id += 1

    obj.screenshot = screenshot
    return obj


class SoulSelectorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.title = template('courtyard_soul_choice_title.png')
        self.button = template('courtyard_soul_choice_button.png')

    def test_declared_windows_admit_the_templates(self):
        x, y, w, h = TITLE_ROI
        self.assertLessEqual(x, TITLE_XY[0])
        self.assertLessEqual(y, TITLE_XY[1])
        self.assertGreaterEqual(x + w, TITLE_XY[0] + self.title.shape[1])
        self.assertGreaterEqual(y + h, TITLE_XY[1] + self.title.shape[0])
        self.assertGreaterEqual(STRIP_BOTTOM - STRIP_TOP, self.button.shape[0])
        for x in BUTTON_SLOTS:
            self.assertGreaterEqual(SLOT_WIDTH, self.button.shape[1])
            self.assertLessEqual(x + SLOT_WIDTH, 1280)

    def test_title_and_all_three_buttons_are_required(self):
        frame = soul_selector_frame()
        kind, _ = courtyard_claim_popup(frame)
        self.assertEqual(kind, 'soul_choice')
        for drop in range(3):
            with self.subTest(missing_button=drop):
                frame = soul_selector_frame(drop_slot=drop)
                self.assertNotEqual((courtyard_claim_popup(frame) or ('none',))[0],
                                    'soul_choice')

    def test_selector_is_not_claimed_from_the_title_alone(self):
        self.assertIsNone(courtyard_claim_popup(soul_selector_frame(drop_slot=0)))

    def test_button_positions_are_relocated_not_assumed(self):
        frame = soul_selector_frame(button_dx=30)
        kind, (x, y) = courtyard_claim_popup(frame)
        self.assertEqual(kind, 'soul_choice')
        self.assertEqual(x, BUTTON_SLOTS[0] + 30 + self.button.shape[1] // 2)
        self.assertEqual(y, STRIP_TOP + self.button.shape[0] // 2)

    def test_fortune_card_and_receipt_still_win_when_no_selector_is_present(self):
        self.assertEqual(courtyard_claim_popup(fortune_frame()), ('sign', (871, 108)))
        receipt = receipt_frame()
        self.assertEqual(courtyard_claim_popup(receipt), ('receipt', (640, 695)))
        self.assertTrue(courtyard_claim_succeeded(receipt))

    def test_other_resolutions_are_never_matched(self):
        self.assertIsNone(courtyard_claim_popup(np.zeros((600, 1280, 3), np.uint8)))
        self.assertFalse(courtyard_claim_succeeded(np.zeros((1080, 1920, 3), np.uint8)))


class CloseCourtyardClaimPopupsTests(unittest.TestCase):
    def test_selector_takes_one_input_then_the_receipt_is_confirmed(self):
        selector = soul_selector_frame()
        gone = blank()
        obj = task([selector, selector, receipt_frame(), receipt_frame(), gone, gone])
        saved = []
        with patch('tasks.DailyTrifles.courtyard_claim.monotonic', Clock()):
            self.assertTrue(close_courtyard_claim_popups(
                obj, on_success=lambda: saved.append(True)))
        self.assertEqual([c[2] for c in obj.device.clicks],
                         ['COURTYARD_SOUL_SELECT', 'COURTYARD_RECEIPT_CLOSE'])
        self.assertEqual(saved, [True])
        self.assertIn('COURTYARD_SOUL_SELECT', obj.device.confirmed[0])

    def test_selector_without_an_active_claim_is_refused(self):
        obj = task([soul_selector_frame()] * 8)
        with patch('tasks.DailyTrifles.courtyard_claim.monotonic', Clock()):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'requires an active claim'):
                close_courtyard_claim_popups(obj)
        self.assertEqual(obj.device.clicks, [])

    def test_each_layer_is_never_clicked_twice(self):
        obj = task([soul_selector_frame()] * 12)
        with patch('tasks.DailyTrifles.courtyard_claim.monotonic', Clock()):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'did not close'):
                close_courtyard_claim_popups(obj, on_success=Mock())
        self.assertEqual(len(obj.device.clicks), 1)
        self.assertEqual(obj.device.confirmed, [])

    def test_stale_screenshots_abort_instead_of_replaying_an_input(self):
        obj = task([soul_selector_frame()] * 8)
        obj.screenshot = Mock()  # image_frame_id never advances
        with patch('tasks.DailyTrifles.courtyard_claim.monotonic', Clock()):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'fresh screenshots'):
                close_courtyard_claim_popups(obj, on_success=Mock())
        self.assertEqual(obj.device.clicks, [])

    def test_recovery_or_invitation_mid_sequence_aborts(self):
        for attr, value in (('_escape_generation', 3),
                            ('_accepted_invitation_generation', 4)):
            with self.subTest(attr=attr):
                frame = soul_selector_frame()
                obj = task([frame] * 8)

                def screenshot():
                    obj.device.image_frame_id += 1
                    if obj.device.image_frame_id == 2:
                        setattr(obj.device, attr, value)
                    obj.device.image = frame

                obj.screenshot = screenshot
                with patch('tasks.DailyTrifles.courtyard_claim.monotonic', Clock()):
                    with self.assertRaisesRegex(TaskRecoveryFailed, 'interrupted by recovery'):
                        close_courtyard_claim_popups(obj, on_success=Mock())
                self.assertEqual(obj.device.clicks, [])

    def test_blocked_input_is_never_treated_as_progress(self):
        obj = task([soul_selector_frame()] * 8)
        obj.device.click = Mock(return_value=False)
        with patch('tasks.DailyTrifles.courtyard_claim.monotonic', Clock()):
            with self.assertRaisesRegex(TaskRecoveryFailed, 'not dispatched'):
                close_courtyard_claim_popups(obj, on_success=Mock())
        self.assertEqual(obj.device.confirmed, [])


if __name__ == '__main__':
    unittest.main()

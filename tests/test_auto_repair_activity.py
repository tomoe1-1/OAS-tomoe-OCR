"""Offline tests for the courtyard right-side activity column scan.

These tests exercise the multi-page activity collection logic added to
``module/script/auto_repair.py`` without touching a real device, without
calling the real ``codex`` CLI, and without spawning any process.

All device interaction is replaced by fakes, every on-disk artifact is
redirected into a temporary directory, and ``subprocess`` calls are stubbed.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path.cwd()))

import module.script.auto_repair as ar  # noqa: E402

HEIGHT = 720
WIDTH = 1280


def frame(value):
    """A solid 1280x720 RGB frame of the given grey value."""
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def region_of(value):
    """The expected activity region of a solid frame."""
    image = frame(value)
    return ar._activity_region(image)


class _FakeDevice:
    def __init__(self, image):
        self.image = image


class _FakeTask:
    """A fake OAS task exposing only what the scan touches.

    It never contacts a device: ``screenshot`` just advances through the
    supplied synthetic frames and updates ``device.image``.
    """

    I_CHECK_MAIN = "CHECK_MAIN"

    def __init__(self, frames, appear_main=True, click_raises=False):
        self.device = _FakeDevice(frames[0])
        self._frames = list(frames)
        self._index = 0
        self._appear_main = appear_main
        self._click_raises = click_raises
        self.click_calls = 0
        self.screenshot_calls = 0
        self.appear_calls = []

    def appear(self, target, **kwargs):
        self.appear_calls.append(target)
        return self._appear_main

    def appear_then_click(self, target, **kwargs):
        if self._click_raises:
            raise RuntimeError("click failure injected by test")
        self.click_calls += 1
        return True

    def screenshot(self):
        self.screenshot_calls += 1
        if self._index + 1 < len(self._frames):
            self._index += 1
        self.device.image = self._frames[self._index]


class _StateMixin:
    """Redirect the module STATE directory into a temporary folder."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="oas-activity-test-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.state = Path(self._tmp)
        patcher = patch.object(ar, "STATE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestActivityRegion(unittest.TestCase):
    def test_normal_size_shape(self):
        image = frame(0)
        region = ar._activity_region(image)
        expected = (int(HEIGHT * .84) - int(HEIGHT * .15), WIDTH - int(WIDTH * .87), 3)
        self.assertEqual(region.shape, expected)
        self.assertEqual(region.shape, (496, 167, 3))

    def test_invalid_inputs_return_none(self):
        self.assertIsNone(ar._activity_region(None))
        self.assertIsNone(ar._activity_region(np.zeros((100, 300, 3), np.uint8)))
        self.assertIsNone(ar._activity_region(object()))
        self.assertIsNone(ar._activity_region(SimpleNamespace()))

    def test_keeps_right_column(self):
        image = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        left = np.array([10, 20, 30], np.uint8)
        right = np.array([200, 150, 100], np.uint8)
        split = int(WIDTH * .87)
        image[:, :split] = left
        image[:, split:] = right
        region = ar._activity_region(image)
        self.assertTrue((region == right).all())
        self.assertFalse((region == left).any())


class TestActivitySignature(unittest.TestCase):
    def test_signature_length(self):
        signature = ar._activity_signature(region_of(100))
        self.assertEqual(len(signature), 12 * 48 * 3)

    def test_identical_signature_same(self):
        first = ar._activity_signature(region_of(100))
        second = ar._activity_signature(region_of(100))
        self.assertTrue(ar._same_activity_column(first, second))

    def test_different_pages_not_same(self):
        dark = ar._activity_signature(region_of(0))
        light = ar._activity_signature(region_of(255))
        self.assertFalse(ar._same_activity_column(dark, light))

    def test_slight_noise_still_same(self):
        rng = np.random.default_rng(20260924)
        base = np.full((HEIGHT, WIDTH, 3), 100, dtype=np.uint8)
        noisy = np.clip(base.astype(np.int16) + rng.integers(-1, 2, base.shape),
                        0, 255).astype(np.uint8)
        first = ar._activity_signature(ar._activity_region(base))
        second = ar._activity_signature(ar._activity_region(noisy))
        self.assertTrue(ar._same_activity_column(first, second))
        # The quantized mean difference documents the effective sensitivity.
        diff = np.abs(np.frombuffer(first, np.uint8).astype(np.int16)
                      - np.frombuffer(second, np.uint8).astype(np.int16))
        self.assertLessEqual(float(diff.mean()), .45)

    def test_quantized_shift_is_detected(self):
        # +24 grey levels flips every quantized bucket by exactly one, which is
        # above the 0.45 mean-difference threshold used by the dedup.
        first = ar._activity_signature(region_of(100))
        second = ar._activity_signature(region_of(124))
        self.assertFalse(ar._same_activity_column(first, second))


class TestCaptureActivityPages(_StateMixin, unittest.TestCase):
    day = "20260924"

    def test_single_page_dedup(self):
        task = _FakeTask([frame(0), frame(0)])
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual(len(pages), 1)
        self.assertEqual(task.click_calls, 2)
        self.assertFalse(pages.complete)
        self.assertEqual(pages.stop_reason, "rotation_unconfirmed")

    def test_missed_first_click_retries_and_confirms_second(self):
        task = _FakeTask([frame(0), frame(0), frame(60), frame(0)])
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual(len(pages), 2)
        self.assertEqual(task.click_calls, 3)
        self.assertEqual(task.screenshot_calls, 3)
        self.assertTrue(pages.complete)

    def test_three_pages_then_cycle(self):
        task = _FakeTask([frame(0), frame(60), frame(120), frame(0)])
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual(len(pages), 3)
        self.assertEqual(task.click_calls, 3)
        self.assertTrue(pages.complete)

    def test_caps_at_max_pages(self):
        frames = [frame(value) for value in range(0, 270, 30)]
        task = _FakeTask(frames)
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual(len(frames), 9)
        self.assertEqual(ar.ACTIVITY_COLUMN_MAX_PAGES, 8)
        self.assertEqual(len(pages), 8)
        self.assertEqual(task.click_calls, 8)
        self.assertFalse(pages.complete)
        self.assertEqual(pages.stop_reason, "page_limit_reached")

    def test_appear_main_false_stops(self):
        task = _FakeTask([frame(0), frame(60)], appear_main=False)
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual(len(pages), 1)
        self.assertEqual(task.click_calls, 0)

    def test_click_exception_returns_partial(self):
        task = _FakeTask([frame(0), frame(60)], click_raises=True)
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual(len(pages), 1)
        self.assertEqual(task.click_calls, 0)

    def test_bare_frame_source(self):
        pages = ar._capture_activity_pages(frame(100), self.day)
        self.assertEqual(len(pages), 1)

    def test_pages_saved_with_expected_names(self):
        task = _FakeTask([frame(0), frame(60), frame(120), frame(0)])
        pages = ar._capture_activity_pages(task, self.day)
        self.assertEqual([p.name for p in pages],
                         [f"activity-{self.day}-page-0.png",
                          f"activity-{self.day}-page-1.png",
                          f"activity-{self.day}-page-2.png"])
        for page in pages:
            self.assertTrue(page.is_file())


class TestQueueActivityScan(_StateMixin, unittest.TestCase):
    def _lock(self):
        day = ar.datetime.now().strftime("%Y%m%d")
        return self.state / f"activity-column-{day}.lock"

    def _event(self):
        day = ar.datetime.now().strftime("%Y%m%d")
        return self.state / f"activity-column-{day}.json"

    def test_disabled_returns_false_no_side_effects(self):
        worker = Mock()
        with patch.object(ar, "enabled", return_value=False), \
                patch.object(ar, "_start_worker", worker):
            result = ar.queue_activity_scan(_FakeTask([frame(0)]))
        self.assertFalse(result)
        self.assertFalse(self._lock().exists())
        self.assertFalse(self._event().exists())
        worker.assert_not_called()

    def test_existing_lock_blocks(self):
        lock = self._lock()
        lock.write_text("ORIGINAL-PID", encoding="utf-8")
        capture = Mock(return_value=[self.state / "unused.png"])
        with patch.object(ar, "enabled", return_value=True), \
                patch.object(ar, "_capture_activity_pages", capture):
            result = ar.queue_activity_scan(_FakeTask([frame(0)]))
        self.assertFalse(result)
        self.assertEqual(lock.read_text(encoding="utf-8"), "ORIGINAL-PID")
        capture.assert_not_called()

    def test_empty_capture_clears_lock(self):
        with patch.object(ar, "enabled", return_value=True), \
                patch.object(ar, "_capture_activity_pages", return_value=[]):
            result = ar.queue_activity_scan(_FakeTask([frame(0)]))
        self.assertFalse(result)
        self.assertFalse(self._lock().exists())
        self.assertFalse(self._event().exists())

    def test_success_zero_process(self):
        pages = [self.state / "activity-x-page-0.png",
                 self.state / "activity-x-page-1.png"]
        for page in pages:
            page.write_bytes(b"\x89PNG")
        worker = Mock()
        with patch.object(ar, "enabled", return_value=True), \
                patch.object(ar, "_capture_activity_pages", return_value=list(pages)), \
                patch.object(ar, "_start_worker", worker), \
                patch.object(ar.subprocess, "Popen") as popen:
            result = ar.queue_activity_scan(_FakeTask([frame(0)]))
        self.assertTrue(result)
        self.assertTrue(self._lock().exists())
        data = json.loads(self._event().read_text(encoding="utf-8"))
        self.assertEqual(data["kind"], "activity_scan")
        self.assertEqual(len(data["images"]), 2)
        worker.assert_called_once_with(self._event())
        popen.assert_not_called()


class TestActivityDecision(_StateMixin, unittest.TestCase):
    def test_no_codex_returns_false(self):
        event = self.state / "evt.json"
        with patch.object(ar, "_codex_binary", return_value=None):
            decision = ar._activity_decision("long enough ocr text", event,
                                             image_path=self.state / "x.png")
        self.assertFalse(decision["new_activity"])

    def test_command_image_flag(self):
        event = self.state / "evt.json"
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)
            return subprocess.CompletedProcess(command, 1)

        image_path = self.state / "crop.png"
        with patch.object(ar, "_codex_binary", return_value=Path("codex")), \
                patch.object(ar.subprocess, "run", side_effect=fake_run):
            ar._activity_decision("x" * 20, event, image_path=image_path)
        with_image = captured["command"]
        self.assertIn("-i", with_image)
        self.assertIn(str(image_path), with_image)
        self.assertEqual(with_image[-1], "-")

        with patch.object(ar, "_codex_binary", return_value=Path("codex")), \
                patch.object(ar.subprocess, "run", side_effect=fake_run):
            ar._activity_decision("x" * 20, event, image_path=None)
        without_image = captured["command"]
        self.assertNotIn("-i", without_image)
        self.assertEqual(without_image[-1], "-")


class TestRepairActivityScan(_StateMixin, unittest.TestCase):
    def _event(self, pages):
        event = self.state / "activity-column-20260924.json"
        event.write_text(json.dumps({
            "kind": "activity_scan",
            "task": "GotoMain",
            "images": [str(page) for page in pages],
            "traceback": "",
            "source_pid": 1,
        }), encoding="utf-8")
        return event

    def _status(self, event):
        return json.loads(event.with_suffix(".status.json").read_text(encoding="utf-8"))

    def _make_pages(self):
        first = self.state / "p0.png"
        second = self.state / "p1.png"
        first.write_bytes(b"\x89PNG")
        second.write_bytes(b"\x89PNG")
        return first, second

    def _run_patch(self, decision_fn):
        return (
            patch.object(ar, "enabled", return_value=True),
            patch.object(ar, "model_evidence_enabled", return_value=True),
            patch.object(ar, "_ocr_text", side_effect=lambda p: "PAGE_TWO"
                         if p.name == "p1.png" else "PAGE_ONE"),
            patch.object(ar, "_activity_decision", side_effect=decision_fn),
        )

    def test_draft_created_on_second_page(self):
        first, second = self._make_pages()
        event = self._event([first, second])

        def decision_fn(ocr_text, page_event, image_path=None):
            return {"new_activity": ocr_text == "PAGE_TWO", "name": "元宵灯会",
                    "evidence": "元宵灯会", "confidence": 0.98}

        patches = self._run_patch(decision_fn)
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        ar.repair(event)
        status = self._status(event)
        self.assertEqual(status["status"], "activity_draft_created")
        self.assertEqual(status["page"], 2)

    def test_no_new_activity_reports_page_count(self):
        first, second = self._make_pages()
        event = self._event([first, second])

        def decision_fn(ocr_text, page_event, image_path=None):
            return {"new_activity": False, "reason": "insufficient_evidence"}

        patches = self._run_patch(decision_fn)
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        ar.repair(event)
        status = self._status(event)
        self.assertEqual(status["status"], "no_new_activity")
        self.assertEqual(status["pages"], 2)

    def test_unconfirmed_rotation_is_not_reported_as_no_new_activity(self):
        first, _ = self._make_pages()
        event = self._event([first])
        data = json.loads(event.read_text(encoding="utf-8"))
        data.update(scan_complete=False, scan_stop_reason="rotation_unconfirmed")
        event.write_text(json.dumps(data), encoding="utf-8")
        patches = self._run_patch(
            lambda *args, **kwargs: {"new_activity": False,
                                     "reason": "insufficient_evidence"})
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        ar.repair(event)
        status = self._status(event)
        self.assertEqual(status["status"], "activity_scan_incomplete")
        self.assertEqual(status["scan_stop_reason"], "rotation_unconfirmed")

    def test_rejects_path_escape(self):
        outside = self.state / "nested" / "evil.png"
        event = self._event([outside])
        with patch.object(ar, "enabled", return_value=True), \
                patch.object(ar, "model_evidence_enabled", return_value=True):
            with self.assertRaises(ValueError) as ctx:
                ar.repair(event)
        self.assertIn("outside diagnostic state", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

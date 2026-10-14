"""Behavior regressions without launching game controls or model generation."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import re
import time
from datetime import datetime, timedelta
import cv2
import numpy as np

from module.device.device import Device
from module.exception import RequestHumanTakeover, GameStuckError, TaskEnd
from module.script.runtime_controller import ScriptRuntimeController, ScriptRuntimeDecision
from module.script import auto_repair as repair
from module.script import repair_budget as budget
from module.server import log_service, api_logger
from module.base.worker_metrics import WorkerMetrics
from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]


def draft_methods(names):
    tree = ast.parse((ROOT / 'tasks/DraftDuel/script_task.py').read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    cls.bases = []
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    ns = dict(time=time, re=re, logger=Mock(), GameStuckError=GameStuckError,
              TaskEnd=TaskEnd, datetime=datetime, timedelta=timedelta,
              cv2=cv2, np=np, CATALOG={name: SimpleNamespace(tier='') for name in 'ABCDEFX'},
              normalize_name=lambda text: text if text in 'ABCDEFX' and len(text) == 1 else None)
    ns['portrait_above_name'] = lambda image, box: image[0:10, 0:10].copy()
    ns['full_portrait_above_name'] = ns['portrait_above_name']
    ns['face_above_name'] = ns['portrait_above_name']
    ns['GeneralBattleAssets'] = SimpleNamespace(I_BATTLE_INFO='battle_info')
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'draft.py', 'exec'), ns)
    result = ns['ScriptTask']()
    result.TEAM_SIZE = 5
    result.device = SimpleNamespace(confirm_control_progress=Mock(), image=np.zeros((720, 1280, 3), np.uint8))
    result.C_SELECT = ['left', 'middle', 'right']
    return result


class DraftProgressTests(unittest.TestCase):
    def test_opening_boundaries_are_same_on_weekends(self):
        task = draft_methods({'_activity_open'})
        for day in (26, 28):
            for hour, minute, expected in ((9, 59, False), (10, 0, True), (22, 59, True), (23, 0, False)):
                self.assertEqual(task._activity_open(datetime(2026, 9, day, hour, minute)), expected)

    def test_closed_activity_reschedules_at_next_ten(self):
        task = draft_methods({'_activity_open', '_require_open_for_match'})
        task.set_next_run = Mock()
        for now, expected in ((datetime(2026, 9, 26, 9), datetime(2026, 9, 26, 10)),
                              (datetime(2026, 9, 26, 23), datetime(2026, 9, 27, 10))):
            ns = task._require_open_for_match.__globals__
            with patch.dict(ns, {'datetime': SimpleNamespace(now=lambda: now)}):
                with self.assertRaises(TaskEnd):
                    task._require_open_for_match()
            self.assertEqual(task.set_next_run.call_args.kwargs['target'], expected)

    def test_blocked_pick_never_becomes_member(self):
        task = draft_methods({'_pick_and_wait'})
        task.click = Mock(return_value=False)
        task._wait_round = Mock(return_value=4)
        self.assertEqual(task._pick_and_wait(1, 3, 30), (False, 4))

    def test_blocked_final_pick_is_unconfirmed(self):
        task = draft_methods({'_pick_and_wait'})
        task.click = Mock(return_value=False)
        task._wait_final_pick_accepted = Mock()
        self.assertEqual(task._pick_and_wait(1, 5, 30), (False, 6))

    def test_skipped_round_is_not_confirmed(self):
        task = draft_methods({'_pick_and_wait'})
        task.click = Mock(return_value=True)
        task._wait_round = Mock(return_value=5)
        self.assertEqual(task._pick_and_wait(1, 2, 30), (False, 5))

    def test_same_or_older_round_cannot_reset_protection(self):
        task = draft_methods({'_accept_pick_round'})
        for number in (3, 3, 2, 4):
            task._accept_pick_round(number)
        self.assertEqual(task.device.confirm_control_progress.call_count, 2)

    def test_noisy_round_requires_two_consecutive_frames(self):
        task = draft_methods({'_wait_round', '_accept_pick_round'})
        task.device.stuck_record_clear = Mock()
        task.screenshot = Mock()
        task._round_number = Mock(side_effect=[3, None, 3, 3])
        with patch('time.sleep'):
            self.assertEqual(task._wait_round(3, 30), 3)
        self.assertEqual(task._round_number.call_count, 4)
        task.device.confirm_control_progress.assert_called_once()

    def test_actual_names_override_planned_names(self):
        task = draft_methods({'_read_battle_lineup', '_resolve_lineup_names'})
        task.click = Mock(return_value=True)
        task.screenshot = Mock()
        task.C_DETAIL_CARDS = [str(i) for i in range(6)]
        task.C_MINE_TAB, task.C_DETAIL_CLOSE = 'mine', 'close'
        task.O_DETAIL_TITLE = SimpleNamespace(ocr=Mock(return_value='阵容详情'))
        task.O_LINEUP_NAMES = [SimpleNamespace(ocr=Mock(return_value=name)) for name in 'XBCDE']
        task._detail_name_boxes = Mock(return_value=[(name, SimpleNamespace(ocr_text=name, box=np.zeros((4, 2)))) for name in 'XBCDE'])
        names, _, _ = task._read_battle_lineup(list('ABCDE'))
        self.assertEqual(names, list('XBCDE'))
        self.assertEqual(task._actual_drafted, list('XBCDE'))

    def test_unknown_actual_name_is_not_filled_from_preference(self):
        task = draft_methods({'_read_battle_lineup', '_resolve_lineup_names'})
        task.click, task.screenshot = Mock(return_value=True), Mock()
        task.C_DETAIL_CARDS = [str(i) for i in range(6)]
        task.C_MINE_TAB, task.C_DETAIL_CLOSE = 'mine', 'close'
        task.O_DETAIL_TITLE = SimpleNamespace(ocr=Mock(return_value='阵容详情'))
        task.O_LINEUP_NAMES = [SimpleNamespace(ocr=Mock(return_value=name)) for name in ['?', 'B', 'C', 'D', 'E']]
        task._detail_name_boxes = Mock(return_value=[(name, SimpleNamespace(ocr_text=name, box=np.zeros((4, 2)))) for name in ['?', 'B', 'C', 'D', 'E']])
        self.assertIsNone(task._read_battle_lineup(list('ABCDE')))
        self.assertFalse(hasattr(task, '_actual_drafted'))

    def test_control_progress_preserves_unrelated_history(self):
        from collections import deque
        device = SimpleNamespace(click_record=deque(['other', 'middle', 'middle'], maxlen=15),
                                 _click_recovery_reason='Too many click for a button: middle',
                                 _click_recovery_at=10, _click_recovery_buttons={'other', 'middle'})
        device.click_record_remove = lambda button: Device.click_record_remove(device, button)
        Device.confirm_control_progress(device, ['left', 'middle', 'right'])
        self.assertEqual(list(device.click_record), ['other'])
        self.assertIsNone(device._click_recovery_at)

    def test_progress_does_not_cancel_unrelated_pause(self):
        from collections import deque
        device = SimpleNamespace(click_record=deque(['other'], maxlen=15),
                                 _click_recovery_reason='Too many click for a button: other',
                                 _click_recovery_at=10, _click_recovery_buttons={'other'})
        device.click_record_remove = lambda button: Device.click_record_remove(device, button)
        Device.confirm_control_progress(device, ['middle'])
        self.assertEqual(device._click_recovery_at, 10)

    def test_five_verified_rounds_keep_limit_on_unmoving_screen(self):
        from test_low_spec import device
        target = device()
        for _ in range(5):
            target.confirm_control_progress(['middle'])
            self.assertTrue(target.handle_control_check('middle'))
        target.confirm_control_progress(['middle'])
        for _ in range(3):
            self.assertTrue(target.handle_control_check('middle'))
        self.assertFalse(target.handle_control_check('middle'))

    def test_timed_click_flag_restored_after_failure(self):
        task = draft_methods({'run'})
        task.device._time_critical_clicks = False
        task._run_matches = Mock(side_effect=ValueError('probe'))
        with self.assertRaises(ValueError):
            task.run()
        self.assertFalse(task.device._time_critical_clicks)


class RestartBudgetTests(unittest.TestCase):
    def owner(self):
        # Startup recovery now clears the Restart failure deadline and records a
        # short 'startup_recovery_failed' outcome; the stub only needs to accept them.
        return SimpleNamespace(run=Mock(return_value=False), wait_until=Mock(return_value=True),
                               device=SimpleNamespace(app_is_running=Mock(return_value=True)),
                               last_task_runtime_outcome=None,
                               _clear_recovery_failure=Mock(),
                               _set_task_runtime_outcome=Mock())

    def test_third_failure_stops_without_counting_business_task(self):
        owner = self.owner()
        controller = ScriptRuntimeController(owner)
        self.assertEqual(controller._run_restart_recovery('probe'), ScriptRuntimeDecision.FAILED)
        self.assertEqual(controller._run_restart_recovery('probe'), ScriptRuntimeDecision.FAILED)
        with self.assertRaises(RequestHumanTakeover):
            controller._run_restart_recovery('probe')
        owner.wait_until.assert_not_called()

    def test_success_resets_budget(self):
        owner = self.owner()
        controller = ScriptRuntimeController(owner)
        controller._run_restart_recovery('probe')
        owner.run.return_value = True
        controller._run_restart_recovery('probe')
        self.assertEqual(controller._restart_recovery_failures, 0)

    def test_maintenance_delay_does_not_count_failure(self):
        owner = self.owner()
        owner.last_task_runtime_outcome = {'status': 'server_update_delayed', 'wait_until': datetime.now() + timedelta(minutes=10)}
        controller = ScriptRuntimeController(owner)
        self.assertEqual(controller._run_restart_recovery('probe'), ScriptRuntimeDecision.RESCHEDULE)
        self.assertEqual(controller._restart_recovery_failures, 0)
        owner.wait_until.assert_not_called()

    def test_game_still_closed_consumes_budget(self):
        owner = self.owner()
        owner.run.return_value = True
        owner.device.app_is_running.return_value = False
        controller = ScriptRuntimeController(owner)
        controller._run_restart_recovery('probe')
        self.assertEqual(controller._restart_recovery_failures, 1)


class RepairQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.patch = patch.object(repair, 'STATE', self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def event(self, name):
        event = self.state / (name + '.json')
        event.write_text('{}')
        repair._write_status(event, 'queued')
        return event

    def test_worker_lease_excludes_second_worker(self):
        with budget.WorkerLease(self.state) as first:
            with budget.WorkerLease(self.state) as second:
                self.assertTrue(first)
                self.assertFalse(second)
        with budget.WorkerLease(self.state) as released:
            self.assertTrue(released)

    def test_network_failure_keeps_evidence_and_stops_queue(self):
        first, second = self.event('first'), self.event('second')
        with patch.object(repair, 'enabled', return_value=True), patch.object(repair, 'repair', side_effect=budget.RepairServiceError('network', 'routing unavailable')) as run:
            repair.run_queue(first)
        self.assertEqual(run.call_count, 1)
        status = json.loads(first.with_suffix('.status.json').read_text())
        self.assertTrue(status['retryable'])
        self.assertGreater(status['next_retry_at'], time.time())
        self.assertTrue(first.exists())
        self.assertEqual(json.loads(second.with_suffix('.status.json').read_text())['status'], 'queued')

    def test_cooldown_prevents_process_spawn(self):
        first = self.event('first')
        budget.service_failed(self.state, budget.RepairServiceError('timeout', 'timeout'))
        with patch.object(repair.subprocess, 'Popen') as spawn:
            repair._start_worker(first)
        spawn.assert_not_called()

    def test_failed_service_recovers_after_due_time(self):
        first = self.event('first')
        budget.service_failed(self.state, budget.RepairServiceError('network', 'offline'))
        value = budget.read_budget(self.state)
        value['next_retry_at'] = 0
        budget.save_budget(self.state, value)
        def finish(event):
            repair._write_status(event, 'repair_draft_ready')
        with patch.object(repair, 'enabled', return_value=True), patch.object(repair, 'repair', side_effect=finish):
            repair.run_queue(first)
        self.assertEqual(budget.read_budget(self.state)['consecutive_failures'], 0)
        self.assertGreater(budget.read_budget(self.state)['last_success_at'], 0)

    def test_incomplete_generation_is_not_retried_as_network(self):
        event = self.event('first')
        with patch.object(repair, 'enabled', return_value=True), patch.object(repair, 'repair', side_effect=budget.RepairServiceError('generation', 'no valid patch')):
            repair.run_queue(event)
        status = json.loads(event.with_suffix('.status.json').read_text())
        self.assertFalse(status['retryable'])

    def test_classifier_reports_safe_network_reason(self):
        path = self.state / 'coder.log'
        path.write_text('secret arbitrary user text\nERROR workspace routing discovery timed out')
        error = budget.coder_failure(path, 1)
        self.assertEqual(error.category, 'network')
        self.assertNotIn('secret', str(error))


class LogCostTests(unittest.TestCase):
    def test_route_keeps_error_detail_and_can_enable_full_diagnostics(self):
        from fastapi import FastAPI
        import asyncio
        import httpx
        app = FastAPI()
        app.router.route_class = api_logger.ApiLoggingRoute
        @app.get('/logs/probe')
        async def route(fail: bool = False):
            return JSONResponse({'detail': 'diagnostic error' if fail else 'normal data'}, status_code=400 if fail else 200)
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                with patch.object(api_logger, 'log_http_access') as log:
                    await client.get('/logs/probe')
                    self.assertNotIn('body', log.call_args.args[0]['response'])
                    await client.get('/logs/probe?fail=true')
                    self.assertEqual(log.call_args.args[0]['response']['body']['detail'], 'diagnostic error')
                    with patch.dict('os.environ', {'OAS_API_LOG_DETAIL': '1'}):
                        await client.get('/logs/probe')
                    self.assertIn('body', log.call_args.args[0]['response'])
        asyncio.run(check())

    def test_compact_success_keeps_counts_but_not_content(self):
        response = JSONResponse({'items': [{'id': 'latest', 'content': 'x' * 4000}]})
        compact = api_logger.summarize_response(response, compact=True)
        detailed = api_logger.summarize_response(response)
        self.assertEqual(compact['items_count'], 1)
        self.assertEqual(compact['latest_error_id'], 'latest')
        self.assertNotIn('body', compact)
        self.assertIn('body', detailed)

    def test_error_index_caches_and_refreshes_new_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tomoe_1790550000000').mkdir()
            with patch.object(log_service, 'ERROR_LOG_ROOT', root):
                service = log_service.LogBrowserService()
                with patch.object(service, '_scan_error_logs', wraps=service._scan_error_logs) as scan:
                    first = service.list_error_logs()
                    first['items'][0]['id'] = 'mutated'
                    second = service.list_error_logs()
                    self.assertNotEqual(second['items'][0]['id'], 'mutated')
                    self.assertEqual(scan.call_count, 1)
                    (root / 'tomoe_1790550000001').mkdir()
                    import os
                    os.utime(root, (time.time() + 1, time.time() + 1))
                    third = service.list_error_logs()
                    self.assertEqual(len(third['items']), 2)
                    self.assertEqual(scan.call_count, 2)


class RecognitionMetricsTests(unittest.TestCase):
    def test_model_warmup_is_not_counted_as_request_latency(self):
        from module.ocr.rpc import OcrRuntime
        runtime = OcrRuntime({'worker_count': 1})
        try:
            with patch.object(runtime, '_get_model', return_value=object()):
                runtime.warmup()
            self.assertEqual(runtime.get_server_info()['scheduler']['timing_samples'], 0)
            runtime._run_request(lambda: 'ok')
            self.assertEqual(runtime.get_server_info()['scheduler']['timing_samples'], 1)
        finally:
            runtime.shutdown()

    def test_scheduler_reports_completed_and_failed_work(self):
        from module.ocr.rpc import OcrTaskScheduler
        from module.image.runtime import ImageTaskScheduler
        for scheduler_type in (OcrTaskScheduler, ImageTaskScheduler):
            scheduler = scheduler_type(1)
            try:
                self.assertEqual(scheduler.submit(lambda: 7).result(timeout=3), 7)
                def failure():
                    raise ValueError('probe')
                with self.assertRaises(ValueError):
                    scheduler.submit(failure).result(timeout=3)
                stats = scheduler.stats()
                self.assertEqual(stats['timing_samples'], 2)
                self.assertEqual(stats['active'], 0)
                self.assertGreaterEqual(stats['run_p95_ms'], 0)
            finally:
                scheduler.shutdown()

    def test_failed_work_still_finishes_metrics(self):
        metrics = WorkerMetrics()
        def failure():
            raise ValueError('probe')
        with self.assertRaises(ValueError):
            metrics.wrap(failure, (), {})()
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot['active'], 0)
        self.assertEqual(snapshot['timing_samples'], 1)

    def test_samples_are_bounded_and_result_is_preserved(self):
        metrics = WorkerMetrics()
        for _ in range(300):
            self.assertEqual(metrics.wrap(lambda x: x + 1, (2,), {})(), 3)
        self.assertEqual(metrics.snapshot()['timing_samples'], 256)


if __name__ == '__main__':
    unittest.main()

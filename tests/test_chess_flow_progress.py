"""Actual Chess round-loop regressions with a simulated capture clock."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from module.exception import GameStuckError, TaskRecoveryFailed
from module.script.task_flow_guard import TaskFlowGuard
from tasks.Chess.runtime.decision import ChessAction, decide_round
from tasks.Chess.runtime.events import ChessEventKind, ChessEventMonitor
from tasks.Chess.runtime.state import ChessGameState


ROOT = Path(__file__).resolve().parents[1]


def methods(path, class_name, names, namespace):
    source = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    owner = next(node for node in source.body
                 if isinstance(node, ast.ClassDef) and node.name == class_name)
    nodes = [node for node in owner.body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    prefix = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[prefix] + nodes, type_ignores=[]))
    exec(compile(module, str(ROOT / path), 'exec'), namespace)


def chess_subject(rounds, *, capture_seconds=200):
    clock = SimpleNamespace(now=0.)
    namespace = dict(logger=Mock(), time=SimpleNamespace(monotonic=lambda: clock.now, sleep=lambda _: None),
                     GameStuckError=GameStuckError, ChessAction=ChessAction, decide_round=decide_round,
                     ChessEventKind=ChessEventKind)
    methods('tasks/Chess/script_task.py', 'ChessLegacyScriptTask',
            ['_run_game_by_rounds_without_device_stuck_timeout'], namespace)
    methods('tasks/Chess/runtime/state_loop.py', 'ChessStateLoopMixin',
            ['run_one_round', '_record_observation', '_publish_chess_events'], namespace)
    # run_one_round dispatches through the interrupted-event helpers; extract the
    # real ones so a relocation cannot silently drop them from the subject.
    methods('tasks/Chess/runtime/event_actions.py', 'ChessEventActionsMixin',
            ['_discover_choices', '_resume_discover_event_if_needed'], namespace)
    task = type('Subject', (), {name: namespace[name] for name in
                ('_run_game_by_rounds_without_device_stuck_timeout', 'run_one_round',
                 '_record_observation', '_publish_chess_events', '_discover_choices',
                 '_resume_discover_event_if_needed')})()
    task.config = SimpleNamespace(global_game=SimpleNamespace(battle=SimpleNamespace(battle_timeout=300)))
    task.chess_state = ChessGameState()
    task.chess_events = ChessEventMonitor()
    task.ROUND_CONFIRM_FRAMES = 2
    task.UNKNOWN_STATE_TIMEOUT = 25
    task.SLOW_POLL_INTERVAL = 1
    task.I_SELECT_GRIGRI = 'grigri'
    task.I_SELECT_SOUL_1, task.I_SELECT_SOUL_2, task.I_SELECT_SOUL_3 = (
        'soul_1', 'soul_2', 'soul_3')
    task.current_count = 0
    task.observed_round = 1
    task._ended = False
    device = SimpleNamespace(image=object(), image_frame_id=0, _task_flow_progress_generation=0,
                             _task_flow_round_generation=0,
                             stuck_record_clear=Mock(),
                             performance=SimpleNamespace(timeout=lambda seconds: seconds))
    device.confirm_control_progress = Mock()
    device.confirm_task_round_progress = Mock()
    task.device = device
    guard = TaskFlowGuard(task, device, clock=lambda: clock.now)
    observations = iter(rounds)

    def capture():
        guard.check()
        device.image_frame_id += 1
        task.observed_round = next(observations, task.observed_round)
        task._ended = task.observed_round is None
        clock.now += capture_seconds

    def read_round():
        task._record_observation(round_no=task.observed_round)
        return task.observed_round

    def read_mode():
        task._record_observation(mode='待')
        return '待'

    task.screenshot = capture
    task._wait_for_round_start = lambda: 1
    task._read_round_number = read_round
    task._read_chess_mode = read_mode
    task._finish_chess_game_after_markers_missing = lambda _: task._ended
    task.appear = lambda _: False
    task._handle_passive_stage = Mock()
    return task, device, clock, guard


class ChessFlowProgressTests(unittest.TestCase):
    def test_healthy_game_over_thirty_minutes_renews_on_confirmed_rounds(self):
        task, device, clock, guard = chess_subject([2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, None])
        with guard:
            task._run_game_by_rounds_without_device_stuck_timeout()
            self.assertEqual(list(guard._chess_round_high_water.values()), [7])
            guard.check()
            self.assertEqual(guard._chess_round_high_water, {})
        self.assertGreater(clock.now, 1800)
        device.confirm_control_progress.assert_not_called()
        device.confirm_task_round_progress.assert_not_called()
        self.assertEqual(device._task_flow_round_generation, 0)
        self.assertEqual(task.current_count, 0)
        self.assertEqual(guard._chess_round_high_water, {})

    def test_stalled_valid_round_expires_even_while_clearing_device_records(self):
        task, device, _, guard = chess_subject([1] * 20, capture_seconds=60)
        with guard, self.assertRaisesRegex(TaskRecoveryFailed, '420s'):
            task._run_game_by_rounds_without_device_stuck_timeout()
        device.confirm_control_progress.assert_not_called()
        self.assertGreaterEqual(device.stuck_record_clear.call_count, 7)

    def test_backward_round_oscillation_cannot_keep_a_game_alive(self):
        task, device, _, guard = chess_subject([2, 2, 1, 1, 2, 2, 1, 1] * 10,
                                             capture_seconds=100)
        with guard, self.assertRaisesRegex(TaskRecoveryFailed, '420s'):
            task._run_game_by_rounds_without_device_stuck_timeout()
        device.confirm_control_progress.assert_not_called()

    def test_one_changed_frame_is_not_confirmed_progress(self):
        task, device, _, guard = chess_subject([2, 1, 2, 1] * 10, capture_seconds=60)
        with guard, self.assertRaisesRegex(TaskRecoveryFailed, '420s'):
            task._run_game_by_rounds_without_device_stuck_timeout()
        device.confirm_control_progress.assert_not_called()


if __name__ == '__main__':
    unittest.main()

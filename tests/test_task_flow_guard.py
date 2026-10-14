"""Offline stack simulations for cooperative task residence limits."""
from datetime import time as clock_time, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from module.exception import TaskRecoveryFailed
from module.script.task_flow_guard import TaskFlowGuard


ROOT = Path(__file__).resolve().parents[1]


def subject(body=None, *, filename='tasks/Example/script_task.py', multiplier=1):
    if body is None:
        body = '''
def run(self):
    while True:
        self.capture()
        yield
'''
    namespace = {}
    exec(compile(body, str(ROOT / filename), 'exec'), namespace)
    cls = type('Subject', (), {name: method for name, method in namespace.items() if callable(method)})
    task = cls()
    task.current_count = 0
    task.limit_time = None
    task.config = SimpleNamespace(global_game=SimpleNamespace(battle=SimpleNamespace(battle_timeout=300)),
                                  guild_activity_monitor=SimpleNamespace(
                                      guild_activity_monitor_combat_time=SimpleNamespace(monitor_duration=15)))
    device = SimpleNamespace(_task_flow_progress_generation=0,
                             performance=SimpleNamespace(timeout=lambda seconds: seconds * multiplier),
                             detect_record={'BATTLE'}, image_frame_id=0)
    clock = SimpleNamespace(now=0)

    def capture():
        device._task_flow_guard.check()
        device.image_frame_id += 1

    task.capture = capture
    task.device = device
    guard = TaskFlowGuard(task, device, clock=lambda: clock.now)
    return task, device, clock, guard


class TaskFlowGuardTests(unittest.TestCase):
    def test_default_loop_fails_before_another_capture_at_420_seconds(self):
        task, device, clock, guard = subject()
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 419
            next(loop)
            clock.now = 420
            with self.assertRaisesRegex(TaskRecoveryFailed, 'no confirmed progress.*420s.*:run'):
                next(loop)
            self.assertEqual(device.image_frame_id, 2)
        self.assertFalse(hasattr(device, '_task_flow_guard'))
        self.assertEqual(guard._active, {})

    def test_repeated_short_helpers_do_not_renew_the_parent_loop(self):
        task, device, clock, guard = subject('''
def helper(self):
    self.capture()
def run(self):
    while True:
        self.helper()
        yield
''')
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for clock.now in (100, 200, 300, 419):
                next(loop)
            self.assertEqual(len(guard._active), 2)
            clock.now = 420
            with self.assertRaisesRegex(TaskRecoveryFailed, ':run'):
                next(loop)
        self.assertEqual(device.image_frame_id, 5)

    def test_screen_animation_clicks_and_record_clearing_do_not_renew_a_loop(self):
        task, device, clock, guard = subject()
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for clock.now in (100, 200, 300, 419):
                device.image_frame_id += 100
                device.detect_record.clear()
                device.click_record = []
                device.last_input = 'ordinary click'
                next(loop)
            clock.now = 420
            with self.assertRaises(TaskRecoveryFailed):
                next(loop)

    def test_completed_round_counter_renews_all_active_callers(self):
        task, _, clock, guard = subject('''
def helper(self):
    self.capture()
def run(self):
    while True:
        self.helper()
        yield
''')
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for count in range(1, 11):
                clock.now = count * 400
                task.current_count = count
                next(loop)
            self.assertEqual(len(guard._active), 2)
            clock.now += 420
            with self.assertRaises(TaskRecoveryFailed):
                next(loop)

    def test_confirmed_ui_progress_renews_callers_without_a_round_count(self):
        task, device, clock, guard = subject()
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 400
            device._task_flow_progress_generation += 1
            next(loop)
            clock.now = 819
            next(loop)
            clock.now = 820
            with self.assertRaises(TaskRecoveryFailed):
                next(loop)

    def test_verified_ui_cycles_cannot_renew_the_hard_residence_limit(self):
        task, device, clock, guard = subject()
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for clock.now in (400, 800, 1200, 1600):
                device._task_flow_progress_generation += 1
                next(loop)
            clock.now = 1800
            device._task_flow_progress_generation += 1
            with self.assertRaisesRegex(TaskRecoveryFailed, '1800s without advancing a round'):
                next(loop)

    def test_confirmed_round_generation_renews_hard_and_soft_limits(self):
        task, device, clock, guard = subject()
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for step in range(1, 11):
                clock.now = step * 400
                device._task_flow_round_generation = step
                next(loop)
            clock.now += 420
            with self.assertRaisesRegex(TaskRecoveryFailed, '420s'):
                next(loop)

    def test_configured_long_duration_widens_the_hard_cap_without_ui_renewal(self):
        task, device, clock, guard = subject()
        task.limit_time = 3600
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for clock.now in (1000, 2000, 3000, 3659):
                device._task_flow_progress_generation += 1
                next(loop)
            clock.now = 3660
            device._task_flow_progress_generation += 1
            with self.assertRaisesRegex(TaskRecoveryFailed, '3660s without advancing a round'):
                next(loop)

    def test_chess_matched_loading_preserves_existing_600_second_window(self):
        task, _, clock, guard = subject('''
def _wait_until_in_chess_game(self, timeout):
    while True:
        self.capture()
        yield
def _start_chess_game(self):
    yield from self._wait_until_in_chess_game(120)
def run(self):
    yield from self._start_chess_game()
''', filename='tasks/Chess/script_task.py')
        task.limit_time = 1800
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for clock.now in (420, 599, 959):
                next(loop)
            clock.now = 960
            with self.assertRaisesRegex(TaskRecoveryFailed, '960s'):
                next(loop)

    def test_counter_or_generation_regression_does_not_create_fake_progress(self):
        for field in ('current_count', '_task_flow_progress_generation'):
            with self.subTest(field=field):
                task, device, clock, guard = subject()
                owner = task if field == 'current_count' else device
                with guard:
                    loop = task.run()
                    self.addCleanup(loop.close)
                    next(loop)
                    clock.now = 400
                    setattr(owner, field, 1)
                    next(loop)
                    clock.now = 600
                    setattr(owner, field, 0)
                    next(loop)
                    clock.now = 700
                    setattr(owner, field, 1)
                    next(loop)
                    clock.now = 820
                    with self.assertRaises(TaskRecoveryFailed):
                        next(loop)

    def test_configured_long_battle_is_preserved(self):
        task, _, clock, guard = subject()
        task.config.global_game.battle.battle_timeout = 1200
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 1199
            next(loop)
            clock.now = 1200
            with self.assertRaisesRegex(TaskRecoveryFailed, '1200s'):
                next(loop)

    def test_active_battle_timer_can_widen_a_wait_without_renewing_its_start(self):
        task, _, clock, guard = subject()
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 400
            task._battle_context = SimpleNamespace(battle_timer=SimpleNamespace(limit=900))
            next(loop)
            clock.now = 899
            next(loop)
            clock.now = 900
            with self.assertRaisesRegex(TaskRecoveryFailed, '900s'):
                next(loop)

    def test_configured_run_duration_allows_long_outer_task(self):
        for limit in (timedelta(hours=1), clock_time(1, 0), 3600):
            with self.subTest(limit=limit):
                task, _, clock, guard = subject()
                task.limit_time = limit
                with guard:
                    loop = task.run()
                    self.addCleanup(loop.close)
                    next(loop)
                    clock.now = 3659
                    next(loop)
                    clock.now = 3660
                    with self.assertRaisesRegex(TaskRecoveryFailed, '3660s'):
                        next(loop)

    def test_inner_ui_wait_does_not_inherit_hours_of_outer_task_duration(self):
        task, _, clock, guard = subject('''
def wait_for_button(self):
    while True:
        self.capture()
        yield
def run(self):
    yield from self.wait_for_button()
''')
        task.limit_time = timedelta(hours=10)
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 420
            with self.assertRaisesRegex(TaskRecoveryFailed, ':wait_for_button'):
                next(loop)

    def test_guild_monitor_uses_its_configured_window_plus_cleanup_grace(self):
        task, _, clock, guard = subject('''
def monitor_activities(self):
    while True:
        self.capture()
        yield
def run(self):
    yield from self.monitor_activities()
''', filename='tasks/GuildActivityMonitor/script_task.py')
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 959
            next(loop)
            clock.now = 960
            with self.assertRaisesRegex(TaskRecoveryFailed, '960s'):
                next(loop)

    def test_other_tasks_do_not_inherit_common_guild_monitor_configuration(self):
        task, _, clock, guard = subject()
        task.config.guild_activity_monitor.guild_activity_monitor_combat_time.monitor_duration = 600
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 420
            with self.assertRaisesRegex(TaskRecoveryFailed, '420s'):
                next(loop)

    def test_drive_wait_preserves_its_window_but_room_helpers_still_expire(self):
        task, _, clock, guard = subject('''
def _run_drive_mode(self):
    while True:
        self.capture()
        yield
def run(self):
    yield from self._run_drive_mode()
''', filename='tasks/LBS/script_task.py')
        task.limit_time = 1800
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 1799
            next(loop)
            clock.now = 1860
            with self.assertRaisesRegex(TaskRecoveryFailed, '1860s'):
                next(loop)
        task, _, clock, guard = subject('''
def create_room(self):
    while True:
        self.capture()
        yield
def _run_drive_mode(self):
    yield from self.create_room()
def run(self):
    yield from self._run_drive_mode()
''', filename='tasks/LBS/script_task.py')
        task.limit_time = 1800
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 420
            with self.assertRaisesRegex(TaskRecoveryFailed, ':create_room'):
                next(loop)

    def test_team_matching_preserves_480_second_wait_with_a_finite_guard(self):
        task, _, clock, guard = subject('''
def _wait_team_battle(self):
    match_timer = self.match_timer
    while True:
        self.capture()
        yield
def _run_team_match(self):
    yield from self._wait_team_battle()
def run(self):
    yield from self._run_team_match()
''', filename='tasks/LBS/script_task.py')
        task.limit_time = 1800
        task.match_timer = SimpleNamespace(limit=480)
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 479
            next(loop)
            clock.now = 540
            with self.assertRaisesRegex(TaskRecoveryFailed, ':_wait_team_battle'):
                next(loop)

    def test_low_spec_scaling_is_applied_once_per_raw_budget(self):
        task, _, clock, guard = subject(multiplier=3)
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            for clock.now in (400, 800, 1200, 1259):
                next(loop)
            clock.now = 1260
            with self.assertRaisesRegex(TaskRecoveryFailed, '1260s'):
                next(loop)

    def test_invalid_budgets_do_not_disable_the_default_cap(self):
        for invalid in (None, 0, -1, True, float('inf'), float('nan'), 'invalid'):
            with self.subTest(invalid=invalid):
                task, _, clock, guard = subject()
                task.limit_time = invalid
                task.config.global_game.battle.battle_timeout = invalid
                task._battle_context = SimpleNamespace(battle_timer=SimpleNamespace(limit=invalid))
                with guard:
                    loop = task.run()
                    self.addCleanup(loop.close)
                    next(loop)
                    clock.now = 420
                    with self.assertRaisesRegex(TaskRecoveryFailed, '420s'):
                        next(loop)

    def test_capture_wrappers_are_excluded_but_business_parent_is_preserved(self):
        task, _, clock, guard = subject('''
def screenshot(self):
    self.capture()
def maybe_screenshot(self):
    self.screenshot()
def run(self):
    while True:
        self.maybe_screenshot()
        yield
''', filename='tasks/base_task.py')
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            self.assertEqual([frame.f_code.co_name for frame in guard._active], ['run'])
            clock.now = 420
            with self.assertRaisesRegex(TaskRecoveryFailed, ':run'):
                next(loop)

    def test_returned_task_frames_are_pruned_on_the_next_check(self):
        task, _, _, guard = subject()
        with guard:
            loop = task.run()
            next(loop)
            self.assertEqual(len(guard._active), 1)
            loop.close()
            guard.check()
            self.assertEqual(guard._active, {})

    def test_module_device_loop_outside_tasks_is_not_guarded(self):
        task, _, clock, guard = subject(filename='module/device/test_capture.py')
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 10000
            next(loop)
            self.assertEqual(guard._active, {})

    def test_context_restores_prior_guard_even_when_task_raises(self):
        task, device, clock, guard = subject()
        previous = object()
        device._task_flow_guard = previous
        with self.assertRaises(TaskRecoveryFailed):
            with guard:
                loop = task.run()
                self.addCleanup(loop.close)
                next(loop)
                clock.now = 420
                next(loop)
        self.assertIs(device._task_flow_guard, previous)
        self.assertEqual(guard._active, {})

    def test_check_after_context_exit_has_no_effect(self):
        task, _, clock, guard = subject()
        with guard:
            loop = task.run()
            next(loop)
            loop.close()
        clock.now = 10000
        guard.check()
        self.assertEqual(guard._active, {})

    def test_explicit_disabled_monitor_is_respected_and_reenable_starts_a_new_budget(self):
        task, device, clock, guard = subject()
        device._task_flow_guard_disabled = True
        with guard:
            loop = task.run()
            self.addCleanup(loop.close)
            next(loop)
            clock.now = 10000
            next(loop)
            self.assertEqual(guard._active, {})
            device._task_flow_guard_disabled = False
            next(loop)
            clock.now = 10420
            with self.assertRaises(TaskRecoveryFailed):
                next(loop)


if __name__ == '__main__':
    unittest.main()

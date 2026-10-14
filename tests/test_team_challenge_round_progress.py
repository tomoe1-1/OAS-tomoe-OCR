"""Verified wins survive cooldowns, task reconstruction and completion crashes."""
import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import TaskEnd, TaskRecoveryFailed
from module.script.task_completion import TaskCompletionGate
from tasks.Component.GeneralRoom.round_progress import TeamChallengeRoundProgress


REPOSITORY = Path(__file__).resolve().parents[1]


class NeverExpiredTimer:
    def __init__(self, *args):
        pass

    def start(self):
        return self

    def reached(self):
        return False


def task_type(task_name, progress_factory):
    """Run the real task methods without loading a device, OCR or UI assets."""
    path = REPOSITORY / 'tasks' / task_name / 'script_task.py'
    source = ast.parse(path.read_text(encoding='utf-8'))
    definition = next(node for node in source.body if isinstance(node, ast.ClassDef)
                      and node.name == 'ScriptTask')
    definition.bases = []
    namespace = dict(TaskEnd=TaskEnd, TaskRecoveryFailed=TaskRecoveryFailed,
                     TeamChallengeRoundProgress=progress_factory, Timer=NeverExpiredTimer,
                     logger=Mock(), page_main='main', page_team='team',
                     page_shikigami_records='records')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[])),
                 str(path), 'exec'), namespace)
    return namespace['ScriptTask']


class RoundProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'log' / 'task_progress'
        self.checkpoint = Path(self.temporary.name) / 'log' / 'task_retry' / 'tomoe.json'
        self.now = datetime.now()

    def progress(self, task='GoldYoukai', profile='tomoe'):
        checkpoint = self.checkpoint.with_name(f'{profile}.json')
        return TeamChallengeRoundProgress(profile, task, root=self.root, checkpoint_path=checkpoint)

    def complete(self, task='GoldYoukai', due=None):
        progress = self.progress(task)
        progress.begin()
        progress.record_win(self.now - timedelta(seconds=2))
        progress.record_win(self.now - timedelta(seconds=1))
        progress.mark_scheduled(self.now + timedelta(days=1) if due is None else due)
        return progress

    def subject(self, task_name='GoldYoukai', cooldowns=None, wins=None):
        key = 'gold_youkai' if task_name == 'GoldYoukai' else 'experience_youkai'
        task = task_type(task_name, lambda profile, name: self.progress(name, profile))()
        buffs = (dict(buff_gold_50_click=False, buff_gold_100_click=False)
                 if task_name == 'GoldYoukai'
                 else dict(buff_exp_50_click=False, buff_exp_100_click=False))
        model = SimpleNamespace(**{key: SimpleNamespace(**buffs)},
                                scheduler=SimpleNamespace(next_run=self.now - timedelta(hours=1)),
                                switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False))
        task.config = SimpleNamespace(config_name='tomoe', **{key: model})
        for method in ('before_run', 'goto_page', 'check_zones', 'ensure_public',
                       'create_ensure', 'screenshot', 'click_fire', 'open_buff', 'close_buff'):
            setattr(task, method, Mock())
        task.I_ADD_5_1 = object()
        task.is_in_room = Mock(return_value=True)
        task.appear = Mock(return_value=False)
        task.create_room = Mock(return_value=True)
        task.check_team_cooldown = Mock(side_effect=cooldowns if cooldowns is not None else [None, None])
        task.run_general_battle = Mock(side_effect=wins if wins is not None else [True, True])

        def schedule(**kwargs):
            model.scheduler.next_run = kwargs.get('target', self.now + timedelta(days=1))

        task.set_next_run = Mock(side_effect=schedule)

        def defer(name, duration):
            target = self.now + duration + timedelta(seconds=65)
            task.set_next_run(task=name, server=False, target=target)
            task.config.task_runtime_outcome = dict(task=name, status='cooldown_delayed', wait_until=target)
            raise TaskEnd(name)

        task.schedule_team_cooldown = Mock(side_effect=defer)
        return task

    def test_restart_after_one_win_and_cooldown_only_fights_remaining_round(self):
        for name in ('GoldYoukai', 'ExperienceYoukai'):
            with self.subTest(task=name):
                first = self.subject(name, cooldowns=[None, timedelta(hours=4)], wins=[True])
                with self.assertRaises(TaskEnd):
                    first.run()
                self.assertEqual(first.run_general_battle.call_count, 1)
                self.assertEqual(self.progress(name).completed_wins, 1)
                self.assertEqual(first.config.task_runtime_outcome['status'], 'cooldown_delayed')
                first.set_next_run.assert_called_once()
                self.assertFalse(first.set_next_run.call_args.kwargs['server'])

                # A new object re-reads disk, rather than retaining in-memory count.
                second = self.subject(name, cooldowns=[None], wins=[True])
                with self.assertRaises(TaskEnd):
                    second.run()
                self.assertEqual(second.create_room.call_count, 1)
                self.assertEqual(second.check_team_cooldown.call_count, 1)
                self.assertEqual(second.run_general_battle.call_count, 1)
                self.assertEqual(self.progress(name).completed_wins, 2)
                self.assertIsNotNone(self.progress(name).next_cycle_at)

    def test_profile_and_task_progress_are_independent(self):
        gold = self.progress()
        gold.seed_confirmed_wins(1)
        self.assertEqual(self.progress('ExperienceYoukai').begin(), 0)
        self.assertEqual(self.progress(profile='another_profile').begin(), 0)
        self.assertEqual(self.progress().completed_wins, 1)
        self.assertNotEqual(gold.path, self.progress('ExperienceYoukai').path)

    def test_literal_victory_only_and_failed_battle_retains_prior_win(self):
        for result in (False, None, 1):
            for name in ('GoldYoukai', 'ExperienceYoukai'):
                with self.subTest(result=result, task=name):
                    progress = self.progress(name)
                    if progress.state is None:
                        progress.seed_confirmed_wins(1)
                    task = self.subject(name, cooldowns=[None], wins=[result])
                    with self.assertRaises(TaskRecoveryFailed):
                        task.run()
                    self.assertEqual(self.progress(name).completed_wins, 1)
                    task.set_next_run.assert_not_called()

    def test_room_creation_failure_does_not_claim_success(self):
        for name in ('GoldYoukai', 'ExperienceYoukai'):
            with self.subTest(task=name):
                self.progress(name).seed_confirmed_wins(1)
                task = self.subject(name, cooldowns=[None])
                task.create_room.return_value = False
                with self.assertRaises(TaskRecoveryFailed):
                    task.run()
                self.assertEqual(self.progress(name).completed_wins, 1)
                task.run_general_battle.assert_not_called()
                task.set_next_run.assert_not_called()

    def test_exception_during_battle_keeps_prior_win(self):
        self.progress().seed_confirmed_wins(1)
        task = self.subject(cooldowns=[None], wins=[KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            task.run()
        self.assertEqual(self.progress().completed_wins, 1)
        task.set_next_run.assert_not_called()

    def test_completed_round_restart_does_not_create_extra_room_or_move_schedule(self):
        for name in ('GoldYoukai', 'ExperienceYoukai'):
            with self.subTest(task=name):
                progress = self.complete(name)
                terminal = progress.path.read_bytes()
                task = self.subject(name)
                with self.assertRaises(TaskEnd):
                    task.run()
                task.create_room.assert_not_called()
                task.check_team_cooldown.assert_not_called()
                task.run_general_battle.assert_not_called()
                self.assertEqual(task.set_next_run.call_args.kwargs['target'], progress.next_cycle_at)
                self.assertFalse(task.set_next_run.call_args.kwargs['server'])
                self.assertNotIn('success', task.set_next_run.call_args.kwargs)
                self.assertEqual(progress.path.read_bytes(), terminal)

    def test_changed_shorter_interval_cannot_advance_terminal_retry_schedule(self):
        progress = self.complete()
        task = self.subject()
        scheduler = task.config.gold_youkai.scheduler

        def interval_min(**kwargs):
            candidates = [kwargs['target']]
            if kwargs.get('success') is not None:
                candidates.append(self.now + timedelta(minutes=1))
            scheduler.next_run = min(candidates)

        task.set_next_run.side_effect = interval_min
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(scheduler.next_run, progress.next_cycle_at)

    def test_second_win_saved_before_normal_schedule_failure(self):
        self.progress().seed_confirmed_wins(1)
        task = self.subject(cooldowns=[None], wins=[True])
        task.set_next_run.side_effect = TaskRecoveryFailed('schedule unavailable')
        with self.assertRaises(TaskRecoveryFailed):
            task.run()
        saved = self.progress()
        self.assertEqual(saved.completed_wins, 2)
        self.assertIsNone(saved.next_cycle_at)
        stamp = saved.state['completed_at']
        resumed = self.subject()
        with self.assertRaises(TaskEnd):
            resumed.run()
        resumed.create_room.assert_not_called()
        self.assertEqual(self.progress().state['completed_at'], stamp)
        self.assertIsNotNone(self.progress().next_cycle_at)

    def test_terminal_rearms_only_after_release_and_next_cycle_due(self):
        progress = self.complete(due=self.now + timedelta(hours=1))
        original_round = progress.state['round_id']
        gate = TaskCompletionGate(self.checkpoint)
        gate.retain('GoldYoukai')
        # The due time alone does not authorize replacing a retained terminal.
        self.assertEqual(self.progress().begin(self.now + timedelta(hours=2)), 2)
        gate.release('GoldYoukai')
        self.assertEqual(TaskCompletionGate(self.checkpoint).task, '')
        # Retain for the next invocation must preserve the receipt atomically.
        TaskCompletionGate(self.checkpoint).retain('GoldYoukai')
        self.assertEqual(self.progress().begin(self.now), 2)
        next_round = self.progress()
        self.assertEqual(next_round.begin(self.now + timedelta(hours=2)), 0)
        self.assertNotEqual(next_round.state['round_id'], original_round)

    def test_older_release_and_other_task_receipts_do_not_rearm(self):
        self.complete(due=self.now - timedelta(minutes=1))
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        for released in ({'GoldYoukai': (self.now - timedelta(days=1)).isoformat()},
                         {'ExperienceYoukai': (self.now + timedelta(minutes=1)).isoformat()}):
            with self.subTest(released=released):
                self.checkpoint.write_text(json.dumps({'task': '', 'released': released}), encoding='utf-8')
                self.assertEqual(self.progress().begin(self.now), 2)

    def test_due_and_released_round_starts_two_new_wins(self):
        previous = self.complete(due=self.now - timedelta(minutes=1))
        prior_id = previous.state['round_id']
        gate = TaskCompletionGate(self.checkpoint)
        gate.retain('GoldYoukai')
        gate.release('GoldYoukai')
        gate.retain('GoldYoukai')
        task = self.subject(cooldowns=[None, None], wins=[True, True])
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(task.create_room.call_count, 2)
        self.assertEqual(task.run_general_battle.call_count, 2)
        current = self.progress()
        self.assertEqual(current.completed_wins, 2)
        self.assertNotEqual(current.state['round_id'], prior_id)

    def test_release_receipt_never_resets_unfinished_round(self):
        self.progress().seed_confirmed_wins(1)
        gate = TaskCompletionGate(self.checkpoint)
        gate.retain('GoldYoukai')
        gate.release('GoldYoukai')
        self.assertEqual(self.progress().begin(self.now + timedelta(days=5)), 1)

    def test_malformed_counts_fail_without_overwriting_retained_file(self):
        progress = self.progress()
        progress.seed_confirmed_wins(1)
        original = dict(progress.state)
        for count in (-1, 3, True, '1', 1.0, None):
            with self.subTest(count=count):
                progress.path.write_text(json.dumps(dict(original, completed_wins=count)), encoding='utf-8')
                corrupt = progress.path.read_bytes()
                with self.assertRaises(TaskRecoveryFailed):
                    self.progress()
                self.assertEqual(progress.path.read_bytes(), corrupt)

    def test_corrupt_receipt_fails_and_preserves_completed_round(self):
        progress = self.complete(due=self.now - timedelta(minutes=1))
        original = progress.path.read_bytes()
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        for raw in ('{broken', '[]', 'null', '{"task": "", "released": []}'):
            with self.subTest(raw=raw):
                self.checkpoint.write_text(raw, encoding='utf-8')
                with self.assertRaises(TaskRecoveryFailed):
                    self.progress().begin(self.now)
                self.assertEqual(progress.path.read_bytes(), original)

    def test_atomic_write_failure_keeps_previous_confirmed_win(self):
        progress = self.progress()
        progress.seed_confirmed_wins(1)
        original = progress.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('replace blocked')):
            with self.assertRaises(TaskRecoveryFailed):
                progress.record_win()
        self.assertEqual(progress.path.read_bytes(), original)
        self.assertEqual(self.progress().completed_wins, 1)
        self.assertEqual(list(progress.path.parent.glob('*.tmp')), [])

    def test_gate_release_and_receipt_are_one_atomic_transition(self):
        gate = TaskCompletionGate(self.checkpoint)
        gate.retain('GoldYoukai')
        original = self.checkpoint.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('replace blocked')):
            with self.assertRaises(OSError):
                gate.release('GoldYoukai')
        self.assertEqual(self.checkpoint.read_bytes(), original)
        self.assertEqual(gate.task, 'GoldYoukai')
        self.assertEqual(gate.released, {})
        gate.release('GoldYoukai')
        disk = json.loads(self.checkpoint.read_text(encoding='utf-8'))
        self.assertEqual(disk['task'], '')
        self.assertIn('GoldYoukai', disk['released'])

    def test_other_task_release_and_disabled_release_do_not_fake_receipt(self):
        gate = TaskCompletionGate(self.checkpoint)
        gate.retain('GoldYoukai')
        gate.release('Restart')
        self.assertEqual(TaskCompletionGate(self.checkpoint).task, 'GoldYoukai')
        self.assertEqual(gate.released, {})
        gate.release()
        self.assertEqual(gate.released, {})
        self.assertFalse(self.checkpoint.exists())

    def test_gate_retains_both_receipts_across_another_business_task(self):
        gate = TaskCompletionGate(self.checkpoint)
        for name in ('GoldYoukai', 'ExperienceYoukai'):
            gate.retain(name)
            gate.release(name)
        receipts = dict(gate.released)
        restarted = TaskCompletionGate(self.checkpoint)
        restarted.retain('WantedQuests')
        restarted.release('WantedQuests')
        self.assertEqual(TaskCompletionGate(self.checkpoint).released, receipts)
        self.assertEqual(TaskCompletionGate(self.checkpoint).task, '')

    def test_disabled_round_release_preserves_older_receipts_without_faking_completion(self):
        gate = TaskCompletionGate(self.checkpoint)
        gate.retain('GoldYoukai')
        gate.release('GoldYoukai')
        receipts = dict(gate.released)
        gate.retain('GoldYoukai')
        gate.release()  # The removed/disabled task path has no completed_task argument.
        restored = TaskCompletionGate(self.checkpoint)
        self.assertEqual(restored.task, '')
        self.assertEqual(restored.released, receipts)

    def test_seed_is_idempotent_non_overwriting_and_has_no_scheduler_dependency(self):
        progress = self.progress()
        self.assertTrue(progress.seed_confirmed_wins(1))
        original = progress.path.read_bytes()
        self.assertFalse(self.progress().seed_confirmed_wins(1))
        self.assertEqual(progress.path.read_bytes(), original)
        progress.record_win()
        terminal = progress.path.read_bytes()
        with self.assertRaises(TaskRecoveryFailed):
            self.progress().seed_confirmed_wins(1)
        self.assertEqual(progress.path.read_bytes(), terminal)

    def test_invalid_names_and_seed_counts_cannot_create_progress_files(self):
        for profile in ('', '..', '../tomoe', 'a\\b', 'C:tomoe', 'CON', 'LPT9'):
            with self.subTest(profile=profile), self.assertRaises(TaskRecoveryFailed):
                TeamChallengeRoundProgress(profile, 'GoldYoukai', root=self.root,
                                           checkpoint_path=self.checkpoint)
        for name in ('../GoldYoukai', 'Nian', ''):
            with self.subTest(task=name), self.assertRaises(TaskRecoveryFailed):
                self.progress(name)
        for count in (0, 2, True, '1'):
            with self.subTest(count=count), self.assertRaises(TaskRecoveryFailed):
                self.progress().seed_confirmed_wins(count)
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()

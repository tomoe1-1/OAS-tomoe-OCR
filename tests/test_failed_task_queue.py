from datetime import datetime, timedelta, time
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from module.config.config import Config
from module.config.failed_tasks import FailedTaskStore
from module.script.task_completion import TaskCompletionGate
from tasks.Script.config_optimization import ScheduleRule


class FailedQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FailedTaskStore('test', self.temp.name)
        self.now = datetime.now().replace(microsecond=0)
        self.config = Config.__new__(Config)
        self.config.config_name = 'test'
        self.config.failed_task_store = self.store
        self.config.lock_config = threading.Lock()
        self.config.task = None
        self.config.scheduler_update_dt = None
        self.config.reload = Mock()
        self.config.save = Mock()
        self.config.model = SimpleNamespace(
            running_task='', script=SimpleNamespace(optimization=SimpleNamespace(schedule_rule=ScheduleRule.PRIORITY)),
            orochi=self.task(priority=1), wanted_quests=self.task(priority=15), daily_trifles=self.task(priority=10))
        self.config.model.dict = lambda: {
            key: value.model_dump() for key, value in vars(self.config.model).items()
            if hasattr(value, 'model_dump')}

    def task(self, priority=5):
        scheduler = SimpleNamespace(enable=True, next_run=self.now-timedelta(minutes=1), priority=priority,
            failure_interval=timedelta(hours=3), success_interval=timedelta(days=1),
            server_update=time(9), float_time=time(0))
        return SimpleNamespace(scheduler=scheduler, model_dump=lambda: {'scheduler': vars(scheduler).copy()})

    def test_due_main_wins_over_high_priority_failed_task(self):
        self.store.mark('Orochi', self.now-timedelta(seconds=1))
        self.assertEqual(self.config.get_next().command, 'DailyTrifles')
        data = self.config.get_schedule_data()
        self.assertEqual([f['name'] for f in data['failed']], ['Orochi'])
        self.assertNotIn('Orochi', [f['name'] for f in data['pending']+data['waiting']])
        self.assertEqual([f['name'] for f in data['pending']], ['WantedQuests'])

    def test_no_ready_main_selects_due_retry_before_future_main(self):
        self.store.mark('Orochi', self.now-timedelta(seconds=1))
        for task in (self.config.model.wanted_quests, self.config.model.daily_trifles):
            task.scheduler.next_run = self.now+timedelta(hours=1)
        self.assertEqual(self.config.get_next().command, 'Orochi')

    def test_future_retry_never_blocks_ready_main(self):
        self.store.mark('Orochi', self.now+timedelta(hours=3))
        self.assertEqual(self.config.get_next().command, 'DailyTrifles')

    def test_wakeup_uses_earliest_main_or_retry_time(self):
        self.store.mark('Orochi', self.now+timedelta(hours=3))
        self.config.model.daily_trifles.scheduler.next_run = self.now+timedelta(hours=1)
        self.config.model.wanted_quests.scheduler.next_run = self.now+timedelta(hours=5)
        self.assertEqual(self.config.get_next().next_run, self.now+timedelta(hours=1))
        self.config.model.daily_trifles.scheduler.next_run = self.now+timedelta(hours=4)
        self.assertEqual(self.config.get_next().command, 'Orochi')

    def test_failure_delay_records_and_success_clears_only_own_failure(self):
        self.config.task_delay('Orochi', success=False)
        self.config.task_delay('WantedQuests', success=False)
        retry_at = self.config.model.orochi.scheduler.next_run
        restored = FailedTaskStore('test', self.temp.name)
        self.assertEqual(restored.read()['Orochi'], retry_at)
        self.config.task_delay('Orochi', success=True, server=False)
        self.assertEqual(set(restored.read()), {'WantedQuests'})

    def test_repeated_failure_is_one_row_with_new_deadline(self):
        self.store.mark('Orochi', self.now+timedelta(hours=3))
        self.store.mark('Orochi', self.now+timedelta(hours=6))
        self.assertEqual(self.store.read(), {'Orochi': self.now+timedelta(hours=6)})

    def test_disabled_failed_task_is_not_scheduled(self):
        self.store.mark('Orochi', self.now-timedelta(seconds=1))
        self.config.model.orochi.scheduler.enable = False
        self.assertEqual(self.config.get_next().command, 'DailyTrifles')
        self.assertEqual(self.config.get_schedule_data()['failed'][0]['enabled'], False)

    def test_failed_completion_checkpoint_does_not_pin_main_queue(self):
        gate = TaskCompletionGate(Path(self.temp.name) / 'checkpoint.json')
        gate.retain('Orochi')
        gate.defer('Orochi', self.now+timedelta(hours=3))
        self.store.mark('Orochi', self.now+timedelta(hours=3))
        self.assertEqual(gate.select(self.config).command, 'DailyTrifles')
        self.assertEqual(gate.task, '')
        self.assertIn('Orochi', self.store.read())
        gate.retain('DailyTrifles')
        self.assertEqual(gate.task, 'DailyTrifles')

    def test_legacy_failed_checkpoint_is_migrated_once(self):
        self.store.legacy_path.parent.mkdir(parents=True)
        self.store.legacy_path.write_text(json.dumps({'task':'Orochi', 'not_before':self.now.isoformat()}))
        self.assertEqual(self.store.read(), {'Orochi': self.now})
        self.store.clear('Orochi')
        self.assertEqual(FailedTaskStore('test', self.temp.name).read(), {})

    def test_main_queue_head_is_not_lost_when_selected_retry_is_waiting(self):
        self.store.mark('Orochi', self.now+timedelta(hours=3))
        self.config.update_scheduler()
        self.config.task = self.config.failed_task[0]
        self.assertEqual(len(self.config.get_schedule_data()['pending']), 2)

    def test_user_can_move_failed_retry_earlier_or_later(self):
        from module.config.config_model import ConfigModel
        from tasks.Orochi.config import Orochi
        model = ConfigModel.model_construct(config_name='test', orochi=Orochi())
        self.config.model.orochi = model.orochi
        model.orochi.scheduler.enable = True
        self.store.mark('Orochi', self.now + timedelta(hours=3))
        for target in (self.now - timedelta(minutes=1), self.now + timedelta(hours=6)):
            with patch.object(ConfigModel, 'save'), patch('module.config.config_model.FailedTaskStore', return_value=self.store):
                self.assertTrue(model.script_set_arg('Orochi', 'scheduler', 'next_run', str(target)))
            self.config.update_scheduler()
            self.assertEqual(self.config.get_schedule_data()['failed'][0]['next_run'], str(target))
            self.assertEqual(self.store.read()['Orochi'], target)
        self.assertEqual(set(self.store.read()), {'Orochi'})

    def test_user_edit_does_not_mark_normal_task_as_failed(self):
        self.assertFalse(self.store.reschedule('Orochi', self.now))
        self.assertEqual(self.store.read(), {})

    def test_invalid_user_time_keeps_failure_deadline(self):
        from module.config.config_model import ConfigModel
        from tasks.Orochi.config import Orochi
        model = ConfigModel.model_construct(config_name='test', orochi=Orochi())
        target = self.now + timedelta(hours=3)
        self.store.mark('Orochi', target)
        with patch.object(ConfigModel, 'save'), patch('module.config.config_model.FailedTaskStore', return_value=self.store):
            self.assertFalse(model.script_set_arg('Orochi', 'scheduler', 'next_run', 'invalid-time'))
        self.assertEqual(self.store.read()['Orochi'], target)

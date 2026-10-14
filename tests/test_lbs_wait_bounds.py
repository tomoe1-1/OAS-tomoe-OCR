"""Replay passenger/room waits against the configured deadline without input."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tasks.LBS.script_task import ScriptTask


class LbsWaitBoundsTests(unittest.TestCase):
    def subject(self):
        task = ScriptTask.__new__(ScriptTask)
        task.create_room = Mock(return_value=True)
        task.ensure_public = Mock()
        task.create_ensure = Mock()
        task.exit_room = Mock(return_value=True)
        task.room_check_can_fire = Mock(return_value=False)
        clock = SimpleNamespace(now=0)
        deadline = SimpleNamespace(reached=lambda: clock.now >= 3)
        return task, clock, deadline

    def test_no_passenger_wait_stops_at_the_outer_limit_and_leaves_room(self):
        task, clock, deadline = self.subject()
        def in_room():
            clock.now += 1
            return True
        task.is_in_room = Mock(side_effect=in_room)
        task._run_drive_mode(deadline, 30)
        self.assertEqual(clock.now, 3)
        task.exit_room.assert_called_once()
        task.create_room.assert_not_called()
        self.assertEqual(task.room_check_can_fire.call_count, 2)

    def test_room_entry_wait_also_stops_when_the_outer_limit_expires(self):
        task, clock, deadline = self.subject()
        def outside():
            clock.now += 1
            return False
        task.is_in_room = Mock(side_effect=outside)
        entry_timer = Mock(reached=Mock(return_value=False))
        entry_timer.start.return_value = entry_timer
        with patch('tasks.LBS.script_task.Timer', return_value=entry_timer):
            task._run_drive_mode(deadline, 30)
        self.assertEqual(clock.now, 3)
        task.create_room.assert_called_once()
        task.exit_room.assert_called_once()
        task.room_check_can_fire.assert_not_called()


if __name__ == '__main__':
    unittest.main()

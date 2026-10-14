import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class RealmRaidTicketCompletionTests(unittest.TestCase):
    def test_verified_ticket_exhaustion_completes_task(self):
        tree = ast.parse((ROOT / 'tasks/RealmRaid/script_task.py').read_text(encoding='utf-8'))
        original = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        run = next(node for node in original.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        subject = ast.ClassDef(name='Subject', bases=[], keywords=[], body=[run], decorator_list=[])
        namespace = {'TaskEnd': StopIteration, 'page_realm_raid': 'raid', 'page_exploration': 'exploration'}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[subject], type_ignores=[])), 'realm', 'exec'), namespace)
        task = namespace['Subject']()
        task.config = SimpleNamespace(realm_raid=SimpleNamespace(raid_config=SimpleNamespace(number_base=0)))
        task.goto_page = Mock()
        task.close_raid_reward_overlay = Mock(return_value=False)
        task.check_ticket = Mock(return_value=False)
        task.set_next_run = Mock()

        with self.assertRaises(StopIteration):
            task.run()

        self.assertEqual(task.goto_page.call_args_list[-1].args, ('exploration',))
        task.set_next_run.assert_called_once_with(task='RealmRaid', success=True, finish=True)


if __name__ == '__main__':
    unittest.main()

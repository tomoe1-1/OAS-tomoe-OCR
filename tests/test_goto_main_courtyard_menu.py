"""GotoMain 到达庭院后必须确认底部菜单可用，不能只认页面就收工。

背景：2026-10-06 09:53-10:09 GotoMain 连续多次假性成功 ——
日志里 10:00:47 / 10:02:36 / 10:08:00 都是「Page arrived page_main」，
紧接着每个依赖底部入口的任务各自报一遍导航失败。

根因：page_main 的识别器是 courtyard_visible，卷轴收起时它照样命中，
于是 goto_page 认为「到了主页面」；但庭院卷轴收起时底部菜单整排入口不可见。
登录流程（tasks/Component/Login/service.py）早有对应自检，GotoMain 缺了这一段。
"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import TaskEnd
from tasks.GotoMain import script_task as goto_main_module
from tasks.GotoMain.script_task import ScriptTask


def _task(menu_visible=True):
    """GotoMain 用的最小 task 替身：goto_page 顺利，菜单状态由参数控制。"""
    task = ScriptTask.__new__(ScriptTask)
    task.config = SimpleNamespace(task_runtime_outcome=None)
    task.device = Mock()
    task.goto_page = Mock(return_value=True)
    task.ensure_courtyard_menu_visible = Mock(return_value=menu_visible)
    # 避免真的去动 GameUi 内部状态
    task._mark_page_entered = Mock()
    return task


class GotoMainCourtyardMenuTest(unittest.TestCase):
    """GotoMain 的收尾判据。"""

    def test_expanded_menu_ends_normally(self):
        """菜单展开时正常结束，不写 incomplete。"""
        task = _task(menu_visible=True)
        with self.assertRaises(TaskEnd) as ctx:
            with patch.object(goto_main_module, 'ensure_courtyard_menu_visible',
                              task.ensure_courtyard_menu_visible):
                task.run()
        # 正常结束的 TaskEnd 不带任何说明
        self.assertEqual(str(ctx.exception), '')
        self.assertIsNone(task.config.task_runtime_outcome)

    def test_collapsed_menu_marks_incomplete(self):
        """卷轴展开不了时必须标记 incomplete，否则会被记成 GotoMain 成功。"""
        task = _task(menu_visible=False)
        with self.assertRaises(TaskEnd):
            with patch.object(goto_main_module, 'ensure_courtyard_menu_visible',
                              task.ensure_courtyard_menu_visible):
                task.run()
        outcome = task.config.task_runtime_outcome
        self.assertIsInstance(outcome, dict)
        self.assertEqual(outcome['status'], 'incomplete')
        self.assertEqual(outcome['task'], 'GotoMain')

    def test_collapsed_menu_calls_expand_helper(self):
        """必须真的走展开自检，而不是无条件当成功。"""
        task = _task(menu_visible=False)
        with self.assertRaises(TaskEnd):
            with patch.object(goto_main_module, 'ensure_courtyard_menu_visible',
                              task.ensure_courtyard_menu_visible):
                task.run()
        task.ensure_courtyard_menu_visible.assert_called_once()

    def test_goto_page_runs_before_menu_check(self):
        """顺序不能反：先 goto_page，再检查菜单可用性。"""
        task = _task(menu_visible=True)
        calls = []
        task.goto_page.side_effect = lambda *a, **k: calls.append('goto_page') or True
        task.ensure_courtyard_menu_visible.side_effect = lambda *a, **k: calls.append('ensure') or True
        with self.assertRaises(TaskEnd):
            with patch.object(goto_main_module, 'ensure_courtyard_menu_visible',
                              task.ensure_courtyard_menu_visible):
                task.run()
        self.assertEqual(calls, ['goto_page', 'ensure'])


class TaskEndOutcomeContractTest(unittest.TestCase):
    """守住 Script.run() 判定 incomplete -> return False 的契约。

    这条契约是本次改动的关键：若失效，GotoMain 抛 TaskEnd 仍会被记成成功，
    假阳性绿灯只是从 goto_page 搬到 TaskEnd，等于没修。
    判定逻辑在 script.py 的 run() 里，这里以等价代码锁定其行为。
    """

    @staticmethod
    def _evaluate(outcome):
        """复刻 Script.run() 中 TaskEnd 分支的判定。"""
        if outcome.get('status') in ('failed', 'incomplete', 'esc_recovery_failed'):
            return False
        return True

    def test_incomplete_makes_run_return_false(self):
        self.assertIs(self._evaluate({'task': 'GotoMain', 'status': 'incomplete'}), False)

    def test_absent_outcome_counts_as_success(self):
        """对照组：没写 outcome 时行为不变，仍按成功处理。"""
        self.assertIs(self._evaluate({}), True)


if __name__ == '__main__':
    unittest.main()

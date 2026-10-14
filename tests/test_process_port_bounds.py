"""Offline regressions for GUI port allocation without starting GUI or processes."""
import ast
from pathlib import Path
import random
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'module/gui/context/process_manager.py'


def load_subject():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in ('is_port_in_use', 'find_available_port')]
    manager = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'ProcessManager')
    add = next(node for node in manager.body if isinstance(node, ast.FunctionDef)
               and node.name == 'add')
    add.decorator_list = []
    nodes.append(add)
    namespace = dict(socket=socket, random=random, logger=Mock())
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(SOURCE), 'exec'), namespace)
    return namespace


class FakeSocket:
    def __init__(self, connect_error=None, timeout_error=None):
        self.connect_error = connect_error
        self.timeout_error = timeout_error
        self.closed = False
        self.timeout = None
        self.address = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        self.timeout = timeout
        if self.timeout_error:
            raise self.timeout_error

    def connect(self, address):
        self.address = address
        if self.connect_error:
            raise self.connect_error


class ProcessPortBoundsTests(unittest.TestCase):
    def setUp(self):
        self.subject = load_subject()

    def test_all_busy_ports_are_probed_once_and_exhaustion_is_finite(self):
        probe = Mock(return_value=True)
        self.subject['is_port_in_use'] = probe
        with self.assertRaisesRegex(RuntimeError, '40000-40200'):
            self.subject['find_available_port']('127.0.0.1', [])
        ports = [call.args[1] for call in probe.call_args_list]
        self.assertEqual(len(ports), 201)
        self.assertEqual(set(ports), set(range(40000, 40201)))

    def test_reserved_ports_are_never_probed_even_when_not_listening(self):
        probe = Mock(return_value=False)
        self.subject['is_port_in_use'] = probe
        with self.assertRaises(RuntimeError):
            self.subject['find_available_port']('127.0.0.1', range(40000, 40201))
        probe.assert_not_called()

    def test_mixed_reserved_and_busy_ports_exhaust_without_duplicate_probes(self):
        probe = Mock(return_value=True)
        self.subject['is_port_in_use'] = probe
        reserved = {40000, 40001, 40200}
        with self.assertRaises(RuntimeError):
            self.subject['find_available_port']('127.0.0.1', reserved)
        ports = [call.args[1] for call in probe.call_args_list]
        self.assertEqual(len(ports), 198)
        self.assertEqual(set(ports), set(range(40000, 40201)) - reserved)

    def test_selection_returns_the_only_free_unreserved_port(self):
        probe = Mock(side_effect=lambda ip, port: port != 40100)
        self.subject['is_port_in_use'] = probe
        with patch.object(random, 'shuffle', side_effect=lambda candidates: candidates.reverse()):
            port = self.subject['find_available_port']('127.0.0.1', [40000, 40200])
        self.assertEqual(port, 40100)
        ports = [call.args[1] for call in probe.call_args_list]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertFalse({40000, 40200} & set(ports))
        self.assertEqual(ports[-1], 40100)

    def test_probe_closes_socket_and_bounds_connect_on_success_and_os_errors(self):
        for error, expected in ((None, True), (ConnectionRefusedError(), False),
                                (socket.timeout(), False)):
            with self.subTest(error=error):
                connection = FakeSocket(connect_error=error)
                with patch.object(socket, 'socket', return_value=connection):
                    self.assertIs(self.subject['is_port_in_use']('127.0.0.1', 40000), expected)
                self.assertTrue(connection.closed)
                self.assertEqual(connection.timeout, 0.5)
                self.assertEqual(connection.address, ('127.0.0.1', 40000))

    def test_probe_closes_socket_and_propagates_unexpected_errors(self):
        for kwargs in (dict(connect_error=ValueError('bad address')),
                       dict(timeout_error=ValueError('bad timeout'))):
            with self.subTest(kwargs=kwargs):
                connection = FakeSocket(**kwargs)
                with patch.object(socket, 'socket', return_value=connection):
                    with self.assertRaises(ValueError):
                        self.subject['is_port_in_use']('127.0.0.1', 40000)
                self.assertTrue(connection.closed)

    def manager_subject(self):
        manager = SimpleNamespace(processes={}, ports={}, clients={}, log_thread={}, update_queue=None)
        manager.start_log = Mock()
        return manager

    def test_add_exhaustion_leaves_all_process_state_unchanged(self):
        manager = self.manager_subject()
        manager.ports = {'existing': 40000}
        probe = Mock(return_value=True)
        self.subject['is_port_in_use'] = probe
        self.subject['ScriptProcess'] = Mock()
        self.subject['zerorpc'] = SimpleNamespace(Client=Mock())
        with self.assertRaises(RuntimeError):
            self.subject['add'](manager, 'new')
        self.assertEqual(manager.ports, {'existing': 40000})
        self.assertEqual(manager.processes, {})
        self.assertEqual(manager.clients, {})
        manager.start_log.assert_not_called()
        self.subject['ScriptProcess'].assert_not_called()
        self.subject['zerorpc'].Client.assert_not_called()
        self.assertEqual(probe.call_count, 200)

    def test_add_assigns_only_a_free_unreserved_port(self):
        manager = self.manager_subject()
        manager.ports = {'existing': 40000}
        manager.log_thread['new'] = Mock()
        process = Mock()
        client = Mock()
        self.subject['ScriptProcess'] = Mock(return_value=process)
        self.subject['zerorpc'] = SimpleNamespace(Client=Mock(return_value=client))
        probe = Mock(side_effect=lambda ip, port: port != 40001)
        self.subject['is_port_in_use'] = probe
        with patch.object(random, 'shuffle', return_value=None):
            self.subject['add'](manager, 'new')
        self.assertEqual(manager.ports, {'existing': 40000, 'new': 40001})
        self.assertIs(manager.processes['new'], process)
        self.assertIs(manager.clients['new'], client)
        probe.assert_called_once_with('127.0.0.1', 40001)
        client.connect.assert_called_once_with('tcp://127.0.0.1:40001')
        process.start.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

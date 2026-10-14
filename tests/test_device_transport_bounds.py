"""Replay device transport stalls without network, emulator or control input."""
import ast
import asyncio
from contextlib import nullcontext
from pathlib import Path
import socket
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]


class TransportError(Exception):
    pass


class Clock:
    def __init__(self):
        self.now = 0

    def sleep(self, seconds):
        self.now += max(1, seconds)

    def timer(self, limit):
        clock = self

        class Deadline:
            def start(self):
                self.started = clock.now
                return self

            def reached(self):
                return clock.now - self.started >= limit

        return Deadline()


def extracted(path, method, namespace, *, http=False, function=False):
    tree = ast.parse((ROOT / path).read_text(encoding='utf8'))
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                  and node.name == method]
    if http:
        node = next(node for node in candidates if any(
            isinstance(decorator, ast.Call) and any(
                keyword.arg == 'DEVICE_OVER_HTTP' and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True for keyword in decorator.keywords)
            for decorator in node.decorator_list))
    else:
        node = candidates[0]
    node.decorator_list = []
    body = [node] if function else [ast.ClassDef(name='Subject', bases=[], keywords=[],
                                                decorator_list=[], body=[node])]
    module = ast.Module(body=body, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace[method] if function else namespace['Subject']()


class MinitouchServiceBoundsTests(unittest.TestCase):
    def subject(self, mode='normal', handshake=False):
        clock = Clock()
        service = SimpleNamespace(phase='stop', stop=Mock(), start=Mock())
        service.stop.side_effect = lambda: setattr(service, 'phase', 'stop')
        service.start.side_effect = lambda: setattr(service, 'phase', 'start')
        service.running = Mock(side_effect=lambda:
                               mode == 'stop_stuck' if service.phase == 'stop'
                               else mode != 'start_stuck')
        websocket = SimpleNamespace(recv=AsyncMock(return_value='ready'))
        if handshake:
            async def never_message():
                await asyncio.Future()
            websocket.recv.side_effect = never_message
        connect = AsyncMock(return_value=websocket)
        received_timeouts = []

        def wait_for(event, timeout):
            received_timeouts.append(timeout)
            # Keep the real cancellation behavior while using a short test clock.
            return asyncio.wait_for(event, timeout=.005)

        obj = extracted('module/device/method/minitouch.py', 'minitouch_init', {
            'logger': Mock(), 'Timer': clock.timer, 'U2Service': lambda *args: service,
            're': __import__('re'), 'websockets': SimpleNamespace(connect=connect),
            'asyncio': SimpleNamespace(wait_for=wait_for, TimeoutError=asyncio.TimeoutError),
            'MinitouchOccupiedError': TransportError}, http=True)
        obj.serial, obj.u2 = 'http://127.0.0.1:7912', object()
        obj.get_orientation = Mock()
        obj.sleep = clock.sleep
        obj._minitouch_loop_run = asyncio.run
        return obj, service, connect, websocket, clock, received_timeouts

    def test_service_that_never_stops_has_a_10_second_deadline(self):
        obj, service, connect, _, clock, _ = self.subject('stop_stuck')
        with self.assertRaisesRegex(TransportError, 'did not stop'):
            obj.minitouch_init()
        self.assertEqual(clock.now, 10)
        service.stop.assert_called_once()
        service.start.assert_not_called()
        connect.assert_not_called()

    def test_service_that_never_starts_has_a_10_second_deadline(self):
        obj, service, connect, _, clock, _ = self.subject('start_stuck')
        with self.assertRaisesRegex(TransportError, 'did not start'):
            obj.minitouch_init()
        self.assertEqual(clock.now, 10)
        service.start.assert_called_once()
        connect.assert_not_called()

    def test_normal_service_and_two_message_handshake_complete(self):
        obj, service, connect, websocket, _, timeouts = self.subject()
        obj.minitouch_init()
        service.stop.assert_called_once()
        service.start.assert_called_once()
        connect.assert_awaited_once_with('ws://127.0.0.1:7912/minitouch')
        self.assertEqual(websocket.recv.await_count, 2)
        self.assertIs(obj._minitouch_ws, websocket)
        self.assertEqual(timeouts, [10])

    def test_silent_websocket_handshake_enters_existing_retry_error(self):
        obj, _, _, _, _, timeouts = self.subject(handshake=True)
        with self.assertRaisesRegex(TransportError, 'websocket handshake'):
            obj.minitouch_init()
        self.assertEqual(timeouts, [10])


class ScrcpyTransportBoundsTests(unittest.TestCase):
    def server(self, alive):
        clock = Clock()
        video = Mock()
        video.recv.side_effect = [b'\0', b'emulator', struct.pack('>HH', 1280, 720)]
        thread = SimpleNamespace(start=Mock(), is_alive=Mock(return_value=alive))
        obj = extracted('module/device/method/scrcpy/core.py', '_scrcpy_server_start', {
            'logger': Mock(), 'Timer': clock.timer, 'sleep': clock.sleep,
            'ScrcpyOptions': SimpleNamespace(command_v120=lambda **kwargs: ['server']),
            'Network': SimpleNamespace(LOCAL_ABSTRACT='local'), 'struct': struct,
            'threading': SimpleNamespace(Thread=lambda **kwargs: thread),
            'AdbError': TransportError, 'ScrcpyError': TransportError})
        obj.config = SimpleNamespace(SCRCPY_FILEPATH_REMOTE='server.jar')
        stream = SimpleNamespace(read=Mock(return_value=b'[server] I'))
        obj.adb = SimpleNamespace(shell=Mock(return_value=stream),
                                  create_connection=Mock(side_effect=[video, Mock()]))
        obj._scrcpy_receive_from_server_stream = Mock(return_value=b' ready')
        obj._scrcpy_stream_loop = Mock()
        obj.sleep = clock.sleep
        return obj, thread, clock

    def capture(self, alive=True, new_frame=False):
        clock = Clock()
        thread = SimpleNamespace(is_alive=Mock(return_value=alive))
        frame = SimpleNamespace(copy=Mock(return_value='fresh image'))
        fake_time = SimpleNamespace(monotonic=lambda: clock.now, time=lambda: 1000 + clock.now)
        obj = extracted('module/device/method/scrcpy/scrcpy.py', 'screenshot_scrcpy', {
            'time': fake_time, 'ScrcpyError': TransportError})
        obj._scrcpy_resolution_check, obj.scrcpy_ensure_running = Mock(), Mock()
        obj._scrcpy_control_socket_lock = nullcontext()
        obj._scrcpy_stream_loop_thread = thread
        obj._scrcpy_last_frame_time, obj._scrcpy_last_frame = 0, frame

        def sleep(seconds):
            clock.sleep(seconds)
            if new_frame and clock.now >= 2:
                obj._scrcpy_last_frame_time = 1000 + clock.now
        fake_time.sleep = sleep
        return obj, frame, clock

    def test_video_thread_that_exits_before_observed_alive_fails_finitely(self):
        obj, thread, clock = self.server(alive=False)
        with self.assertRaisesRegex(TransportError, 'thread did not start'):
            obj._scrcpy_server_start()
        self.assertEqual(clock.now, 3)
        thread.start.assert_called_once()

    def test_normal_video_thread_start_is_accepted(self):
        obj, thread, clock = self.server(alive=True)
        obj._scrcpy_server_start()
        thread.start.assert_called_once()
        self.assertTrue(obj._scrcpy_alive)
        self.assertEqual(clock.now, 0)

    def test_live_decoder_without_new_frames_fails_after_10_seconds(self):
        obj, frame, clock = self.capture()
        with self.assertRaisesRegex(TransportError, 'no new frame'):
            obj.screenshot_scrcpy()
        self.assertEqual(clock.now, 10)
        frame.copy.assert_not_called()

    def test_new_frame_returns_without_using_a_stale_image(self):
        obj, frame, clock = self.capture(new_frame=True)
        self.assertEqual(obj.screenshot_scrcpy(), 'fresh image')
        frame.copy.assert_called_once()
        self.assertEqual(clock.now, 2)

    def test_dead_decoder_fails_immediately(self):
        obj, frame, clock = self.capture(alive=False)
        with self.assertRaisesRegex(TransportError, 'thread died'):
            obj.screenshot_scrcpy()
        frame.copy.assert_not_called()
        self.assertEqual(clock.now, 1)


class ReceiveBudgetTests(unittest.TestCase):
    def subject(self, chunks=None, idle=False):
        clock = Clock()
        state = dict(timeout=None, reads=0)
        stream = Mock()
        def settimeout(value):
            state['timeout'] = value
        def receive(size):
            state['reads'] += 1
            clock.now += min(1, state['timeout'])
            if idle:
                raise socket.timeout()
            return b'data' if chunks is None else chunks.pop(0)
        stream.settimeout.side_effect, stream.recv.side_effect = settimeout, receive
        class AdbStream:
            def __init__(self, connection):
                self.conn = connection
        func = extracted('module/device/method/utils.py', 'recv_all', {
            'time': SimpleNamespace(monotonic=lambda: clock.now,
                                    sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)),
            'socket': socket, '_AdbStreamConnection': AdbStream,
            'AdbTimeout': TransportError, 'remove_shell_warning': lambda data: data}, function=True)
        return func, stream, AdbStream, clock, state

    def test_continuously_arriving_data_cannot_renew_total_deadline(self):
        func, stream, _, clock, state = self.subject()
        with self.assertRaisesRegex(TransportError, 'total read timeout'):
            func(stream)
        self.assertEqual(clock.now, 30)
        self.assertEqual(state['reads'], 30)
        self.assertLessEqual(state['timeout'], 1)

    def test_finite_receive_still_returns_all_fragments(self):
        func, stream, _, clock, _ = self.subject([b'a', b'b', b''])
        self.assertEqual(func(stream), b'ab')
        self.assertEqual(clock.now, 3)

    def test_socket_idle_timeout_keeps_the_existing_adb_error(self):
        func, stream, _, _, _ = self.subject(idle=True)
        with self.assertRaisesRegex(TransportError, 'adb read timeout'):
            func(stream)

    def test_adb_stream_wrapper_and_explicit_budget_are_supported(self):
        func, stream, AdbStream, clock, _ = self.subject()
        with self.assertRaises(TransportError):
            func(AdbStream(stream), total_timeout=3)
        self.assertEqual(clock.now, 3)


if __name__ == '__main__':
    unittest.main()

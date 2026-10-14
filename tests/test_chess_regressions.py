"""Offline regressions for timed Chess rounds and expired image frames."""
import ast
from functools import wraps
import inspect
from pathlib import Path
import pickle
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from unittest.mock import Mock

import numpy as np
import zerorpc

ROOT = Path(__file__).resolve().parents[1]


class VirtualClock:
    """Advancing fake time.

    The click fences hold 8-second budgets behind ``while time.monotonic() <
    deadline`` loops, so a frozen clock would hang the suite instead of
    failing it. Every ``monotonic()`` moves forward and ``sleep()`` accumulates.
    """

    def __init__(self):
        self.now = 0.

    def monotonic(self):
        value = self.now
        self.now += .2
        return value

    def sleep(self, seconds):
        self.now += max(float(seconds), .01)

    def perf_counter(self):
        return self.monotonic()


def extract(path, class_name, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    if class_name:
        tree = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in nodes:
        # Resolve type hints lazily, as the production RPC module does.
        if class_name != 'ImageClient':
            node.decorator_list = []
    prefix = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    code = ast.fix_missing_locations(ast.Module(body=[prefix] + nodes, type_ignores=[]))
    exec(compile(code, path, 'exec'), namespace)
    return namespace


class ChessRegressionTests(unittest.TestCase):
    def test_thirteen_card_scan_survives_actual_server_cache_expiry(self):
        clock = SimpleNamespace(now=0.)
        ns = dict(time=SimpleNamespace(time=lambda: clock.now), pickle=pickle, np=np, logger=Mock())
        names = ['_resolve_image', '_get_frame_entry', '_cleanup_frames', '_delete_frame']
        extract('module/image/runtime.py', 'ImageRuntime', names, ns)
        runtime = type('Runtime', (), {name: ns[name] for name in names})()
        pixels = np.zeros((3, 13, 3), dtype=np.uint8)
        runtime._frames = {'hand': SimpleNamespace(image=pixels, config_name='test',
            created_at=0., last_access_at=0., expire_seconds=3.)}
        runtime._config_frames = {'test': 'hand'}
        runtime._lock = threading.RLock()
        runtime._cache_stats = dict(frame_misses=0, frame_hits=0, frame_evictions=0, frame_expired=0)
        runtime.settings = SimpleNamespace(frame_cache_max_count=24)
        def match(rule, frame, payload, threshold):
            return runtime._resolve_image(frame, payload)
        client = self.image_client()
        client.client = SimpleNamespace(match_rule=match)
        for card in range(13):
            # Local OCR/template classification can spend >3s between RPCs.
            clock.now = card * 3.5
            np.testing.assert_array_equal(client.match_rule({}, pixels, 'hand'), pixels)
        self.assertGreater(runtime._cache_stats['frame_misses'], 0)
        self.assertEqual(runtime._cache_stats['frame_evictions'], 1)

    def image_client(self):
        ns = dict(inspect=inspect, wraps=wraps, pickle=pickle, logger=Mock(), zerorpc=zerorpc)
        extract('module/image/rpc.py', None, ['_retry_expired_frame'], ns)
        names = ['_encode_image_payload', 'match_rule', 'match_rule_with_brightness_window',
                 'match_many', 'match_all', 'match_all_any', 'match_all_any_many', 'match_dynamic_template']
        extract('module/image/rpc.py', 'ImageClient', names, ns)
        cls = type('Subject', (), {n: ns[n] for n in names})
        return cls()

    def test_all_image_endpoints_recover_expired_frame_without_changing_image(self):
        pixels = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
        for name in ('match_rule', 'match_rule_with_brightness_window', 'match_many',
                     'match_all', 'match_all_any', 'match_all_any_many', 'match_dynamic_template'):
            for error in (KeyError('Unknown frame id: expired'),
                          zerorpc.RemoteError('KeyError', "'Unknown frame id: expired'", 'trace')):
                with self.subTest(endpoint=name, error=type(error).__name__):
                    d = self.image_client()
                    remote = Mock(side_effect=[error, {'matches': list(range(13))}])
                    d.client = SimpleNamespace(**{name: remote})
                    first = pixels if name == 'match_dynamic_template' else {}
                    result = getattr(d, name)(first, image=pixels, frame_id='expired')
                    self.assertEqual(len(result['matches']), 13)
                    self.assertEqual(remote.call_count, 2)
                    initial, retry = [call.args for call in remote.call_args_list]
                    self.assertEqual(initial[1:3], ('expired', None))
                    self.assertIsNone(retry[1])
                    np.testing.assert_array_equal(pickle.loads(retry[2]), pixels)
                    self.assertEqual(initial[3:], retry[3:])

    def test_frame_retry_is_bounded_and_only_for_matching_cache_miss(self):
        d = self.image_client()
        image = np.zeros((3, 3, 3), dtype=np.uint8)
        for error, frame, pixels, calls in (
            (KeyError('Unknown frame id: old'), 'old', image, 2),
            (KeyError('Unknown frame id: another'), 'old', image, 1),
            (KeyError('missing template'), 'old', image, 1),
            (RuntimeError('offline'), 'old', image, 1),
            (KeyError('Unknown frame id: old'), 'old', None, 1),
        ):
            d.client = SimpleNamespace(match_rule=Mock(side_effect=error))
            with self.assertRaises(type(error)):
                d.match_rule({}, pixels, frame)
            self.assertEqual(d.client.match_rule.call_count, calls)

    def test_cached_frame_success_still_avoids_upload(self):
        d = self.image_client()
        d.client = SimpleNamespace(match_rule=Mock(return_value={'matched': True}))
        d.match_rule({}, np.zeros((3, 3, 3)), 'active')
        d.client.match_rule.assert_called_once_with({}, 'active', None, None)

    def test_round_preserves_timing_flag_and_clears_stuck_timer(self):
        ns = extract('tasks/Chess/script_task.py', 'ChessLegacyScriptTask', ['_run_round_loop'], {})
        for initial in (True, False):
            for error in (None, RuntimeError('failed round')):
                device = SimpleNamespace(_time_critical_clicks=initial, stuck_record_clear=Mock())
                def run():
                    self.assertIs(device._time_critical_clicks, initial)
                    if error:
                        raise error
                task = SimpleNamespace(device=device, _run_game_by_rounds_without_device_stuck_timeout=run)
                if error:
                    with self.assertRaises(RuntimeError):
                        ns['_run_round_loop'](task)
                else:
                    ns['_run_round_loop'](task)
                self.assertIs(device._time_critical_clicks, initial)
                self.assertEqual(device.stuck_record_clear.call_count, 2)

    def test_timed_click_avoids_long_rest_other_tasks_keep_it(self):
        ns = dict(logger=Mock(), ensure_int=lambda x, y: (x, y), sampled_width=lambda p: 10,
                  sample_dwell=lambda **kw: .1, time=SimpleNamespace(perf_counter=lambda: 0),
                  point2str=lambda x, y: str((x, y)),
                  # Module-level helpers the extracted Control.click body calls.
                  # before_click gained an exploration_retry keyword; accept **kw
                  # so this stub keeps working when callers pass new flags.
                  before_click=lambda device, x, y, name, **kw: None,
                  after_click=lambda device, x, y, name, before, **kw: None)
        extract('module/device/control.py', 'Control', ['click'], ns)
        for timed in (False, True):
            d = SimpleNamespace(_time_critical_clicks=timed, handle_control_check=Mock(return_value=True),
                _invalidate_image_batch_cache=Mock(), human_click_session=SimpleNamespace(
                    before_click=Mock(), after_click=Mock(return_value=50.), rng=None),
                config=SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method='minitouch'))),
                long_click_methods={'minitouch': Mock()}, long_click_adb=Mock(),
                _format_action_duration=str, sleep=Mock(), stuck_record_clear=Mock())
            ns['click'](d, 10, 20)
            d.long_click_methods['minitouch'].assert_called_once_with(10, 20, .1)
            self.assertEqual(d.sleep.call_count, 0 if timed else 1)

    def test_experience_unknown_ocr_blocks_without_advancing(self):
        ns = extract('tasks/Chess/runtime/economy.py', 'ChessEconomyMixin',
                     ['_run_economy_atomic_batch'], dict(logger=Mock()))
        for initial_mode in (False, True):
            for result in ('unknown', 'no_progress', 'success'):
                with self.subTest(result=result, battle_mode=initial_mode):
                    d = SimpleNamespace(_economy_pending=True, _economy_battle_mode=initial_mode,
                        _is_purchase_allowed=lambda: True, _read_level=lambda: 5,
                        _read_shop_gold=lambda: 71, _next_economy_operation=lambda level: 'experience',
                        _can_execute_economy_operation=lambda *args: True,
                        _click_economy_button_and_confirm_gold=Mock(return_value=result),
                        _advance_economy_operation_counter=Mock(),
                        _ensure_shop_open=Mock(), purchase_lineup_cards_once=Mock(),
                        I_EXPERIENCE=object(), EXPERIENCE_COST=4)
                    self.assertEqual(ns['_run_economy_atomic_batch'](d),
                                     'pending' if result == 'success' else 'blocked')
                    self.assertEqual(d._advance_economy_operation_counter.call_count,
                                     int(result == 'success'))
                    self.assertTrue(d._economy_pending)
                    self.assertIs(d._economy_battle_mode, initial_mode)
                    d._ensure_shop_open.assert_not_called()
                    d.purchase_lineup_cards_once.assert_not_called()

    def experience_subject(self, initial=None, pre=(), post=(), remaining=60,
                           static_image=False):
        """Drive the real upgrade entry point; only image and input are replaced.

        ``pre``/``post`` are timelines of *task-level* observations (one entry
        per ``self.screenshot()``). Device-level refreshes driven by the click
        fence do not consume them, so a change in how many frames the fence
        takes cannot silently shift what a test case means.

        The dispatch target is the production ``C_EXPERIENCE_CENTER`` rule and
        the before/after fences are the real ones, so permit consumption,
        replay fencing and receipt identity are all exercised.
        """
        import module.device.click_verification as verification
        from module.device.click_verification import ClickResponseUnchanged
        from tasks.Chess.runtime.economy import ChessEconomyMixin

        clock = VirtualClock()
        patcher = mock.patch.object(verification, 'time', clock)
        patcher.start()
        self.addCleanup(patcher.stop)

        ns = dict(logger=Mock(), time=clock, ClickResponseUnchanged=ClickResponseUnchanged,
                  before_click=verification.before_click,
                  after_click=verification.after_click,
                  signature=verification.signature)
        extract('tasks/Chess/runtime/economy.py', 'ChessEconomyMixin',
                ['_experience_context', '_dispatch_experience_guarded',
                 '_click_economy_button_and_confirm_gold'], ns)

        shape = (720, 1280, 3)
        frame = dict(round=19, mode='备', gold=71, level=5, experience=None,
                     grigri=False, use_soul=False, button=True, choices=())
        frame.update(initial or {})
        pre, post = list(pre), list(post)
        device = SimpleNamespace(image_frame_id=0, _escape_generation=0,
                                 _accepted_invitation_generation=0,
                                 _handling_invitation=False, _verified_click=None)
        device.image = np.zeros(shape, dtype=np.uint8)
        scene = [0]
        applied = {'pre': 0, 'post': 0}

        def apply(update):
            if 'id' not in update:
                device.image_frame_id += 1
            for name, value in update.items():
                if name == 'id':
                    device.image_frame_id = value
                elif name == 'generation':
                    device._escape_generation = value
                elif name == 'invitation':
                    device._handling_invitation = value
                elif name == 'accepted':
                    device._accepted_invitation_generation = value
                else:
                    frame[name] = value

        def refresh():
            if not static_image:
                scene[0] += 1
                device.image = np.full(shape, (scene[0] * 53) % 256, np.uint8)

        def observe():
            refresh()
            if device._verified_click is not None:
                update = post[applied['post']] if applied['post'] < len(post) else {}
                applied['post'] += 1
            else:
                update = pre[applied['pre']] if applied['pre'] < len(pre) else {}
                applied['pre'] += 1
            apply(update)

        def dispatch(x, y, control_check=True, control_name='Click'):
            # The real Control.click returns None after a successful dispatch.
            applied['post'] = 0
            return None

        device.screenshot = Mock(side_effect=refresh)
        device.click = Mock(side_effect=dispatch)
        device.handle_control_check = Mock(return_value=True)

        button = object()
        d = SimpleNamespace(
            device=device, chess_state=SimpleNamespace(game_number=1),
            C_EXPERIENCE_CENTER=ChessEconomyMixin.C_EXPERIENCE_CENTER,
            I_SELECT_GRIGRI=object(), I_USE_SOUL=object(), ACTION_SETTLE_INTERVAL=.1,
            _read_round_number=lambda: frame['round'],
            _read_chess_mode=lambda: frame['mode'],
            _read_shop_gold=lambda: frame['gold'],
            _read_level=lambda: frame['level'],
            _read_experience_counter=lambda: frame['experience'],
            _read_remaining_time=lambda: remaining,
            _discover_choices=lambda: frame['choices'],
            _lineup_final_level=lambda: 8,
            _is_preparation_mode=lambda: frame['mode'] == '备',
            _clear_economy_click_history=Mock())
        d.appear = lambda rule: (
            frame['grigri'] if rule is d.I_SELECT_GRIGRI
            else frame['use_soul'] if rule is d.I_USE_SOUL
            else frame['button'])
        d.screenshot = Mock(side_effect=observe)
        # Plain functions on a namespace do not bind self, so bind explicitly.
        d._experience_context = lambda: ns['_experience_context'](d)
        d._dispatch_experience_guarded = (
            lambda pending, button: ns['_dispatch_experience_guarded'](d, pending, button))

        def upgrade():
            return ns['_click_economy_button_and_confirm_gold'](
                d, button, 4, 'buy experience', False)
        d.upgrade = upgrade
        d.device_dispatches = device.click.call_count
        return d, upgrade

    def dispatched_points(self, device):
        return [call.args for call in device.click.call_args_list]

    def shop_subject(self, visible=(), context=(), initial=None, opened=True,
                     allowed_modes=('备', '战')):
        """Drive the real shop toggle; only image and input are replaced.

        ``visible`` is the destination-state timeline, consumed solely by
        ``_observe_shop_visibility`` probes: ``True``/``False`` for a decided
        answer and ``None`` for "cannot tell", which must never pass as the
        destination. The last entry sticks. ``context`` is a separate timeline
        consumed by task-level frames, so recovery/round changes can be
        scheduled without disturbing the visibility script.
        """
        import module.device.click_verification as verification
        from module.device.click_verification import ClickResponseUnchanged
        from tasks.Chess.runtime.economy import ChessEconomyMixin

        clock = VirtualClock()
        patcher = mock.patch.object(verification, 'time', clock)
        patcher.start()
        self.addCleanup(patcher.stop)

        ns = dict(logger=Mock(), time=clock, ClickResponseUnchanged=ClickResponseUnchanged,
                  before_click=verification.before_click,
                  after_click=verification.after_click,
                  signature=verification.signature)
        extract('tasks/Chess/runtime/economy.py', 'ChessEconomyMixin',
                ['_market_toggle_context', '_click_market_toggle_guarded',
                 '_ensure_shop_visibility'], ns)

        shape = (720, 1280, 3)
        state = dict(round=19, mode='备', generation=0, accepted=0,
                     invitation=False, purchase=True)
        state.update(initial or {})
        visible, context = list(visible), list(context)
        device = SimpleNamespace(image_frame_id=0, _escape_generation=0,
                                 _accepted_invitation_generation=0,
                                 _handling_invitation=False, _verified_click=None)
        device.image = np.zeros(shape, dtype=np.uint8)
        scene = [0]
        probed = [0]
        framed = [0]

        def refresh():
            scene[0] += 1
            device.image = np.full(shape, (scene[0] * 53) % 256, np.uint8)
            device.image_frame_id += 1

        def observe():
            refresh()
            update = context[framed[0]] if framed[0] < len(context) else {}
            framed[0] += 1
            state.update(update)
            for name, attr in (('generation', '_escape_generation'),
                               ('accepted', '_accepted_invitation_generation'),
                               ('invitation', '_handling_invitation')):
                setattr(device, attr, state[name])

        device.screenshot = Mock(side_effect=refresh)
        device.click = Mock(side_effect=lambda *a, **k: None)
        device.click_record_remove = Mock()
        device.handle_control_check = Mock(return_value=True)

        d = SimpleNamespace(
            device=device, chess_state=SimpleNamespace(game_number=1),
            C_MARKET_TOGGLE=ChessEconomyMixin.C_MARKET_TOGGLE,
            I_SELECT_GRIGRI=object(), SHOP_OPEN_TIMEOUT=8, SHOP_CLOSE_TIMEOUT=8,
            SCREENSHOT_INTERVAL=.05, ACTION_SETTLE_INTERVAL=.05,
            _read_round_number=lambda: state['round'],
            _read_chess_mode=lambda: state['mode'],
            _is_preparation_mode=lambda: state['mode'] == '备',
            _is_purchase_allowed=lambda: state['purchase'])
        d.appear = lambda rule: False
        d.screenshot = Mock(side_effect=observe)
        d._market_toggle_context = lambda flag: ns['_market_toggle_context'](d, flag)
        d._click_market_toggle_guarded = (
            lambda key, flag, deadline: ns['_click_market_toggle_guarded'](
                d, key, flag, deadline))

        def visibility():
            if not visible:
                return False
            answer = visible[probed[0]] if probed[0] < len(visible) else visible[-1]
            probed[0] += 1
            return answer

        d._observe_shop_visibility = visibility

        def toggle():
            return ns['_ensure_shop_visibility'](d, opened, allowed_modes)
        d.toggle = toggle
        return d, toggle

    def test_shop_toggle_dispatches_the_circle_rule_not_the_recognition_asset(self):
        # Closed, closed -> one input; the guard also re-probes closed before
        # dispatching, then two fresh frames must agree that it opened.
        d, toggle = self.shop_subject(visible=[False, False, False, True, True, True])
        self.assertTrue(toggle())
        self.assertEqual(d.device.click.call_count, 1)
        name = d.device.click.call_args.kwargs['control_name']
        self.assertEqual(name, str(d.C_MARKET_TOGGLE))
        roi = d.C_MARKET_TOGGLE.roi_front
        x, y = d.device.click.call_args.args
        self.assertTrue(roi[0] <= x < roi[0] + roi[2])
        self.assertTrue(roi[1] <= y < roi[1] + roi[3])

    def test_shop_toggle_never_dispatches_twice_for_one_key(self):
        # The destination never confirms; still exactly one input, then give up.
        d, toggle = self.shop_subject(visible=[False] * 14)
        self.assertFalse(toggle())
        self.assertEqual(d.device.click.call_count, 1)

    def test_shop_toggle_never_accepts_a_single_ambiguous_frame(self):
        # ``None`` is "cannot tell" and must never satisfy the destination.
        d, toggle = self.shop_subject(visible=[False, False, False,
                                               None, None, True, True])
        self.assertTrue(toggle())

    def test_shop_toggle_rejects_before_dispatch_on_bad_context(self):
        for initial in ({'round': None}, {'mode': '鬼'}):
            with self.subTest(initial=initial):
                d, toggle = self.shop_subject(initial=initial)
                self.assertFalse(toggle())
                d.device.click.assert_not_called()
        d, toggle = self.shop_subject(initial={'purchase': False})
        self.assertFalse(toggle())
        d.device.click.assert_not_called()
        for change in ({'round': 20}, {'generation': 1}, {'invitation': True}):
            with self.subTest(change=change):
                # A context change after the input went out cannot undo it;
                # the safety property is that it never confirms and never
                # spends a second input on the same key.
                d, toggle = self.shop_subject(
                    visible=[False] * 8, context=[{}, change])
                self.assertFalse(toggle())
                self.assertLessEqual(d.device.click.call_count, 1)

    def test_shop_toggle_releases_only_its_own_receipt(self):
        d, toggle = self.shop_subject(visible=[False, False, False, True, True])
        self.assertTrue(toggle())
        self.assertEqual(d.device._verified_click['name'], str(d.C_MARKET_TOGGLE))
        self.assertTrue(d.device._verified_click['changed'])
        self.assertIsNone(d._shop_toggle_pending)

    def test_experience_dispatches_the_fixed_center_rule_with_a_fenced_receipt(self):
        d, upgrade = self.experience_subject(post=[{'gold': 67}, {'gold': 67}])
        self.assertEqual(upgrade(), 'success')
        x, y = self.dispatched_points(d.device)[0]
        roi = d.C_EXPERIENCE_CENTER.roi_front
        self.assertTrue(roi[0] <= x < roi[0] + roi[2])
        self.assertTrue(roi[1] <= y < roi[1] + roi[3])
        receipt = d.device._verified_click
        self.assertEqual(receipt['name'], str(d.C_EXPERIENCE_CENTER))
        self.assertTrue(receipt['changed'])
        self.assertIsNone(d._experience_pending)

    def test_experience_confirms_only_on_two_fresh_progressed_frames(self):
        # One progressed frame is not a confirmation: the second must agree.
        for post, expected in (([{'gold': 67}, {'gold': 71}], 'unknown'),
                               ([{'gold': 67}, {'gold': 67}], 'success')):
            with self.subTest(post=post):
                d, upgrade = self.experience_subject(post=post)
                self.assertEqual(upgrade(), expected)
                self.assertEqual(d._experience_pending is None, expected == 'success')

    def test_experience_readable_and_flat_never_confirms_on_gold_alone(self):
        # A gold deduction may be income, not spending, when experience reads.
        d, upgrade = self.experience_subject(
            initial={'experience': (18, 24)},
            post=[{'gold': 67}, {'gold': 67}])
        self.assertEqual(upgrade(), 'unknown')
        self.assertIsNotNone(d._experience_pending)

    def test_experience_unreadable_may_fall_back_to_two_paid_frames(self):
        d, upgrade = self.experience_subject(
            initial={'experience': None},
            post=[{'gold': 67}, {'gold': 67}])
        self.assertEqual(upgrade(), 'success')

    def test_experience_retries_once_only_after_two_unchanged_frames(self):
        d, upgrade = self.experience_subject(
            initial={'experience': (18, 24)}, post=[{}, {}, {}, {}])
        self.assertEqual(upgrade(), 'unknown')
        self.assertEqual(d.device.click.call_count, 1)
        # Two unchanged frames positively prove the input did nothing.
        self.assertEqual(upgrade(), 'unknown')
        self.assertEqual(d.device.click.call_count, 2)
        # The retry allowance is spent; no third input may be dispatched.
        self.assertEqual(upgrade(), 'unknown')
        self.assertEqual(d.device.click.call_count, 2)

    def test_experience_guard_rejects_before_any_hardware_input(self):
        cases = ({'grigri': True}, {'use_soul': True}, {'choices': ('a',)},
                 {'button': False}, {'round': None}, {'mode': '鬼'},
                 {'gold': 53}, {'level': 8})
        for initial in cases:
            with self.subTest(initial=initial):
                d, upgrade = self.experience_subject(initial=initial)
                self.assertEqual(upgrade(), 'no_progress')
                d.device.click.assert_not_called()
                self.assertIsNone(getattr(d, '_experience_pending', None))
        d, upgrade = self.experience_subject(remaining=7)
        self.assertEqual(upgrade(), 'no_progress')
        d.device.click.assert_not_called()

    def test_experience_boundary_change_after_dispatch_never_confirms(self):
        # ``changed`` on the receipt only reports that pixels moved; the
        # business outcome is the return value plus the pending ledger.
        for boundary in ({'mode': '战'}, {'round': 20}, {'generation': 1},
                         {'grigri': True}, {'invitation': True},
                         {'accepted': 1}):
            with self.subTest(boundary=boundary):
                d, upgrade = self.experience_subject(post=[dict(boundary, gold=67), {}])
                self.assertEqual(upgrade(), 'unknown')
                self.assertEqual(d.device.click.call_count, 1)
                self.assertIsNotNone(d._experience_pending)

    def test_experience_stale_frame_cannot_authorize_a_dispatch(self):
        # The guard needs a genuinely new frame after its own pre-click check.
        d, upgrade = self.experience_subject(pre=[{'id': 4}, {'id': 4}])
        self.assertEqual(upgrade(), 'no_progress')
        d.device.click.assert_not_called()

    def test_experience_replay_is_fenced_when_the_screen_never_responded(self):
        d, upgrade = self.experience_subject(
            initial={'experience': (18, 24)}, post=[{}, {}, {}, {}],
            static_image=True)
        self.assertEqual(upgrade(), 'unknown')
        self.assertEqual(d.device.click.call_count, 1)
        self.assertEqual(upgrade(), 'unknown')
        self.assertEqual(d.device.click.call_count, 1)

    def test_experience_device_failure_is_not_swallowed(self):
        d, upgrade = self.experience_subject()
        d.device.click.side_effect = RuntimeError('device failure')
        with self.assertRaisesRegex(RuntimeError, 'device failure'):
            upgrade()


if __name__ == '__main__':
    unittest.main()

"""Regressions for upstream fixes ported from sibling OAS repositories.

Each test pins one of the ported fixes so the bug cannot silently return.

ported from Hurry2/OnmyojiAutoScript (Forme):

1. ActivityShikigami re-run must re-switch souls (switched_soul reset in run()).
2. WeeklyTrifles touch-fish must read the CURRENT ticket count, not the remainder
   (DigitCounter returns (current, total - current, total)).
3. Delegation completion must click a dedicated region after OCR detects "完成".
4. BondlingFairyland must stop when the plate-less prompt appears instead of
   relying on the removed plate-count OCR.

ported from runhey/OnmyojiAutoScript (solana), commit 988145c041:

5. The GameUi summon-room template must be the refreshed crop; the pre-refresh
   image no longer matches the current game version, so room entry silently fails.
"""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def find_class(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def find_func(node, name):
    return next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name)


# runhey/solana 988145c041 refreshed this crop; the pre-fix image was 6639 bytes
# (sha256 of the stale file differed only in bytes, not in ROI size), so the
# digest is what actually guards the port.
_REFRESHED_SUMMON_TEMPLATE_SHA256 = 'eb154b477b9d9ad1db1e74df85065e2551f9e6f34f7344e33d214c9d20a187e7'


def _find_rule_image(rel_path, attr_name):
    """Return {'roi_front': (x, y, w, h), 'file': str} for a RuleImage assignment.

    Parsing the asset module is cheaper and safer than importing it: these
    modules pull in the whole device/OCR stack.
    """
    tree = ast.parse(read(rel_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if attr_name not in names:
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        out = {}
        for kw in call.keywords:
            if kw.arg == 'roi_front' and isinstance(kw.value, ast.Tuple):
                out['roi_front'] = tuple(
                    elt.value for elt in kw.value.elts if isinstance(elt, ast.Constant)
                )
            elif kw.arg == 'file' and isinstance(kw.value, ast.Constant):
                out['file'] = kw.value.value
        if 'roi_front' in out and 'file' in out:
            return out
    raise AssertionError(f'{attr_name} not found in {rel_path}')


class PortedUpstreamFixTests(unittest.TestCase):

    # ---------------------------------------------------------------- fix 1
    def test_activity_run_resets_switched_soul_so_rerun_switches_again(self):
        """`switched_soul` is initialised once in BaseAct.__init__; unless run()
        clears it, a second run in the same session skips soul switching."""
        src = read('tasks/ActivityShikigami/script_task.py')
        tree = ast.parse(src)
        cls = find_class(tree, 'ScriptTask')
        run = find_func(cls, 'run')
        body = ast.unparse(run)
        self.assertIn('switched_soul', body,
                      'ActivityShikigami.run() must reset switched_soul each run')
        # It must be a fresh all-False mapping over the battle types.
        self.assertIn('BATTLE_TYPES', body)
        self.assertIn('False', body)
        # ...and the name has to be imported, or run() raises NameError.
        self.assertTrue(
            any(isinstance(n, ast.ImportFrom)
                and n.module == 'tasks.ActivityShikigami.config'
                and any(a.name == 'BATTLE_TYPES' for a in n.names)
                for n in tree.body),
            'BATTLE_TYPES must be imported from tasks.ActivityShikigami.config',
        )

    def test_activity_reset_precedes_task_sequence_execution(self):
        """The reset must happen before the activities run, not after."""
        tree = ast.parse(read('tasks/ActivityShikigami/script_task.py'))
        run = find_func(find_class(tree, 'ScriptTask'), 'run')
        lines = ast.unparse(run).splitlines()
        reset = next(i for i, l in enumerate(lines) if 'switched_soul' in l)
        first_task = next(i for i, l in enumerate(lines) if 'method_name' in l and '=' in l)
        self.assertLess(reset, first_task)

    # ---------------------------------------------------------------- fix 2
    def test_touch_fish_reads_current_ticket_count_not_remainder(self):
        """DigitCounter.ocr_digit_counter -> (current, total - current, total).
        Unpacking the *second* element used the remainder as the current count."""
        src = read('tasks/WeeklyTrifles/script_task.py')
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and 'O_WT_LUCKY_TICKETS' in ast.unparse(node.value):
                target = node
                break
        self.assertIsNotNone(target, 'touch-fish ticket OCR assignment not found')
        names = [t.id for t in target.targets[0].elts]
        self.assertEqual(
            names[0], 'cu_tickts',
            'the FIRST element of DigitCounter is the current count; '
            f'got {names}',
        )
        self.assertEqual(names, ['cu_tickts', '_', '_'])

    def test_touch_fish_save_all_only_fires_once(self):
        """Re-clicking I_WT_SAVE_ALL spins until timeout; a one-shot flag guards it."""
        src = read('tasks/WeeklyTrifles/script_task.py')
        self.assertIn('not_save_flag', src)
        # Both the "enough tickets" and "not enough tickets" branches gate on it.
        self.assertIn('cu_tickts >= cost_tickts and not_save_flag', src)
        self.assertIn('cu_tickts < cost_tickts and not_save_flag', src)
        # The old self-nulling hack must be gone.
        self.assertNotIn('cu_tickts = 0', src)

    # ---------------------------------------------------------------- fix 3
    def test_delegation_uses_dedicated_click_region_for_completion(self):
        src = read('tasks/Delegation/script_task.py')
        assets = read('tasks/Delegation/assets.py')
        self.assertIn('C_D_DONE', assets, 'C_D_DONE click region must be defined')
        self.assertIn('C_D_DONE', src, 'completion must click the dedicated region')
        # Detect with OCR, then click explicitly.
        self.assertIn('self.ocr_appear(self.O_D_DONE', src)
        self.assertIn('self.click(self.C_D_DONE', src)

    def test_delegation_keeps_its_timeout_break(self):
        """The OCR-appear split must not drop the check_timer escape hatch."""
        src = read('tasks/Delegation/script_task.py')
        seg = src[src.index('if not self.appear(self.I_REWARDS_MIN)'):]
        near = seg[: seg.index('continue', seg.index('C_D_DONE'))]
        self.assertIn('check_timer.reached()', near,
                      'the loop must still break on timeout around the click')

    def test_delegation_click_json_matches_asset(self):
        import json
        data = json.loads(read('tasks/Delegation/rewards/click.json'))
        entry = next(e for e in data if e['itemName'] == 'd_done')
        assets = read('tasks/Delegation/assets.py')
        self.assertIn(entry['roiFront'], assets)

    # ---------------------------------------------------------------- fix 4
    def test_bondling_plate_count_ocr_is_gone(self):
        """The fragile plate-count OCR was replaced by an image match."""
        src = read('tasks/BondlingFairyland/script_task.py')
        self.assertNotIn('def check_plate_number', src)
        self.assertNotIn('check_plate_number()', src)

    def test_bondling_leader_stops_on_plate_less_prompt(self):
        src = read('tasks/BondlingFairyland/script_task.py')
        self.assertIn('I_C_PLATE_LESS', src)
        self.assertIn('no_plate', src)
        # Must not treat a plate-less abort as a normal completion.
        self.assertIn('not create_bond_team() and not no_plate', src)

    def test_bondling_plate_less_asset_and_json_present(self):
        import json
        assets = read('tasks/BondlingFairyland/assets.py')
        self.assertIn('I_C_PLATE_LESS', assets)
        data = json.loads(read('tasks/BondlingFairyland/capture/image.json'))
        names = {e['itemName'] for e in data}
        self.assertIn('c_plate_less', names)
        for png in ('capture_c_plate_less.png', 'capture_c_plate_buy.png'):
            p = ROOT / 'tasks/BondlingFairyland/capture' / png
            self.assertTrue(p.exists(), f'{png} missing')
            with open(p, 'rb') as fh:
                self.assertEqual(fh.read(8), b'\x89PNG\r\n\x1a\n', f'{png} not a PNG')


    # ------------------------------------------------- fix 5 (runhey/solana)
    def test_gameui_summon_template_is_the_refreshed_one(self):
        """988145c041 replaced the summon-room template because the old crop
        stopped matching the live game. Pin the refreshed bytes so a later
        re-capture or revert cannot silently reintroduce the stale image.

        If the game re-skins the summon room again, re-capture the crop and
        update this digest -- that is the intended, explicit maintenance step.
        """
        import hashlib
        png = ROOT / 'tasks/GameUi/page/page_main_goto_summon.png'
        digest = hashlib.sha256(png.read_bytes()).hexdigest()
        self.assertEqual(
            digest, _REFRESHED_SUMMON_TEMPLATE_SHA256,
            'tasks/GameUi/page/page_main_goto_summon.png is not the refreshed '
            'template (runhey/solana 988145c041)',
        )

    def test_gameui_summon_template_size_matches_rule_roi(self):
        """A RuleImage's template must be exactly as large as its roi_front.
        Template/ROI drift makes matching fail even when the bytes look fine."""
        rule = _find_rule_image('tasks/GameUi/assets.py', 'I_MAIN_GOTO_SUMMON')
        w, h = rule['roi_front'][2], rule['roi_front'][3]
        png = (ROOT / rule['file'].lstrip('./')).resolve()
        with open(png, 'rb') as fh:
            head = fh.read(24)
        self.assertEqual(head[:8], b'\x89PNG\r\n\x1a\n', f'{png.name} is not a PNG')
        import struct
        actual = struct.unpack('>II', head[16:24])
        self.assertEqual(
            actual, (w, h),
            f'{png.name} is {actual[0]}x{actual[1]} but '
            f'I_MAIN_GOTO_SUMMON.roi_front asks for {w}x{h}',
        )


if __name__ == '__main__':
    unittest.main()

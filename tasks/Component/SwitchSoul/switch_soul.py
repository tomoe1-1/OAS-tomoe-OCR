# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep, monotonic
from typing import Union

from module.atom.click import RuleClick
from module.atom.long_click import RuleLongClick
from module.atom.ocr import RuleOcr
from tasks.base_task import BaseTask
from tasks.Component.SwitchSoul.assets import SwitchSoulAssets
from module.logger import logger
from module.exception import TaskRecoveryFailed
from module.device.confirm_retry import click_confirm_once
from tasks.Component.preset_text import SOUL_ENTRY, SOUL_TEAM_TITLE, SOUL_SOUL_TITLE, SOUL_CONFIRM, soul_slot_center


def switch_parser(switch_str: str) -> tuple:
    switch_list = switch_str.split(',')
    if len(switch_list) != 2:
        raise ValueError('Switch_str must be 2 length')
    return int(switch_list[0]), int(switch_list[1])


class SwitchSoul(BaseTask, SwitchSoulAssets):


    def _preset_match(self, images, text, interval=None):
        """All existing image variants take priority over OCR on this frame."""
        if not isinstance(images, tuple):
            images = (images,)
        for image in images:
            if self.appear(image, interval=interval):
                return image
        if self.ocr_appear(text, interval=interval):
            return text
        return None

    def _preset_appear(self, images, text):
        return self._preset_match(images, text) is not None

    def _preset_click(self, images, text, interval=None):
        if text is SOUL_CONFIRM:
            variants = images if isinstance(images, tuple) else (images,)
            return click_confirm_once(self, lambda: self._preset_match(images, text),
                                      [*variants, text])
        target = self._preset_match(images, text, interval=interval)
        if target is None:
            return False
        # Use the matched image center, or the OCR text box center on fallback.
        return self.device.click(*target.coord(), control_name=target.name) is not False

    def _soul_preset_visible_ocr(self):
        images = (self.I_SOU_TEAM_PRESENT, self.I_SOU_SWITCH_1, self.I_SOU_SWITCH_2,
                  self.I_SOU_SWITCH_3, self.I_SOU_SWITCH_4)
        for image in images:
            if self.appear(image):
                return True
        return self.ocr_appear(SOUL_TEAM_TITLE) or self.ocr_appear(SOUL_SOUL_TITLE)

    def _soul_slot_control(self, slot):
        if not 1 <= slot <= 4:
            raise ValueError('Soul preset slot must be in [1-4]')
        image = getattr(self, f'I_SOU_SWITCH_{slot}')
        if self.appear(image):
            return image
        rows = self.O_SS_TEAM_NAME.detect_and_ocr(self.device.image)
        y = soul_slot_center(rows, self.O_SS_TEAM_NAME.roi, slot)
        if y is None:
            return None
        return RuleClick(roi_front=(978, y-10, 24, 20), roi_back=(978, y-10, 24, 20),
                         name=f'SOUL_PRESET_OCR_SLOT_{slot}')

    def _click_soul_slot_ocr(self, slot):
        target = self._soul_slot_control(slot)
        return target is not None and self.device.click(*target.coord(), control_name=target.name) is not False

    def _soul_generation(self):
        return (getattr(self.device, '_escape_generation', 0),
                getattr(self.device, '_accepted_invitation_generation', 0))

    def _soul_guard(self, generation):
        if self._soul_generation() != generation:
            raise TaskRecoveryFailed('Soul preset operation interrupted by recovery or invitation')

    def _soul_capture(self, generation):
        previous = self.device.image_frame_id
        self.screenshot()
        self._soul_guard(generation)
        if self.device.image_frame_id is None or self.device.image_frame_id == previous:
            raise TaskRecoveryFailed('Soul preset operation needs a new screenshot frame')

    def _soul_wait(self, predicate, generation, timeout=12):
        deadline = monotonic() + self.device.performance.timeout(timeout)
        stable = 0
        while monotonic() < deadline:
            self._soul_capture(generation)
            stable = stable + 1 if predicate() else 0
            self._soul_guard(generation)
            if stable == 2 and monotonic() < deadline:
                return
        raise TaskRecoveryFailed('Soul preset target was not confirmed in two fresh frames')

    def _soul_row(self, rule, name):
        rows = []
        for result in rule.detect_and_ocr(self.device.image):
            if result.score < .8 or result.ocr_text.strip() != name:
                continue
            box = result.box
            if len(box) != 4:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            if not (0 <= min(xs) < max(xs) <= rule.roi[2]
                    and 0 <= min(ys) < max(ys) <= rule.roi[3]):
                continue
            rows.append((int(rule.roi[0] + (min(xs)+max(xs))/2),
                         int(rule.roi[1] + (min(ys)+max(ys))/2)))
        return rows[0] if len(rows) == 1 else None

    def _soul_group_highlight(self, group):
        image = self.device.image
        if image is None or image.shape != (720, 1280, 3):
            return False
        x, y, width, height = getattr(self, f'C_SOU_GROUP_{group}').roi_front
        center = int(y + height/2)
        # Two separate areas inside the actual gold selected-group background,
        # away from names and outside the border. This is a group marker only.
        for left in (1098, 1225):
            pixels = image[center-8:center+8, left:left+8].astype('int16')
            if pixels.shape != (16, 8, 3):
                return False
            golden = ((pixels[:, :, 0] > 180) & (pixels[:, :, 1] > 130)
                      & (pixels[:, :, 1] < 235) & (pixels[:, :, 2] < 180)
                      & (pixels[:, :, 0] - pixels[:, :, 2] > 65))
            if float(golden.mean()) < .85:
                return False
        return True

    def _soul_selected_group(self, group):
        if not self._soul_preset_visible_ocr():
            return False
        highlighted = [i for i in range(1, 8) if self._soul_group_highlight(i)]
        return highlighted == [group]

    def _soul_selected_row(self, y):
        rows = []
        for i in range(1, 8):
            roi = getattr(self, f'C_SOU_GROUP_{i}').roi_front
            if abs(roi[1] + roi[3]/2 - y) < 30:
                rows.append(i)
        return len(rows) == 1 and self._soul_selected_group(rows[0])

    @staticmethod
    def _soul_same_layout(first, second):
        # Real consecutive frames shift OCR box vertices by 1-3 pixels on a
        # stationary list. Compare to the fixed baseline, never accumulate
        # this tolerance across frames, and keep text/row count exact.
        if not first or len(first) != len(second):
            return False
        for (name1, box1), (name2, box2) in zip(first, second):
            if name1 != name2 or len(box1) != 4 or len(box2) != 4:
                return False
            if any(abs(x1-x2) > 3 or abs(y1-y2) > 3
                   for (x1, y1), (x2, y2) in zip(box1, box2)):
                return False
        return True

    def _soul_scroll_top(self, rule, swipe, generation):
        def layout():
            return tuple((r.ocr_text.strip(), tuple((round(float(x)), round(float(y))) for x, y in r.box))
                         for r in rule.detect_and_ocr(self.device.image)
                         if r.score >= .8 and r.ocr_text.strip())
        for _ in range(8):
            self._soul_capture(generation)
            if not self._soul_preset_visible_ocr():
                raise TaskRecoveryFailed('Soul preset page lost while scrolling')
            before = layout()
            self._soul_guard(generation)
            self.swipe(swipe, 1.5)
            sleep(.5)
            self._soul_guard(generation)
            self._soul_capture(generation)
            after = layout()
            if self._soul_same_layout(before, after):
                self._soul_wait(lambda: self._soul_preset_visible_ocr() and self._soul_same_layout(after, layout()),
                                generation, timeout=12)
                return
        raise TaskRecoveryFailed('Soul preset list did not reach a readable top within eight swipes')

    def _soul_find_row(self, rule, name, swipe, generation):
        for _ in range(12):
            self._soul_capture(generation)
            if not self._soul_preset_visible_ocr():
                raise TaskRecoveryFailed('Soul preset page lost while finding a named row')
            row = self._soul_row(rule, name)
            if row is not None:
                return row
            self._soul_guard(generation)
            self.swipe(swipe, 1.5)
            sleep(.5)
            self._soul_guard(generation)
        raise TaskRecoveryFailed(f'Soul preset row not found within twelve swipes: {name}')

    def _soul_stable_row(self, rule, name, baseline, generation):
        current = None
        def matches():
            nonlocal current
            current = self._soul_row(rule, name)
            return (self._soul_preset_visible_ocr() and current is not None
                    and abs(current[0]-baseline[0]) <= 3
                    and abs(current[1]-baseline[1]) <= 3)
        self._soul_wait(matches, generation)
        return current



    @staticmethod
    def _soul_control_key(control):
        if control is None:
            return None
        # coord() samples a random physical input point and records click
        # history. Observations must use the matched ROI without sampling.
        return (control.name, tuple(control.roi_front))

    @staticmethod
    def _soul_same_target(baseline, current):
        if baseline is None or current is None or baseline[0] != current[0]:
            return False
        x, y, width, height = baseline[1]
        cx, cy, cwidth, cheight = current[1]
        return (width == cwidth and height == cheight and width > 0 and height > 0
                and abs(x-cx) <= 3 and abs(y-cy) <= 3)

    def _soul_apply_once(self, locate, selected, label, generation):
        # Equipment of the already worn preset is silent in the actual UI.
        # End the operation on its stable target page; do not manufacture a
        # success toast or replay the equipment action to obtain a response.
        if getattr(self, '_soul_pending_action', None) is not None:
            raise TaskRecoveryFailed('Previous soul preset input is still unverified')
        deadline = monotonic() + self.device.performance.timeout(12)
        baseline = None
        stable = 0
        while monotonic() < deadline:
            self._soul_capture(generation)
            current = locate() if self._soul_preset_visible_ocr() and selected() else None
            key = self._soul_control_key(current)
            self._soul_guard(generation)
            if monotonic() >= deadline:
                break
            if key is None:
                baseline = None
                stable = 0
            elif baseline is None:
                baseline = key
                stable = 1
            elif self._soul_same_target(baseline, key):
                stable += 1
            else:
                # Keep the original baseline: gradual motion must not turn
                # into stability by accumulating the per-frame tolerance.
                stable = 0
            if stable >= 2:
                break
        if stable < 2 or monotonic() >= deadline:
            raise TaskRecoveryFailed('Soul preset target was not stable in two fresh frames')
        if self._preset_appear(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM) or self.appear(self.I_CHECK_BLOCK):
            raise TaskRecoveryFailed('Unexpected modal before soul preset selection')
        target = locate()
        if not self._soul_same_target(baseline, self._soul_control_key(target)):
            raise TaskRecoveryFailed('Soul preset target moved before input')
        point = target.coord()
        self._soul_guard(generation)
        if monotonic() >= deadline:
            raise TaskRecoveryFailed('Soul preset preparation exceeded its deadline')
        self._soul_pending_action = label
        if self.device.click(*point, control_name=target.name) is False:
            self._soul_pending_action = None
            raise TaskRecoveryFailed('Soul preset selection input blocked')
        self._soul_guard(generation)
        deadline = monotonic() + self.device.performance.timeout(12)
        settle_until = monotonic() + min(self.device.performance.timeout(1.5), self.device.performance.timeout(12))
        stable = 0
        confirmed = False
        while monotonic() < deadline:
            self._soul_capture(generation)
            if self.appear(self.I_CHECK_BLOCK):
                raise TaskRecoveryFailed('Soul preset click opened a shikigami dialog')
            if self._preset_appear(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM):
                if confirmed:
                    raise TaskRecoveryFailed('Soul preset confirmation appeared again')
                self._soul_guard(generation)
                if not self._preset_click(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM):
                    raise TaskRecoveryFailed('Soul preset confirmation input blocked')
                confirmed = True
                stable = 0
                settle_until = monotonic() + self.device.performance.timeout(1.5)
                self._soul_guard(generation)
                continue
            current = locate() if self._soul_preset_visible_ocr() and selected() else None
            page_ok = self._soul_same_target(baseline, self._soul_control_key(current))
            stable = stable + 1 if page_ok else 0
            self._soul_guard(generation)
            if monotonic() >= deadline:
                break
            if stable >= 2 and monotonic() >= settle_until:
                self._soul_pending_action = None
                # Preserve the device's response record. A silent operation
                # does not prove soul equality and must not grant a replay.
                logger.info(f'Soul preset single input finished on target page: {label}; confirmation={confirmed}')
                return
        raise TaskRecoveryFailed(f'Soul preset target page not stable after one input: {label}')

    def _dismiss_soul_confirm(self):
        self.screenshot()
        if not self._preset_appear(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM):
            return False
        if not self._preset_click(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM):
            raise TaskRecoveryFailed('Soul preset confirmation input blocked')
        self._ui_verify(lambda: not self._preset_appear(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM),
                        [self.I_SOU_SWITCH_SURE, SOUL_CONFIRM])
        return True

    def run_switch_soul(self, target: tuple | list[tuple] | str):
        """
        保证在式神录的界面
        :return:
        """
        target = self._soul_targets(target)
        self.click_preset()
        self.switch_souls(target)

    def click_preset(self) -> None:
        generation = self._soul_generation()
        deadline = monotonic() + self.device.performance.timeout(12)
        sent = False
        while monotonic() < deadline:
            self._soul_capture(generation)
            if self._soul_preset_visible_ocr():
                self._soul_wait(self._soul_preset_visible_ocr, generation)
                logger.info('Soul preset page confirmed: image first, OCR fallback')
                return
            if not sent and self._preset_click(self.I_SOUL_PRESET, SOUL_ENTRY, interval=0):
                sent = True
            self._soul_guard(generation)
        raise TaskRecoveryFailed('Soul preset entry/title was not recognized by image or OCR')

    def switch_soul_one(self, group: int, team: int) -> None:
        if not 1 <= group <= 7:
            raise ValueError('Switch soul_one group must be in [1-7]')
        if not 1 <= team <= 4:
            raise ValueError('Switch soul_one team must be in [1-4]')
        if getattr(self, '_soul_pending_action', None) is not None:
            raise TaskRecoveryFailed('Previous soul preset input is still unverified')
        generation = self._soul_generation()
        self._soul_scroll_top(self.O_SS_GROUP_NAME, self.S_SS_GROUP_SWIPE_UP, generation)
        target_click = getattr(self, f'C_SOU_GROUP_{group}')
        selected = lambda: self._soul_selected_group(group)
        if not selected():
            self._soul_guard(generation)
            if self.device.click(*target_click.coord(), control_name=target_click.name) is False:
                raise TaskRecoveryFailed('Soul preset group input blocked')
        self._soul_wait(lambda: self._soul_preset_visible_ocr() and selected(), generation)
        self.device.confirm_control_progress([target_click])
        self._soul_scroll_top(self.O_SS_TEAM_NAME, self.S_SS_TEAM_SWIPE_DOWN, generation)
        self._soul_apply_once(lambda: self._soul_slot_control(team), selected,
                              f'group {group} team {team}', generation)

    @staticmethod
    def _soul_targets(target):
        if isinstance(target, str):
            target = switch_parser(target)
        if isinstance(target, tuple):
            target = [target]
        if not isinstance(target, list) or not target:
            raise ValueError('Soul preset targets must be a nonempty tuple or list')
        result = []
        for item in target:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError('Soul preset target must contain group and team')
            group, team = item
            if any(isinstance(value, bool) or not isinstance(value, (int, str))
                   for value in (group, team)):
                raise ValueError('Soul preset indices must be integers or integer strings')
            group, team = int(group), int(team)
            if not 1 <= group <= 7 or not 1 <= team <= 4:
                raise ValueError('Soul preset group/team outside [1-7]/[1-4]')
            if not result or result[-1] != (group, team):
                result.append((group, team))
        return result

    def switch_souls(self, target: tuple or list[tuple]) -> None:
        """
        切换御魂
        :param target: [(1, 1), (2, 2), (3, 3), (4, 4)]  或者是单独的一个元组(4, 4) 第一个是组, 第二个是队伍
        :return:
        """
        for group, team in self._soul_targets(target):
            self.switch_soul_one(group, team)

    def exit_shikigami_records(self, exited=None) -> None:
        """
        退出式神录的界面
        :return:
        """
        if exited is None:
            from tasks.GameUi.courtyard import courtyard_visible
            exited = lambda: courtyard_visible(self)
        generation = self._soul_generation()
        deadline = monotonic() + self.device.performance.timeout(12)
        sent = False
        stable = 0
        while monotonic() < deadline:
            self._soul_capture(generation)
            in_records = self.appear(self.I_SOU_CHECK_IN) or self._soul_preset_visible_ocr()
            stable = stable + 1 if not in_records and exited() else 0
            self._soul_guard(generation)
            if stable >= 2 and monotonic() < deadline:
                logger.info('Exit shikigami records confirmed in two fresh frames')
                return
            if not sent and in_records and self.appear(self.I_RECORD_SOUL_BACK):
                self._soul_guard(generation)
                if self.device.click(*self.I_RECORD_SOUL_BACK.coord(), control_name=self.I_RECORD_SOUL_BACK.name) is False:
                    raise TaskRecoveryFailed('Soul preset exit input blocked')
                sent = True
                self._soul_guard(generation)
        raise TaskRecoveryFailed('Soul preset exit destination not confirmed after one back input')

    def run_switch_soul_by_name(self, groupName, teamName):
        """
        保证在式神录的界面
        :return:
        """
        if not isinstance(groupName, str) or not groupName.strip() or not isinstance(teamName, str) or not teamName.strip():
            raise ValueError('Soul preset group and team names must be nonempty strings')
        self.click_preset()
        self.switch_soul_by_name(groupName, teamName)

    def switch_soul_by_name(self, groupName, teamName):
        if not isinstance(groupName, str) or not groupName.strip() or not isinstance(teamName, str) or not teamName.strip():
            raise ValueError('Soul preset group and team names must be nonempty strings')
        groupName, teamName = groupName.strip(), teamName.strip()
        if getattr(self, '_soul_pending_action', None) is not None:
            raise TaskRecoveryFailed('Previous soul preset input is still unverified')
        generation = self._soul_generation()
        self._soul_scroll_top(self.O_SS_GROUP_NAME, self.S_SS_GROUP_SWIPE_UP, generation)
        row = self._soul_find_row(self.O_SS_GROUP_NAME, groupName, self.S_SS_GROUP_SWIPE_DOWN, generation)
        row = self._soul_stable_row(self.O_SS_GROUP_NAME, groupName, row, generation)
        def selected():
            current = self._soul_row(self.O_SS_GROUP_NAME, groupName)
            return current is not None and self._soul_selected_row(current[1])
        if not selected():
            current_row = self._soul_row(self.O_SS_GROUP_NAME, groupName)
            if (current_row is None or abs(current_row[0]-row[0]) > 3
                    or abs(current_row[1]-row[1]) > 3):
                raise TaskRecoveryFailed('Named soul preset group moved before input')
            if self._preset_appear(self.I_SOU_SWITCH_SURE, SOUL_CONFIRM) or self.appear(self.I_CHECK_BLOCK):
                raise TaskRecoveryFailed('Unexpected modal before named soul preset group selection')
            self._soul_guard(generation)
            if self.device.click(*current_row, control_name='SOUL_NAMED_GROUP_SELECT') is False:
                raise TaskRecoveryFailed('Named soul preset group input blocked')
        self._soul_wait(lambda: self._soul_preset_visible_ocr() and selected(), generation)
        self._soul_scroll_top(self.O_SS_TEAM_NAME, self.S_SS_TEAM_SWIPE_DOWN, generation)
        self._soul_find_row(self.O_SS_TEAM_NAME, teamName, self.S_SS_TEAM_SWIPE_UP, generation)
        def locate():
            current = self._soul_row(self.O_SS_TEAM_NAME, teamName)
            if current is None:
                return None
            y = current[1]
            return RuleClick(roi_front=(978, y-10, 24, 20), roi_back=(978, y-10, 24, 20),
                             name='SOUL_NAMED_TEAM_EQUIP')
        self._soul_apply_once(locate, selected, f'group {groupName} team {teamName}', generation)

    def ocr_appear_click_by_rule(self,
                                 target: RuleOcr,
                                 action: Union[RuleClick, RuleLongClick] = None,
                                 interval: float = None,
                                 duration: float = None) -> bool:
        """
        ocr识别目标，如果目标存在，则触发动作
        :param target:
        :param action:
        :param interval:
        :param duration:
        :return:
        """
        appear = self.ocr_appear(target, interval)

        if not appear:
            return False

        x1, y1 = target.coord()
        x, y = action.coord()

        self.device.click(x=x, y=y1, control_name=target.name)
        return True


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    s = SwitchSoul(c, d)

    s.click_preset()
    # s.switch_soul_one(4, 1)
    # s.switch_soul_by_name('契灵', '茨球')
    s.switch_soul_by_name('默认分组', '队伍5')

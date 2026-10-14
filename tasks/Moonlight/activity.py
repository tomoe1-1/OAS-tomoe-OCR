"""月映千灯：月华流光挑战。"""
import time

from module.exception import GameStuckError
from module.logger import logger
import tasks.Moonlight.page as pages


class MoonlightAct:
    def _enter_moonlight_battle(self) -> bool:
        """只在已识别挑战页点击一次，等待进入战斗；资源弹窗不购买。"""
        self.screenshot()
        if not self.appear(self.I_MOON_BATTLE):
            raise GameStuckError('Moonlight challenge page is not visible')
        resource = self.O_MOON_RESOURCE.ocr_digit(self.device.image)
        if resource < 6:
            time.sleep(.3)
            self.screenshot()
            if not self.appear(self.I_MOON_BATTLE):
                raise GameStuckError('Moonlight page changed while checking challenge resource')
            resource = self.O_MOON_RESOURCE.ocr_digit(self.device.image)
            if resource < 6:
                logger.info('Moonlight challenge resource below 6 on two reads; stop challenges')
                return False
        # Never accept a blind challenge click or an unknown purchase prompt.
        if not self.appear_then_click(self.I_MOON_CHALLENGE, interval=0):
            raise GameStuckError('Moonlight challenge button is not visible or click was blocked')
        deadline = time.monotonic() + self.device.performance.timeout(30)
        reward_cap_confirmed = False
        while time.monotonic() < deadline:
            self.screenshot()
            if self.is_in_battle(False):
                return True
            if self.appear(self.I_UI_CONFIRM) or self.appear(self.I_UI_CONFIRM_SAMLL):
                notice = ''.join(
                    result.ocr_text for result in self.O_MOON_REWARD_NOTICE.detect_and_ocr(self.device.image)
                ).replace(' ', '')
                if '奖励已达获取上限' in notice and '继续挑战' in notice:
                    if not reward_cap_confirmed:
                        if not (self.appear_then_click(self.I_UI_CONFIRM, interval=0)
                                or self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=0)):
                            raise GameStuckError('Moonlight reward-cap confirmation could not be clicked')
                        reward_cap_confirmed = True
                    time.sleep(.3)
                    continue
                logger.warning('Moonlight challenge blocked by unknown confirmation dialog; stop safely')
                if (self.appear_then_click(self.I_UI_CANCEL, interval=0)
                        or self.appear_then_click(self.I_UI_CANCEL_SAMLL, interval=0)):
                    return False
                raise GameStuckError('Moonlight confirmation dialog could not be cancelled')
            time.sleep(.3)
        raise GameStuckError('Moonlight did not enter battle after challenge')

    def run_moonlight(self):
        logger.hr('Start activity: 月华流光', 1)
        if self.action_limit('moonlight') <= 0 or self.time_limit_reached():
            return
        self.goto_page(pages.page_moon_battle)
        first_battle = True
        while self.prepare_next_action('moonlight'):
            self.goto_page(pages.page_moon_battle)
            if not self._enter_moonlight_battle():
                return
            self.record_action('moonlight')
            source = self.battle_config('rich_man')
            battle = source.copy(update={
                'preset_enable': source.preset_enable and first_battle,
                'lock_team_enable': False,
                'continuous_battle': False,
                'max_continuous': 0,
            })
            self.run_general_battle(
                battle, battle_key=f'moonlight_{time.monotonic_ns()}',
                exit_matcher=self.I_MOON_BATTLE,
            )
            first_battle = False



from collections import deque
from contextlib import contextmanager
from module.device.performance import PerformanceProfile
from datetime import datetime
import time

# Patch pkg_resources before importing adbutils and uiautomator2
from module.device.pkg_resources import get_distribution
# Just avoid being removed by import optimization
_ = get_distribution

from module.base.utils import get_color
from module.device.env import IS_WINDOWS
from module.base.timer import Timer
from module.config.utils import get_server_next_update
from module.device.app_control import AppControl
from module.device.control import Control
from module.device.platform2 import Platform
from module.device.screenshot import Screenshot
from module.exception import (GameNotRunningError,
                              GameStuckError,
                              GameTooManyClickError,
                              TaskRecoveryFailed,
                              RequestHumanTakeover,
                              EmulatorNotRunningError)
from module.logger import logger


class Device(Platform, Screenshot, Control, AppControl):
    performance = PerformanceProfile()
    _login_deadline = None
    _last_control_time = None
    _screen_size_checked = False
    detect_record = set()
    click_record = deque(maxlen=15)
    stuck_timer = Timer(60, count=60).start()
    stuck_timer_long = Timer(300, count=300).start()
    stuck_long_wait_list = ['BATTLE_STATUS_S', 'PAUSE', 'LOGIN_CHECK', 'PREPARE_BEFORE_BATTLE']

    def __init__(self, *args, **kwargs):
        for trial in range(4):
            try:
                super().__init__(*args, **kwargs)
                break
            except EmulatorNotRunningError:
                if trial >= 3:
                    logger.critical('Failed to start emulator after 3 trial')
                    raise RequestHumanTakeover
                # Try to start emulator
                if self.emulator_instance is not None:
                    self.emulator_start()
                else:
                    logger.critical(
                        f'No emulator with serial "{self.config.Emulator_Serial}" found, '
                        f'please set a correct serial'
                    )
                    raise RequestHumanTakeover

        # Auto-fill emulator info
        if IS_WINDOWS and self.config.script.device.emulatorinfo_type == 'auto':
            _ = self.emulator_instance

        self.performance = PerformanceProfile(bool(self.config.script.device.low_spec_mode))
        self.detect_record = set()
        self.click_record = deque(maxlen=15)
        self.stuck_timer = Timer(self.performance.timeout(60), count=60).start()
        self.stuck_timer_long = Timer(300, count=300).start()
        self._login_deadline = None
        self._last_control_time = None
        self._error_learning = None
        self.reset_task_recovery()
        self._screenshot_interval = Timer(0.1)
        self.screenshot_interval_set()
        self._image_batch_cache_frame_id: str | None = None
        self._image_batch_cache: dict[int, dict] = {}

        # Auto-select the fastest screenshot method
        if self.config.script.device.screenshot_method == 'auto':
            self.run_simple_screenshot_benchmark()

    def reset_image_batch_cache(self, frame_id: str | None = None) -> None:
        self._image_batch_cache_frame_id = frame_id
        self._image_batch_cache = {}

    def invalidate_image_batch_cache(self) -> None:
        self.reset_image_batch_cache()

    def get_image_batch_cache(self, target, frame_id: str | None = None) -> dict | None:
        active_frame_id = self.image_frame_id if frame_id is None else frame_id
        if active_frame_id is None:
            return None
        if self._image_batch_cache_frame_id != active_frame_id:
            return None
        return self._image_batch_cache.get(id(target))

    def update_image_batch_cache(self, targets: list, results: list[dict], frame_id: str | None = None) -> None:
        active_frame_id = self.image_frame_id if frame_id is None else frame_id
        if active_frame_id is None:
            return
        if self._image_batch_cache_frame_id != active_frame_id:
            self.reset_image_batch_cache(active_frame_id)
        for target, result in zip(targets, results):
            self._image_batch_cache[id(target)] = dict(result)

    def run_simple_screenshot_benchmark(self):
        """
        Perform a screenshot method benchmark, test 3 times on each method.
        The fastest one will be set into config.
        """
        logger.info('run_simple_screenshot_benchmark')
        # Check resolution first
        # self.resolution_check_uiautomator2()
        # Perform benchmark
        from module.daemon.benchmark import Benchmark
        bench = Benchmark(config=self.config, device=self)
        method = bench.run_simple_screenshot_benchmark()
        # Set
        self.config.script.device.screenshot_method = method
        self.config.save()

    def handle_night_commission(self, daily_trigger='21:00', threshold=30):
        """
        Args:
            daily_trigger (int): Time for commission refresh.
            threshold (int): Seconds around refresh time.

        Returns:
            bool: If handled.
        """
        update = get_server_next_update(daily_trigger=daily_trigger)
        now = datetime.now()
        diff = (update.timestamp() - now.timestamp()) % 86400
        if threshold < diff < 86400 - threshold:
            return False

        # if GET_MISSION.match(self.image, offset=True):
        #     logger.info('Night commission appear.')
        #     self.click(GET_MISSION)
        #     return True

        return False

    def screenshot(self):
        """
        Returns:
            np.ndarray:
        """
        flow_guard = getattr(self, '_task_flow_guard', None)
        if flow_guard is not None:
            flow_guard.check()
        try:
            super().screenshot()
        except RequestHumanTakeover as e:
            raise RequestHumanTakeover

        if self.handle_night_commission():
            super().screenshot()

        self.reset_image_batch_cache(self.image_frame_id)
        self._await_recovery_frame = False
        handled = self.check_global_invitation()
        if not handled and not getattr(self, '_handling_invitation', False):
            self.stuck_record_check()
        return self.image

    def check_global_invitation(self):
        """Handle invitations on every device frame, on the caller's thread."""
        if not getattr(self, '_global_invitation_enabled', False) or getattr(self, '_handling_invitation', False):
            return False
        self._handling_invitation = True
        try:
            handler = getattr(self, '_invitation_handler', None)
            if handler is None:
                from tasks.base_task import BaseTask
                handler = BaseTask(config=self.config, device=self)
                self._invitation_handler = handler
            handler.config = self.config
            return handler._burst()
        finally:
            self._handling_invitation = False

    def poll_invitation_during_wait(self):
        """Observe a running game without interpreting deliberate inactivity as stuck."""
        if not self.app_is_running():
            return False
        records = set(self.detect_record)
        self.stuck_record_clear()
        self.detect_record.update(records)
        generation = getattr(self, '_accepted_invitation_generation', 0)
        self.screenshot()
        return getattr(self, '_accepted_invitation_generation', 0) != generation

    def sleep(self, second):
        from module.base.utils import ensure_time
        duration = ensure_time(second)
        if duration <= 2 or getattr(self, '_handling_invitation', False):
            time.sleep(duration)
            return
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            time.sleep(min(2, max(0, deadline - time.monotonic())))
            self.poll_invitation_during_wait()

    def release_during_wait(self):
        # Scrcpy server is still sending video stream,
        # stop it during wait
        # self.config.script.device.screenshot_method = 'scrcpy'
        if self.config.script.device.screenshot_method == 'scrcpy':
            self._scrcpy_server_stop()
        if self.config.Emulator_ScreenshotMethod == 'nemu_ipc':
            self.nemu_ipc_release()

    def stuck_record_add(self, button):
        """
        当你要设置这个时候检测为长时间的时候，你需要在这里添加
        如果取消后，需要在`stuck_record_clear`中清除
        :param button:
        :return:
        """
        self.detect_record.add(str(button))
        logger.info(f'Add stuck record: {button}')

    @contextmanager
    def login_wait(self):
        """Keep slow-login protection across clicks, with a finite total budget."""
        if not self.performance.low_spec:
            yield
            return
        previous = self._login_deadline
        self._login_deadline = previous or (time.monotonic() + self.performance.timeout(300))
        self.stuck_record_clear()
        try:
            yield
        finally:
            self._login_deadline = previous
            self.stuck_record_clear()
            self.click_record_clear()

    def _check_login_deadline(self):
        if self._login_deadline is not None and time.monotonic() >= self._login_deadline:
            self.recover_by_escape('Low spec login exceeded 900 seconds')
            return True
        return False

    def reset_task_recovery(self):
        self._native_exit_cancel_receipt = None
        self._native_exit_dialog_handling = False
        self._task_flow_guard_disabled = False
        self._escape_attempts = 0
        self._escape_generation = 0
        self._click_recovery_at = None
        self._click_recovery_reason = ''
        self._click_recovery_buttons = set()
        self._await_recovery_frame = False

    def recover_by_escape(self, reason):
        if getattr(self, '_recovery_handling', False) or getattr(self, '_native_exit_dialog_handling', False):
            raise TaskRecoveryFailed('Nested Android BACK refused during bounded recovery')
        if not self.app_is_running():
            raise GameNotRunningError('Game died')
        if self._escape_attempts >= 3:
            raise TaskRecoveryFailed(f'ESC recovery failed after 3 attempts: {reason}')
        learning = getattr(self, '_error_learning', None)
        if learning is not None:
            try:
                # Sets have unstable repr order; use a stable wait signature.
                learning_reason = ('wait:' + ','.join(sorted(self.detect_record))) if reason.startswith('Wait too long:') else reason
                plan = learning.begin(learning_reason, getattr(self, 'image', None))
                if plan:
                    logger.info(f'Learned recovery: {plan["action"]}, verify page={plan["page"]}, settle={plan["settle"]:.1f}s')
            except Exception as exc:
                logger.warning(f'Error learning disabled for this task: {exc}')
                self._error_learning = None
        if not getattr(self, '_failed_original_page_captured', False):
            try:
                from module.script.failed_task_recovery import page_key
                self._failed_original_page = page_key(self.config, self)
            except Exception:
                self._failed_original_page = None
            self._failed_original_page_captured = True
        self._escape_attempts += 1
        logger.warning(f'ESC recovery {self._escape_attempts}/3: {reason}')
        try:
            self.press_escape()
        except Exception as exc:
            raise TaskRecoveryFailed(f'Unable to send ESC: {exc}') from exc
        self.invalidate_image_batch_cache()
        self._escape_generation += 1
        self._await_recovery_frame = True
        self._click_recovery_at = None
        self._click_recovery_buttons.clear()
        self.click_record_clear()
        records = self.detect_record.copy()
        if self._login_deadline is not None:
            self._login_deadline = time.monotonic() + self.performance.timeout(300)
        self.stuck_record_clear()
        self.detect_record.update(records)

    def _check_click_recovery(self):
        if self._click_recovery_at is None:
            return False
        if time.monotonic() - self._click_recovery_at >= self.performance.timeout(60):
            self.recover_by_escape(self._click_recovery_reason)
        return True

    def stuck_record_clear(self):
        self.detect_record = {'LOGIN_CHECK'} if self._login_deadline is not None else set()
        self.stuck_timer.reset()
        self.stuck_timer_long.reset()

    def stuck_record_check(self):
        """
        Raises:
            GameStuckError:
        """
        # The outer recovery/dismissal owns a finite deadline. Do not open a
        # second native exit dialog while that same recovery is still running.
        if getattr(self, '_recovery_handling', False) or getattr(self, '_native_exit_dialog_handling', False):
            return False
        if self._check_login_deadline() or self._check_click_recovery():
            return False
        # Login has its own total deadline; battle/other long waits stay at 300s.
        if self._login_deadline is not None:
            return False
        reached = self.stuck_timer.reached()
        reached_long = self.stuck_timer_long.reached()

        if not reached:
            return False
        if not reached_long:
            for button in self.stuck_long_wait_list:
                if button in self.detect_record:
                    return False

        logger.warning('Wait too long')
        logger.warning(f'Waiting for {self.detect_record}')
        self.recover_by_escape(f'Wait too long: {self.detect_record}')
        return False

    def handle_control_check(self, button):
        if self._await_recovery_frame or self._check_login_deadline():
            return False
        if self._check_click_recovery():
            if self._await_recovery_frame or str(button) in self._click_recovery_buttons:
                return False
            self._click_recovery_at = None
            self._click_recovery_buttons.clear()
        if self.performance.click_interval and self._last_control_time is not None:
            remaining = self.performance.click_interval - (time.monotonic() - self._last_control_time)
            if remaining > 0:
                time.sleep(remaining)
        if self._check_login_deadline():
            return False
        self._last_control_time = time.monotonic()
        self.stuck_record_clear()
        self.click_record_add(button)
        history = set(self.click_record)
        try:
            self.click_record_check()
        except GameTooManyClickError as exc:
            self._click_recovery_at = time.monotonic()
            self._click_recovery_reason = str(exc)
            self._click_recovery_buttons = history
            logger.warning(f'Repeated clicks paused; wait {self.performance.timeout(60)}s before ESC')
            return False
        return True

    def click_record_add(self, button):
        self.click_record.append(str(button))

    def click_record_clear(self):
        self.click_record.clear()

    def click_record_remove(self, button):
        """
        Remove a button from `click_record`

        Args:
            button (Button):

        Returns:
            int: Number of button removed
        """
        removed = 0
        for _ in range(self.click_record.maxlen):
            try:
                self.click_record.remove(str(button))
                removed += 1
            except ValueError:
                # Value not in queue
                break

        return removed

    def confirm_task_round_progress(self):
        """Renew task residence only after a new round is confirmed in fresh frames."""
        self._task_flow_round_generation = getattr(self, '_task_flow_round_generation', 0) + 1
        self.confirm_control_progress(())

    def confirm_control_progress(self, buttons):
        """Call only after verifying a new page/round, never on a button match."""
        self._task_flow_progress_generation = getattr(self, '_task_flow_progress_generation', 0) + 1
        names = {str(button) for button in buttons}
        pending = getattr(self, '_verified_click', None)
        if pending and pending['name'] in names:
            self._verified_click = None
        for button in buttons:
            self.click_record_remove(button)
        reason = self._click_recovery_reason
        prefixes = ('Too many click for a button: ', 'Too many click between 2 buttons: ')
        for prefix in prefixes:
            if reason.startswith(prefix):
                blocked = set(reason[len(prefix):].split(', '))
                if blocked <= names:
                    self._click_recovery_at = None
                    self._click_recovery_reason = ''
                    self._click_recovery_buttons.clear()
                break

    def click_record_check(self):
        """
        Raises:
            GameTooManyClickError:
        """
        from module.device.repeat_policy import chess_repeat_unrestricted
        if chess_repeat_unrestricted(self):
            return
        if not self.click_record:
            return
        count = {}
        for key in self.click_record:
            count[key] = count.get(key, 0) + 1
        count = sorted(count.items(), key=lambda item: item[1], reverse=True)
        if count[0][1] >= self.performance.click_limit:
            logger.warning(f'Too many click for a button: {count[0][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click for a button: {count[0][0]}')
        if len(count) >= 2 and count[0][1] >= self.performance.alternating_click_limit and count[1][1] >= self.performance.alternating_click_limit:
            logger.warning(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')

    def disable_stuck_detection(self):
        """
        Disable stuck detection and its handler. Usually uses in semi auto and debugging.
        """
        logger.info('Disable stuck detection')
        self._task_flow_guard_disabled = True

        def empty_function(*arg, **kwargs):
            return False

        self.click_record_check = empty_function
        self.stuck_record_check = empty_function

    def app_start(self):
        if not self.config.script.error.handle_error:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_start()
        self.stuck_record_clear()
        self.click_record_clear()

    def app_stop(self):
        if not self.config.script.error.handle_error:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_stop()
        self.stuck_record_clear()
        self.click_record_clear()

    def wait_app_start_ready(self, timeout: float = 15.0, interval: float = 0.5) -> None:
        """
        在启动app后，等待包名切换成功且画面脱离纯黑状态。

        这里直接调用底层截图方法做静默探测，避免app刚启动时首屏黑场造成成批告警。

        Args:
            timeout: 最大等待秒数。
            interval: 每轮探测的间隔秒数。
        """
        deadline = time.monotonic() + self.performance.timeout(timeout)
        screenshot_method = self.screenshot_methods.get(
            self.config.script.device.screenshot_method,
            self.screenshot_adb
        )

        while time.monotonic() < deadline:
            if not self.app_is_running():
                time.sleep(interval)
                continue

            try:
                image = screenshot_method()
            except Exception as e:
                logger.info(f'Wait game start ready: screenshot probe failed: {e}')
                time.sleep(interval)
                continue

            color = get_color(image, area=(0, 0, 1280, 720))
            if sum(color) >= 1:
                logger.info(f'Game start ready, frame color: {color}')
                return

            time.sleep(interval)

        logger.info('Wait game start ready timeout, continue with login flow')


if __name__ == "__main__":
    device = Device(config="oas1")
    # cv2.imshow("imgSrceen", device.screenshot())  # 显示
    # cv2.waitKey(0)
    # cv2.destroyAllWindows()

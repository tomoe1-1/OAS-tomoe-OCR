# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import zerorpc
import zmq
import re
import cv2
import time
import os
import inflection
import json
import random

from datetime import date
import threading
from module.device.device import Device
from typing import Any, Callable
from datetime import datetime, timedelta
from pathlib import Path
from cached_property import cached_property
from pydantic import BaseModel, ValidationError
from threading import Thread
from multiprocessing.queues import Queue
from module.config.utils import convert_to_underscore
from module.config.config import Config
from module.device.env import IS_WINDOWS
from module.base.utils import load_module
from module.base.decorator import del_cached_property
from module.atom.scatter import RuleScatter
from module.logger import logger
from module.exception import *
from module.server.i18n import I18n
from module.image.rpc import ensure_image_server_ready, set_image_low_spec_mode
from module.ocr.rpc import (
    ensure_ocr_server_ready,
    set_ocr_logging_enabled,
    set_ocr_low_spec_mode,
    set_ocr_model_size,
    get_ocr_client,
)
from module.script import ScriptRuntimeController, ScriptRuntimeDecision
from module.script.task_completion import TaskCompletionGate
from tasks.Restart.server_update import delay_pending_tasks_for_server_update, is_server_update_window
from module.server.log_service import build_error_log_dir_name

_log_switch_lock = threading.Lock()#线程锁


class Script:
    def __init__(self, config_name: str ='oas') -> None:
        logger.hr('Start', level=0)
        self.server = None
        self.state_queue: Queue = None
        self._emulator_down = False
        self.runtime = ScriptRuntimeController(self)
        self.gui_update_task: Callable = None  # 回调函数, gui进程注册当每次config更新任务的时候更新gui的信息
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        self.completion_gate = TaskCompletionGate(Path('log/task_retry') / f'{config_name}.json')
        self._task_failure_deadlines = dict(self.completion_gate.recovery_not_before)
        self.last_task_runtime_outcome: dict[str, Any] | None = None
        # 连续任务休息只统计调度器未进入等待状态的墙钟时间。
        self._continuous_task_started_at: float | None = None
        self._continuous_task_limit_seconds: int | None = None
        self._continuous_task_interval_range: tuple[int, int] | None = None
        # 运行loop的线程
        self.loop_thread: Thread = None
        # 低配模式属于进程级运行参数，只在脚本进程创建时读取一次。
        # OASX 中途修改配置不会影响正在运行的脚本。
        self.low_spec_mode = bool(self.config.script.device.low_spec_mode)
        set_image_low_spec_mode(self.low_spec_mode)
        set_ocr_low_spec_mode(self.low_spec_mode)
        # 低配模式与普通模式统一使用现有 PaddleOCR medium 模型，
        # 避免小模型降低关键文字的识别稳定性。
        set_ocr_model_size('medium')
        self.resource_precache_enable = bool(
            self.config.script.device.resource_precache_enable
        )
        if self.low_spec_mode:
            logger.info(
                'Low spec mode enabled: frame cache=10s, '
                'OCR model=medium, OCR timeout=30s, OCR cache=2s, '
                'image threshold unchanged; idle timeout=180s, login budget=900s, '
                'screenshot interval>=0.8s, combat>=1.5s, control interval>=1.2s'
            )

    @cached_property
    def config(self) -> "Config":
        try:
            from module.config.config import Config
            config = Config(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self) -> Device | None:
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        """
        占位函数，在alas中是检查服务器是否正常的
        :return:
        """
        return None

    def save_error_log(self):
        """
        保存错误现场到 ./log/error/<script_name>_<timestamp_ms>。

        保存内容包括:
        - 最近一段截图, 文件名为时间戳 PNG。
        - 当前脚本日志的截取内容, 文件名为 log.txt。

        说明:
        - 新错误目录名会带上脚本名, 便于前端区分不同脚本产生的错误。
        - 目录名和脚本名都会经过统一净化, 避免路径注入。
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.script.error.save_error:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            # 用统一规则生成错误目录名, 目录格式为 <script_name>_<timestamp_ms>。
            folder_name = build_error_log_dir_name(self.config_name, int(time.time() * 1000))
            folder = f'./log/error/{folder_name}'
            logger.warning(f'Saving error: {folder}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def init_server(self, port: int) -> int:
        """
        初始化zerorpc服务，返回端口号
        :return:
        """
        self.server = zerorpc.Server(self)
        try:
            self.server.bind(f'tcp://127.0.0.1:{port}')
            return port
        except zmq.error.ZMQError:
            logger.error(f"Ocr server cannot bind on port {port}")
            return None

    def run_server(self) -> None:
        """
        启动zerorpc服务
        :return:
        """
        self.server.run()

    def gui_args(self, task: str) -> str:
        """
        获取给gui显示的参数
        :return:
        """
        return self.config.gui_args(task=task)

    def gui_menu(self) -> str:
        """
        获取给gui显示的菜单
        :return:
        """
        return self.config.gui_menu

    def gui_task(self, task: str) -> str:
        """
        获取给gui显示的任务 的参数的具体值
        :return:
        """
        return self.config.model.gui_task(task=task)

    def gui_set_task(self, task: str, group: str, argument: str, value) -> bool:
        """
        设置给gui显示的任务 的参数的具体值
        :return:
        """
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)
        # pandtic验证
        if isinstance(value, str):
            if len(value) == 8:
                try:
                    value = datetime.strptime(value, '%H:%M:%S').time()
                except ValueError:
                    pass


        path = f'{task}.{group}.{argument}'
        task_object = getattr(self.config.model, task, None)
        group_object = getattr(task_object, group, None)
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{value} failed')
            return False

        try:
            setattr(group_object, argument, value)
            argument_object = getattr(group_object, argument, None)
            logger.info(f'Set arg {task}.{group}.{argument}.{argument_object}')
            self.config.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    @zerorpc.stream
    def gui_mirror_image(self):
        """
        获取给gui显示的镜像
        :return: cv2的对象将 numpy 数组转换为字节串。接下来MsgPack 进行序列化发送方将图像数据转换为字节串
        """
        # return msgpack.packb(cv2.imencode('.jpg', self.device.screenshot())[1].tobytes())
        img = cv2.cvtColor(self.device.screenshot(), cv2.COLOR_RGB2BGR)
        self.device.stuck_record_clear()
        ret, buffer = cv2.imencode('.jpg', img)
        yield buffer.tobytes()

    def _gui_update_tasks(self) -> None:
        """
        获取更新任务后 pending waiting 的任务 和 当前的任务的数据。打包给gui显示
        :return:
        """
        data = {}
        pending = []
        waiting = []
        task = {}
        if self.config.task is not None and self.config.task.next_run < datetime.now():
            task["name"] = self.config.task.command
            task["next_run"] = str(self.config.task.next_run)
        data["task"] = task

        for p in self.config.pending_task[1:]:
            item = {"name": p.command, "next_run": str(p.next_run)}
            pending.append(item)

        for w in self.config.waiting_task:
            item = {"name": w.command, "next_run": str(w.next_run)}
            waiting.append(item)


        data["pending"] = pending
        data["waiting"] = waiting

        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def _gui_set_status(self, status: str) -> None:
        """
        设置给gui显示的状态
        :param status: 可以在gui中显示的状态 有 "Init", "Empty"(不显示), "Run"(运行中), "Error", "Free"(空闲)
        :return:
        """
        data = {"status": status}
        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def gui_task_list(self) -> str:
        """
        获取给gui显示的任务列表
        :return:
        """
        result = {}
        for key, value in self.config.model.dict().items():
            if isinstance(value, str):
                continue
            if key == "restart":
                continue
            if "scheduler" not in value:
                continue

            scheduler = value["scheduler"]
            item = {"enable": scheduler["enable"],
                    "next_run": str(scheduler["next_run"])}
            key = self.config.model.type(key)
            result[key] = item
        return json.dumps(result)

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"[{self.config_name}] exited. Reason: Update")
            #         exit(0)

            time.sleep(5)

            # Do not instantiate/wake a closed emulator just to inspect an invite.
            device = self.__dict__.get('device')
            runtime = self.__dict__.get('runtime')
            if device is not None and not getattr(runtime, 'emulator_down', False):
                device._global_invitation_enabled = True
                if device.poll_invitation_during_wait():
                    logger.info('Invitation accepted during wait; refresh scheduler')
                    return False
                if runtime is not None and runtime.release_verified_home_wait():
                    return False

            if self.config.should_reload():
                return False

    @staticmethod
    def _in_sleep_window(t, start, end) -> bool:
        if start == end:
            return False
        if start < end:
            return start <= t < end
        return t >= start or t < end

    @staticmethod
    def _next_time_point(now: datetime, end) -> datetime:
        candidate = now.replace(hour=end.hour, minute=end.minute, second=end.second, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _parse_continuous_task_interval(value: str) -> tuple[int, int]:
        """解析连续任务休息间隔，格式为“最小分钟,最大分钟”。"""
        matched = re.fullmatch(r'\s*(\d+)\s*[,，]\s*(\d+)\s*', str(value))
        if matched is None:
            raise ValueError(f'Invalid continuous task rest interval: {value!r}')
        lower, upper = (int(item) for item in matched.groups())
        if lower <= 0 or upper <= 0 or lower > upper:
            raise ValueError(f'Invalid continuous task rest interval: {value!r}')
        return lower, upper

    @staticmethod
    def _sample_continuous_task_rest_seconds() -> tuple[int, str]:
        """休息时长：90% 落在2-8分钟的对称三角分布，其余10% 在8-20分钟内逐渐降低概率。"""
        if random.random() < 0.9:
            minutes = random.triangular(2, 8, 5)
            branch = 'short'
        else:
            minutes = random.triangular(8, 20, 8)
            branch = 'long_tail'
        return max(120, min(1200, round(minutes * 60))), branch

    def _reset_continuous_task_rest(self, *, log_idle: bool = False) -> None:
        if log_idle and self._continuous_task_started_at is not None:
            logger.info('Scheduler entered task waiting state; reset continuous task timer')
        self._continuous_task_started_at = None
        self._continuous_task_limit_seconds = None
        self._continuous_task_interval_range = None

    def _handle_continuous_task_rest(self) -> bool:
        """在新任务开始前处理连续运行休息，已休息时返回 True 要求重新调度。"""
        device_config = self.config.script.device
        if not device_config.continuous_task_rest_enable:
            self._reset_continuous_task_rest()
            return False

        try:
            interval_range = self._parse_continuous_task_interval(
                device_config.continuous_task_rest_interval
            )
        except ValueError as exc:
            logger.warning(f'{exc}; fallback to 60,120 minutes')
            interval_range = (60, 120)

        now = time.monotonic()
        if (
            self._continuous_task_started_at is None
            or self._continuous_task_interval_range != interval_range
        ):
            limit_minutes = random.randint(*interval_range)
            self._continuous_task_started_at = now
            self._continuous_task_limit_seconds = limit_minutes * 60
            self._continuous_task_interval_range = interval_range
            logger.info(
                'Continuous task timer started: '
                f'limit={limit_minutes}m, range={interval_range[0]}-{interval_range[1]}m'
            )
            return False

        elapsed = now - self._continuous_task_started_at
        if elapsed < self._continuous_task_limit_seconds:
            return False

        rest_seconds, branch = self._sample_continuous_task_rest_seconds()
        logger.info(
            'Continuous task limit reached: '
            f'elapsed={elapsed / 60:.1f}m, '
            f'rest={rest_seconds / 60:.1f}m, distribution={branch}'
        )
        self.wait_until(datetime.now() + timedelta(seconds=rest_seconds))
        self._reset_continuous_task_rest()
        logger.info('Continuous task rest finished; reschedule pending tasks')
        return True

    def _antiban_wake_time(self, now: datetime):
        ab = self.config.script.anti_ban
        if not ab.enable:
            return None
        wake = None
        if self._in_sleep_window(now.time(), ab.sleep_start, ab.sleep_end):
            wake = self._next_time_point(now, ab.sleep_end)
        limit = ab.daily_active_limit.total_seconds()
        if limit > 0:
            if getattr(self, '_active_date', None) != now.date():
                self._active_date = now.date()
                self._active_seconds_today = 0
            rest_until = getattr(self, '_rest_until', None)
            if rest_until and now < rest_until:
                wake = max(wake, rest_until) if wake else rest_until
            elif getattr(self, '_active_seconds_today', 0) >= limit:
                self._rest_until = now + ab.long_rest_duration
                self._active_seconds_today = 0
                wake = max(wake, self._rest_until) if wake else self._rest_until
        return wake

    def get_next_task(self) -> str:
        """
        获取下一个任务的名字, 大驼峰。
        :return:
        """
        while True:
            task = self.completion_gate.select(self.config)
            self.config.task = task
            if self.state_queue:
                self.state_queue.put({"schedule": self.config.get_schedule_data()})
            now = datetime.now()
            antiban_wake = self._antiban_wake_time(now)
            if antiban_wake is not None:
                task.next_run = max(task.next_run, antiban_wake)
            # 任务时间到了返回任务名称
            if task.next_run <= now:
                if self._handle_continuous_task_rest():
                    continue
                return task.command
            self._reset_continuous_task_rest(log_idle=True)
            # 根据策略执行等待逻辑
            self.last_task_runtime_outcome = None
            decision = self.runtime.handle_wait_during_idle(task.next_run)
            outcome = self.last_task_runtime_outcome or {}
            if decision == ScriptRuntimeDecision.RESCHEDULE:
                logger.info('Idle wait requested scheduler refresh, reload config and reschedule')
                del_cached_property(self, "config")
            elif decision == ScriptRuntimeDecision.FAILED:
                command = outcome.get('task') or 'GotoMain'
                retry_at = self._defer_failed_task(command)
                self.record_task_result(command, False)
                if not self.config.script.error.handle_error:
                    raise RequestHumanTakeover('Idle preparation failed with HandleError disabled')
                # A failed idle GotoMain/Restart must not form a tight loop while
                # the business task is waiting. Wake for whichever is due first.
                if retry_at is not None:
                    self.wait_until(min(task.next_run, retry_at))
                del_cached_property(self, "config")

    def exception_handler(self, e: Exception, command: str) -> None:
        # 处理御魂溢出
        from tasks.Utils.post_diagnotor import PostDiagnotor, AnalyzeType
        image = getattr(self.device, 'image', None)
        # image为None则不做处理
        if image is None:
            return
        analyse_type = PostDiagnotor().handle(e=e, command=command, image=image)
        if analyse_type == AnalyzeType.SoulOverflow:
            self.config.task_call('SoulsTidy')
            time.sleep(1)

    def _reset_task_runtime_outcome(self) -> None:
        self.last_task_runtime_outcome = None
        self._last_failure_scheduled = None
        if 'config' in self.__dict__:
            self.config.task_runtime_outcome = None

    def _defer_failed_task(self, command: str) -> datetime | None:
        """Apply the GUI failure interval once per attempt and retain progress."""
        if not command:
            return None
        outcome = self.last_task_runtime_outcome or {}
        if outcome.get('status') == 'server_update_delayed':
            return outcome.get('wait_until')
        if (outcome.get('status') == 'startup_recovery_failed'
                or (command == 'Restart' and getattr(self, '_startup_recovery_active', False))):
            # Starting a stopped game is runtime preparation, not a scheduled
            # task retry. Never persist the GUI failure interval for this path.
            retry_at = outcome.get('wait_until') or datetime.now() + timedelta(seconds=5)
            self._set_task_runtime_outcome('Restart', 'startup_recovery_failed', retry_at)
            return retry_at
        previous = getattr(self, '_last_failure_scheduled', None)
        if previous is not None and previous[0] == command:
            return previous[1]
        if command == 'GotoMain':
            # Home recovery is independent of business and scheduled Restart.
            retry_at = datetime.now() + timedelta(minutes=5)
            # getattr 与下方 _clear_recovery_failure 等处保持一致：不假设
            # __init__ 一定跑过（测试用 __new__ 构造实例），否则直接赋值会
            # 抛 AttributeError 而不是按“无既有 deadline”处理。
            deadlines = getattr(self, '_task_failure_deadlines', None)
            if deadlines is None:
                deadlines = self._task_failure_deadlines = {}
            deadlines['GotoMain'] = retry_at
            self._last_failure_scheduled = (command, retry_at)
            self.completion_gate.release_recovery('GotoMain')
            self.completion_gate.defer_recovery('GotoMain', retry_at)
            self._set_task_runtime_outcome(command, 'incomplete')
            logger.warning(f'Home recovery failed; retry at {retry_at}; business retries unchanged')
            return retry_at
        # Real tasks keep their configured failure intervals.
        scheduler_command = command
        self.config.task_delay(task=scheduler_command, success=False, server=False)
        task_model = getattr(self.config.model, convert_to_underscore(scheduler_command), None)
        if task_model is None or not hasattr(task_model, 'scheduler'):
            raise ScriptError(f'Cannot defer failed task {command}: scheduler is missing')
        retry_at = task_model.scheduler.next_run
        deadlines = getattr(self, '_task_failure_deadlines', {})
        deadlines[command] = retry_at
        deadlines[scheduler_command] = retry_at
        self._task_failure_deadlines = deadlines
        self._last_failure_scheduled = (command, retry_at)
        gate = getattr(self, 'completion_gate', None)
        if gate is not None and gate.task == command:
            gate.defer(command, retry_at)
        elif gate is not None and gate.task and command == 'Restart':
            # Failed environment recovery must also delay the pinned business
            # task, including after the backend process is restarted.
            gate.defer(gate.task, retry_at)
        if gate is not None and scheduler_command == 'Restart':
            gate.defer_recovery(scheduler_command, retry_at)
            if command == 'GotoMain':
                gate.defer_recovery(command, retry_at)
        if not outcome:
            self._set_task_runtime_outcome(command, 'incomplete')
        if command not in ('Restart', 'GotoMain'):
            try:
                from module.script.failed_task_recovery import remember
                remember(self.config, self.device, self.config_name, command)
            except Exception as error:
                logger.warning(f'Cannot save failed-page identity: {error}')
        logger.warning(f'Task `{command}` failed; retry at {retry_at} using scheduler.failure_interval')
        return retry_at

    def _clear_recovery_failure(self, command: str) -> None:
        if command not in ('Restart', 'GotoMain'):
            return
        store = getattr(self.config, 'failed_task_store', None)
        if store is not None and command == 'Restart':
            store.clear(command)
        getattr(self, '_task_failure_deadlines', {}).pop(command, None)
        gate = getattr(self, 'completion_gate', None)
        if gate is not None:
            gate.release_recovery(command)

    def _set_task_runtime_outcome(self, task: str, status: str, wait_until: datetime | None = None) -> None:
        outcome = {
            'task': task,
            'status': status,
        }
        if wait_until is not None:
            outcome['wait_until'] = wait_until
        self.last_task_runtime_outcome = outcome
        if 'config' in self.__dict__:
            self.config.task_runtime_outcome = outcome

    def _capture_task_runtime_outcome(self, command: str) -> None:
        outcome = getattr(self.config, 'task_runtime_outcome', None)
        self.last_task_runtime_outcome = outcome if isinstance(outcome, dict) else None
        if self.last_task_runtime_outcome is None:
            return
        status = self.last_task_runtime_outcome.get('status')
        if status == 'server_update_delayed':
            wait_until = self.last_task_runtime_outcome.get('wait_until')
            logger.info(f'{command} runtime outcome: server_update_delayed (wait_until={wait_until})')
            if isinstance(wait_until, datetime):
                self.runtime.server_update_wait_until = wait_until
                self.runtime.server_update_wait_log_until = None
            return
        if command != 'Restart':
            return
        if status == 'recovered':
            logger.info('Restart runtime outcome: recovered')
            return
        logger.info(f'Restart runtime outcome: {status}')

    def _delay_tasks_for_server_update(self, task: str, reason: str) -> bool:
        if not is_server_update_window():
            return False

        delay_target = delay_pending_tasks_for_server_update(self.config, reason=reason)
        self._set_task_runtime_outcome(task=task, status='server_update_delayed', wait_until=delay_target)
        return True

    def run(self, command: str) -> bool:
        """
        :param command:  大写驼峰命名的任务名字
        :return:
        """
        if command == 'start' or command == 'goto_main':
            logger.error(f'Invalid command `{command}`')

        self._reset_task_runtime_outcome()
        from module.atom.click import RuleClick
        RuleClick.reset_task_points()
        set_ocr_logging_enabled(self.config.global_game.ocr.save_ocr_log)
        task_instance = None
        try:
            self.device._global_invitation_enabled = True
            self.device.reset_task_recovery()
            self.device._failed_original_page = None
            self.device._failed_original_page_captured = False
            logger.info('Recovery policy: Android BACK v2; maximum 3 attempts; page verification required')
            self.config._learning_task_outcomes = {}
            self.device._error_learning = None
            try:
                from module.device.error_learning import create_session
                self.device._error_learning = create_session(self.config, command, logger.warning, self.device)
            except Exception as exc:
                logger.warning(f'Error learning unavailable; original behavior retained: {exc}')
            self.device.screenshot()
            if command not in ('Restart', 'GotoMain'):
                from module.script.failed_task_recovery import prepare, clear
                route = prepare(self.config, self.device, self.config_name, command)
                clear(self.config_name, command)
                logger.info(f'Failed-stage entry for {command}: {route}')
            module_name = 'script_task'
            module_path = str(Path.cwd() / 'tasks' / command / (module_name + '.py'))
            logger.info(f'module_path: {module_path}, module_name: {module_name}')
            task_module = load_module(module_name, module_path)
            task_instance = task_module.ScriptTask(config=self.config, device=self.device)
            # prepare() has already verified the failed-stage entry. Recovery
            # during preparation belongs to the previous stack, not this task.
            # A new ESC increments the device generation and is still handled.
            task_instance._seen_escape_generation = getattr(self.device, '_escape_generation', 0)
            from module.script.task_flow_guard import TaskFlowGuard
            with TaskFlowGuard(task_instance, self.device, command=command):
                task_instance.run()
        except Exception as e:
            if not isinstance(e, TaskEnd):
                if command not in ('Restart', 'GotoMain'):
                    try:
                        from module.script.failed_task_recovery import remember
                        remember(self.config, self.device, self.config_name, command)
                    except Exception as recovery_error:
                        logger.warning(f'Cannot save failed-page identity: {recovery_error}')
                self._finish_error_learning(False, error=e)
                try:
                    from module.script.auto_repair import queue_repair
                    device = self.__dict__.get('device')
                    image = getattr(device, 'image', None) if device is not None else None
                    page = getattr(getattr(task_instance, 'navigator', None), 'current_page', None)
                    page_name = getattr(page, 'name', '')
                    phase = f'{command}:{page_name}' if page_name else command
                    if queue_repair(command, e, image=image, phase=phase):
                        logger.warning(f'Smart task diagnosis queued for {command}')
                except Exception as repair_error:
                    logger.warning(f'Automatic code repair unavailable: {repair_error}')
            result = self._handle_task_exception(e, command)
            if not result:
                self._defer_failed_task(command)
            elif (self.last_task_runtime_outcome or {}).get('status') != 'server_update_delayed':
                self._clear_recovery_failure(command)
            if isinstance(e, TaskEnd):
                if result and (self.last_task_runtime_outcome or {}).get('status') != 'server_update_delayed':
                    from module.script.failed_task_recovery import clear, write_switch
                    clear(self.config_name, command)
                    if (self.last_task_runtime_outcome or {}).get('cleanup_pending') is True:
                        # Verified business success is separate from navigation.
                        # Persist the return obligation before releasing its gate.
                        declared = self.config._learning_task_outcomes.get(convert_to_underscore(command))
                        if declared is True:
                            write_switch(self.config_name, {'task': command})
                outcome = self.last_task_runtime_outcome or {}
                declared_success = self.config._learning_task_outcomes.get(convert_to_underscore(command))
                verified = declared_success is True or outcome.get('status') in ('success', 'recovered')
                self._finish_error_learning(result and verified and declared_success is not False
                                            and outcome.get('status') in (None, 'success', 'recovered'))
                if command == 'GotoMain' and result and outcome.get('status') != 'server_update_delayed':
                    try:
                        from module.script.auto_repair import queue_activity_scan
                        if queue_activity_scan(task_instance):
                            logger.info('Daily activity scan queued')
                    except Exception as scan_error:
                        logger.warning(f'Activity scan unavailable: {scan_error}')
            return result
        self._finish_error_learning(False)
        self._defer_failed_task(command)
        return False

    def _finish_error_learning(self, success, error=None):
        learning = getattr(self.device, '_error_learning', None)
        if learning is not None:
            try:
                learning.finish(success, error=error)
            except Exception as exc:
                logger.warning(f'Unable to save error experience; task outcome unchanged: {exc}')

    def record_task_result(self, task: str, success: bool) -> None:
        """Count consecutive failures; three in a row means a human must look.

        退出策略（2026-10-07 修正）：
        本方法一度对**所有**非 GotoMain 任务改成「连败 3 次只 warning、不退出」。
        但 loop() 在任务失败且 `handle_error=True` 时走 `continue`（见 :881 之后），
        而失败任务的 checkpoint 被 retain ⇒ get_next_task() 仍返回同一个任务
        ⇒ 永久失败的任务会被**无限重试、永不停机**，也不再退出等人接管。
        实测症状：tests/test_task_completion_gate.py::
        test_three_failures_stop_without_advancing_and_keep_checkpoint 死循环
        （faulthandler 抓到 script.py:872 loop -> task_completion.retain 反复调用）。
        因此恢复「连败 3 次即退出」的有界语义。

        GotoMain 同样计入这个计数，这是有意的（2026-10-07 大哥拍板）：
        回主页反复失败意味着配置错了或功能坏了，属于**该叫人来看**的情况。
        它的重试走独立的 5 分钟短周期（见 `_defer_failed_task`），所以三次
        连败约 10 分钟就升级上报，而不是像借用 Restart 间隔时那样拖 9 小时。
        `runtime_controller._bounded_home_retry_at` 把过长的回主页退避一并压到
        5 分钟，与此同源：宁可早叫人，不要静默地等几小时。

        ⚠️ 别把这条路径当成"不会空转"：闲置等待中的 GotoMain 失败走的是
        get_next_task 的 `while`，不是 loop()。守卫见
        tests/test_task_failure_policy_review.py::
        test_repeated_home_failure_reports_human_instead_of_waiting_forever
        （把 GotoMain 从计数里摘掉会报红）。
        """
        failed = 0 if success else self.failure_record.get(task, 0) + 1
        self.failure_record[task] = failed
        if failed < 3:
            return

        logger.critical(f"Task `{task}` failed 3 or more times.")
        logger.critical("Possible reason #1: You haven't used it correctly. "
                        "Please read the help text of the options.")
        logger.critical("Possible reason #2: There is a problem with this task. "
                        "Please contact developers or try to fix it yourself.")
        logger.critical('Request human takeover')
        self.config.notifier.push(
            title=f'{I18n.trans_zh_cn(task)}{task}',
            content=f"<{self.config_name}> 任务连续失败三次，请上线查看"
        )
        if self.config.script.error.error_repeated:
            self.device.emulator_stop()
        exit(1)

    def loop(self):
        """
        Main loop of scheduler.
        :return:
        """
        with _log_switch_lock:
            logger.set_file_logger(self.config_name, do_cleanup=True)
        start_day = date.today()
        logger.info(f'Start scheduler loop: {self.config_name}')
        if self.resource_precache_enable:
            logger.info('Resource precache enabled: warming up OCR model')
            try:
                get_ocr_client().warmup()
            except Exception as exc:
                logger.exception(exc)
                raise ScriptError('OCR model resource precache failed') from exc
            logger.info('Resource precache completed; scheduler tasks can start')
        self.config.model.running_task = ''
        self._active_seconds_today = 0
        self._active_date = date.today()
        self._rest_until = None

        # Update GUI 防呆, 读取设置并立刻显示后台模拟器到前台
        if not self.config.script.device.run_background_only and IS_WINDOWS:
            from module.device.platform2.platform_windows import minimize_by_name, show_window_by_name
            target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
            if self.config.script.device.emulator_window_minimize:
                minimize_by_name(target_window_name)
                logger.info(f'重新显示: {target_window_name}')
            else:
                show_window_by_name(target_window_name)
                
        while 1:
            if date.today() > start_day:
                with _log_switch_lock:
                    logger.set_file_logger(self.config_name, do_cleanup=True)
                start_day = date.today()

            try:
                from module.script.auto_repair import resume_pending_repairs
                resume_pending_repairs()
            except Exception as repair_error:
                logger.warning(f'Pending repair queue unavailable: {repair_error}')

            task = ""
            try:
                # Get task
                task = self.get_next_task()
                # Skip first restart
                if (self.is_first_task and task == 'Restart' and not self.completion_gate.task
                        and not (getattr(self.config, 'failed_task_store', None)
                                 and 'Restart' in self.config.failed_task_store.read())):
                    logger.info('Skip task `Restart` at scheduler start')
                    self.config.task_delay(task='Restart', success=True, server=True)
                    del_cached_property(self, 'config')
                    continue
                self.completion_gate.retain(task)
                self._reset_task_runtime_outcome()
                decision = self.runtime.prepare_task_execution(task)
            except Exception as e:
                if not self._handle_task_exception(e, task) and task:
                    self._defer_failed_task(task)
                    self.record_task_result(task, False)
                    if not self.config.script.error.handle_error:
                        break
                # 本轮 prepare 失败,重新调度
                del_cached_property(self, 'config')
                continue

            if decision == ScriptRuntimeDecision.RESCHEDULE:
                logger.info(f'Runtime preparation for `{task}` requested reschedule, reload config and retry scheduling')
                del_cached_property(self, 'config')
                continue
            if decision == ScriptRuntimeDecision.FAILED:
                if (self.last_task_runtime_outcome or {}).get('status') == 'startup_recovery_failed':
                    # No business task ran: keep its progress/schedule intact
                    # and retry environment preparation after the short wait.
                    self.wait_until(self.last_task_runtime_outcome['wait_until'])
                    del_cached_property(self, 'config')
                    continue
                self._defer_failed_task(task)
                self.record_task_result(task, False)
                if not self.config.script.error.handle_error:
                    break
                del_cached_property(self, 'config')
                continue

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            self.config.model.running_task = task
            _task_start = datetime.now()
            RuleScatter.begin_task(task)
            try:
                success = self.run(inflection.camelize(task))
            finally:
                RuleScatter.end_task(task)
            self.config.model.running_task = ''
            if success:
                store = getattr(self.config, 'failed_task_store', None)
                if store is not None:
                    store.clear(task)
                self.completion_gate.release(task)
                logger.info(f'Scheduler: End task `{task}`')
            else:
                self.completion_gate.retain(task)
                self._defer_failed_task(task)
                logger.warning(f'Scheduler: task `{task}` incomplete; progress retained until its retry time')
            self.is_first_task = False
            if self._active_date != date.today():
                self._active_date = date.today()
                self._active_seconds_today = 0
            self._active_seconds_today += (datetime.now() - _task_start).total_seconds()

            self.record_task_result(task, success)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.script.error.handle_error:
                del_cached_property(self, 'config')
                continue
            else:
                break

    def _handle_task_exception(self, e: Exception, command: str) -> bool:
        """
        统一处理任务执行 / 准备阶段抛出的异常。
        Returns:
            True  -> 视为正常结束或已自动恢复 (例如已 task_call('Restart')),
                     调度器继续推进
            False -> 视为失败,脚本继续运行
        对致命异常 (ScriptError / RequestHumanTakeover / 未识别 Exception)
        在内部直接 exit(1)。
        """
        if isinstance(e, TaskRecoveryFailed):
            logger.error(e)
            self.save_error_log()
            self._set_task_runtime_outcome(task=command, status='esc_recovery_failed')
            return False

        if isinstance(e, TaskEnd):
            self._capture_task_runtime_outcome(command)
            declared = getattr(self.config, '_learning_task_outcomes', {}).get(convert_to_underscore(command))
            outcome = self.last_task_runtime_outcome or {}
            if declared is False or outcome.get('status') in ('failed', 'incomplete', 'esc_recovery_failed'):
                logger.warning(f'{command} ended without completing its work; retain the task')
                self._set_task_runtime_outcome(task=command, status='incomplete')
                return False
            return True

        if isinstance(e, GameNotRunningError):
            logger.warning(e)
            self.exception_handler(e=e, command=command)
            self.config.task_call('Restart')
            self._set_task_runtime_outcome(task=command, status='restart_required')
            return False

        if isinstance(e, (GameStuckError, GameTooManyClickError)):
            logger.error(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}',
                                      content=f"<{self.config_name}> GameStuckError or GameTooManyClickError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False

        if isinstance(e, GameBugError):
            logger.warning(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False

        if isinstance(e, GamePageUnknownError):
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            if command == 'GotoMain' and self._delay_tasks_for_server_update(
                    task=command,
                    reason='failed to goto main during morning server update window',
            ):
                logger.info('GotoMain failed during server update window, delayed pending tasks and reschedule')
                return False
            logger.critical('Game page unknown')
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> GamePageUnknownError",
            )
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False

        if isinstance(e, ScriptError):
            self._defer_failed_task(command)
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> ScriptError",
            )
            exit(1)

        if isinstance(e, RequestHumanTakeover):
            self._defer_failed_task(command)
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('Request human takeover')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> RequestHumanTakeover",
            )
            exit(1)

        # generic
        self._defer_failed_task(command)
        logger.exception(e)
        self.exception_handler(e=e, command=command)
        self.save_error_log()
        self.config.notifier.push(
            title=f'{I18n.trans_zh_cn(command)}{command}',
            content=f"<{self.config_name}> Exception occured",
        )
        exit(1)
        return False

    def start_loop(self) -> None:
        """
        创建一个线程，运行loop
        :return:
        """
        if self.loop_thread is None:
            self.loop_thread = Thread(target=self.loop, name='Script_loop')
            self.loop_thread.start()


if __name__ == "__main__":
    ensure_image_server_ready()
    ensure_ocr_server_ready()
    script = Script("oas1")
    script.loop()

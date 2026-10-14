# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from contextvars import ContextVar
from math import ceil, hypot
from time import monotonic

from module.device.human_click import TargetBox, reset_task, sample_point
from module.logger import logger


class RuleClick:
    _task_click_state = ContextVar('ruleclick_task_click_state', default=None)
    _recent_limit = 6
    _history_limit = 256
    # ``profile`` 只保留上游的资源兼容语义；实际落点由 human_click 的
    # 截断高斯采样决定，因此三个档位共用同一实现。
    _DENSITY_PROFILES = {
        None: (0.98, 0.02),
        'High': (1.0, 0.0),
        'More': (0.95, 0.05),
    }

    @classmethod
    def reset_task_points(cls):
        """每次任务启动清空各规则的近期落点和全局上一落点。"""
        RuleClick._task_click_state.set({
            'rules': {},
            'previous_point': None,
            'history': [],
        })
        reset_task()

    def __init__(
        self,
        roi_front: tuple,
        roi_back: tuple,
        name: str = None,
        functional: bool = True,
        profile: str = None,
    ) -> None:
        """
        初始化
        :param roi_front:
        :param roi_back:
        """
        self.roi_front = roi_front
        self.roi_back = roi_back
        if name:
            self.name = name
        else:
            self.name = 'click'
        self.functional = functional
        if profile in ('', 'Default'):
            profile = None
        if isinstance(profile, str):
            profile = {'high': 'High', 'more': 'More'}.get(profile.lower(), profile)
        if profile not in self._DENSITY_PROFILES:
            raise ValueError('RuleClick profile must be High, More, or omitted')
        self.profile = profile

    @classmethod
    def task_click_history(cls) -> tuple:
        state = cls._task_click_state.get()
        if state is None:
            cls.reset_task_points()
            state = cls._task_click_state.get()
        return tuple(state['history'])

    def _record_task_click(self, point: tuple[int, int]) -> None:
        task_state = RuleClick._task_click_state.get()
        if task_state is None:
            self.reset_task_points()
            task_state = RuleClick._task_click_state.get()
        task_state['previous_point'] = point
        history = task_state['history']
        history.append((point[0], point[1], monotonic(), self.functional))
        del history[:-self._history_limit]

    def coord(self) -> tuple:
        """
        获取坐标，在 roi_front 内按截断高斯采样。
        :return:
        """
        return self._circle_normal_coord(self.roi_front)

    def coord_more(self) -> tuple:
        """
        在 roi_back 内按截断高斯采样。
        :return:
        """
        return self._circle_normal_coord(self.roi_back)

    def _circle_normal_coord(self, roi: tuple) -> tuple:
        """Keep the task-level recent-point bookkeeping, delegate the draw to
        human_click so every click shares one sampling implementation."""
        x, y, width, height = roi
        if width <= 0 or height <= 0:
            raise ValueError(f'RuleClick roi must have positive size: {roi}')

        left, right = ceil(x), ceil(x + width)
        top, bottom = ceil(y), ceil(y + height)
        if left >= right or top >= bottom:
            raise ValueError(f'RuleClick roi contains no integer pixel: {roi}')
        task_state = RuleClick._task_click_state.get()
        if task_state is None:
            self.reset_task_points()
            task_state = RuleClick._task_click_state.get()
        rules = task_state['rules']
        # 动态创建的同名同区域规则也复用本任务的点击状态。
        key = (self.name, tuple(roi), self.profile)
        if key not in rules:
            rules[key] = {
                'recent': [],
                'count': 0,
            }
        rule_state = rules[key]

        click_x, click_y = sample_point(TargetBox(x, y, width, height))

        recent = rule_state['recent']
        recent.append((click_x, click_y))
        del recent[:-self._recent_limit]
        rule_state['count'] += 1
        self._record_task_click((click_x, click_y))
        return click_x, click_y

    @staticmethod
    def _point_in_inner_ellipse(click_x: float, click_y: float, roi: tuple) -> bool:
        x, y, width, height = roi
        center_x, center_y = x + width / 2, y + height / 2
        # 用像素中心判断，保证 1x1 等极小区域也有内椭圆落点。
        normalized_x = (click_x + 0.5 - center_x) / max(width / 2, 0.5)
        normalized_y = (click_y + 0.5 - center_y) / max(height / 2, 0.5)
        return normalized_x ** 2 + normalized_y ** 2 <= 1.0

    @property
    def center(self) -> tuple:
        """
        返回roi_front的中心坐标
        :return:
        """
        x, y, w, h = self.roi_front
        return x + w // 2, y + h // 2

    def move(self, x: int, y: int) -> None:
        """
        移动roi_front, 需要限幅x是0-1280, y是0-720
        :param x:
        :param y:
        :return:
        """
        x, y, w, h = self.roi_front
        x += x
        y += y
        if x <= 0:
            x = 0
        elif x >= 1280:
            x = 1280

        if y <= 0:
            y = 0
        elif y >= 720:
            y = 720

        self.roi_front = x, y, w, h

    def __repr__(self):
        return self.name

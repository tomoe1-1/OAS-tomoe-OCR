"""Cooperative bounds for task loops that keep capturing without real progress."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as clock_time, timedelta
from math import isfinite
import sys
import time
from types import FrameType

from module.exception import TaskRecoveryFailed


_MISSING = object()


def _positive_seconds(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, timedelta):
        value = value.total_seconds()
    elif isinstance(value, clock_time):
        value = value.hour * 3600 + value.minute * 60 + value.second
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if isfinite(seconds) and seconds > 0 else None


@dataclass
class _Residence:
    started: float
    limit: float
    hard_started: float
    hard_limit: float


class TaskFlowGuard:
    """Keep every active task caller bounded, including repeatedly renewed helpers.

    Only confirmed UI transitions and advancing battle counters renew the callers'
    residence time. Screen animation, ordinary input and record clearing do not.
    This guard operates only while installed by the runner's context manager.
    """

    DEFAULT_TIMEOUT = 420.0
    DEFAULT_HARD_TIMEOUT = 1800.0
    WRAPPERS = frozenset({'screenshot', 'maybe_screenshot'})

    def __init__(self, task, device, *, clock=None, command=None):
        self.task = task
        self.command = command
        self.device = device
        self._clock = clock or time.monotonic
        self._active: dict[FrameType, _Residence] = {}
        self._chess_round_high_water: dict[FrameType, int] = {}
        self._previous = _MISSING
        self._entered = False
        self._last_count = self._round_count()
        self._last_generation = self._progress_generation()
        self._last_round_generation = self._round_generation()

    def __enter__(self):
        if self._entered:
            raise RuntimeError('TaskFlowGuard context is already active')
        self._previous = getattr(self.device, '_task_flow_guard', _MISSING)
        self.device._task_flow_guard = self
        self._entered = True
        self._active.clear()
        self._chess_round_high_water.clear()
        self._last_count = self._round_count()
        self._last_generation = self._progress_generation()
        self._last_round_generation = self._round_generation()
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self._previous is _MISSING:
                if getattr(self.device, '_task_flow_guard', None) is self:
                    del self.device._task_flow_guard
            else:
                self.device._task_flow_guard = self._previous
        finally:
            self._active.clear()
            self._chess_round_high_water.clear()
            self._previous = _MISSING
            self._entered = False
        return False

    def _round_count(self):
        value = getattr(self.task, 'current_count', None)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return None
        return value

    def _progress_generation(self):
        value = getattr(self.device, '_task_flow_progress_generation', 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return 0
        return value

    def _round_generation(self):
        value = getattr(self.device, '_task_flow_round_generation', 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return 0
        return value

    def _confirmed_chess_round_advanced(self, frames):
        """Observe the unchanged coordinator's already-confirmed round number.

        Its local round_no changes only after run_one_round has returned a
        number confirmed in fresh frames. Reading the inner OCR candidate would
        treat an unconfirmed frame as progress. Keep one high-water value per
        game frame so backward OCR oscillation cannot renew either budget.
        """
        advanced = False
        current = set(frames)
        self._chess_round_high_water = {
            frame: value for frame, value in self._chess_round_high_water.items()
            if frame in current
        }
        for frame in frames:
            filename = frame.f_code.co_filename.replace('\\', '/').lower()
            if (not ('/tasks/chess/script_task.py' in '/' + filename)
                    or frame.f_code.co_name != '_run_game_by_rounds_without_device_stuck_timeout'):
                continue
            round_no = frame.f_locals.get('round_no')
            if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no <= 0:
                continue
            highest = self._chess_round_high_water.get(frame)
            if highest is None:
                self._chess_round_high_water[frame] = round_no
            elif round_no > highest:
                self._chess_round_high_water[frame] = round_no
                advanced = True
        return advanced

    @staticmethod
    def _is_task_frame(frame):
        filename = frame.f_code.co_filename.replace('\\', '/').lower()
        return ('/tasks/' in '/' + filename
                and frame.f_code.co_name not in TaskFlowGuard.WRAPPERS)

    def _raw_limit(self, frame):
        owner = frame.f_locals.get('self', self.task)
        config = getattr(owner, 'config', None) or getattr(self.task, 'config', None)
        battle = getattr(getattr(config, 'global_game', None), 'battle', None)
        context = getattr(owner, '_battle_context', None) or getattr(self.task, '_battle_context', None)
        values = [self.DEFAULT_TIMEOUT, getattr(battle, 'battle_timeout', None),
                  getattr(getattr(context, 'battle_timer', None), 'limit', None)]
        if frame.f_code.co_name == 'run':
            task_limit = _positive_seconds(getattr(owner, 'limit_time', None))
            if task_limit is not None:
                values.append(task_limit + 60)

        filename = frame.f_code.co_filename.replace('\\', '/').lower()
        if ('/tasks/chess/' in '/' + filename
                and frame.f_code.co_name in {'_wait_until_in_chess_game', '_start_chess_game'}):
            # Queue and matched-loading windows are longer than an ordinary
            # UI wait. Re-entering a phase cannot restart this residence cap.
            match = _positive_seconds(getattr(owner, '_matchmaking_timeout_seconds', 60)) or 60
            loading = _positive_seconds(getattr(owner, 'MATCHED_LOADING_TIMEOUT', 600)) or 600
            entry = (_positive_seconds(frame.f_locals.get('timeout'))
                     or _positive_seconds(getattr(owner, 'GAME_ENTER_TIMEOUT', 120)) or 120)
            values.append(match * 3 + loading + max(entry, 120) + 60)
        if ('/tasks/lbs/' in '/' + filename
                and frame.f_code.co_name in {'_run_drive_mode', '_run_team_match'}):
            # Passenger/team loops have the user's finite activity window.
            # Short room/navigation helpers keep their own cap.
            drive_limit = _positive_seconds(getattr(owner, 'limit_time', None))
            if drive_limit is not None:
                values.append(drive_limit + 60)
        if ('/tasks/lbs/' in '/' + filename
                and frame.f_code.co_name == '_wait_team_battle'):
            match_limit = _positive_seconds(getattr(frame.f_locals.get('match_timer'), 'limit', None))
            if match_limit is not None:
                values.append(match_limit + 60)
        if ('/tasks/guildactivitymonitor/' in '/' + filename
                and frame.f_code.co_name in {'run', 'monitor_activities'}):
            monitor = getattr(getattr(config, 'guild_activity_monitor', None),
                              'guild_activity_monitor_combat_time', None)
            minutes = _positive_seconds(getattr(monitor, 'monitor_duration', None))
            if minutes is not None:
                values.append(minutes * 60 + 60)
        return max(value for value in map(_positive_seconds, values) if value is not None)

    def _limit(self, frame):
        raw = self._raw_limit(frame)
        performance = getattr(self.device, 'performance', None)
        scaled = performance.timeout(raw) if performance is not None else raw
        return _positive_seconds(scaled) or raw

    def _hard_limit(self, frame):
        raw = max(self.DEFAULT_HARD_TIMEOUT, self._raw_limit(frame))
        performance = getattr(self.device, 'performance', None)
        scaled = performance.timeout(raw) if performance is not None else raw
        return _positive_seconds(scaled) or raw

    def check(self):
        """Called before a capture; raise before another input can use that frame."""
        if not self._entered:
            return
        if getattr(self.device, '_task_flow_guard_disabled', False):
            self._active.clear()
            self._chess_round_high_water.clear()
            self._last_count = self._round_count()
            self._last_generation = self._progress_generation()
            self._last_round_generation = self._round_generation()
            return

        now = self._clock()
        count, generation = self._round_count(), self._progress_generation()
        round_generation = self._round_generation()
        round_advanced = ((count is not None and self._last_count is not None and count > self._last_count)
                          or round_generation > self._last_round_generation)
        advanced = round_advanced or generation > self._last_generation
        if count is not None and (self._last_count is None or count > self._last_count):
            self._last_count = count
        self._last_generation = max(self._last_generation, generation)
        self._last_round_generation = max(self._last_round_generation, round_generation)

        frames = []
        cursor = sys._getframe(1)
        try:
            while cursor is not None:
                if self._is_task_frame(cursor):
                    frames.append(cursor)
                cursor = cursor.f_back
            current = set(frames)
            self._active = {frame: residence for frame, residence in self._active.items()
                            if frame in current}
            if self._confirmed_chess_round_advanced(frames):
                round_advanced = advanced = True
            for frame in reversed(frames):
                limit = self._limit(frame)
                hard_limit = self._hard_limit(frame)
                residence = self._active.get(frame)
                if residence is None:
                    residence = self._active[frame] = _Residence(now, limit, now, hard_limit)
                else:
                    # Late task configuration may widen a valid wait, never restart it.
                    residence.limit = max(residence.limit, limit)
                    residence.hard_limit = max(residence.hard_limit, hard_limit)
                    if advanced:
                        residence.started = now
                    if round_advanced:
                        residence.hard_started = now
                if now - residence.hard_started >= residence.hard_limit:
                    function = frame.f_code.co_name
                    filename = frame.f_code.co_filename.replace('\\', '/')
                    raise TaskRecoveryFailed(
                        f'Task flow exceeded {residence.hard_limit:g}s without advancing a round: '
                        f'{filename}:{function}')
                if now - residence.started >= residence.limit:
                    function = frame.f_code.co_name
                    filename = frame.f_code.co_filename.replace('\\', '/')
                    raise TaskRecoveryFailed(
                        f'Task flow made no confirmed progress within {residence.limit:g}s: '
                        f'{filename}:{function}')
        finally:
            # Frame locals can hold entire task/device graphs. Release traversal references.
            del cursor
            frames.clear()

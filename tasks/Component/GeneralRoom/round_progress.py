"""Durable victories for a team challenge round, independent of its cooldown."""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from module.exception import TaskRecoveryFailed


TEAM_ROUND_TASKS = frozenset(('GoldYoukai', 'ExperienceYoukai'))


def _profile_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[\w-]+', value)
            or value.upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                *(f'COM{i}' for i in range(1, 10)),
                                *(f'LPT{i}' for i in range(1, 10))}):
        raise TaskRecoveryFailed('Invalid team challenge profile name')
    return value


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be text')
    result = datetime.fromisoformat(value)
    if result.tzinfo is not None:
        raise ValueError('Timestamp must use the local scheduler clock')
    return result


class TeamChallengeRoundProgress:
    """Keep a finished round until both its release and next due time are proven."""

    target = 2

    def __init__(self, profile, task, root=None, checkpoint_path=None):
        self.profile = _profile_name(profile)
        if not isinstance(task, str) or task not in TEAM_ROUND_TASKS:
            raise TaskRecoveryFailed('Invalid team challenge task')
        self.task = task
        self.root = Path('log/task_progress') if root is None else Path(root)
        self.path = self.root / self.profile / f'{task}.json'
        self.checkpoint_path = (Path('log/task_retry') / f'{self.profile}.json'
                                if checkpoint_path is None else Path(checkpoint_path))
        self.state = self._read()

    def _read(self):
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            count = data['completed_wins']
            if (data['version'] != 1 or data['profile'] != self.profile
                    or data['task'] != self.task or data['target'] != self.target
                    or type(count) is not int or not 0 <= count <= self.target
                    or not isinstance(data['round_id'], str)
                    or not re.fullmatch(r'[0-9a-f]{32}', data['round_id'])):
                raise ValueError('Invalid round metadata or victory count')
            completed = data.get('completed_at')
            next_cycle = data.get('next_cycle_at')
            if count == self.target:
                _timestamp(completed)
                if next_cycle is not None:
                    _timestamp(next_cycle)
            elif completed is not None or next_cycle is not None:
                raise ValueError('An unfinished round cannot have completion metadata')
            return data
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise TaskRecoveryFailed(f'Invalid retained {self.task} round progress: {exc}') from exc

    def _write(self, state):
        temporary = self.path.with_name(f'{self.path.name}.{uuid4().hex}.tmp')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open('w', encoding='utf-8') as handle:
                json.dump(state, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            raise TaskRecoveryFailed(f'Cannot retain {self.task} round progress: {exc}') from exc
        finally:
            temporary.unlink(missing_ok=True)
        self.state = state

    def _new_round(self, count=0):
        return dict(version=1, profile=self.profile, task=self.task, target=self.target,
                    round_id=uuid4().hex, completed_wins=count,
                    completed_at=None, next_cycle_at=None)

    @property
    def completed_wins(self):
        return 0 if self.state is None else self.state['completed_wins']

    @property
    def next_cycle_at(self):
        value = None if self.state is None else self.state.get('next_cycle_at')
        return None if value is None else _timestamp(value)

    def _released(self):
        if not self.checkpoint_path.exists():
            return False
        try:
            ledger = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
            if not isinstance(ledger, dict):
                raise ValueError('Invalid checkpoint ledger')
            if not isinstance(ledger.get('task', ''), str):
                raise ValueError('Invalid checkpoint task')
            released = ledger.get('released', {})
            if not isinstance(released, dict):
                raise ValueError('Invalid checkpoint release ledger')
            value = released.get(self.task)
            return value is not None and _timestamp(value) >= _timestamp(self.state['completed_at'])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise TaskRecoveryFailed(f'Cannot verify {self.task} round release: {exc}') from exc

    def begin(self, now=None):
        now = datetime.now() if now is None else now
        if self.state is None:
            self._write(self._new_round())
        elif (self.completed_wins == self.target and self.next_cycle_at is not None
              and now >= self.next_cycle_at and self._released()):
            self._write(self._new_round())
        return self.completed_wins

    def record_win(self, now=None):
        if self.state is None:
            raise TaskRecoveryFailed('Start the round before recording a victory')
        if self.completed_wins >= self.target:
            raise TaskRecoveryFailed(f'{self.task} round is already complete')
        state = dict(self.state)
        state['completed_wins'] += 1
        if state['completed_wins'] == self.target:
            state['completed_at'] = (datetime.now() if now is None else now).isoformat()
        self._write(state)
        return self.completed_wins

    def mark_scheduled(self, next_cycle_at):
        if self.completed_wins != self.target:
            raise TaskRecoveryFailed(f'{self.task} has not completed its two victories')
        if not isinstance(next_cycle_at, datetime) or next_cycle_at.tzinfo is not None:
            raise TaskRecoveryFailed('Invalid next round schedule')
        if self.next_cycle_at is None:
            state = dict(self.state)
            state['next_cycle_at'] = next_cycle_at.isoformat()
            self._write(state)
        elif self.next_cycle_at != next_cycle_at:
            raise TaskRecoveryFailed('Completed round schedule cannot be overwritten')

    def seed_confirmed_wins(self, count=1):
        """Migrate independently verified wins while stopped; never change next_run."""
        if type(count) is not int or not 0 < count < self.target:
            raise TaskRecoveryFailed('Only unfinished, confirmed victories can be seeded')
        if self.state is not None:
            if self.completed_wins == count:
                return False
            raise TaskRecoveryFailed('Refuse to overwrite retained round progress')
        self._write(self._new_round(count))
        return True

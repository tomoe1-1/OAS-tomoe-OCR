"""Persistent retry queue, separate from normal task configuration."""
import json
import os
from datetime import datetime
from pathlib import Path


class FailedTaskStore:
    def __init__(self, config_name, root='log'):
        self.path = Path(root) / 'task_failed' / f'{config_name}.json'
        self.legacy_path = Path(root) / 'task_retry' / f'{config_name}.json'

    def read(self):
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return {name: datetime.fromisoformat(value) for name, value in data.items()}
        # Migrate the previous single pinned failure on first use. Once a new
        # ledger exists (even empty), old receipts cannot resurrect successes.
        data = {}
        if self.legacy_path.exists():
            old = json.loads(self.legacy_path.read_text(encoding='utf-8'))
            if old.get('task') and old.get('not_before'):
                data[old['task']] = datetime.fromisoformat(old['not_before'])
            recovery = old.get('recovery_not_before', {})
            if recovery.get('Restart'):
                data['Restart'] = datetime.fromisoformat(recovery['Restart'])
        if data:
            self._write(data)
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f'.{os.getpid()}.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump({name: value.isoformat() for name, value in data.items()}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def mark(self, task, retry_at):
        data = self.read()
        data[task] = retry_at
        self._write(data)

    def clear(self, task):
        data = self.read()
        if task in data:
            del data[task]
            self._write(data)

    def reschedule(self, task, retry_at):
        """An explicit user edit replaces a failed task's old retry deadline."""
        data = self.read()
        if task not in data:
            return False
        data[task] = retry_at
        self._write(data)
        return True

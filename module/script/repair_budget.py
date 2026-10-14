"""One repair worker per installation, with a persistent service cooldown."""
import json
import os
import time
from pathlib import Path


class RepairServiceError(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def coder_failure(log_path, returncode=None):
    try:
        with Path(log_path).open('rb') as stream:
            stream.seek(max(0, Path(log_path).stat().st_size - 65536))
            text = stream.read().decode('utf-8', errors='replace').lower()
    except OSError:
        text = ''
    if any(word in text for word in ('unauthorized', 'authentication failed', 'http 401', 'status code 401', 'login required', 'invalid api key')):
        return RepairServiceError('authentication', 'Repair service authentication failed; check sign-in')
    if any(word in text for word in ('routing discovery timed out', 'reconnecting', 'error sending request', 'connection refused', 'transport channel closed')):
        return RepairServiceError('network', 'Repair service connection or routing failed')
    if 'timed out' in text or 'timeout' in text:
        return RepairServiceError('timeout', 'Repair service request timed out')
    return RepairServiceError('generation', f'Repair generation failed (exit {returncode}); see local coder log')


class WorkerLease:
    """OS lock releases on process death; no stale PID lock to expire."""
    def __init__(self, state):
        self.path = Path(state) / 'worker.lock'
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.close()


def read_budget(state):
    try:
        value = json.loads((Path(state) / 'service-budget.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_budget(state, value):
    path = Path(state) / 'service-budget.json'
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def service_failed(state, error):
    value = read_budget(state)
    count = min(5, int(value.get('consecutive_failures', 0)) + 1)
    delay = min(3600, 300 * 2 ** (count - 1))
    value.update(consecutive_failures=count, next_retry_at=time.time() + delay,
                 error_category=error.category, reason=str(error))
    save_budget(state, value)
    return value['next_retry_at']


def service_succeeded(state):
    value = read_budget(state)
    value.update(consecutive_failures=0, next_retry_at=0, error_category='',
                 reason='', last_success_at=time.time())
    save_budget(state, value)


def pending_events(state, now=None):
    now = time.time() if now is None else now
    pending = []
    for path in Path(state).glob('*.status.json'):
        try:
            status = json.loads(path.read_text(encoding='utf-8'))
            retryable = status.get('status') in ('queued', 'running') or status.get('retryable') is True
            event = path.with_name(path.name.removesuffix('.status.json') + '.json')
            if retryable and float(status.get('next_retry_at', 0)) <= now and event.exists():
                pending.append(event)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(pending, key=lambda path: path.stat().st_mtime)


def budget_status(state):
    value = read_budget(state)
    value['cooldown_seconds'] = max(0, round(float(value.get('next_retry_at', 0)) - time.time()))
    value['pending_count'] = len(pending_events(state, now=float('inf')))
    value['active'] = False
    if (Path(state) / 'worker.lock').exists():
        with WorkerLease(state) as acquired:
            value['active'] = not acquired
    return value

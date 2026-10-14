"""Bounded timing samples for local recognition service tuning."""
from collections import deque
import threading
import time


class WorkerMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._wait = deque(maxlen=256)
        self._run = deque(maxlen=256)
        self._active = 0

    def wrap(self, function, args, kwargs):
        queued = time.monotonic()
        def execute():
            started = time.monotonic()
            with self._lock:
                self._active += 1
            try:
                return function(*args, **kwargs)
            finally:
                finished = time.monotonic()
                with self._lock:
                    self._active -= 1
                    self._wait.append((started - queued) * 1000)
                    self._run.append((finished - started) * 1000)
        return execute

    def snapshot(self):
        with self._lock:
            wait, run, active = sorted(self._wait), sorted(self._run), self._active
        def percentile(values, fraction):
            return round(values[min(len(values)-1, int((len(values)-1)*fraction))], 3) if values else 0
        return {'active': active, 'timing_samples': len(run),
                'queue_p50_ms': percentile(wait, .5), 'queue_p95_ms': percentile(wait, .95),
                'run_p50_ms': percentile(run, .5), 'run_p95_ms': percentile(run, .95)}


def process_metrics():
    import os
    result = {'pid': os.getpid(), 'cpu_time_seconds': round(time.process_time(), 3)}
    try:
        import psutil
        result['rss_bytes'] = psutil.Process().memory_info().rss
    except (ImportError, OSError):
        pass
    return result

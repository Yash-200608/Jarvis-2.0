from threading import RLock


MAX_FAILURES = 2
MAX_TRACKED_FAILURES = 1000
fail_counts = {}
_lock = RLock()


def should_retry(task_id: str) -> bool:
    with _lock:
        return fail_counts.get(task_id, 0) < MAX_FAILURES


def record_failure(task_id: str):
    with _lock:
        fail_counts[task_id] = fail_counts.get(task_id, 0) + 1
        while len(fail_counts) > MAX_TRACKED_FAILURES:
            fail_counts.pop(next(iter(fail_counts)))

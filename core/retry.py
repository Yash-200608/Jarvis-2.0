fail_counts = {}


def should_retry(task_id: str) -> bool:
    return fail_counts.get(task_id, 0) < 2


def record_failure(task_id: str):
    fail_counts[task_id] = fail_counts.get(task_id, 0) + 1

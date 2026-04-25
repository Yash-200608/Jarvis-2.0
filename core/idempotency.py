executed_ids: set[str] = set()


def is_duplicate(command_id: str) -> bool:
    return command_id in executed_ids


def mark_executed(command_id: str) -> None:
    executed_ids.add(command_id)

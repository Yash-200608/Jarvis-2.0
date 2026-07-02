import json
from pathlib import Path
from threading import RLock


_STORE_PATH = Path(__file__).with_name(".executed_ids.json")
_lock = RLock()
executed_ids: set[str] = set()
_in_progress_ids: set[str] = set()


def _load() -> None:
    if not _STORE_PATH.exists():
        return
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, list):
        executed_ids.update(item for item in data if isinstance(item, str))


def _persist() -> None:
    temp_path = _STORE_PATH.with_suffix(_STORE_PATH.suffix + ".tmp")
    temp_path.write_text(json.dumps(sorted(executed_ids), indent=2), encoding="utf-8")
    temp_path.replace(_STORE_PATH)


_load()


def _valid_command_id(command_id: str) -> bool:
    return isinstance(command_id, str) and bool(command_id)


def is_duplicate(command_id: str) -> bool:
    if not _valid_command_id(command_id):
        return False
    with _lock:
        return command_id in executed_ids or command_id in _in_progress_ids


def reserve(command_id: str) -> bool:
    if not _valid_command_id(command_id):
        return False
    with _lock:
        if command_id in executed_ids or command_id in _in_progress_ids:
            return False
        _in_progress_ids.add(command_id)
        return True


def mark_executed(command_id: str) -> None:
    if not _valid_command_id(command_id):
        raise ValueError("command_id must be a non-empty string")
    with _lock:
        executed_ids.add(command_id)
        _in_progress_ids.discard(command_id)
        _persist()


def release(command_id: str) -> None:
    if not _valid_command_id(command_id):
        return
    with _lock:
        _in_progress_ids.discard(command_id)

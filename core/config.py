from threading import RLock


_VALID_MODES = {"execute", "simulate"}
_lock = RLock()
EXECUTION_MODE = "execute"


def set_execution_mode(mode: str) -> None:
    global EXECUTION_MODE
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid execution mode: {mode}")
    with _lock:
        EXECUTION_MODE = mode


def get_execution_mode() -> str:
    with _lock:
        mode = EXECUTION_MODE
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid execution mode: {mode}")
    return mode


def is_execute():
    return get_execution_mode() == "execute"


def is_simulate():
    return get_execution_mode() == "simulate"

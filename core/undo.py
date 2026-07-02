from threading import RLock


MAX_HISTORY = 20
history = []
_lock = RLock()


def record_action(action, result):
    with _lock:
        if len(history) >= MAX_HISTORY:
            return
        history.append({"action": action, "result": result, "undone": False})


def undo_last_action():
    with _lock:
        if not history:
            return None

        record = history.pop()
        action = record.get("action", {})
        inverse = _build_inverse(action)
        if inverse is None:
            record["undone"] = False
            record["undo_error"] = "No inverse operation is registered"
            return record

        record["undone"] = True
        record["inverse_action"] = inverse
        return record


def _build_inverse(action):
    if not isinstance(action, dict):
        return None

    name = action.get("name")
    params = action.get("params", {})
    if name == "move_to_trash" and isinstance(params, dict) and "path" in params:
        return {"name": "restore_from_trash", "params": {"path": params["path"]}}

    return None

MAX_HISTORY = 20
history = []


def record_action(action, result):
    history.append({"action": action, "result": result})
    if len(history) > MAX_HISTORY:
        del history[0]


def undo_last_action():
    if not history:
        return None

    return history.pop()

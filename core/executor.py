from core import config
from core import idempotency
from core import safety
from core import sandbox
from core import state
from core import undo
from core import validator


def execute_task(command_id: str, action: dict):
    if idempotency.is_duplicate(command_id):
        return

    if not validator.validate_action(action):
        return

    action = safety.apply_safety(action)

    params = action.get("params", {})
    if "path" in params and not sandbox.validate_path(params["path"]):
        return

    task_state = state.TaskState()
    task_state.update(state.ExecutionState.VALIDATED)

    task_state.update(state.ExecutionState.EXECUTING)

    if not config.is_simulate():
        print(action)

    result = {"state": task_state.state, "action": action}
    undo.record_action(action, result)
    idempotency.mark_executed(command_id)
    task_state.update(state.ExecutionState.SUCCESS)
    result["state"] = task_state.state

    return result

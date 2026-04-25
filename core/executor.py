from core import config
from core import idempotency
from core import retry
from core import risk
from core import safety
from core import sandbox
from core import state
from core import undo
from core import validator


PATH_PARAM_NAMES = {"path", "src", "dest", "to_path", "from_path", "target_path"}


def _result(status: str, task_state: state.TaskState, **kwargs):
    payload = {"status": status, "state": task_state.state}
    payload.update(kwargs)
    return payload


def _fail(task_state: state.TaskState, reason: str, result_status: str = "rejected", **kwargs):
    if task_state.state != state.ExecutionState.FAILED:
        task_state.update(state.ExecutionState.FAILED)
    return _result(result_status, task_state, reason=reason, **kwargs)


def _validate_paths(action: dict) -> tuple[bool, str | None]:
    params = action.get("params", {})
    for key, value in params.items():
        if key in PATH_PARAM_NAMES or key.endswith("_path"):
            if not sandbox.validate_path(value):
                return False, key
    return True, None


def _dispatch(action: dict):
    if config.is_simulate():
        return {"executed": False, "simulated": True, "action": action}
    if config.is_execute():
        return {"executed": True, "action": action}
    raise ValueError("Invalid execution mode")


def execute_task(command_id: str, action: dict):
    task_state = state.TaskState()

    if not isinstance(command_id, str) or not command_id:
        return _fail(task_state, "invalid_command_id")

    if not idempotency.reserve(command_id):
        return _result("duplicate", task_state, reason="duplicate_command_id")

    try:
        if not validator.validate_action(action):
            return _fail(task_state, "invalid_action")

        action_risk = risk.get_risk_level(action["name"])
        if action_risk == "high" and action.get("confirmed") is not True:
            return _fail(
                task_state,
                "confirmation_required",
                result_status="confirmation_required",
                risk=action_risk,
            )

        action = safety.apply_safety(action)

        if not validator.validate_action(action):
            return _fail(task_state, "invalid_action_after_safety", action=action)

        if action.get("preview") is True and action.get("confirmed") is not True:
            return _fail(
                task_state,
                "preview_required",
                result_status="preview_required",
                action=action,
            )

        paths_valid, invalid_path_param = _validate_paths(action)
        if not paths_valid:
            return _fail(
                task_state,
                "sandbox_rejected",
                invalid_path_param=invalid_path_param,
            )

        task_state.update(state.ExecutionState.VALIDATED)
        task_state.update(state.ExecutionState.EXECUTING)

        dispatch_result = _dispatch(action)
        result = _result(
            "success",
            task_state,
            action=action,
            dispatch=dispatch_result,
        )
        undo.record_action(action, result)
        task_state.update(state.ExecutionState.SUCCESS)
        result["state"] = task_state.state
        idempotency.mark_executed(command_id)
        return result

    except Exception as exc:
        retry.record_failure(command_id)
        retryable = retry.should_retry(command_id)
        if retryable:
            idempotency.release(command_id)
        else:
            idempotency.mark_executed(command_id)

        if task_state.state != state.ExecutionState.FAILED:
            task_state.update(state.ExecutionState.FAILED)
        return _result(
            "failed",
            task_state,
            reason=str(exc),
            retryable=retryable,
        )
    finally:
        if command_id not in idempotency.executed_ids:
            idempotency.release(command_id)

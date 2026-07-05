import hashlib
import json

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
_approved_commands = {}


def _fingerprint(action: dict) -> str:
    payload = {
        "name": action.get("name"),
        "params": action.get("params", {}),
    }
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode()).hexdigest()


def _result(status: str, task_state: state.TaskState, **kwargs):
    payload = {"status": status, "state": task_state.state}
    payload.update(kwargs)
    return payload


def _fail(task_state: state.TaskState, reason: str, result_status: str = "rejected", **kwargs):
    if task_state.state not in {state.ExecutionState.FAILED, state.ExecutionState.SUCCESS}:
        task_state.update(state.ExecutionState.FAILED)
    return _result(result_status, task_state, reason=reason, **kwargs)


def _validate_paths(action: dict) -> tuple[bool, str | None]:
    params = action.get("params", {})
    for key, value in params.items():
        if key in PATH_PARAM_NAMES or key.endswith("_path"):
            resolved = sandbox.resolve_path(value)
            if resolved is None:
                return False, key
            params[key] = resolved
    return True, None


# Real dispatch handlers (Priority #4 Milestone 6) -- until this registry,
# execute-mode _dispatch() was a stub that claimed "executed: True" without
# doing anything. Handlers are registered per tool name by whoever wires
# real execution (jarvis_node_sdk/pc_skills.py registers the pc subset);
# a tool with no registered handler keeps the historical stub behavior, so
# every pre-existing kernel test and caller is unaffected.
DISPATCH_HANDLERS: dict = {}


def register_handler(name: str, fn) -> None:
    """Register `fn(action: dict) -> dict` as the real executor for tool
    `name`. The returned dict is merged into the dispatch result."""
    if not name:
        raise ValueError("tool name must be non-empty")
    DISPATCH_HANDLERS[name] = fn


def _dispatch(action: dict):
    if config.is_simulate():
        return {"executed": False, "simulated": True, "action": action}
    if config.is_execute():
        handler = DISPATCH_HANDLERS.get(action.get("name"))
        if handler is not None:
            handler_result = handler(action)
            return {"executed": True, "action": action, "result": handler_result}
        return {"executed": True, "action": action}
    raise ValueError("Invalid execution mode")


def execute_task(command_id: str, action: dict):
    task_state = state.TaskState()
    execution_committed = False

    if not isinstance(command_id, str) or not command_id:
        return _fail(task_state, "invalid_command_id")

    if not idempotency.reserve(command_id):
        return _result("duplicate", task_state, reason="duplicate_command_id")

    try:
        user_requested_confirmation = False
        if isinstance(action, dict):
            user_requested_confirmation = action.get("confirmed", False) is True
            action = dict(action)
            action.pop("confirmed", None)
            action.pop("_approved", None)

        if not validator.validate_action(action):
            return _fail(task_state, "invalid_action")

        # Risk is classified, and approval fingerprint-bound, on the action the
        # caller actually requested -- BEFORE any safety rewrite. Computing risk
        # after apply_safety() let a high-risk request (e.g. delete_file) get
        # silently downgraded to a medium-risk one (move_to_trash) before the
        # risk check ever ran, so the confirmation gate never fired.
        requested_name = action["name"]
        fp = _fingerprint(action)
        action_risk = risk.get_risk_level(requested_name)
        if action_risk == "high":
            approved_fp = _approved_commands.get(command_id)
            if approved_fp != fp:
                if user_requested_confirmation:
                    _approved_commands[command_id] = fp
                    return _result("approved", task_state)
                return _fail(
                    task_state,
                    "confirmation_required",
                    result_status="confirmation_required",
                    risk=action_risk,
                )

        action = safety.apply_safety(action)

        if not validator.validate_action(action):
            return _fail(task_state, "invalid_action_after_safety", action=action)

        if action.get("preview") is True:
            approved_fp = _approved_commands.get(command_id)
            if approved_fp != fp:
                if user_requested_confirmation:
                    _approved_commands[command_id] = fp
                    return _result("approved", task_state, action=action)
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

        try:
            dispatch_result = _dispatch(action)
            execution_committed = True
        except Exception:
            if not execution_committed:
                idempotency.release(command_id)
            raise
        result = _result(
            "success",
            task_state,
            action=action,
            dispatch=dispatch_result,
        )
        if requested_name != action.get("name"):
            # The safety layer rewrote the requested action (e.g. delete_file ->
            # move_to_trash). Keep the original request visible in the audit
            # trail rather than only recording what was actually dispatched.
            result["requested_action"] = requested_name
        try:
            undo.record_action(action, result)
        except Exception as exc:
            result["undo_error"] = str(exc)
        idempotency.mark_executed(command_id)
        task_state.update(state.ExecutionState.SUCCESS)
        _approved_commands.pop(command_id, None)
        result["state"] = task_state.state
        return result

    except Exception as exc:
        retry.record_failure(command_id)
        retryable = not execution_committed and retry.should_retry(command_id)
        if not execution_committed and retryable:
            idempotency.release(command_id)

        if task_state.state not in {state.ExecutionState.FAILED, state.ExecutionState.SUCCESS}:
            task_state.update(state.ExecutionState.FAILED)
        return _result(
            "failed",
            task_state,
            reason=str(exc),
            retryable=retryable,
        )
    finally:
        if not execution_committed and command_id not in idempotency.executed_ids:
            idempotency.release(command_id)

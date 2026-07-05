"""Dispatch-handler registry tests (Priority #4 Milestone 6) -- the first
real execute-mode handlers behind core/executor.py's formerly-stub
_dispatch(). Belongs to the root kernel suite (run as
`pytest core/tests/ tests/`), NOT jarvis_node_sdk's -- these prove kernel
behavior in isolation.
"""

import uuid

import pytest

from core import config, executor


@pytest.fixture(autouse=True)
def _clean_kernel_state():
    saved_handlers = dict(executor.DISPATCH_HANDLERS)
    saved_mode = config.get_execution_mode()
    yield
    executor.DISPATCH_HANDLERS.clear()
    executor.DISPATCH_HANDLERS.update(saved_handlers)
    config.set_execution_mode(saved_mode)


def _cmd_id() -> str:
    return uuid.uuid4().hex


def test_registered_handler_runs_in_execute_mode():
    config.set_execution_mode("execute")
    calls = []

    def fake_lock(action: dict) -> dict:
        calls.append(action)
        return {"locked": True}

    executor.register_handler("lock_device", fake_lock)

    result = executor.execute_task(_cmd_id(), {"name": "lock_device", "params": {}})

    assert result["status"] == "success"
    assert result["dispatch"]["executed"] is True
    assert result["dispatch"]["result"] == {"locked": True}
    assert calls and calls[0]["name"] == "lock_device"


def test_unregistered_tool_keeps_stub_behavior():
    """A tool with no handler must behave exactly as before this registry
    existed -- executed: True, no result key."""
    config.set_execution_mode("execute")

    result = executor.execute_task(_cmd_id(), {"name": "open_app", "params": {"app_name": "x"}})

    assert result["status"] == "success"
    assert result["dispatch"]["executed"] is True
    assert "result" not in result["dispatch"]


def test_simulate_mode_never_calls_handlers():
    config.set_execution_mode("simulate")
    calls = []

    executor.register_handler("lock_device", lambda a: calls.append(a) or {})

    result = executor.execute_task(_cmd_id(), {"name": "lock_device", "params": {}})

    assert result["dispatch"]["simulated"] is True
    assert calls == []


def test_handler_for_high_risk_tool_still_gated_by_confirmation():
    """Registering a real handler must not weaken the kernel's risk gate:
    a high-risk tool (run_command) still demands confirmation before its
    handler ever runs."""
    config.set_execution_mode("execute")
    calls = []

    executor.register_handler("run_command", lambda a: calls.append(a) or {"ran": True})

    result = executor.execute_task(
        _cmd_id(), {"name": "run_command", "params": {"command": "echo hi"}}
    )

    assert result["status"] == "confirmation_required"
    assert calls == []  # the handler never fired


def test_high_risk_handler_runs_after_two_phase_approval():
    config.set_execution_mode("execute")
    calls = []
    executor.register_handler("run_command", lambda a: calls.append(a) or {"ran": True})

    cmd_id = _cmd_id()
    action = {"name": "run_command", "params": {"command": "echo hi"}}

    approved = executor.execute_task(cmd_id, {**action, "confirmed": True})
    assert approved["status"] == "approved"
    assert calls == []  # approval phase never executes

    executed = executor.execute_task(cmd_id, action)
    assert executed["status"] == "success"
    assert executed["dispatch"]["result"] == {"ran": True}
    assert len(calls) == 1


def test_new_tool_rows_are_valid_schemas():
    from core.validator import validate_action

    assert validate_action({"name": "lock_device", "params": {}})
    assert validate_action({"name": "media_control", "params": {"command": "play_pause"}})
    assert validate_action({"name": "run_command", "params": {"command": "dir"}})
    # exact-param-set rule still enforced
    assert not validate_action({"name": "lock_device", "params": {"extra": "x"}})

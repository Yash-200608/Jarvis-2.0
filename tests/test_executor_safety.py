import uuid

import pytest

from core import config
from core import executor
from core import idempotency
from core import retry
from core import undo


def command_id() -> str:
    return f"test-{uuid.uuid4()}"


def read_action(path: str = "C:/Users/Hp/test.txt") -> dict:
    return {"name": "read_file", "params": {"path": path}}


@pytest.fixture(autouse=True)
def isolate_executor_state(monkeypatch):
    idempotency.executed_ids.clear()
    idempotency._in_progress_ids.clear()
    retry.fail_counts.clear()
    undo.history.clear()
    config.set_execution_mode("execute")
    monkeypatch.setattr(idempotency, "_persist", lambda: None)
    yield
    idempotency.executed_ids.clear()
    idempotency._in_progress_ids.clear()
    retry.fail_counts.clear()
    undo.history.clear()
    config.set_execution_mode("execute")


def test_no_double_execution_with_same_command_id(monkeypatch):
    calls = []

    def fake_dispatch(action):
        calls.append(action)
        return {"executed": True}

    monkeypatch.setattr(executor, "_dispatch", fake_dispatch)
    cid = command_id()

    first = executor.execute_task(cid, read_action())
    second = executor.execute_task(cid, read_action())

    assert first["status"] == "success"
    assert second["status"] == "duplicate"
    assert len(calls) == 1


def test_undo_failure_still_succeeds_and_does_not_duplicate(monkeypatch):
    calls = []

    def fake_dispatch(action):
        calls.append(action)
        return {"executed": True}

    def broken_record_action(action, result):
        raise RuntimeError("undo failed")

    monkeypatch.setattr(executor, "_dispatch", fake_dispatch)
    monkeypatch.setattr(undo, "record_action", broken_record_action)
    cid = command_id()

    first = executor.execute_task(cid, read_action())
    second = executor.execute_task(cid, read_action())

    assert first["status"] == "success"
    assert "undo_error" in first
    assert second["status"] == "duplicate"
    assert len(calls) == 1


def test_mark_executed_occurs_before_success_state(monkeypatch):
    order = []
    original_update = executor.state.TaskState.update

    def fake_dispatch(action):
        order.append("dispatch")
        return {"executed": True}

    def mark_executed(command_id):
        order.append("mark_executed")
        idempotency.executed_ids.add(command_id)
        idempotency._in_progress_ids.discard(command_id)

    def tracked_update(self, new_state):
        if new_state == executor.state.ExecutionState.SUCCESS:
            order.append("success")
        return original_update(self, new_state)

    monkeypatch.setattr(executor, "_dispatch", fake_dispatch)
    monkeypatch.setattr(idempotency, "mark_executed", mark_executed)
    monkeypatch.setattr(executor.state.TaskState, "update", tracked_update)

    result = executor.execute_task(command_id(), read_action())

    assert result["status"] == "success"
    assert order.index("mark_executed") < order.index("success")


def test_external_confirmation_does_not_bypass_safeguards(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "_dispatch", lambda action: calls.append(action))

    result = executor.execute_task(
        command_id(),
        {"name": "delete_file", "params": {"path": "C:/Users/Hp/test.txt"}, "confirmed": True},
    )

    assert result["status"] == "confirmation_required"
    assert calls == []


def test_idempotency_repeated_command_is_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "_dispatch", lambda action: calls.append(action) or {"executed": True})
    cid = command_id()

    executor.execute_task(cid, read_action())
    result = executor.execute_task(cid, read_action())

    assert result["status"] == "duplicate"
    assert len(calls) == 1


def test_command_id_is_not_released_after_dispatch(monkeypatch):
    released = []

    monkeypatch.setattr(executor, "_dispatch", lambda action: {"executed": True})
    monkeypatch.setattr(undo, "record_action", lambda action, result: (_ for _ in ()).throw(RuntimeError("undo failed")))
    monkeypatch.setattr(idempotency, "release", lambda command_id: released.append(command_id))

    result = executor.execute_task(command_id(), read_action())

    assert result["status"] == "success"
    assert released == []


def test_invalid_path_fails_sandbox(monkeypatch):
    calls = []
    monkeypatch.setattr(executor.sandbox, "ALLOWED_ROOTS", ["C:/DefinitelyAllowedJarvisTestRoot"])
    monkeypatch.setattr(executor, "_dispatch", lambda action: calls.append(action))

    result = executor.execute_task(command_id(), read_action("../../etc/passwd"))

    assert result["status"] == "rejected"
    assert result["reason"] == "sandbox_rejected"
    assert calls == []


def test_retry_does_not_duplicate_side_effects_after_execution(monkeypatch):
    calls = []
    cid = command_id()

    def fake_dispatch(action):
        calls.append(action)
        return {"executed": True}

    monkeypatch.setattr(executor, "_dispatch", fake_dispatch)
    monkeypatch.setattr(idempotency, "mark_executed", lambda command_id: (_ for _ in ()).throw(RuntimeError("persist failed")))

    first = executor.execute_task(cid, read_action())
    second = executor.execute_task(cid, read_action())

    assert first["status"] == "failed"
    assert first["retryable"] is False
    assert second["status"] == "duplicate"
    assert len(calls) == 1


def test_mark_executed_failure_does_not_crash(monkeypatch):
    monkeypatch.setattr(executor, "_dispatch", lambda action: {"executed": True})
    monkeypatch.setattr(idempotency, "mark_executed", lambda command_id: (_ for _ in ()).throw(RuntimeError("persist failed")))

    result = executor.execute_task(command_id(), read_action())

    assert result["status"] == "failed"
    assert result["retryable"] is False


def test_simulation_mode_does_not_report_actual_execution():
    config.set_execution_mode("simulate")

    result = executor.execute_task(command_id(), read_action())

    assert result["status"] == "success"
    assert result["dispatch"]["executed"] is False
    assert result["dispatch"]["simulated"] is True

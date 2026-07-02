import json
import threading
import uuid
from pathlib import Path

import pytest

from core import config
from core import executor
from core import idempotency
from core import retry
from core import sandbox
from core import undo


def new_command_id() -> str:
    return f"adv-{uuid.uuid4()}"


def read_action(path: str = "C:/Users/Hp/adversarial.txt") -> dict:
    return {"name": "read_file", "params": {"path": path}}


class AtomicCounter:
    def __init__(self) -> None:
        self.value = 0
        self.lock = threading.Lock()

    def increment(self) -> int:
        with self.lock:
            self.value += 1
            return self.value


@pytest.fixture(autouse=True)
def isolate_global_state(monkeypatch):
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


def test_same_command_id_race_executes_once(monkeypatch):
    counter = AtomicCounter()
    release = threading.Event()
    entered = threading.Event()

    def slow_dispatch(action):
        counter.increment()
        entered.set()
        release.wait(timeout=5)
        return {"executed": True}

    monkeypatch.setattr(executor, "_dispatch", slow_dispatch)
    cid = new_command_id()
    results = []
    results_lock = threading.Lock()

    def run_task():
        result = executor.execute_task(cid, read_action())
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=run_task) for _ in range(20)]
    for thread in threads:
        thread.start()

    assert entered.wait(timeout=5)
    release.set()

    for thread in threads:
        thread.join(timeout=5)

    assert counter.value == 1
    assert sum(result["status"] == "success" for result in results) == 1
    assert sum(result["status"] == "duplicate" for result in results) == 19


def test_same_payload_with_different_command_ids_executes_each_time(monkeypatch):
    counter = AtomicCounter()
    monkeypatch.setattr(executor, "_dispatch", lambda action: counter.increment() or {"executed": True})
    action = read_action()

    first = executor.execute_task(new_command_id(), action)
    second = executor.execute_task(new_command_id(), action)

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert counter.value == 2


def test_corrupted_idempotency_storage_does_not_crash(monkeypatch):
    store_path = Path("tests") / f".corrupt_idempotency_{uuid.uuid4().hex}.json"
    store_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(idempotency, "_STORE_PATH", store_path)

    try:
        idempotency.executed_ids.clear()
        idempotency._load()
        assert idempotency.executed_ids == set()
        assert idempotency.reserve(new_command_id()) is True
    finally:
        store_path.unlink(missing_ok=True)


def test_partial_failure_after_dispatch_does_not_execute_twice(monkeypatch):
    counter = AtomicCounter()
    cid = new_command_id()

    def fake_dispatch(action):
        counter.increment()
        return {"executed": True}

    monkeypatch.setattr(executor, "_dispatch", fake_dispatch)
    monkeypatch.setattr(
        idempotency,
        "mark_executed",
        lambda command_id: (_ for _ in ()).throw(RuntimeError("mark failed")),
    )

    first = executor.execute_task(cid, read_action())
    second = executor.execute_task(cid, read_action())

    assert first["status"] == "failed"
    assert first["retryable"] is False
    assert second["status"] == "duplicate"
    assert counter.value == 1


def test_undo_overflow_does_not_crash_or_reexecute(monkeypatch):
    counter = AtomicCounter()
    undo.history[:] = [
        {"action": read_action(), "result": {"status": "success"}, "undone": False}
        for _ in range(undo.MAX_HISTORY)
    ]
    monkeypatch.setattr(executor, "_dispatch", lambda action: counter.increment() or {"executed": True})
    cid = new_command_id()

    first = executor.execute_task(cid, read_action())
    second = executor.execute_task(cid, read_action())

    assert first["status"] == "success"
    assert second["status"] == "duplicate"
    assert counter.value == 1


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "C:/AllowedRoot/link/../../Windows/System32/config",
        "C:/AllowedRoot/%2e%2e/Windows/System32/config",
        "//server/share/file",
    ],
)
def test_sandbox_escape_attempts_are_blocked(monkeypatch, path):
    calls = []
    monkeypatch.setattr(sandbox, "ALLOWED_ROOTS", ["C:/AllowedRoot/safe"])
    monkeypatch.setattr(executor, "_dispatch", lambda action: calls.append(action))

    result = executor.execute_task(new_command_id(), read_action(path))

    assert result["status"] == "rejected"
    assert result["reason"] == "sandbox_rejected"
    assert calls == []


def test_toctou_validation_path_is_same_path_used_for_execution(monkeypatch):
    seen_paths = []
    safe_resolved_path = "c:\\allowedroot\\safe\\file.txt"

    monkeypatch.setattr(sandbox, "resolve_path", lambda path: safe_resolved_path)

    def fake_dispatch(action):
        seen_paths.append(action["params"]["path"])
        return {"executed": True}

    monkeypatch.setattr(executor, "_dispatch", fake_dispatch)

    result = executor.execute_task(new_command_id(), read_action("C:/AllowedRoot/safe/../unsafe/file.txt"))

    assert result["status"] == "success"
    assert seen_paths == [safe_resolved_path]


def test_retry_abuse_does_not_duplicate_side_effects_after_execution(monkeypatch):
    counter = AtomicCounter()
    cid = new_command_id()

    def side_effect_then_fail(action):
        counter.increment()
        return {"executed": True}

    monkeypatch.setattr(executor, "_dispatch", side_effect_then_fail)
    monkeypatch.setattr(
        idempotency,
        "mark_executed",
        lambda command_id: (_ for _ in ()).throw(RuntimeError("persistent failure")),
    )

    results = [executor.execute_task(cid, read_action()) for _ in range(5)]

    assert results[0]["status"] == "failed"
    assert results[0]["retryable"] is False
    assert all(result["status"] == "duplicate" for result in results[1:])
    assert counter.value == 1


def test_confirmation_bypass_attack_is_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "_dispatch", lambda action: calls.append(action))

    # An attacker sending confirmed=True in the same request as the action,
    # with no prior confirmation_required round-trip, must not execute in
    # this call. Fingerprint-bound approval registers the intent ("approved")
    # but a second, separate call is required to actually dispatch it -- the
    # in-band flag alone can never trigger execution.
    result = executor.execute_task(
        new_command_id(),
        {
            "name": "delete_file",
            "params": {"path": "C:/Users/Hp/important.txt"},
            "confirmed": True,
        },
    )

    assert result["status"] != "success"
    assert calls == []


def test_memory_state_pressure_with_many_unique_ids(monkeypatch):
    counter = AtomicCounter()
    monkeypatch.setattr(executor, "_dispatch", lambda action: counter.increment() or {"executed": True})

    command_ids = [new_command_id() for _ in range(1000)]
    for cid in command_ids:
        result = executor.execute_task(cid, read_action())
        assert result["status"] == "success"

    duplicate = executor.execute_task(command_ids[-1], read_action())

    assert duplicate["status"] == "duplicate"
    assert counter.value == 1000
    assert len(idempotency.executed_ids) == 1000
    assert len(idempotency._in_progress_ids) == 0


def test_simulation_mode_zero_real_execution_under_stress(monkeypatch):
    config.set_execution_mode("simulate")
    real_execution_calls = []

    def simulated_dispatch(action):
        result = executor.config.is_execute()
        if result:
            real_execution_calls.append(action)
        return {"executed": result, "simulated": not result}

    monkeypatch.setattr(executor, "_dispatch", simulated_dispatch)

    results = [executor.execute_task(new_command_id(), read_action()) for _ in range(100)]

    assert all(result["status"] == "success" for result in results)
    assert all(result["dispatch"]["executed"] is False for result in results)
    assert real_execution_calls == []

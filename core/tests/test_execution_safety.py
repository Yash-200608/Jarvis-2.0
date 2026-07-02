import pytest

from core import config
from core import executor
from core import idempotency
from core import retry
from core import sandbox
from core import undo
from core import validator


@pytest.fixture(autouse=True)
def reset_core_state(monkeypatch):
    idempotency.executed_ids.clear()
    idempotency._in_progress_ids.clear()
    retry.fail_counts.clear()
    undo.history.clear()
    config.set_execution_mode("simulate")
    monkeypatch.setattr(idempotency, "_persist", lambda: None)


def test_validator_rejects_non_dict_bad_param_types_and_extra_params():
    assert validator.validate_action(None) is False
    assert validator.validate_action({"name": "read_file", "params": {"path": None}}) is False
    assert validator.validate_action(
        {"name": "read_file", "params": {"path": "C:/Users/a.txt", "extra": "x"}}
    ) is False


def test_tools_referenced_by_safety_are_registered_and_validated():
    assert validator.validate_action(
        {"name": "send_whatsapp", "params": {"phone_number": "+1000", "message": "hi"}}
    )
    assert validator.validate_action(
        {"name": "move_to_trash", "params": {"path": "C:/Users/Hp/file.txt"}}
    )


def test_sandbox_normalizes_paths_and_enforces_root_boundaries():
    assert sandbox.validate_path("C:/Users/Hp/file.txt")
    assert sandbox.validate_path("c:/users/Hp/file.txt")
    assert not sandbox.validate_path("C:/Userspublic/secret.txt")
    assert not sandbox.validate_path("C:/Users/%2e%2e/Windows/System32/cmd.exe")
    assert not sandbox.validate_path(None)


def test_high_risk_action_requires_confirmation_before_safety_rewrite():
    result = executor.execute_task(
        "cmd-1",
        {"name": "delete_file", "params": {"path": "C:/Users/Hp/file.txt"}},
    )

    assert result["status"] == "confirmation_required"
    assert result["state"] == "failed"


def test_confirmed_delete_is_rewritten_revalidated_and_sandboxed():
    # First call: confirmed=True in the same request as the action registers
    # approval but must not execute -- in-band confirmation never triggers
    # execution in the same call it arrives in.
    approved = executor.execute_task(
        "cmd-1",
        {
            "name": "delete_file",
            "params": {"path": "C:/Users/Hp/file.txt"},
            "confirmed": True,
        },
    )
    assert approved["status"] == "approved"

    # Second call, same command_id and same requested action: the fingerprint
    # matches the recorded approval, so risk classification (computed on the
    # original delete_file request) is satisfied and the safety-rewritten,
    # undoable primitive actually executes.
    result = executor.execute_task(
        "cmd-1",
        {"name": "delete_file", "params": {"path": "C:/Users/Hp/file.txt"}},
    )

    assert result["status"] == "success"
    assert result["state"] == "success"
    assert result["action"]["name"] == "move_to_trash"
    assert result["requested_action"] == "delete_file"


def test_high_risk_action_confirmation_required_before_rewrite_and_bypass_fails():
    # The core regression this fix closes: risk must be classified on the
    # ORIGINAL requested action, not on whatever the safety layer rewrites it
    # to. Previously, delete_file (high risk) was silently rewritten to
    # move_to_trash (medium risk) before the risk check ran, so this entire
    # confirmation gate was unreachable for delete_file.
    no_confirm = executor.execute_task(
        "cmd-bypass",
        {"name": "delete_file", "params": {"path": "C:/Users/Hp/file.txt"}},
    )
    assert no_confirm["status"] == "confirmation_required"

    # A single call with confirmed=True (no prior confirmation_required round
    # trip observed by the caller) must also not execute -- it only records
    # approval for a subsequent call.
    inline_confirm = executor.execute_task(
        "cmd-bypass",
        {
            "name": "delete_file",
            "params": {"path": "C:/Users/Hp/file.txt"},
            "confirmed": True,
        },
    )
    assert inline_confirm["status"] == "approved"

    # Only a genuinely separate, subsequent call against the matching
    # fingerprint executes.
    executed = executor.execute_task(
        "cmd-bypass",
        {"name": "delete_file", "params": {"path": "C:/Users/Hp/file.txt"}},
    )
    assert executed["status"] == "success"
    assert executed["action"]["name"] == "move_to_trash"


def test_preview_actions_do_not_execute_without_confirmation():
    result = executor.execute_task(
        "cmd-1",
        {
            "name": "send_sms",
            "params": {"phone_number": "+1000", "message": "hi"},
        },
    )

    assert result["status"] == "preview_required"
    assert result["state"] == "failed"


def test_duplicate_command_ids_are_reserved_atomically():
    assert idempotency.reserve("cmd-1") is True
    assert idempotency.reserve("cmd-1") is False


def test_executor_returns_failed_state_and_retry_signal_on_exception(monkeypatch):
    monkeypatch.setattr(executor, "_dispatch", lambda action: (_ for _ in ()).throw(RuntimeError("boom")))

    result = executor.execute_task(
        "cmd-1",
        {"name": "read_file", "params": {"path": "C:/Users/Hp/file.txt"}},
    )

    assert result["status"] == "failed"
    assert result["state"] == "failed"
    assert result["retryable"] is True
    assert idempotency.is_duplicate("cmd-1") is False


def test_invalid_execution_mode_does_not_fall_through_to_execute():
    with pytest.raises(ValueError):
        config.set_execution_mode("typo")

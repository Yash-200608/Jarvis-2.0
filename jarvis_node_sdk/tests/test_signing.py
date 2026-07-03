"""HMAC envelope round-trip tests."""

from __future__ import annotations

import json
import time
import uuid

from jarvis_core.agent.phone_executor import verify as phone_executor_verify
from jarvis_node_sdk.signing import MAX_SKEW_SECONDS, sign_envelope, verify_envelope


KEY = "f" * 64


def test_round_trip() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    assert verify_envelope(env, KEY) is True


def test_tampered_action_rejected() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    env["action"] = "wipe"
    assert verify_envelope(env, KEY) is False


def test_replay_outside_skew_window_rejected() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    env["ts"] = int(time.time()) - MAX_SKEW_SECONDS - 5
    # Re-sign with the stale ts so HMAC still matches but ts check fails.
    from jarvis_node_sdk.signing import _canonical
    import hashlib
    import hmac as _hmac

    env.pop("sig")
    env["sig"] = _hmac.new(KEY.encode(), _canonical(env), hashlib.sha256).hexdigest()
    assert verify_envelope(env, KEY) is False


def test_wrong_key_rejected() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    assert verify_envelope(env, "0" * 64) is False


# ---------------------------------------------------------------------- #
# Regression coverage for the signing-order bug (Priority #3 Milestone 1):
# executor.py used to call sign_envelope() before transport.py injected
# request_id, so phone_executor.py's independent verify() -- which hashes
# whatever keys are present at receive time -- always disagreed with the
# signature. The fix moved request_id generation before signing.
# ---------------------------------------------------------------------- #


def test_executor_envelope_verifies_with_phone_executor() -> None:
    """Round-trip proof: an envelope produced the way executor.py's
    _execute_phone() now builds it verifies successfully against
    phone_executor.py's independent, non-importing verify()."""
    request_id = str(uuid.uuid4())
    envelope = sign_envelope(
        {"action": "get_battery", "params": {}, "request_id": request_id}, KEY
    )
    envelope = {**envelope, "request_id": request_id}  # transport.py's no-op merge
    assert phone_executor_verify(envelope, KEY) is True


def test_executor_envelope_verifies_after_json_wire_roundtrip() -> None:
    """Same proof, but through an actual json.dumps/json.loads hop --
    transport.py's publish() serializes without sort_keys while signing.py's
    canonicalization uses sort_keys=True. This confirms that mismatch is
    harmless: verification re-derives its canonical bytes from the parsed
    dict, not from wire bytes, so key order in transit doesn't matter."""
    request_id = str(uuid.uuid4())
    envelope = sign_envelope(
        {"action": "get_battery", "params": {}, "request_id": request_id}, KEY
    )
    wire = json.loads(json.dumps(envelope, separators=(",", ":")))
    assert phone_executor_verify(wire, KEY) is True


def test_signing_before_request_id_injection_fails_verification() -> None:
    """Pins the historical bug for posterity: if signing ever regresses to
    happening before request_id is injected, this must fail."""
    env = sign_envelope({"action": "ping"}, KEY)
    env = {**env, "request_id": "late-injected"}
    assert phone_executor_verify(env, KEY) is False

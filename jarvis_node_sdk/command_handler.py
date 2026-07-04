"""Dummy command handler -- first consumer of the vendored chimera_contract
module, proves Brain-to-JARVIS command dispatch (Priority #3 Milestone 6).

NOT a skill/task framework: exactly one dummy action ("ping"), nothing
else. This proves the authenticated request/response path -- a real
dispatch table, EventBus wiring, and device capabilities are later work.

Priority #4 Milestone 2: echoes the inbound cmd's request_id back on the
response envelope, so Brain's mqtt/signed.py can correlate by request_id
(safe for concurrent overlapping commands) instead of falling back to
(verb, action) matching for every real round trip.
"""

from __future__ import annotations

from pydantic import ValidationError

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign, verify


def handle_command(payload: dict, key: str, node: str) -> ChimeraEnvelope | None:
    """Verifies an inbound "cmd" envelope and returns a signed "response"
    envelope.

    Returns None on any verification/shape failure (mirrors Brain's
    verify_payload()'s non-raising contract) -- a failure never reaches the
    dummy dispatch step at all. An unrecognized action still returns a
    valid signed response (params={"error": "unknown_action"}), proving
    the round trip for the failure case too, not just the happy path.
    """
    try:
        envelope = ChimeraEnvelope.model_validate(payload)
    except (ValidationError, TypeError):
        return None
    if not verify(envelope, key):
        return None
    if envelope.verb != "cmd":
        return None

    if envelope.action == "ping":
        result = {"pong": True}
    else:
        result = {"error": "unknown_action"}

    response = ChimeraEnvelope(
        node=node,
        verb="response",
        action=envelope.action,
        params=result,
        request_id=envelope.request_id,
    )
    return sign(response, key)

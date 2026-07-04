"""Proves JARVIS's side of the first command/response round trip
(Priority #3 Milestone 6): hand-builds+signs a "cmd" envelope with
JARVIS's own vendored chimera_contract.sign() -- simulating what Brain
would actually publish -- and feeds it through handle_command(), asserting
the returned envelope is a validly-signed "response".

No live broker, no cross-repo import of Brain's code -- this only proves
JARVIS's own real production code (the vendored contract + the new
handler) behaves correctly against the shared wire format.
"""

from __future__ import annotations

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign, verify
from jarvis_node_sdk.command_handler import handle_command

KEY = "f" * 64
NODE = "jarvis-core-pc"


def _signed_cmd_payload(action: str, **overrides) -> dict:
    fields = {"node": NODE, "verb": "cmd", "action": action}
    fields.update(overrides)
    envelope = ChimeraEnvelope(**fields)
    return sign(envelope, KEY).model_dump()


def test_valid_ping_returns_verified_pong_response() -> None:
    payload = _signed_cmd_payload("ping")

    response = handle_command(payload, KEY, NODE)

    assert response is not None
    assert response.verb == "response"
    assert response.action == "ping"
    assert response.params == {"pong": True}
    assert verify(response, KEY) is True


def test_valid_ping_echoes_request_id_on_response() -> None:
    """Priority #4 Milestone 2: handle_command() must copy the inbound
    cmd's request_id onto the response so Brain's send_command_and_await_
    response() can correlate by request_id instead of the (verb, action)
    fallback."""
    payload = _signed_cmd_payload("ping", request_id="abc-123")

    response = handle_command(payload, KEY, NODE)

    assert response is not None
    assert response.request_id == "abc-123"


def test_ping_with_no_request_id_echoes_none() -> None:
    """A cmd with no request_id (an older caller) must not have one
    invented for it -- the response's request_id stays None, which is
    exactly what makes Brain's (verb, action) fallback path trigger."""
    payload = _signed_cmd_payload("ping")

    response = handle_command(payload, KEY, NODE)

    assert response is not None
    assert response.request_id is None


def test_unknown_action_still_returns_a_validly_signed_response() -> None:
    payload = _signed_cmd_payload("do-a-backflip")

    response = handle_command(payload, KEY, NODE)

    assert response is not None
    assert response.params == {"error": "unknown_action"}
    assert verify(response, KEY) is True


def test_tampered_cmd_returns_none() -> None:
    payload = _signed_cmd_payload("ping")
    tampered = {**payload, "action": "wipe_disk"}

    assert handle_command(tampered, KEY, NODE) is None


def test_wrong_key_returns_none() -> None:
    payload = _signed_cmd_payload("ping")

    assert handle_command(payload, "0" * 64, NODE) is None


def test_wrong_verb_returns_none() -> None:
    payload = _signed_cmd_payload("ping", verb="state")

    assert handle_command(payload, KEY, NODE) is None


def test_malformed_payload_returns_none_without_raising() -> None:
    assert handle_command({"garbage": True}, KEY, NODE) is None

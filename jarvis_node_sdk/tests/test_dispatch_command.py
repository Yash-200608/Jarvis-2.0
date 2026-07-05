"""dispatch_command() tests (Priority #4 Milestone 5) -- the async,
table-driven dispatcher. handle_command()'s ping-only contract is already
covered by test_command_handler.py; this file covers the new dispatch
rules: table lookup, built-in ping, raising-skill containment, and the
listener wiring with a skills table through the REAL dispatch loop.
"""

from __future__ import annotations

import asyncio
import json

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign, verify
from jarvis_node_sdk.command_handler import dispatch_command
from jarvis_node_sdk.command_listener import start_command_listener
from jarvis_node_sdk.transport import MqttTransport

KEY = "f" * 64
NODE = "jarvis-core-pc"


def _signed_cmd_payload(action: str, params: dict | None = None, **overrides) -> dict:
    fields: dict = {"node": NODE, "verb": "cmd", "action": action, "params": params or {}}
    fields.update(overrides)
    return sign(ChimeraEnvelope(**fields), KEY).model_dump()


def test_dispatches_to_registered_skill_with_params() -> None:
    seen: dict = {}

    async def fake_skill(params: dict) -> dict:
        seen.update(params)
        return {"ok": True, "echo": params}

    payload = _signed_cmd_payload("phone.tts", {"text": "hello"}, request_id="rid-1")

    response = asyncio.run(
        dispatch_command(payload, KEY, NODE, {"phone.tts": fake_skill})
    )

    assert seen == {"text": "hello"}
    assert response is not None
    assert response.verb == "response"
    assert response.action == "phone.tts"
    assert response.params == {"ok": True, "echo": {"text": "hello"}}
    assert response.request_id == "rid-1"
    assert verify(response, KEY) is True


def test_ping_is_built_in_regardless_of_table() -> None:
    payload = _signed_cmd_payload("ping")

    response = asyncio.run(dispatch_command(payload, KEY, NODE, {}))

    assert response is not None
    assert response.params == {"pong": True}


def test_unknown_action_answers_unknown_action() -> None:
    payload = _signed_cmd_payload("no.such.skill")

    response = asyncio.run(dispatch_command(payload, KEY, NODE, {}))

    assert response is not None
    assert response.params == {"error": "unknown_action"}
    assert verify(response, KEY) is True


def test_raising_skill_answers_truthful_failure_not_silence() -> None:
    async def broken_skill(params: dict) -> dict:
        raise RuntimeError("boom")

    payload = _signed_cmd_payload("phone.broken")

    response = asyncio.run(
        dispatch_command(payload, KEY, NODE, {"phone.broken": broken_skill})
    )

    assert response is not None
    assert response.params["ok"] is False
    assert "RuntimeError" in response.params["error"]
    assert verify(response, KEY) is True


def test_unverified_payload_gets_no_reply() -> None:
    payload = _signed_cmd_payload("ping")
    tampered = {**payload, "action": "phone.tts"}

    response = asyncio.run(dispatch_command(tampered, KEY, NODE, {}))

    assert response is None


# ---------------------------------------------------------------------- #
# Listener wiring with a skills table, through the REAL dispatch loop      #
# (same _FakeMsg/_FakeMessages/_FakeAiomqttClient trio as                  #
# test_command_listener.py)                                                #
# ---------------------------------------------------------------------- #


class _FakeMqttConfig:
    host = "localhost"
    port = 1883
    client_id = "test-client"
    keepalive = 30
    clean_session = False
    qos = 1
    username = None
    password = None


class _FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class _FakeMessages:
    def __init__(self, msgs):
        self._msgs = msgs

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._msgs:
            yield m


class _FakeAiomqttClient:
    def __init__(self, messages):
        self.messages = messages


def test_listener_with_skills_publishes_skill_result_via_real_dispatch() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["topic"] = topic
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def fake_battery(params: dict) -> dict:
        return {"ok": True, "battery": {"level": 55}}

    cmd_bytes = json.dumps(
        _signed_cmd_payload("phone.battery"), separators=(",", ":")
    ).encode()

    async def run():
        await start_command_listener(
            transport, KEY, NODE, skills={"phone.battery": fake_battery}
        )
        fake_client = _FakeAiomqttClient(
            _FakeMessages([_FakeMsg(f"chimera/{NODE}/cmd", cmd_bytes)])
        )
        await transport._dispatch_loop(fake_client)

    asyncio.run(run())

    assert published["topic"] == f"chimera/{NODE}/response"
    response = ChimeraEnvelope.model_validate(published["payload"])
    assert response.params == {"ok": True, "battery": {"level": 55}}
    assert verify(response, KEY) is True


def test_listener_without_skills_keeps_ping_only_contract() -> None:
    """The default (no table) must behave exactly like the Priority #3
    listener: ping answers, a real skill name answers unknown_action."""
    transport = MqttTransport(_FakeMqttConfig())
    published: list = []

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published.append(payload)

    transport.publish = fake_publish  # type: ignore[method-assign]

    cmd_bytes = json.dumps(
        _signed_cmd_payload("phone.battery"), separators=(",", ":")
    ).encode()

    async def run():
        await start_command_listener(transport, KEY, NODE)
        fake_client = _FakeAiomqttClient(
            _FakeMessages([_FakeMsg(f"chimera/{NODE}/cmd", cmd_bytes)])
        )
        await transport._dispatch_loop(fake_client)

    asyncio.run(run())

    response = ChimeraEnvelope.model_validate(published[0])
    assert response.params == {"error": "unknown_action"}

"""Proves start_command_listener()'s REAL subscribe()+dispatch wiring
(Priority #3 Milestone 8) -- not just handle_command() in isolation,
already covered by test_command_handler.py.

Technique: drive MqttTransport._dispatch_loop() directly against a fake
async-iterable of fake MQTT messages (porting the _FakeMsg/_FakeMessages
idiom Brain's test_mqtt_client.py already uses), then assert on what
publish() was called with -- proving the real subscribe()-registered
handler in command_listener.py actually fires and responds.
"""

from __future__ import annotations

import asyncio

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign, verify
from jarvis_node_sdk.command_listener import start_command_listener
from jarvis_node_sdk.transport import MqttTransport

KEY = "f" * 64
NODE = "jarvis-core-pc"


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


def _signed_cmd_bytes(action: str, **overrides) -> bytes:
    import json

    fields = {"node": NODE, "verb": "cmd", "action": action}
    fields.update(overrides)
    envelope = ChimeraEnvelope(**fields)
    signed = sign(envelope, KEY)
    return json.dumps(signed.model_dump(), separators=(",", ":")).encode()


def test_valid_ping_publishes_a_verified_signed_response() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["topic"] = topic
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        await start_command_listener(transport, KEY, NODE)
        fake_client = _FakeAiomqttClient(
            _FakeMessages([_FakeMsg(f"chimera/{NODE}/cmd", _signed_cmd_bytes("ping"))])
        )
        await transport._dispatch_loop(fake_client)

    asyncio.run(run())

    assert published.get("topic") == f"chimera/{NODE}/response"
    response = ChimeraEnvelope.model_validate(published["payload"])
    assert response.verb == "response"
    assert response.params == {"pong": True}
    assert verify(response, KEY) is True


def test_tampered_cmd_publishes_nothing() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published = {"called": False}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["called"] = True

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        await start_command_listener(transport, KEY, NODE)
        import json

        tampered = json.loads(_signed_cmd_bytes("ping"))
        tampered["action"] = "wipe_disk"
        fake_client = _FakeAiomqttClient(
            _FakeMessages(
                [_FakeMsg(f"chimera/{NODE}/cmd", json.dumps(tampered, separators=(",", ":")).encode())]
            )
        )
        await transport._dispatch_loop(fake_client)

    asyncio.run(run())

    assert published["called"] is False

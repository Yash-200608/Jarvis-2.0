"""Proves state_publisher's real sign()+publish() wiring
(Priority #3 Milestone 10) -- the JARVIS-side counterpart to Brain's
mqtt/state.py subscriber (Milestone 7). Params are opaque (no schema
imposed), mirroring Brain's own storage-shape decision.
"""

from __future__ import annotations

import asyncio

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, verify
from jarvis_node_sdk.state_publisher import publish_state
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


def test_publish_state_publishes_verified_signed_envelope_with_params() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["topic"] = topic
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        await publish_state(transport, KEY, NODE, {"battery": 80, "cpu": 12})

    asyncio.run(run())

    assert published["topic"] == f"chimera/{NODE}/state"
    envelope = ChimeraEnvelope.model_validate(published["payload"])
    assert envelope.verb == "state"
    assert envelope.node == NODE
    assert envelope.params == {"battery": 80, "cpu": 12}
    assert verify(envelope, KEY) is True


def test_publish_state_empty_dict_round_trips() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        await publish_state(transport, KEY, NODE, {})

    asyncio.run(run())

    envelope = ChimeraEnvelope.model_validate(published["payload"])
    assert envelope.params == {}

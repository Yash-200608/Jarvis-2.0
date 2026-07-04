"""Proves presence_publisher's real sign()+publish() wiring
(Priority #3 Milestone 10) -- the JARVIS-side counterpart to Brain's
mqtt/presence.py subscriber (Milestone 5).
"""

from __future__ import annotations

import asyncio

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, verify
from jarvis_node_sdk.presence_publisher import publish_presence, start_presence_heartbeat
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


def test_publish_presence_publishes_verified_signed_envelope() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["topic"] = topic
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        await publish_presence(transport, KEY, NODE)

    asyncio.run(run())

    assert published["topic"] == f"chimera/{NODE}/presence"
    envelope = ChimeraEnvelope.model_validate(published["payload"])
    assert envelope.verb == "presence"
    assert envelope.node == NODE
    assert verify(envelope, KEY) is True


def test_start_presence_heartbeat_publishes_repeatedly() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    calls: list = []

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        calls.append((topic, payload))

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        task = asyncio.create_task(
            start_presence_heartbeat(transport, KEY, NODE, interval=0.01)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())

    assert len(calls) >= 2
    assert all(topic == f"chimera/{NODE}/presence" for topic, _ in calls)


def test_start_presence_heartbeat_survives_publish_failure() -> None:
    """A single failed publish must not kill the loop -- the next tick
    still fires."""
    transport = MqttTransport(_FakeMqttConfig())
    call_count = {"n": 0}

    async def flaky_publish(topic, payload, *, qos=None, retain=False):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient publish failure")

    transport.publish = flaky_publish  # type: ignore[method-assign]

    async def run():
        task = asyncio.create_task(
            start_presence_heartbeat(transport, KEY, NODE, interval=0.01)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())

    assert call_count["n"] >= 2

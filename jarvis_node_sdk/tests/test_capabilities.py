"""Proves SkillRegistry + publish_capabilities()'s real sign()+publish()
wiring (Priority #4 Milestone 3) -- the node-side half of capability
declaration (NP-5): Brain (Milestone 4) must plan only from what a node
actually declares here, never a hardcoded list.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_node_sdk.capabilities import SkillRegistry, publish_capabilities
from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, verify
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


def test_registry_register_list_is_sorted_and_deduplicated() -> None:
    registry = SkillRegistry()
    registry.register("phone.battery")
    registry.register("phone.torch")
    registry.register("phone.battery")  # duplicate, should not appear twice

    assert registry.list() == ["phone.battery", "phone.torch"]


def test_registry_unregister_removes_a_skill() -> None:
    registry = SkillRegistry()
    registry.register("phone.battery")
    registry.unregister("phone.battery")

    assert registry.list() == []


def test_registry_unregister_missing_skill_is_a_noop() -> None:
    registry = SkillRegistry()
    registry.unregister("never-registered")  # must not raise

    assert registry.list() == []


def test_registry_rejects_empty_skill_name() -> None:
    registry = SkillRegistry()
    with pytest.raises(ValueError):
        registry.register("")


def test_publish_capabilities_publishes_verified_signed_envelope() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["topic"] = topic
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    registry = SkillRegistry()
    registry.register("phone.battery")
    registry.register("phone.tts")

    async def run():
        await publish_capabilities(transport, KEY, NODE, registry)

    asyncio.run(run())

    assert published["topic"] == f"chimera/{NODE}/capabilities"
    envelope = ChimeraEnvelope.model_validate(published["payload"])
    assert envelope.verb == "capabilities"
    assert envelope.node == NODE
    assert envelope.params == {"skills": ["phone.battery", "phone.tts"]}
    assert verify(envelope, KEY) is True


def test_publish_capabilities_with_empty_registry_publishes_empty_list() -> None:
    transport = MqttTransport(_FakeMqttConfig())
    published: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published["payload"] = payload

    transport.publish = fake_publish  # type: ignore[method-assign]

    async def run():
        await publish_capabilities(transport, KEY, NODE, SkillRegistry())

    asyncio.run(run())

    envelope = ChimeraEnvelope.model_validate(published["payload"])
    assert envelope.params == {"skills": []}


def test_publish_capabilities_reflects_registry_changes_across_calls() -> None:
    """Proves the "on connect + on change" publish model: the same
    registry, published twice after a change, produces two different
    signed declarations -- there is no automatic re-publish loop, the
    caller controls when to call this again."""
    transport = MqttTransport(_FakeMqttConfig())
    published: list[dict] = []

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        published.append(payload)

    transport.publish = fake_publish  # type: ignore[method-assign]

    registry = SkillRegistry()
    registry.register("phone.battery")

    async def run():
        await publish_capabilities(transport, KEY, NODE, registry)
        registry.register("pc.system.lock")
        await publish_capabilities(transport, KEY, NODE, registry)

    asyncio.run(run())

    first = ChimeraEnvelope.model_validate(published[0])
    second = ChimeraEnvelope.model_validate(published[1])
    assert first.params == {"skills": ["phone.battery"]}
    assert second.params == {"skills": ["pc.system.lock", "phone.battery"]}

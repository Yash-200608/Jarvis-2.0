"""Presence publisher -- the JARVIS-side counterpart to Brain's
mqtt/presence.py subscriber (Priority #3 Milestone 10). Publishes a signed
presence envelope to chimera/<node>/presence, proving a real node can
announce itself using the vendored chimera_contract.

NOT a full sensor/telemetry system -- one signed presence heartbeat loop,
nothing else. jarvis_core/core/sensors.py's separate metrics-publishing
system (old, unsigned jarvis/* namespace) is untouched.
"""

from __future__ import annotations

import asyncio
import logging

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign
from jarvis_node_sdk.transport import MqttTransport

logger = logging.getLogger("presence_publisher")


async def publish_presence(transport: MqttTransport, key: str, node: str) -> None:
    """Signs and publishes one presence envelope to chimera/{node}/presence."""
    envelope = ChimeraEnvelope(node=node, verb="presence")
    signed = sign(envelope, key)
    await transport.publish(f"chimera/{node}/presence", signed.model_dump())


async def start_presence_heartbeat(
    transport: MqttTransport, key: str, node: str, *, interval: float = 30.0
) -> None:
    """Publishes a signed presence envelope every `interval` seconds,
    forever, until the enclosing task is cancelled. Callers run this as a
    background task, e.g. `asyncio.create_task(start_presence_heartbeat(...))`
    -- this coroutine itself never returns under normal operation.
    """
    while True:
        try:
            await publish_presence(transport, key, node)
        except Exception:  # noqa: BLE001 -- one failed heartbeat must not kill the loop
            logger.exception("presence heartbeat publish failed")
        await asyncio.sleep(interval)

"""State publisher -- the JARVIS-side counterpart to Brain's mqtt/state.py
subscriber (Priority #3 Milestone 10). Publishes a signed state envelope
to chimera/<node>/state, carrying an opaque, caller-supplied dict --
mirrors Brain's own "no imposed schema on params" decision (Milestone 7).

One-shot only, deliberately not a polling loop -- unlike presence (which
needs a heartbeat for Device.is_online() to mean anything), a single state
report is a complete, self-contained fact. A caller that wants periodic
state reporting composes its own loop around publish_state(); building
that loop here would be automation beyond what the execution spine
requires.
"""

from __future__ import annotations

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign
from jarvis_node_sdk.transport import JsonDict, MqttTransport


async def publish_state(transport: MqttTransport, key: str, node: str, state: JsonDict) -> None:
    """Signs and publishes one state envelope to chimera/{node}/state.
    `state` is stored opaquely by Brain -- no schema is imposed here."""
    envelope = ChimeraEnvelope(node=node, verb="state", params=state)
    signed = sign(envelope, key)
    await transport.publish(f"chimera/{node}/state", signed.model_dump())

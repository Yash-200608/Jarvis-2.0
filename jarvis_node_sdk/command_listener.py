"""Command listener -- wires an inbound chimera/<node>/cmd MQTT message to
the command dispatcher, publishing the signed response back to
chimera/<node>/response. First real subscribe/publish wiring for the
command path (proven purely via hand-built dicts in Milestone 6).

Priority #4 Milestone 5: gains an optional `skills` table, routed through
command_handler.dispatch_command(). With no table (the default), behavior
is identical to the Priority #3 ping-only listener -- dispatch_command()
with an empty table reproduces handle_command()'s exact contract (ping is
built in; anything else answers unknown_action).

Still NOT a general task/skill framework -- one dict lookup per command,
no chaining/queueing/scheduling here (NP-1/NP-2: plans live Brain-side).
"""

from __future__ import annotations

from typing import Mapping

from jarvis_node_sdk.command_handler import SkillFn, dispatch_command
from jarvis_node_sdk.transport import JsonDict, MqttTransport


async def start_command_listener(
    transport: MqttTransport,
    key: str,
    node: str,
    skills: Mapping[str, SkillFn] | None = None,
) -> None:
    """Subscribes `transport` to chimera/{node}/cmd. Each inbound message
    is verified+dispatched via dispatch_command() against `skills` (empty
    when None); a signed response (if any) is published to
    chimera/{node}/response.
    """
    skill_table: Mapping[str, SkillFn] = skills or {}

    async def _on_cmd(topic: str, payload: JsonDict) -> None:
        response = await dispatch_command(payload, key, node, skill_table)
        if response is not None:
            await transport.publish(f"chimera/{node}/response", response.model_dump())

    await transport.subscribe(f"chimera/{node}/cmd", _on_cmd)

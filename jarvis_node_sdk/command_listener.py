"""Command listener -- wires an inbound chimera/<node>/cmd MQTT message to
command_handler.handle_command(), publishing the signed response back to
chimera/<node>/response. First real subscribe/publish wiring for the
command path (proven purely via hand-built dicts in Milestone 6).

NOT a general task/skill framework -- dispatches to the existing single
dummy handler unmodified.
"""

from __future__ import annotations

from jarvis_node_sdk.command_handler import handle_command
from jarvis_node_sdk.transport import JsonDict, MqttTransport


async def start_command_listener(transport: MqttTransport, key: str, node: str) -> None:
    """Subscribes `transport` to chimera/{node}/cmd. Each inbound message
    is verified+dispatched via handle_command(); a signed response (if
    any) is published to chimera/{node}/response. handle_command() is
    synchronous (pure HMAC/pydantic work, no I/O) -- called inline, no
    executor offload needed.
    """

    async def _on_cmd(topic: str, payload: JsonDict) -> None:
        response = handle_command(payload, key, node)
        if response is not None:
            await transport.publish(f"chimera/{node}/response", response.model_dump())

    await transport.subscribe(f"chimera/{node}/cmd", _on_cmd)

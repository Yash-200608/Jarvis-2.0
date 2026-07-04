"""Standalone live demo node -- Priority #3 Milestone 12 (Execution Spine
Capstone). Connects a real MqttTransport to a real broker and wires
together jarvis_node_sdk's command listener (M8) + presence heartbeat
(M10), the minimum needed to prove the execution spine end-to-end against
a live broker with at least one real command.

Deliberately self-contained: does NOT call jarvis_node_sdk.config.load_config()
or touch jarvis_core.yaml -- this demo doesn't depend on Ollama/memory/
sandbox config being valid, since it has nothing to do with cognition.
Not a general-purpose node runner or process supervisor; narrow to this
milestone's demonstration need.

Usage:
    python run_chimera_demo_node.py --node jarvis-core-pc --key <hmac-hex>
    python run_chimera_demo_node.py --node jarvis-core-pc --key <hmac-hex> \
        --host 127.0.0.1 --port 1883 --state '{"battery": 91}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from jarvis_node_sdk.command_listener import start_command_listener
from jarvis_node_sdk.presence_publisher import publish_presence, start_presence_heartbeat
from jarvis_node_sdk.state_publisher import publish_state
from jarvis_node_sdk.transport import MqttTransport


class _DemoMqttConfig:
    """Duck-typed MqttTransport config -- transport.py only ever reads
    these eight attributes (confirmed by direct read), never isinstance-
    checks against jarvis_node_sdk.config.MqttConfig. Mirrors the test
    suite's own _FakeMqttConfig shape."""

    def __init__(self, host: str, port: int, node: str) -> None:
        self.host = host
        self.port = port
        self.client_id = node
        self.keepalive = 30
        self.clean_session = False
        self.qos = 1
        self.username = None
        self.password = None


async def run(node: str, key: str, host: str, port: int, state: dict | None) -> None:
    cfg = _DemoMqttConfig(host, port, node)
    async with MqttTransport(cfg) as transport:
        await start_command_listener(transport, key, node)
        await publish_presence(transport, key, node)
        if state is not None:
            await publish_state(transport, key, node, state)
        print(
            f"[demo-node] {node!r} connected to {host}:{port} -- "
            f"listening on chimera/{node}/cmd, presence+state published.",
            flush=True,
        )
        heartbeat = asyncio.create_task(
            start_presence_heartbeat(transport, key, node, interval=15.0)
        )
        try:
            await asyncio.Event().wait()  # run until the process is killed
        finally:
            heartbeat.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--state", default=None, help="JSON dict published once as initial state")
    args = parser.parse_args()

    state = json.loads(args.state) if args.state else None

    # aiomqtt needs add_reader()/add_writer(), which ProactorEventLoop --
    # asyncio's Windows default since 3.8 -- does not implement. Unlike
    # Brain's uvicorn-hosted process (main.py, Brain repo), this script
    # calls asyncio.run() directly with no framework hardcoding the loop
    # factory, so the standard set_event_loop_policy() fix works here.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(run(args.node, args.key, args.host, args.port, state))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

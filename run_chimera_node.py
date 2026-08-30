"""Chimera production node entrypoint (Priority #4 Milestone 8).

The always-on JARVIS node process: connects to the broker, declares its
full skill table, answers signed commands, and heartbeats presence --
config-sourced from the environment (see jarvis_node_sdk/node_config.py),
never CLI flags, so secrets stay out of process lists and shell history.

    # PowerShell (PC node)
    $env:CHIMERA_NODE_ID  = "jarvis-core-pc"
    $env:CHIMERA_HMAC_KEY = "<shared spine key>"
    python run_chimera_node.py

    # or with an env file
    python run_chimera_node.py --env-file C:\\chimera\\node.env

Graceful shutdown: SIGINT/SIGTERM (Ctrl+C, service stop) cancels the
heartbeat and closes the transport cleanly.

AUTO-RESTART (operator guidance, not enforced here):
  * Windows: register with Task Scheduler ("At startup", restart on
    failure) or wrap with NSSM: `nssm install ChimeraNode python
    C:\\...\\run_chimera_node.py`; put the CHIMERA_* variables in the
    service environment, not a world-readable file.
  * Termux (phone node): use termux-services (runit) with a service dir
    exporting the CHIMERA_* variables -- see the provisioning doc
    (Milestone 9).

Reconnect behavior: MqttTransport's supervisor reconnects with backoff
and re-subscribes automatically; presence heartbeats resume on their own.
Capability declarations are published once at startup -- Brain's registry
persists them, so a broker restart does not erase Brain's knowledge of
this node's skills.

`run_chimera_demo_node.py` is the DEV TOOL variant (CLI-flag key, ad-hoc
state) -- do not run it as a service.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from jarvis_node_sdk.capabilities import SkillRegistry, publish_capabilities
from jarvis_node_sdk.command_listener import start_command_listener
from jarvis_node_sdk.node_config import NodeConfig, NodeConfigError, load_node_config
from jarvis_node_sdk.pc_skills import build_pc_skills
from jarvis_node_sdk.phone_skills import build_phone_skills
from jarvis_node_sdk.presence_publisher import publish_presence, start_presence_heartbeat
from jarvis_node_sdk.transport import MqttTransport


class _NodeMqttConfig:
    """Duck-typed MqttTransport config (transport.py reads exactly these)."""

    def __init__(self, cfg: NodeConfig) -> None:
        self.host = cfg.broker_host
        self.port = cfg.broker_port
        self.client_id = cfg.node_id
        self.keepalive = 30
        self.clean_session = False
        self.qos = 1
        self.username = None
        self.password = None


async def run_node(cfg: NodeConfig) -> None:
    stop = asyncio.Event()

    def _request_stop(*_args) -> None:
        stop.set()

    # Windows has no loop.add_signal_handler; signal.signal works for
    # SIGINT/SIGTERM delivered to the process on both platforms.
    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (ValueError, AttributeError):  # pragma: no cover -- platform-dependent
        pass

    async with MqttTransport(_NodeMqttConfig(cfg)) as transport:
        if cfg.skill_set == "phone":
            skills = build_phone_skills()
        elif cfg.skill_set == "pc":
            skills = build_pc_skills(ceilings_path=cfg.ceilings_path)
        else:
            skills = {
                **build_phone_skills(),
                **build_pc_skills(ceilings_path=cfg.ceilings_path),
            }
        registry = SkillRegistry()
        for name in skills:
            registry.register(name)

        await start_command_listener(transport, cfg.hmac_key, cfg.node_id, skills=skills)
        await publish_capabilities(transport, cfg.hmac_key, cfg.node_id, registry)
        await publish_presence(transport, cfg.hmac_key, cfg.node_id)
        print(
            f"[chimera-node] {cfg.node_id!r} up -- broker {cfg.broker_host}:{cfg.broker_port}, "
            f"{len(skills)} skills declared, ceilings={'file:' + cfg.ceilings_path if cfg.ceilings_path else 'defaults'}",
            flush=True,
        )

        heartbeat = asyncio.create_task(
            start_presence_heartbeat(transport, cfg.hmac_key, cfg.node_id, interval=15.0)
        )
        try:
            await stop.wait()
        finally:
            heartbeat.cancel()
            print(f"[chimera-node] {cfg.node_id!r} shutting down cleanly", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=None, help="KEY=VALUE file seeding missing CHIMERA_* vars")
    args = parser.parse_args()

    try:
        cfg = load_node_config(env_file=args.env_file)
    except NodeConfigError as exc:
        print(f"[chimera-node] refusing to start: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)

    # aiomqtt needs add_reader()/add_writer(); ProactorEventLoop (Windows
    # default) lacks them. Bare asyncio.run() honors the policy, unlike
    # uvicorn-hosted processes (see Brain/main.py for that variant).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(run_node(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

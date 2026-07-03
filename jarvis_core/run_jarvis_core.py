"""Jarvis-Core PC entrypoint.

Wires Transport -> Sensors -> Brain -> Executor -> Scheduler into a single
asyncio event loop. Ctrl-C gracefully tears everything down.

Usage::

    python run_jarvis_core.py --config configs/jarvis_core.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path
from typing import Any

# jarvis_node_sdk/ lives one level up (JARVIS repo root), a sibling of
# jarvis_core/ -- this script is normally launched with cwd=jarvis_core/, so
# `core.*` resolves via Python's implicit script-directory sys.path entry,
# but jarvis_node_sdk needs the repo root added explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.brain import Brain
from core.config import load_config
from core.executor import Executor
from core.logging_setup import configure_logging, get_logger
from core.memory import MemoryStore
from core.ollama_client import OllamaClient
from core.scheduler import MaintenanceScheduler
from jarvis_node_sdk.sensors import SensorEvent, SensorLoop
from jarvis_node_sdk.transport import MqttTransport

log = get_logger("CORE")


async def _pc_heartbeat(bus: MqttTransport, topic: str) -> None:
    """Periodic retained heartbeat from the PC side."""
    while True:
        await bus.publish(
            topic,
            {"status": "online", "ts": time.time(), "source": "pc"},
            retain=True,
        )
        await asyncio.sleep(30)


async def main_async(cfg_path: str | None) -> None:
    cfg = load_config(cfg_path)
    configure_logging(
        log_file=cfg.logging.file,
        level=cfg.logging.level,
        rotation=cfg.logging.rotation,
        retention=cfg.logging.retention,
    )
    log.info(f"Jarvis-Core starting — broker={cfg.mqtt.host}:{cfg.mqtt.port}")

    # Make sure runtime directories exist up front.
    for p in (
        cfg.brain.inbox_dir,
        cfg.sandbox.root,
        Path(cfg.memory.chroma_path),
        Path(cfg.logging.file).parent,
    ):
        Path(p).mkdir(parents=True, exist_ok=True)

    # Long-term memory is best-effort — a Chroma crash shouldn't nuke the
    # whole brain, so fall back to None.
    memory: MemoryStore | None
    try:
        memory = MemoryStore(cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Long-term memory unavailable: {exc!r}")
        memory = None

    ollama = OllamaClient(cfg.ollama)

    # Wire up LWT so PC absence is visible to the phone too.
    lwt = {"status": "offline", "ts": time.time(), "source": "pc"}
    async with MqttTransport(
        cfg.mqtt,
        will_topic=cfg.mqtt.topics.heartbeat_pc,
        will_payload=lwt,
    ) as bus:
        executor = Executor(cfg, bus)
        brain = Brain(cfg, bus, ollama, executor, memory)

        events: asyncio.Queue[SensorEvent] = asyncio.Queue(maxsize=1024)
        sensors = SensorLoop(cfg, bus, events)
        await sensors.start()

        scheduler = MaintenanceScheduler(cfg, bus, ollama)
        await scheduler.start()

        tasks = [
            asyncio.create_task(
                _pc_heartbeat(bus, cfg.mqtt.topics.heartbeat_pc), name="hb-pc"
            ),
            asyncio.create_task(brain.run(events), name="brain"),
        ]

        stop = asyncio.Event()

        def _handle_signal(*_: Any) -> None:
            log.warning("Signal received — shutting down")
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except NotImplementedError:
                pass  # Windows

        try:
            await stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            await scheduler.stop()
            await sensors.stop()
            await ollama.aclose()
            if memory is not None:
                await memory.aclose()
            log.info("Jarvis-Core stopped cleanly")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jarvis-Core PC brain")
    parser.add_argument("--config", default=None, help="Path to YAML config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(main_async(args.config))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

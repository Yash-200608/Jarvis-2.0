"""Level-1 Sensor loop.

Polls three sources and emits :class:`SensorEvent` instances onto an
``asyncio.Queue`` consumed by the brain:

1. ``brain/inbox/*.txt``  — user CLI / voice commands dropped as files.
2. MQTT subscriptions     — phone battery, SMS, location pushed by the
                            Termux executor or any other device.
3. System metrics         — CPU%, RAM%, active window title (best-effort).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from .config import JarvisConfig
from .logging_setup import get_logger
from .transport import MqttTransport

log = get_logger("SENSOR")


@dataclass
class SensorEvent:
    source: str                       # "inbox" | "mqtt" | "system"
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)


class SensorLoop:
    """Background pump producing :class:`SensorEvent`s for the brain."""

    def __init__(
        self,
        cfg: JarvisConfig,
        bus: MqttTransport,
        out_queue: asyncio.Queue[SensorEvent],
        *,
        inbox_poll_seconds: float = 1.0,
        system_poll_seconds: float = 30.0,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._out = out_queue
        self._inbox_poll = inbox_poll_seconds
        self._system_poll = system_poll_seconds
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        Path(self._cfg.brain.inbox_dir).mkdir(parents=True, exist_ok=True)
        # Fan out — one task per sensor source.
        self._tasks = [
            asyncio.create_task(self._inbox_loop(), name="sensor-inbox"),
            asyncio.create_task(self._system_loop(), name="sensor-system"),
            asyncio.create_task(self._mqtt_loop(), name="sensor-mqtt"),
        ]
        log.info("Sensor loops started")

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ #
    # Sources                                                            #
    # ------------------------------------------------------------------ #

    async def _inbox_loop(self) -> None:
        inbox = Path(self._cfg.brain.inbox_dir)
        seen: set[str] = set()
        while not self._stop.is_set():
            try:
                for path in sorted(inbox.glob("*.txt")):
                    if path.name in seen:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace").strip()
                    if not text:
                        path.unlink(missing_ok=True)
                        continue
                    await self._out.put(
                        SensorEvent(
                            source="inbox",
                            payload={"file": path.name, "text": text},
                        )
                    )
                    log.info(f"Inbox command: {path.name} ({len(text)} chars)")
                    # Move out so we don't reprocess; keep an audit trail.
                    archived = inbox / "_processed"
                    archived.mkdir(exist_ok=True)
                    path.rename(archived / path.name)
                    seen.add(path.name)
            except Exception as exc:  # noqa: BLE001
                log.exception(f"Inbox poll error: {exc!r}")
            await asyncio.sleep(self._inbox_poll)

    async def _system_loop(self) -> None:
        # Prime psutil — the first cpu_percent() call always returns 0.0.
        psutil.cpu_percent(interval=None)
        while not self._stop.is_set():
            try:
                metrics = {
                    "cpu_pct": psutil.cpu_percent(interval=None),
                    "ram_pct": psutil.virtual_memory().percent,
                    "active_window": _active_window_title(),
                }
                await self._out.put(
                    SensorEvent(source="system", payload=metrics)
                )
                # Echo to MQTT so other devices can see PC health too.
                await self._bus.publish(
                    f"{self._cfg.mqtt.topics.sensors.rstrip('/#')}/pc",
                    metrics,
                    retain=True,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception(f"System metrics error: {exc!r}")
            await asyncio.sleep(self._system_poll)

    async def _mqtt_loop(self) -> None:
        async def _on(topic: str, payload: dict[str, Any]) -> None:
            await self._out.put(
                SensorEvent(source="mqtt", payload={"topic": topic, **payload})
            )

        # Listen to anything under jarvis/sensors/# plus phone state changes.
        await self._bus.subscribe(self._cfg.mqtt.topics.sensors, _on)
        await self._bus.subscribe(self._cfg.mqtt.topics.phone_state, _on)
        # Block forever — handlers do the work via callbacks.
        await self._stop.wait()


def _active_window_title() -> str:
    """Best-effort active window title; returns "" if unavailable."""
    try:
        import pygetwindow  # type: ignore[import-not-found]

        win = pygetwindow.getActiveWindow()
        return getattr(win, "title", "") or ""
    except Exception:  # noqa: BLE001
        return ""

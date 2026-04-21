"""Recursive self-maintenance scheduler.

Three jobs run on an interval (default 1 h, matching the spec):

1. **Context compression** — if ``history.log`` exceeds the token budget,
   summarise it via the Planner model and append the summary to
   ``knowledge_graph.md``, then truncate the log.
2. **Phone health check** — read the most recent retained heartbeat from
   ``jarvis/heartbeat/phone``. If older than the offline threshold, write
   ``NEED_ATTENTION.md`` and emit an alert.
3. **VRAM management** — call :meth:`OllamaClient.unload_idle` to release
   any model that hasn't been used for ``ollama.idle_unload_seconds``.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import tiktoken

from .config import JarvisConfig
from .logging_setup import get_logger
from .ollama_client import OllamaClient
from .transport import MqttTransport

log = get_logger("SCHED")


class MaintenanceScheduler:
    """Run periodic housekeeping tasks for Jarvis-Core."""

    def __init__(
        self,
        cfg: JarvisConfig,
        bus: MqttTransport,
        ollama: OllamaClient,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._ollama = ollama
        # Track latest phone heartbeat — updated by an MQTT subscription.
        self._last_phone_heartbeat: float = 0.0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

        try:
            self._encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001
            self._encoder = None  # tiktoken bundles encoders; rare to fail

    async def start(self) -> None:
        await self._bus.subscribe(
            self._cfg.mqtt.topics.heartbeat_phone, self._on_phone_heartbeat
        )
        self._task = asyncio.create_task(self._run(), name="maintenance")
        log.info("Maintenance scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ #
    # Heartbeat tracking                                                 #
    # ------------------------------------------------------------------ #

    async def _on_phone_heartbeat(
        self, _topic: str, payload: dict[str, Any]
    ) -> None:
        # Phone publishes ``{"status":"online", "ts": ...}`` periodically and
        # an LWT ``{"status":"offline"}`` on disconnect.
        if payload.get("status") == "online":
            self._last_phone_heartbeat = float(payload.get("ts", time.time()))

    # ------------------------------------------------------------------ #
    # Loop                                                               #
    # ------------------------------------------------------------------ #

    async def _run(self) -> None:
        # Run once immediately on startup so we don't wait an hour to compress.
        while not self._stop.is_set():
            try:
                await self._maintenance_pass()
            except Exception as exc:  # noqa: BLE001
                log.exception(f"Maintenance pass failed: {exc!r}")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._cfg.scheduler.maintenance_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _maintenance_pass(self) -> None:
        log.info("Maintenance pass starting")
        await self._compress_history()
        await self._check_phone_health()
        unloaded = await self._ollama.unload_idle()
        if unloaded:
            log.info(f"Unloaded idle models: {unloaded}")
        log.info("Maintenance pass complete")

    # ------------------------------------------------------------------ #
    # Job 1 — context compression                                        #
    # ------------------------------------------------------------------ #

    async def _compress_history(self) -> None:
        path = Path(self._cfg.brain.history_log)
        if not path.is_file():
            return
        text = path.read_text("utf-8")
        token_count = self._count_tokens(text)
        if token_count < self._cfg.brain.history_token_limit:
            log.debug(f"history.log {token_count} tokens — under limit")
            return

        log.info(
            f"history.log at {token_count} tokens — compressing via planner"
        )
        prompt = (
            "Summarise the following Jarvis-Core history log into concise "
            "bullet points capturing standing facts, recurring user "
            "preferences, and unresolved open items. Drop low-signal noise. "
            "Output Markdown.\n\n"
            f"{text[-12000:]}"  # last ~12 KB is plenty of context
        )
        summary = await self._ollama.generate(
            self._cfg.ollama.planner_model,
            prompt,
            temperature=0.3,
        )

        kg_path = Path(self._cfg.brain.knowledge_graph)
        kg_path.parent.mkdir(parents=True, exist_ok=True)
        with kg_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n\n## Summary @ {time.strftime('%Y-%m-%d %H:%M')}\n\n")
            fh.write(summary.strip() + "\n")

        # Keep last 1000 lines for short-term context, archive the rest.
        lines = text.splitlines()
        keep = lines[-1000:]
        archive = path.with_suffix(f".log.{int(time.time())}")
        archive.write_text(text, encoding="utf-8")
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        log.info(f"Compressed history; archived to {archive.name}")

    def _count_tokens(self, text: str) -> int:
        if self._encoder is None:
            # Fallback heuristic: ~4 chars per token.
            return len(text) // 4
        return len(self._encoder.encode(text))

    # ------------------------------------------------------------------ #
    # Job 2 — phone health                                               #
    # ------------------------------------------------------------------ #

    async def _check_phone_health(self) -> None:
        if self._last_phone_heartbeat == 0.0:
            log.debug("No phone heartbeat seen yet — skipping health check")
            return
        age = time.time() - self._last_phone_heartbeat
        if age < self._cfg.scheduler.phone_offline_after_seconds:
            return
        log.warning(f"Phone offline for {age:.0f}s — raising alert")
        attention = (
            Path(self._cfg.brain.knowledge_graph).parent / "NEED_ATTENTION.md"
        )
        attention.parent.mkdir(parents=True, exist_ok=True)
        with attention.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n\n## Phone offline @ {time.strftime('%Y-%m-%d %H:%M')}\n"
                f"- last heartbeat: {age:.0f}s ago\n"
                "- Tasker watchdog should restart phone_executor.py within 10 min.\n"
            )
        await self._bus.publish(
            self._cfg.mqtt.topics.alerts,
            {
                "level": "warning",
                "code": "PHONE_OFFLINE",
                "age_seconds": int(age),
                "ts": time.time(),
            },
            retain=True,
        )

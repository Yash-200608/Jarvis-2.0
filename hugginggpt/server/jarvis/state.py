"""Cluster-wide state store.

Two layers:
  1. In-memory dict populated from retained MQTT `state` topics (free,
     already consistent — the broker does the work).
  2. Optional persistence: JSON file (simple) or Redis (multi-process).

The brain usually owns the persistent copy; limbs only keep the live
dict so they can introspect other nodes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .schema import State

log = logging.getLogger("jarvis.state")


class NodeStateStore:
    """Single-process state cache, optionally backed by a JSON file or Redis."""

    def __init__(self,
                 persist_path: Optional[str] = None,
                 redis_url: Optional[str] = None):
        self._nodes: dict[str, dict[str, Any]] = {}  # node_id -> {meta, online, ts}
        self._lock = asyncio.Lock()
        self._persist_path = Path(persist_path).expanduser() if persist_path else None
        self._redis = None

        if redis_url:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(redis_url, decode_responses=True)
                log.info("state backend: redis %s", redis_url)
            except ImportError:
                log.warning("redis requested but `redis` package not installed — "
                            "falling back to JSON file")

        if self._persist_path and self._persist_path.exists():
            try:
                self._nodes = json.loads(self._persist_path.read_text())
                log.info("state loaded from %s (%d nodes)",
                         self._persist_path, len(self._nodes))
            except Exception as e:
                log.warning("failed to load state from %s: %s", self._persist_path, e)

    async def update_from_state(self, node_id: str, st: State):
        async with self._lock:
            self._nodes[node_id] = {
                "online": st.online,
                "meta":   st.meta,
                "ts":     st.ts,
            }
            await self._persist()

    async def update_presence(self, node_id: str, online: bool):
        async with self._lock:
            rec = self._nodes.setdefault(node_id,
                                         {"meta": {}, "ts": time.time()})
            rec["online"] = online
            rec["ts"] = time.time()
            await self._persist()

    async def get(self, node_id: str) -> Optional[dict[str, Any]]:
        async with self._lock:
            rec = self._nodes.get(node_id)
            return dict(rec) if rec else None

    async def all(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            return {k: dict(v) for k, v in self._nodes.items()}

    async def remember(self, key: str, value: Any):
        """Free-form scratch memory — last command, last query, etc.
        Stored in redis if available, else written to the JSON file."""
        async with self._lock:
            self._nodes.setdefault("_memory", {})[key] = {
                "value": value, "ts": time.time(),
            }
            await self._persist()

    async def recall(self, key: str) -> Any:
        async with self._lock:
            return self._nodes.get("_memory", {}).get(key, {}).get("value")

    async def _persist(self):
        if self._redis is not None:
            try:
                await self._redis.set("jarvis:state",
                                      json.dumps(self._nodes))
                return
            except Exception as e:
                log.warning("redis persist failed: %s", e)
        if self._persist_path is not None:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
                self._persist_path.write_text(json.dumps(self._nodes, indent=2))
            except Exception as e:
                log.warning("json persist failed: %s", e)

"""ChromaDB-backed long-term RAG memory.

Each entry is an atomic observation (a sensor reading, a completed plan, a
summarisation output) keyed by UUID. The collection is persistent — living
on disk under ``brain/chroma/`` — so Jarvis-Core remembers across restarts.

Embeddings come from the local Ollama ``nomic-embed-text`` model so nothing
leaves the machine.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from .config import JarvisConfig
from .logging_setup import get_logger

log = get_logger("MEMORY")


class MemoryStore:
    """Thin wrapper around a persistent Chroma collection."""

    def __init__(self, cfg: JarvisConfig) -> None:
        self._cfg = cfg
        # chromadb is heavy — import lazily so Termux agents that don't
        # need it never have to install it.
        import chromadb  # noqa: WPS433 — deliberate lazy import
        from chromadb.config import Settings

        self._client = chromadb.PersistentClient(
            path=cfg.memory.chroma_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._col = self._client.get_or_create_collection(cfg.memory.collection)
        self._http = httpx.AsyncClient(
            base_url=cfg.ollama.base_url,
            timeout=httpx.Timeout(cfg.ollama.request_timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ #
    # Embedding                                                          #
    # ------------------------------------------------------------------ #

    async def _embed(self, text: str) -> list[float]:
        resp = await self._http.post(
            "/api/embeddings",
            json={"model": self._cfg.memory.embedding_model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    # ------------------------------------------------------------------ #
    # CRUD                                                               #
    # ------------------------------------------------------------------ #

    async def remember(
        self,
        text: str,
        *,
        kind: str = "observation",
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Persist ``text`` with embedding. Returns the new entry id."""
        vec = await self._embed(text)
        rid = uuid.uuid4().hex
        meta = {"kind": kind, "ts": time.time(), **(extra or {})}
        self._col.add(
            ids=[rid], embeddings=[vec], documents=[text], metadatas=[meta]
        )
        log.debug(f"Stored memory {rid[:8]} ({kind}, {len(text)} chars)")
        return rid

    async def recall(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        """Return the top-k relevant memories for ``query``."""
        vec = await self._embed(query)
        res = self._col.query(query_embeddings=[vec], n_results=k)
        hits: list[dict[str, Any]] = []
        # Chroma returns lists-of-lists keyed by query index; we only send one.
        for doc, meta, rid, dist in zip(
            res["documents"][0],
            res["metadatas"][0],
            res["ids"][0],
            res["distances"][0],
            strict=False,
        ):
            hits.append(
                {"id": rid, "text": doc, "meta": meta, "distance": dist}
            )
        return hits

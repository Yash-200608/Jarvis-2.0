"""Async Ollama HTTP client with idle-aware VRAM management.

Wraps the local Ollama daemon's ``/api/generate`` and ``/api/chat`` endpoints.
Tracks the timestamp of the last request per model so the scheduler can
unload anything that's been idle past the configured threshold by issuing
a ``keep_alive: 0`` call — exactly as the spec requires.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import OllamaConfig
from .logging_setup import get_logger

log = get_logger("BRAIN")


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    """Lightweight async wrapper around the local Ollama daemon."""

    def __init__(self, cfg: OllamaConfig) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(cfg.request_timeout_seconds),
        )
        # model -> last activity timestamp (monotonic seconds).
        self._last_used: dict[str, float] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Generation                                                         #
    # ------------------------------------------------------------------ #

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        format_: str | None = None,
        temperature: float = 0.2,
        keep_alive: str | int | None = None,
    ) -> str:
        """Single-shot generation. Returns the raw response text."""
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            body["system"] = system
        if format_:
            body["format"] = format_
        if keep_alive is not None:
            body["keep_alive"] = keep_alive

        try:
            resp = await self._client.post("/api/generate", json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama generate failed: {exc!r}") from exc

        self._last_used[model] = time.monotonic()
        data = resp.json()
        return data.get("response", "")

    async def generate_json(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Like :meth:`generate` but enforces JSON mode + parses the result."""
        text = await self.generate(
            model,
            prompt,
            system=system,
            format_="json",
            temperature=temperature,
        )
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Try to salvage by extracting the first {...} block — Llama 3
            # occasionally wraps JSON in prose despite format=json.
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            raise OllamaError(
                f"Could not parse JSON from {model}: {text[:200]!r}"
            ) from exc

    # ------------------------------------------------------------------ #
    # VRAM management                                                    #
    # ------------------------------------------------------------------ #

    async def unload_idle(self) -> list[str]:
        """Unload any model idle longer than the configured threshold.

        Returns the list of models that were unloaded — useful for log lines.
        """
        unloaded: list[str] = []
        cutoff = self._cfg.idle_unload_seconds
        now = time.monotonic()
        for model, last in list(self._last_used.items()):
            if (now - last) >= cutoff:
                try:
                    await self._client.post(
                        "/api/generate",
                        json={"model": model, "keep_alive": 0, "prompt": ""},
                    )
                    unloaded.append(model)
                    self._last_used.pop(model, None)
                    log.info(f"Unloaded idle model {model}")
                except httpx.HTTPError as exc:
                    log.warning(f"Failed to unload {model}: {exc!r}")
        return unloaded

    def mark_used(self, model: str) -> None:
        self._last_used[model] = time.monotonic()

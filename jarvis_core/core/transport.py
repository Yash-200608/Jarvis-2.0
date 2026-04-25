"""Robust async MQTT wrapper for Jarvis-Core.

Features
--------
* Uses ``aiomqtt`` over a Tailscale-internal broker (100.x.x.x).
* Persistent session — ``clean_session=False`` — so messages queued while the
  PC is asleep are delivered on reconnect.
* Exponential-backoff auto-reconnect supervised by an outer task.
* Helpers for retained messages, QoS-1 publish, and subscription fan-out.
* Correlation layer: :meth:`MqttTransport.request` attaches a ``request_id``
  to the payload, suspends the caller until the matching response arrives on
  the specified reply topic, and returns the payload — turning MQTT's fire-
  and-forget model into an awaitable RPC.

Topic layout mirrors :class:`core.config.MqttTopics`::

    jarvis/phone/cmd   -- PC -> phone (QoS 1)
    jarvis/phone/state -- phone -> PC (retained LWT + live state)
    jarvis/pc/cmd      -- phone -> PC
    jarvis/pc/state    -- PC -> phone
    jarvis/heartbeat/* -- periodic liveness pings
    jarvis/alerts      -- human-facing alerts
"""

from __future__ import annotations

import asyncio
import json
import ssl
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

import aiomqtt

from .config import MqttConfig
from .logging_setup import get_logger

log = get_logger("TRANSPORT")

# Type aliases keep method signatures readable.
JsonDict = dict[str, Any]
Handler = Callable[[str, JsonDict], Awaitable[None]]


@dataclass
class PendingRequest:
    """Book-keeping entry for an in-flight correlated request."""

    future: asyncio.Future[JsonDict]
    sent_at: float = field(default_factory=time.monotonic)


class MqttTransport:
    """Async MQTT client with reconnect + request/response correlation.

    Usage
    -----
    ::

        async with MqttTransport(cfg.mqtt) as bus:
            await bus.subscribe("jarvis/phone/state", on_phone_state)
            reply = await bus.request(
                topic="jarvis/phone/cmd",
                payload={"action": "get_battery"},
                reply_topic="jarvis/phone/state",
                timeout=15.0,
            )
    """

    def __init__(
        self,
        cfg: MqttConfig,
        *,
        will_topic: str | None = None,
        will_payload: JsonDict | None = None,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self._cfg = cfg
        self._tls = tls
        self._will_topic = will_topic
        self._will_payload = will_payload

        self._client: aiomqtt.Client | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._stopping = asyncio.Event()

        # topic -> list of handlers
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        # request_id -> PendingRequest
        self._pending: dict[str, PendingRequest] = {}

    # ------------------------------------------------------------------ #
    # Async context manager                                              #
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> "MqttTransport":
        self._supervisor = asyncio.create_task(
            self._supervise(), name="mqtt-supervisor"
        )
        # Wait for the first successful CONNACK before handing control back.
        await self._connected.wait()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._stopping.set()
        if self._supervisor:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):
                pass
        # Cancel any awaiting RPC futures so callers don't hang forever.
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()

    # ------------------------------------------------------------------ #
    # Supervised connect loop                                            #
    # ------------------------------------------------------------------ #

    async def _supervise(self) -> None:
        """Keep a client connected forever with exponential backoff."""
        backoff = 1.0
        max_backoff = 60.0
        will = None
        if self._will_topic and self._will_payload is not None:
            will = aiomqtt.Will(
                topic=self._will_topic,
                payload=json.dumps(self._will_payload).encode(),
                qos=self._cfg.qos,
                retain=True,
            )

        while not self._stopping.is_set():
            try:
                log.info(
                    f"Connecting to broker {self._cfg.host}:{self._cfg.port} "
                    f"as {self._cfg.client_id!r}"
                )
                async with aiomqtt.Client(
                    hostname=self._cfg.host,
                    port=self._cfg.port,
                    identifier=self._cfg.client_id,
                    username=self._cfg.username,
                    password=self._cfg.password,
                    keepalive=self._cfg.keepalive,
                    clean_session=self._cfg.clean_session,
                    will=will,
                    tls_context=self._tls,
                ) as client:
                    self._client = client
                    self._connected.set()
                    backoff = 1.0  # reset on successful connect
                    # Re-subscribe to every topic we had before the drop.
                    for topic in list(self._handlers.keys()):
                        await client.subscribe(topic, qos=self._cfg.qos)
                        log.debug(f"Re-subscribed to {topic}")
                    await self._dispatch_loop(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — we want every failure
                log.warning(f"MQTT disconnected: {exc!r} — retry in {backoff:.1f}s")
            finally:
                self._client = None
                self._connected.clear()

            if self._stopping.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=backoff
                )
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, max_backoff)

    async def _dispatch_loop(self, client: aiomqtt.Client) -> None:
        """Fan incoming messages out to handlers + resolve RPC futures."""
        async for msg in client.messages:
            topic = str(msg.topic)
            try:
                payload: JsonDict = json.loads(msg.payload.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                log.warning(f"Non-JSON payload on {topic!r} — dropping")
                continue

            # 1) Correlate response to a pending request.
            rid = payload.get("request_id")
            if rid and rid in self._pending:
                pending = self._pending.pop(rid)
                if not pending.future.done():
                    pending.future.set_result(payload)

            # 2) Fire user handlers.
            for handler in self._handlers.get(topic, []):
                try:
                    await handler(topic, payload)
                except Exception as exc:  # noqa: BLE001
                    log.exception(f"Handler for {topic} raised: {exc!r}")

            # 3) Wildcard handlers (jarvis/sensors/# etc.) — match by prefix.
            for pattern, handlers in self._handlers.items():
                if pattern.endswith("#") and topic.startswith(pattern[:-1]):
                    for handler in handlers:
                        try:
                            await handler(topic, payload)
                        except Exception as exc:  # noqa: BLE001
                            log.exception(
                                f"Wildcard handler for {pattern} raised: {exc!r}"
                            )

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    async def wait_connected(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def subscribe(self, topic: str, handler: Handler) -> None:
        """Register a handler and subscribe (or queue) to the topic."""
        self._handlers[topic].append(handler)
        if self._client is not None:
            await self._client.subscribe(topic, qos=self._cfg.qos)
            log.info(f"Subscribed to {topic}")
        else:
            log.debug(f"Queued subscription to {topic} (not connected yet)")

    async def publish(
        self,
        topic: str,
        payload: JsonDict,
        *,
        qos: int | None = None,
        retain: bool = False,
    ) -> None:
        """Publish a JSON payload. Blocks briefly until reconnected."""
        if self._client is None:
            await self.wait_connected(timeout=10.0)
        assert self._client is not None  # for type checkers
        data = json.dumps(payload, separators=(",", ":")).encode()
        await self._client.publish(
            topic, data, qos=qos if qos is not None else self._cfg.qos, retain=retain
        )
        log.debug(f"PUB {topic} ({len(data)} B, retain={retain})")

    async def request(
        self,
        topic: str,
        payload: JsonDict,
        reply_topic: str,
        *,
        timeout: float = 30.0,
    ) -> JsonDict:
        """Publish a command and await the correlated response.

        The wrapper attaches ``request_id`` to the payload; the responder is
        expected to echo the same ``request_id`` in its reply so the transport
        can route it back to this coroutine. Duplicate responses — possible
        under QoS 1 — are silently dropped.
        """
        request_id = payload.get("request_id") or str(uuid.uuid4())
        payload = {**payload, "request_id": request_id}

        loop = asyncio.get_running_loop()
        future: asyncio.Future[JsonDict] = loop.create_future()
        self._pending[request_id] = PendingRequest(future=future)

        # Make sure the reply topic has a subscription in place.
        if reply_topic not in self._handlers:
            await self.subscribe(reply_topic, self._noop_handler)

        try:
            await self.publish(topic, payload)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(f"RPC timeout on {topic!r} rid={request_id}")
            raise
        finally:
            self._pending.pop(request_id, None)

    async def iter_messages(self, topic: str) -> AsyncIterator[tuple[str, JsonDict]]:
        """Async iterator for power-users who want a queue instead of handlers."""
        queue: asyncio.Queue[tuple[str, JsonDict]] = asyncio.Queue()

        async def _on(t: str, p: JsonDict) -> None:
            await queue.put((t, p))

        await self.subscribe(topic, _on)
        while True:
            yield await queue.get()

    @staticmethod
    async def _noop_handler(_topic: str, _payload: JsonDict) -> None:
        return None

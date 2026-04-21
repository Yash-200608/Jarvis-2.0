"""MQTTRouter — the heart of the JARVIS nervous system.

Runs on every node (brain + limbs). Handles:
  • connect / reconnect with LWT for presence
  • subscribe to `cmd` topic; dispatch to SkillRegistry; publish response
  • send commands to other nodes and await response by correlation_id
  • periodic heartbeat + state publish
  • retained skill catalog publish on connect

Transport: aiomqtt (asyncio wrapper around paho-mqtt).
Install:   pip install aiomqtt
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable, Optional

import aiomqtt
from aiomqtt import Will, ProtocolVersion

from .schema import Command, Event, Request, Response, State, T
from .skills import SkillRegistry

log = logging.getLogger("jarvis.router")

HeartbeatProvider = Callable[[], Awaitable[dict[str, Any]]]
EventHandler     = Callable[[Event], Awaitable[None]]
RequestHandler   = Callable[[Request], Awaitable[None]]
StateHandler     = Callable[[str, State], Awaitable[None]]   # (node_id, state)


class MQTTRouter:
    """One instance per process. Use `async with router:` or `await router.run()`."""

    def __init__(self,
                 node_id: str,
                 broker: str,
                 port: int = 1883,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 tls: bool = False,
                 ca_cert: Optional[str] = None,
                 heartbeat_secs: float = 20.0,
                 keepalive: int = 60):
        self.node_id = node_id
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.ca_cert = ca_cert
        self.heartbeat_secs = heartbeat_secs
        self.keepalive = keepalive

        self.skills = SkillRegistry()

        # inbound handlers (all optional)
        self._heartbeat_provider: Optional[HeartbeatProvider] = None
        self._event_handler:     Optional[EventHandler]     = None
        self._request_handler:   Optional[RequestHandler]   = None
        self._state_handler:     Optional[StateHandler]     = None
        self._presence_handler:  Optional[Callable[[str, bool], Awaitable[None]]] = None

        # outstanding outbound commands we're awaiting responses for
        self._pending: dict[str, asyncio.Future] = {}

        self._client: Optional[aiomqtt.Client] = None
        self._stop = asyncio.Event()

    # ── configuration hooks ────────────────────────────────────────────
    def on_heartbeat(self, fn: HeartbeatProvider):
        """Returns a dict describing current node state; published as State."""
        self._heartbeat_provider = fn
        return fn

    def on_event(self, fn: EventHandler):         self._event_handler = fn;   return fn
    def on_request(self, fn: RequestHandler):     self._request_handler = fn; return fn
    def on_state(self, fn: StateHandler):         self._state_handler = fn;   return fn
    def on_presence(self, fn):                    self._presence_handler = fn;return fn

    # ── public API ─────────────────────────────────────────────────────
    async def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        if self._client is None:
            raise RuntimeError("router not connected")
        await self._client.publish(topic, payload=payload, qos=qos, retain=retain)

    async def emit_event(self, kind: str, data: dict[str, Any] | None = None):
        evt = Event(kind=kind, data=data or {}, from_=self.node_id)
        await self.publish(T.event(self.node_id), evt.to_json(), qos=1)

    async def issue_command(self,
                            to: str,
                            skill: str,
                            params: dict[str, Any] | None = None,
                            timeout: float = 20.0) -> Response:
        """Brain-side helper. Publishes a Command and awaits the matching
        Response (correlated by id)."""
        cmd = Command(skill=skill, params=params or {},
                      to=to, from_=self.node_id, ttl=timeout)
        fut = asyncio.get_event_loop().create_future()
        self._pending[cmd.id] = fut
        try:
            await self.publish(T.cmd(to), cmd.to_json(), qos=1)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return Response(id=cmd.id, ok=False,
                            error=f"node '{to}' did not respond within {timeout}s",
                            from_=self.node_id)
        finally:
            self._pending.pop(cmd.id, None)

    async def send_request(self, text: str):
        """Limb-side helper: 'hey brain, plan this for me'."""
        req = Request(text=text, from_=self.node_id)
        await self.publish(T.BROADCAST_REQUEST, req.to_json(), qos=1)

    # ── lifecycle ──────────────────────────────────────────────────────
    async def run(self):
        """Main loop with auto-reconnect. Call this from the entry point."""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except aiomqtt.MqttError as e:
                log.warning("mqtt dropped: %s — reconnecting in %.1fs", e, backoff)
            except Exception as e:
                log.exception("router error: %s — reconnecting in %.1fs", e, backoff)
            if self._stop.is_set():
                break
            await asyncio.sleep(min(30.0, backoff))
            backoff = min(60.0, backoff * 2)

    async def stop(self):
        self._stop.set()

    # ── internals ──────────────────────────────────────────────────────
    async def _run_once(self):
        lwt_payload = json.dumps({"online": False, "ts": time.time(),
                                  "node_id": self.node_id})
        tls_params = None
        if self.tls:
            ctx = ssl.create_default_context(cafile=self.ca_cert) if self.ca_cert \
                  else ssl.create_default_context()
            tls_params = aiomqtt.TLSParameters(ca_certs=self.ca_cert) if self.ca_cert else None

        async with aiomqtt.Client(
            hostname=self.broker,
            port=self.port,
            username=self.username,
            password=self.password,
            identifier=self.node_id,
            protocol=ProtocolVersion.V5,
            keepalive=self.keepalive,
            tls_params=tls_params,
            will=Will(
                topic=T.presence(self.node_id),
                payload=lwt_payload,
                qos=1,
                retain=True,
            ),
        ) as client:
            self._client = client
            log.info("connected to mqtt://%s:%d as '%s'",
                     self.broker, self.port, self.node_id)

            await self._on_connect()
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            try:
                async for msg in client.messages:
                    await self._dispatch(msg)
            finally:
                heartbeat_task.cancel()
                # Clean goodbye (so other subscribers see us offline
                # even if the broker doesn't fire LWT in time).
                try:
                    await client.publish(
                        T.presence(self.node_id),
                        json.dumps({"online": False, "ts": time.time(),
                                    "node_id": self.node_id}),
                        qos=1, retain=True,
                    )
                except Exception:
                    pass
                self._client = None

    async def _on_connect(self):
        client = self._client
        # Subscribe to our own cmd topic.
        await client.subscribe(T.cmd(self.node_id), qos=1)
        # Subscribe to presence of everyone (so we can react to nodes coming
        # online/offline — brain uses this, limbs mostly ignore).
        await client.subscribe(T.ALL_PRESENCE, qos=1)
        # Subscribe to state updates from all nodes.
        await client.subscribe(T.ALL_STATE, qos=1)
        # Brain-only topics — harmless for limbs to also subscribe.
        await client.subscribe(T.ALL_RESPONSE, qos=1)
        await client.subscribe(T.ALL_EVENT, qos=1)
        await client.subscribe(T.BROADCAST_REQUEST, qos=1)
        await client.subscribe(T.ALL_CATALOG, qos=1)

        # Publish our presence + skill catalog as retained messages.
        await client.publish(T.presence(self.node_id),
                             json.dumps({"online": True, "ts": time.time(),
                                         "node_id": self.node_id}),
                             qos=1, retain=True)
        await client.publish(T.catalog(self.node_id),
                             json.dumps(self.skills.catalog()),
                             qos=1, retain=True)

    async def _heartbeat_loop(self):
        try:
            while True:
                await asyncio.sleep(self.heartbeat_secs)
                if self._client is None:
                    continue
                try:
                    meta = {}
                    if self._heartbeat_provider is not None:
                        meta = await self._heartbeat_provider()
                    state = State(node_id=self.node_id, online=True, meta=meta)
                    # state is retained so late-joiners get it
                    await self._client.publish(T.state(self.node_id),
                                               state.to_json(),
                                               qos=1, retain=True)
                    # heartbeat is transient
                    await self._client.publish(T.heartbeat(self.node_id),
                                               json.dumps({"ts": time.time(),
                                                           "node_id": self.node_id}),
                                               qos=0, retain=False)
                except Exception as e:
                    log.warning("heartbeat publish failed: %s", e)
        except asyncio.CancelledError:
            pass

    async def _dispatch(self, msg):
        topic = str(msg.topic)
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            return

        # Commands directed at us.
        if topic == T.cmd(self.node_id):
            await self._handle_cmd(payload)
            return

        # Responses to commands we issued.
        if topic.startswith("jarvis/node/") and topic.endswith("/response"):
            try:
                resp = Response.from_json(payload)
            except Exception:
                return
            fut = self._pending.get(resp.id)
            if fut is not None and not fut.done():
                fut.set_result(resp)
            return

        # Presence updates from other nodes.
        if topic.startswith("jarvis/node/") and topic.endswith("/presence"):
            try:
                d = json.loads(payload)
            except Exception:
                return
            if self._presence_handler is not None:
                try:
                    await self._presence_handler(d.get("node_id", ""),
                                                 bool(d.get("online", False)))
                except Exception:
                    log.exception("presence handler crashed")
            return

        # State updates.
        if topic.startswith("jarvis/node/") and topic.endswith("/state"):
            try:
                st = State.from_json(payload)
            except Exception:
                return
            if self._state_handler is not None:
                try:
                    await self._state_handler(st.node_id, st)
                except Exception:
                    log.exception("state handler crashed")
            return

        # Events.
        if topic.startswith("jarvis/node/") and topic.endswith("/event"):
            try:
                evt = Event.from_json(payload)
            except Exception:
                return
            if self._event_handler is not None:
                try:
                    await self._event_handler(evt)
                except Exception:
                    log.exception("event handler crashed")
            return

        # Broadcast user requests (brain consumes).
        if topic == T.BROADCAST_REQUEST:
            try:
                req = Request.from_json(payload)
            except Exception:
                return
            if self._request_handler is not None:
                try:
                    await self._request_handler(req)
                except Exception:
                    log.exception("request handler crashed")
            return

    async def _handle_cmd(self, payload: str):
        try:
            cmd = Command.from_json(payload)
        except Exception as e:
            log.warning("bad cmd payload: %s", e)
            return
        log.info("cmd %s skill=%s from=%s", cmd.id[:8], cmd.skill, cmd.from_)

        skill = self.skills.get(cmd.skill)
        if skill is None:
            resp = Response(id=cmd.id, ok=False,
                            error=f"unknown skill: {cmd.skill}",
                            from_=self.node_id)
        else:
            try:
                result = await skill.handler(self, cmd.params or {})
                if isinstance(result, dict):
                    ok = bool(result.get("ok", True))
                    err = result.get("error")
                    resp = Response(id=cmd.id, ok=ok, result=result,
                                    error=err, from_=self.node_id)
                else:
                    resp = Response(id=cmd.id, ok=True, result=result,
                                    from_=self.node_id)
            except Exception as e:
                log.exception("skill '%s' crashed", cmd.skill)
                resp = Response(id=cmd.id, ok=False,
                                error=f"{type(e).__name__}: {e}",
                                from_=self.node_id)

        await self.publish(T.response(self.node_id), resp.to_json(), qos=1)

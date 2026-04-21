"""
JARVIS relay_server
====================
A tiny WebSocket rendezvous so the PC (controller) and the phone (agent)
can exchange JSON commands even when they are on different networks and
behind NATs.

ARCHITECTURE
------------
    ┌──────────────┐   WSS     ┌────────────────┐    WSS   ┌─────────────┐
    │ PC           │<──────────│  relay_server  │──────────>│ phone      │
    │ phone_relay_ │ controller│  (this file)   │  agent    │ phone_agent│
    │ bridge.py    │           │   port 8093    │           │ (Termux)   │
    └──────────────┘           └────────────────┘           └─────────────┘

DEPLOYMENT OPTIONS
------------------
1. Run on the PC itself (simplest for same-LAN or port-forwarded setups).
2. Run on a free-tier cloud (Fly.io, Railway, Render) for "anywhere"
   connectivity without touching your router.
3. Self-host on any VPS that can accept inbound WebSocket connections.

PROTOCOL (JSON over WS, UTF-8)
------------------------------
Client → Server, as the very first message:
    {"type": "hello", "role": "agent"|"controller",
     "agent_id": "ian-pixel-001", "token": "<shared-secret>"}
Server → Client:
    {"type": "hello_ok"}                       on success
    {"type": "err", "error": "..."}            on failure (then closed)

Controller → Server:
    {"type": "cmd", "req_id": "<uuid>",
     "agent_id": "<target>", "action": "...", "params": {...}}

Server relays the frame to the named agent verbatim (minus the token).

Agent → Server:
    {"type": "res", "req_id": "<uuid>", "result": {...}}
    {"type": "err", "req_id": "<uuid>", "error": "..."}

Server routes the response back to whichever controller issued that req_id.

Keepalive:
    {"type": "ping"}   ↔   {"type": "pong"}

USAGE
-----
    python relay_server.py --port 8093
    RELAY_TOKEN=<secret> python relay_server.py

EnvVars: RELAY_TOKEN (required), RELAY_HOST (default 0.0.0.0),
         RELAY_PORT (default 8093), RELAY_MAX_SIZE (default 8MB)
"""

import argparse
import asyncio
import json
import logging
import os
import time
from collections import defaultdict

import websockets
from websockets.exceptions import ConnectionClosed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [relay] %(levelname)s %(message)s",
)
log = logging.getLogger("relay")


class Relay:
    """In-memory hub. One process, no external dependencies."""

    def __init__(self, token: str):
        self.token = token
        # agent_id -> websocket
        self.agents: dict[str, "websockets.WebSocketServerProtocol"] = {}
        # req_id -> controller_websocket (so responses route back to the
        # controller that issued them, not broadcast to all controllers)
        self.req_routes: dict[str, "websockets.WebSocketServerProtocol"] = {}
        # agent_id -> list of req_ids (so we can GC when an agent disconnects)
        self.agent_reqs: dict[str, set] = defaultdict(set)
        # controller_websocket -> set of req_ids (GC when controller drops)
        self.ctrl_reqs: dict["websockets.WebSocketServerProtocol", set] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def _send(self, ws, obj: dict):
        try:
            await ws.send(json.dumps(obj))
        except ConnectionClosed:
            pass
        except Exception as e:
            log.warning("send failed: %s", e)

    async def register_agent(self, agent_id: str, ws) -> bool:
        async with self._lock:
            existing = self.agents.get(agent_id)
            if existing is not None and existing is not ws:
                # Kick the stale connection — the new one wins.
                try:
                    await existing.close(code=4001, reason="superseded")
                except Exception:
                    pass
            self.agents[agent_id] = ws
        log.info("agent registered: %s", agent_id)
        return True

    async def unregister_agent(self, agent_id: str, ws):
        async with self._lock:
            if self.agents.get(agent_id) is ws:
                del self.agents[agent_id]
            # fail any in-flight requests for this agent
            dead_reqs = list(self.agent_reqs.get(agent_id, set()))
            for rid in dead_reqs:
                ctrl = self.req_routes.pop(rid, None)
                self.agent_reqs[agent_id].discard(rid)
                if ctrl is not None:
                    await self._send(ctrl, {
                        "type": "err",
                        "req_id": rid,
                        "error": f"agent '{agent_id}' disconnected",
                    })
                    self.ctrl_reqs[ctrl].discard(rid)
            self.agent_reqs.pop(agent_id, None)
        log.info("agent unregistered: %s", agent_id)

    async def handle_controller_cmd(self, ctrl_ws, msg: dict):
        req_id = msg.get("req_id")
        agent_id = msg.get("agent_id")
        if not req_id or not agent_id:
            await self._send(ctrl_ws, {
                "type": "err", "req_id": req_id or "",
                "error": "missing req_id or agent_id",
            })
            return

        async with self._lock:
            agent_ws = self.agents.get(agent_id)
            if agent_ws is None:
                await self._send(ctrl_ws, {
                    "type": "err", "req_id": req_id,
                    "error": f"agent '{agent_id}' not connected",
                })
                return
            # record route
            self.req_routes[req_id] = ctrl_ws
            self.agent_reqs[agent_id].add(req_id)
            self.ctrl_reqs[ctrl_ws].add(req_id)

        # Strip anything server-only (token) before forwarding. Pass the
        # rest of the message (req_id, agent_id, action, params) to the
        # agent verbatim.
        forward = {k: v for k, v in msg.items() if k != "token"}
        await self._send(agent_ws, forward)

    async def handle_agent_response(self, agent_ws, agent_id: str, msg: dict):
        req_id = msg.get("req_id")
        if not req_id:
            return
        async with self._lock:
            ctrl = self.req_routes.pop(req_id, None)
            self.agent_reqs[agent_id].discard(req_id)
            if ctrl is not None:
                self.ctrl_reqs[ctrl].discard(req_id)
        if ctrl is None:
            return  # controller already gone — drop
        await self._send(ctrl, msg)

    async def drop_controller(self, ctrl_ws):
        async with self._lock:
            reqs = list(self.ctrl_reqs.pop(ctrl_ws, set()))
            for rid in reqs:
                self.req_routes.pop(rid, None)
                # also cleanup the agent-side record
                for aid, rset in self.agent_reqs.items():
                    rset.discard(rid)

    async def handler(self, ws):
        # websockets>=11 removed the path arg; fine.
        remote = ws.remote_address if hasattr(ws, "remote_address") else "?"
        try:
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        except (asyncio.TimeoutError, ConnectionClosed):
            log.info("no hello from %s — closing", remote)
            await ws.close(code=4000, reason="hello timeout")
            return

        try:
            hello = json.loads(hello_raw)
        except json.JSONDecodeError:
            await self._send(ws, {"type": "err", "error": "invalid JSON in hello"})
            await ws.close(code=4000, reason="bad hello")
            return

        if hello.get("type") != "hello" or hello.get("token") != self.token:
            await self._send(ws, {"type": "err", "error": "auth failed"})
            await ws.close(code=4001, reason="auth")
            return

        role = hello.get("role")
        agent_id = hello.get("agent_id", "").strip()

        if role not in ("agent", "controller"):
            await self._send(ws, {"type": "err", "error": "invalid role"})
            await ws.close(code=4002, reason="role")
            return
        if not agent_id:
            await self._send(ws, {"type": "err", "error": "agent_id required"})
            await ws.close(code=4002, reason="agent_id")
            return

        await self._send(ws, {"type": "hello_ok", "server_time": int(time.time())})

        if role == "agent":
            await self.register_agent(agent_id, ws)
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    if mtype == "ping":
                        await self._send(ws, {"type": "pong"})
                    elif mtype in ("res", "err"):
                        await self.handle_agent_response(ws, agent_id, msg)
                    # agents can't issue cmds — ignore silently
            except ConnectionClosed:
                pass
            finally:
                await self.unregister_agent(agent_id, ws)
        else:
            # controller
            log.info("controller connected for agent '%s'", agent_id)
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    if mtype == "ping":
                        await self._send(ws, {"type": "pong"})
                    elif mtype == "cmd":
                        # controller may target any agent_id; the default
                        # is what they said in hello
                        msg.setdefault("agent_id", agent_id)
                        await self.handle_controller_cmd(ws, msg)
            except ConnectionClosed:
                pass
            finally:
                await self.drop_controller(ws)
                log.info("controller for '%s' disconnected", agent_id)


async def _amain(host: str, port: int, token: str, max_size: int):
    relay = Relay(token)
    log.info("starting relay on %s:%d (max_size=%d bytes)", host, port, max_size)
    async with websockets.serve(
        relay.handler,
        host,
        port,
        max_size=max_size,
        ping_interval=30,
        ping_timeout=30,
    ):
        await asyncio.Future()  # run forever


def main():
    ap = argparse.ArgumentParser(description="JARVIS relay rendezvous server")
    ap.add_argument("--host", default=os.environ.get("RELAY_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("RELAY_PORT", "8093")))
    ap.add_argument("--token", default=os.environ.get("RELAY_TOKEN", ""),
                    help="shared bearer token — REQUIRED")
    ap.add_argument("--max-size", type=int,
                    default=int(os.environ.get("RELAY_MAX_SIZE", str(8 * 1024 * 1024))),
                    help="max ws frame size in bytes (default 8 MB for screenshots)")
    args = ap.parse_args()

    if not args.token:
        raise SystemExit(
            "error: --token (or RELAY_TOKEN env var) is required. "
            "Pick a long random string and configure both the PC bridge "
            "and the phone agent with the same value."
        )

    try:
        asyncio.run(_amain(args.host, args.port, args.token, args.max_size))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()

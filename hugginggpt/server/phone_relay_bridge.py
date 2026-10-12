"""
JARVIS phone_relay_bridge
==========================
Drop-in replacement for phone_bridge.py that forwards every action to a
phone_agent.py running on the actual Android device via relay_server.py.

Exposes the SAME HTTP API as phone_bridge.py (POST /execute, GET /health
on the same port, 8091 by default) so awesome_chat.py does not need to
change.

    awesome_chat.py  ─ HTTP ─>  this file  ─ WebSocket ─>  relay_server  ─ WebSocket ─>  phone_agent

USAGE
-----
    python phone_relay_bridge.py \
        --port 8091 \
        --relay-url wss://your-relay-host:8093 \
        --relay-token <shared-secret> \
        --agent-id ian-pixel-001 \
        --http-token <shared-secret>

EnvVars override: PHONE_HTTP_TOKEN, RELAY_URL, RELAY_TOKEN, AGENT_ID,
                  RELAY_REQ_TIMEOUT (seconds, default 45)
"""

import argparse
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Optional

import websockets
from flask import Flask, jsonify, request
from websockets.exceptions import ConnectionClosed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [phone-relay] %(levelname)s %(message)s",
)
log = logging.getLogger("phone-relay")


# ──────────────────────────────────────────────────────────────────────────
#  RelayClient: persistent outbound WebSocket to relay_server.py
#  Runs in its own asyncio event loop on a background thread.
# ──────────────────────────────────────────────────────────────────────────
class RelayClient:
    def __init__(self, relay_url: str, relay_token: str, agent_id: str,
                 req_timeout: float = 45.0):
        self.relay_url = relay_url
        self.relay_token = relay_token
        self.agent_id = agent_id
        self.req_timeout = req_timeout

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._connected_event: Optional[asyncio.Event] = None
        self._stop = False

    # ── public API (thread-safe, called from Flask request threads) ─────
    def start_in_thread(self):
        """Spin up asyncio loop in a daemon thread."""
        t = threading.Thread(target=self._thread_main, daemon=True)
        t.start()
        # Wait up to 10s for first connection — not fatal if it's down,
        # we'll keep retrying and requests will just fail-fast.
        for _ in range(100):
            if self._loop is not None:
                break
            time.sleep(0.1)

    def send_command(self, action: str, params: dict) -> dict:
        """Blocking send. Called from Flask handler thread.
        Returns the JSON result dict from the agent, or raises RuntimeError.
        """
        if self._loop is None:
            raise RuntimeError("relay client not started")

        req_id = uuid.uuid4().hex
        fut = asyncio.run_coroutine_threadsafe(
            self._send_and_wait(req_id, action, params),
            self._loop,
        )
        try:
            return fut.result(timeout=self.req_timeout + 5)
        except Exception as e:
            # cancel the coroutine if we gave up first
            try:
                fut.cancel()
            except Exception:
                pass
            raise

    # ── internals (all on the async loop thread) ────────────────────────
    def _thread_main(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._connected_event = asyncio.Event()
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self):
        backoff = 1.0
        while not self._stop:
            try:
                log.info("connecting to relay %s", self.relay_url)
                async with websockets.connect(
                    self.relay_url,
                    max_size=8 * 1024 * 1024,
                    ping_interval=30,
                    ping_timeout=30,
                ) as ws:
                    self._ws = ws
                    # hello
                    await ws.send(json.dumps({
                        "type": "hello",
                        "role": "controller",
                        "agent_id": self.agent_id,
                        "token": self.relay_token,
                    }))
                    hello_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                    try:
                        hello_obj = json.loads(hello_resp)
                    except json.JSONDecodeError:
                        hello_obj = {}
                    if hello_obj.get("type") != "hello_ok":
                        log.error("relay rejected hello: %s", hello_obj)
                        await asyncio.sleep(min(60.0, backoff))
                        backoff = min(60.0, backoff * 2)
                        continue

                    log.info("connected to relay, forwarding for agent '%s'",
                             self.agent_id)
                    backoff = 1.0
                    self._connected_event.set()

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") in ("res", "err"):
                            rid = msg.get("req_id")
                            fut = self._pending.pop(rid, None)
                            if fut and not fut.done():
                                fut.set_result(msg)
                        elif msg.get("type") == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
            except Exception as e:
                log.warning("relay connection dropped: %s", e)
            finally:
                self._ws = None
                self._connected_event.clear()
                # Fail any in-flight requests so callers don't hang forever.
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_result({
                            "type": "err",
                            "error": "relay disconnected",
                        })
                self._pending.clear()

            if self._stop:
                break
            await asyncio.sleep(min(30.0, backoff))
            backoff = min(60.0, backoff * 2)

    async def _send_and_wait(self, req_id: str, action: str, params: dict) -> dict:
        # Wait briefly if we're between reconnects.
        if self._ws is None:
            try:
                await asyncio.wait_for(self._connected_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                return {"type": "err", "req_id": req_id,
                        "error": "relay not connected"}
        if self._ws is None:
            return {"type": "err", "req_id": req_id,
                    "error": "relay not connected"}

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._ws.send(json.dumps({
                "type": "cmd",
                "req_id": req_id,
                "agent_id": self.agent_id,
                "action": action,
                "params": params,
            }))
        except Exception as e:
            self._pending.pop(req_id, None)
            return {"type": "err", "req_id": req_id,
                    "error": f"send failed: {e}"}

        try:
            msg = await asyncio.wait_for(fut, timeout=self.req_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return {"type": "err", "req_id": req_id,
                    "error": f"agent did not respond within {self.req_timeout}s"}
        return msg


# ──────────────────────────────────────────────────────────────────────────
#  Flask app — mirrors phone_bridge.py's public contract
# ──────────────────────────────────────────────────────────────────────────
def create_app(http_token: str, client: RelayClient):
    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        connected = client._ws is not None
        return jsonify({
            "ok": True,
            "device": "phone",
            "mode": "relay",
            "agent_id": client.agent_id,
            "relay_url": client.relay_url,
            "relay_connected": connected,
        })

    @app.route("/execute", methods=["POST"])
    def execute():
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {http_token}":
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        action = payload.get("action")
        params = payload.get("params") or {}

        if not action:
            return jsonify({"ok": False, "error": "Missing action"}), 400

        try:
            msg = client.send_command(action, params)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 502

        if msg.get("type") == "err":
            return jsonify({
                "ok": False,
                "device": "phone",
                "action": action,
                "params": params,
                "error": msg.get("error", "unknown relay error"),
            }), 502

        # success — pass the agent's result through verbatim, but ensure
        # callers can rely on the same envelope shape as phone_bridge.py.
        result = msg.get("result") or {}
        if not isinstance(result, dict):
            result = {"raw": result}
        result.setdefault("ok", True)
        result.setdefault("device", "phone")
        result.setdefault("action", action)
        result.setdefault("params", params)
        return jsonify(result)

    return app


def main():
    ap = argparse.ArgumentParser(
        description="JARVIS phone relay bridge (PC side, same HTTP API as phone_bridge)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--http-token",
                    default=os.environ.get("PHONE_HTTP_TOKEN",
                                           os.environ.get("BRIDGE_TOKEN", "")),
                    help="bearer token required on /execute")
    ap.add_argument("--relay-url",
                    default=os.environ.get("RELAY_URL", "ws://localhost:8093"))
    ap.add_argument("--relay-token",
                    default=os.environ.get("RELAY_TOKEN", ""))
    ap.add_argument("--agent-id",
                    default=os.environ.get("AGENT_ID", "ian-phone"))
    ap.add_argument("--req-timeout", type=float,
                    default=float(os.environ.get("RELAY_REQ_TIMEOUT", "45")))
    args = ap.parse_args()

    if not args.http_token:
        raise SystemExit(
            "error: --http-token (or PHONE_HTTP_TOKEN / BRIDGE_TOKEN env) required")
    if not args.relay_token:
        raise SystemExit(
            "error: --relay-token (or RELAY_TOKEN env) required")
    if not args.agent_id:
        raise SystemExit("error: --agent-id required")

    client = RelayClient(args.relay_url, args.relay_token, args.agent_id,
                         args.req_timeout)
    client.start_in_thread()

    app = create_app(args.http_token, client)
    log.info("phone_relay_bridge listening on %s:%d (agent_id=%s, relay=%s)",
             args.host, args.port, args.agent_id, args.relay_url)
    # Disable Flask's reloader/debug — this is a bridge, keep it boring.
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False,
            threaded=True)


if __name__ == "__main__":
    main()

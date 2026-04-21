"""JARVIS phone agent — production build.

Runs on the Android phone inside Termux. Survives:
  • network transitions (WiFi ↔ mobile data ↔ no signal)
  • broker reboots (reconnects with exponential backoff)
  • being backgrounded (termux-wake-lock + Tasker watchdog)
  • QoS-1 persisted sessions (Mosquitto replays queued cmds on reconnect)

Architectural commitments:
  • aiomqtt 2.x, MQTT v5, clean_start=False + session_expiry_interval=24h
    → broker queues QoS-1 commands while the phone is offline and replays
      them the instant we reconnect.
  • Fixed `identifier` (client_id) so Mosquitto can find our persisted
    session.
  • LWT on presence topic (retained, QoS 1) flips us to offline within
    1.5 × keepalive if we drop ungracefully.
  • Heartbeat every 20 s publishes battery + uptime to `state` (retained).
  • All skill I/O is non-blocking: asyncio.create_subprocess_exec, no
    blocking subprocess.run in the event loop.
  • Single event-loop process. No worker pools, no background threads.
    Keeps RSS under ~30 MB — well below Android's Phantom Process
    Killer threshold.

Run:
    termux-wake-lock
    python agent_phone.py \
        --broker 100.xx.xx.xx \
        --username jarvis --password <passwd> \
        --node-id ian-phone

Env overrides: JARVIS_BROKER, JARVIS_PORT, JARVIS_USER, JARVIS_PASS,
               JARVIS_NODE_ID, JARVIS_CA_CERT (for TLS).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import ssl
import sys
import time
import urllib.parse
from typing import Any, Awaitable, Callable

import aiomqtt
from aiomqtt import Client, ProtocolVersion, Will
# paho provides the MQTT-v5 Properties we attach to CONNECT.
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

# Shared protocol — keep this file byte-identical on both nodes.
from protocol import (
    HEARTBEAT_SECS, KEEPALIVE_SECS, QOS, SESSION_EXPIRY_SECS,
    Command, Event, Response, T, presence_payload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
)
log = logging.getLogger("agent")

SkillFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# ──────────────────────────────────────────────────────────────────────
#  Non-blocking shell helpers
# ──────────────────────────────────────────────────────────────────────
async def sh(*argv: str, timeout: float = 15.0,
             stdin: bytes | None = None) -> dict[str, Any]:
    """Run `argv` non-blocking. Never raises — always returns a dict."""
    try:
        async with asyncio.timeout(timeout):
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE if stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate(input=stdin)
            return {
                "exit_code": proc.returncode,
                "stdout": out.decode("utf-8", errors="replace"),
                "stderr": err.decode("utf-8", errors="replace"),
            }
    except TimeoutError:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s"}
    except FileNotFoundError as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"not found: {e}"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


async def sh_raw(*argv: str, timeout: float = 15.0) -> tuple[int, bytes, bytes]:
    """Like sh() but returns raw bytes (for binary stdout like screenshots)."""
    try:
        async with asyncio.timeout(timeout):
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            return proc.returncode or 0, out, err
    except Exception as e:
        return -1, b"", str(e).encode()


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


# Detect capabilities once at startup.
HAS_TERMUX = have("termux-sms-send")
HAS_AM     = have("am")
HAS_INPUT  = have("input")


# ──────────────────────────────────────────────────────────────────────
#  Skills
# ──────────────────────────────────────────────────────────────────────
async def skill_battery(params: dict) -> dict:
    if HAS_TERMUX:
        r = await sh("termux-battery-status", timeout=10)
        if r["exit_code"] == 0:
            try:
                info = json.loads(r["stdout"])
                return {
                    "ok": True,
                    "battery": {
                        "level":    info.get("percentage"),
                        "charging": info.get("status", "").upper() == "CHARGING",
                        "health":   info.get("health"),
                        "temp":     info.get("temperature"),
                    },
                }
            except json.JSONDecodeError:
                pass
    # Fallback: dumpsys
    r = await sh("dumpsys", "battery", timeout=10)
    info = {}
    for line in r["stdout"].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower()] = v.strip()
    level = info.get("level")
    return {
        "ok": r["exit_code"] == 0,
        "battery": {
            "level": int(level) if level and level.isdigit() else None,
            "charging": any(info.get(k, "false").lower() == "true"
                            for k in ("ac powered", "usb powered")),
        },
    }


async def skill_tts(params: dict) -> dict:
    text = str(params.get("text", "")).strip()
    if not text:
        return {"ok": False, "error": "missing text"}
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    r = await sh("termux-tts-speak", text, timeout=60)
    return {"ok": r["exit_code"] == 0, **r}


async def skill_sms_send(params: dict) -> dict:
    phone   = str(params.get("phone", "")).strip()
    message = str(params.get("message", "")).strip()
    if not phone or not message:
        return {"ok": False, "error": "phone + message required"}
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    r = await sh("termux-sms-send", "-n", phone, message, timeout=20)
    return {"ok": r["exit_code"] == 0, "phone": phone, **r}


async def skill_torch(params: dict) -> dict:
    on = bool(params.get("on", True))
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    r = await sh("termux-torch", "on" if on else "off", timeout=5)
    return {"ok": r["exit_code"] == 0, "on": on, **r}


async def skill_vibrate(params: dict) -> dict:
    ms = int(params.get("ms", 500))
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    r = await sh("termux-vibrate", "-d", str(ms), timeout=max(10, ms // 1000 + 3))
    return {"ok": r["exit_code"] == 0, "ms": ms, **r}


async def skill_notify(params: dict) -> dict:
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    r = await sh(
        "termux-notification",
        "--id",      str(int(params.get("id", 9000))),
        "--title",   str(params.get("title", "JARVIS")),
        "--content", str(params.get("content", "")),
        timeout=10,
    )
    return {"ok": r["exit_code"] == 0, **r}


async def skill_ring(params: dict) -> dict:
    """Find-my-phone. Flash, vibrate, and loud TTS for N seconds."""
    seconds = max(1, min(60, int(params.get("seconds", 10))))
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    await sh("termux-volume", "music", "15", timeout=5)
    await sh("termux-torch",  "on",          timeout=3)
    # Interleave vibrate + TTS so it's hard to ignore.
    async def _flasher():
        for _ in range(seconds):
            await sh("termux-vibrate", "-d", "400", timeout=3)
            await asyncio.sleep(0.5)
    async def _talker():
        for _ in range(seconds):
            await sh("termux-tts-speak", "-r", "0.9", "-p", "1.3",
                     "Jarvis locator ping", timeout=4)
            await asyncio.sleep(0.2)
    try:
        async with asyncio.timeout(seconds + 5):
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_flasher())
                tg.create_task(_talker())
    finally:
        await sh("termux-torch", "off", timeout=3)
    return {"ok": True, "seconds": seconds}


async def skill_location(params: dict) -> dict:
    if not HAS_TERMUX:
        return {"ok": False, "error": "termux-api not installed"}
    r = await sh("termux-location", "-p", "gps", timeout=30)
    if r["exit_code"] != 0:
        return {"ok": False, **r}
    try:
        return {"ok": True, "location": json.loads(r["stdout"])}
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad json from termux-location", **r}


async def skill_open_app(params: dict) -> dict:
    app = str(params.get("app", "")).strip()
    if not app:
        return {"ok": False, "error": "missing app"}
    PKGS = {
        "whatsapp": "com.whatsapp", "chrome": "com.android.chrome",
        "youtube": "com.google.android.youtube",
        "gmail": "com.google.android.gm", "spotify": "com.spotify.music",
        "instagram": "com.instagram.android", "settings": "com.android.settings",
        "maps": "com.google.android.apps.maps",
    }
    pkg = PKGS.get(app.lower(), app)
    r = await sh("monkey", "-p", pkg, "-c",
                 "android.intent.category.LAUNCHER", "1", timeout=15)
    return {"ok": r["exit_code"] == 0, "package": pkg, **r}


async def skill_whatsapp_send(params: dict) -> dict:
    phone = re.sub(r"[^\d+]", "", str(params.get("phone", "")))
    msg   = str(params.get("message", ""))
    if not phone or not msg:
        return {"ok": False, "error": "phone + message required"}
    url = f"https://api.whatsapp.com/send?phone={phone}&text={urllib.parse.quote(msg)}"
    r = await sh("am", "start", "-a", "android.intent.action.VIEW",
                 "-d", url, timeout=15)
    return {"ok": r["exit_code"] == 0, **r,
            "note": "composer opened — user must tap send"}


# Skill name → handler. Add a row to extend.
SKILLS: dict[str, SkillFn] = {
    "phone.battery":       skill_battery,
    "phone.tts":           skill_tts,
    "phone.sms.send":      skill_sms_send,
    "phone.torch":         skill_torch,
    "phone.vibrate":       skill_vibrate,
    "phone.notify":        skill_notify,
    "phone.ring":          skill_ring,
    "phone.location":      skill_location,
    "phone.app.open":      skill_open_app,
    "phone.whatsapp.send": skill_whatsapp_send,
}


# ──────────────────────────────────────────────────────────────────────
#  Agent
# ──────────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self,
                 broker: str, port: int,
                 username: str | None, password: str | None,
                 node_id: str, ca_cert: str | None):
        self.broker   = broker
        self.port     = port
        self.username = username
        self.password = password
        self.node_id  = node_id
        self.ca_cert  = ca_cert
        self._stop    = asyncio.Event()
        self._client: Client | None = None
        self._boot_ts = time.time()

    def request_stop(self):
        self._stop.set()

    # ── main loop with auto-reconnect ────────────────────────────────
    async def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except aiomqtt.MqttError as e:
                log.warning("mqtt dropped: %s — reconnect in %.1fs", e, backoff)
            except Exception as e:
                log.exception("agent error: %s — reconnect in %.1fs", e, backoff)
            if self._stop.is_set():
                break
            try:
                async with asyncio.timeout(backoff):
                    await self._stop.wait()
                break  # stop was requested during sleep
            except TimeoutError:
                pass
            backoff = min(60.0, backoff * 2)

    async def _run_once(self):
        tls = None
        if self.ca_cert:
            tls = aiomqtt.TLSParameters(ca_certs=self.ca_cert)

        will = Will(
            topic=T.presence(self.node_id),
            payload=presence_payload(self.node_id, online=False),
            qos=QOS,
            retain=True,
        )

        # MQTT-v5 CONNECT properties. session_expiry_interval is THE
        # reason QoS-1 commands queue for us on the broker while we're
        # offline — it has to be set at CONNECT time.
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = SESSION_EXPIRY_SECS

        async with Client(
            hostname=self.broker,
            port=self.port,
            username=self.username,
            password=self.password,
            identifier=self.node_id,            # FIXED client_id — enables session resume
            protocol=ProtocolVersion.V5,
            clean_start=False,                  # resume session on reconnect
            properties=props,
            keepalive=KEEPALIVE_SECS,
            tls_params=tls,
            will=will,
        ) as client:
            self._client = client
            log.info("connected to mqtt://%s:%d as '%s' (clean_start=False)",
                     self.broker, self.port, self.node_id)

            await client.subscribe(T.cmd(self.node_id), qos=QOS)

            # Retained: I am online.
            await client.publish(
                T.presence(self.node_id),
                payload=presence_payload(self.node_id, online=True),
                qos=QOS, retain=True,
            )

            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._heartbeat_loop())
                    tg.create_task(self._message_loop())
                    tg.create_task(self._watch_stop())
            finally:
                # Graceful goodbye — flip presence to offline before exit.
                try:
                    await client.publish(
                        T.presence(self.node_id),
                        payload=presence_payload(self.node_id, online=False),
                        qos=QOS, retain=True,
                    )
                except Exception:
                    pass
                self._client = None

    async def _watch_stop(self):
        await self._stop.wait()
        raise aiomqtt.MqttError("stop requested")

    async def _heartbeat_loop(self):
        while True:
            try:
                async with asyncio.timeout(HEARTBEAT_SECS):
                    await self._stop.wait()
                return  # stop requested
            except TimeoutError:
                pass
            if self._client is None:
                continue
            try:
                batt = await skill_battery({})
                state = {
                    "node_id": self.node_id,
                    "online":  True,
                    "meta": {
                        "battery":  batt.get("battery"),
                        "uptime":   int(time.time() - self._boot_ts),
                        "has_termux_api": HAS_TERMUX,
                    },
                    "ts": time.time(),
                    "type": "state",
                }
                await self._client.publish(
                    T.state(self.node_id),
                    payload=json.dumps(state).encode(),
                    qos=QOS, retain=True,
                )
                # Separate heartbeat: disposable, QoS 0.
                await self._client.publish(
                    T.heartbeat(self.node_id),
                    payload=json.dumps({"ts": time.time()}).encode(),
                    qos=0, retain=False,
                )
            except Exception as e:
                log.warning("heartbeat publish failed: %s", e)

    async def _message_loop(self):
        assert self._client is not None
        async for msg in self._client.messages:
            if self._stop.is_set():
                return
            if str(msg.topic) != T.cmd(self.node_id):
                continue
            # Dispatch each command concurrently so a slow skill can't
            # block unrelated ones — but we never spawn > ~4 in practice.
            asyncio.create_task(self._handle_cmd(msg.payload))

    async def _handle_cmd(self, payload: bytes):
        try:
            cmd = Command.from_bytes(payload)
        except Exception as e:
            log.warning("bad command payload: %s", e)
            return

        log.info("cmd %s skill=%s from=%s",
                 cmd.id[:8], cmd.skill, cmd.from_)

        fn = SKILLS.get(cmd.skill)
        if fn is None:
            resp = Response(id=cmd.id, ok=False,
                            error=f"unknown skill: {cmd.skill}",
                            from_=self.node_id)
        else:
            try:
                async with asyncio.timeout(max(5.0, cmd.ttl)):
                    result = await fn(cmd.params or {})
                ok = bool(result.get("ok", True))
                resp = Response(id=cmd.id, ok=ok, result=result,
                                error=result.get("error"),
                                from_=self.node_id)
            except TimeoutError:
                resp = Response(id=cmd.id, ok=False,
                                error=f"skill timed out after {cmd.ttl}s",
                                from_=self.node_id)
            except Exception as e:
                log.exception("skill '%s' crashed", cmd.skill)
                resp = Response(id=cmd.id, ok=False,
                                error=f"{type(e).__name__}: {e}",
                                from_=self.node_id)

        try:
            await self._client.publish(T.response(self.node_id),
                                       payload=resp.to_bytes(), qos=QOS)
        except Exception as e:
            log.warning("response publish failed for %s: %s", cmd.id[:8], e)


# ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="JARVIS phone agent (Termux)")
    ap.add_argument("--broker",   default=os.environ.get("JARVIS_BROKER", ""))
    ap.add_argument("--port",     type=int,
                    default=int(os.environ.get("JARVIS_PORT", "1883")))
    ap.add_argument("--username", default=os.environ.get("JARVIS_USER", ""))
    ap.add_argument("--password", default=os.environ.get("JARVIS_PASS", ""))
    ap.add_argument("--node-id",  default=os.environ.get("JARVIS_NODE_ID", "ian-phone"))
    ap.add_argument("--ca-cert",  default=os.environ.get("JARVIS_CA_CERT", ""))
    args = ap.parse_args()

    if not args.broker:
        print("error: --broker (or JARVIS_BROKER env) required", file=sys.stderr)
        sys.exit(2)

    agent = Agent(
        broker=args.broker, port=args.port,
        username=args.username or None,
        password=args.password or None,
        node_id=args.node_id,
        ca_cert=args.ca_cert or None,
    )

    # Graceful shutdown on SIGTERM / SIGINT so presence flips to offline.
    loop = asyncio.new_event_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        s = getattr(signal, sig_name, None)
        if s is None:
            continue
        try:
            loop.add_signal_handler(s, agent.request_stop)
        except NotImplementedError:
            # Windows lacks add_signal_handler in asyncio proactor.
            signal.signal(s, lambda *_: agent.request_stop())

    log.info("capabilities: termux-api=%s am=%s input=%s",
             HAS_TERMUX, HAS_AM, HAS_INPUT)
    try:
        loop.run_until_complete(agent.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

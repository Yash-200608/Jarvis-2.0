"""Termux-side Jarvis-Core agent.

Designed to run unattended inside Termux on Android, supervised by the
Tasker watchdog described in ``android/Tasker_Watchdog.xml``.

Responsibilities
----------------
* Connect to the Mosquitto broker over Tailscale (persistent session, QoS 1).
* Publish a retained ``online`` heartbeat every 30 s and an LWT
  ``offline`` payload that the broker delivers when we drop.
* Subscribe to ``jarvis/phone/cmd``, verify the HMAC envelope, and execute
  the requested ``termux-api`` command.
* Echo the result back on ``jarvis/phone/state`` carrying the same
  ``request_id`` so the PC brain's RPC layer can correlate.

Run with::

    python phone_executor.py --config phone.yaml

(Or set ``$JARVIS_PHONE_CONFIG`` to point at a YAML.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiomqtt
import yaml
from loguru import logger

# We keep the executor self-contained — no relative import on `core` so it
# can ship as a single .py file inside Termux's home dir.
import hashlib
import hmac

MAX_SKEW_SECONDS = 120
HEARTBEAT_INTERVAL = 30  # seconds


# ---------------------------------------------------------------------- #
# Tiny config loader (no Pydantic — keep deps light on Termux).          #
# ---------------------------------------------------------------------- #


@dataclass
class PhoneConfig:
    broker_host: str
    broker_port: int
    client_id: str
    username: str | None
    password: str | None
    hmac_key: str
    cmd_topic: str
    state_topic: str
    heartbeat_topic: str
    qos: int = 1
    keepalive: int = 30


def load_phone_config(path: str) -> PhoneConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    mqtt = raw["mqtt"]
    return PhoneConfig(
        broker_host=mqtt["host"],
        broker_port=int(mqtt.get("port", 1883)),
        client_id=mqtt.get("client_id", "jarvis-phone"),
        username=os.environ.get("MQTT_USERNAME") or mqtt.get("username"),
        password=os.environ.get("MQTT_PASSWORD") or mqtt.get("password"),
        hmac_key=os.environ.get("JARVIS_HMAC_KEY", "") or raw.get("hmac_key", ""),
        cmd_topic=mqtt["topics"]["phone_cmd"],
        state_topic=mqtt["topics"]["phone_state"],
        heartbeat_topic=mqtt["topics"]["heartbeat_phone"],
        qos=int(mqtt.get("qos", 1)),
        keepalive=int(mqtt.get("keepalive", 30)),
    )


# ---------------------------------------------------------------------- #
# Signature verification (mirrors core/signing.py).                      #
# ---------------------------------------------------------------------- #


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def verify(envelope: dict[str, Any], key: str) -> bool:
    if not key or "sig" not in envelope or "ts" not in envelope:
        return False
    sig = envelope.pop("sig")
    if abs(time.time() - envelope.get("ts", 0)) > MAX_SKEW_SECONDS:
        envelope["sig"] = sig
        return False
    expected = hmac.new(key.encode(), _canonical(envelope), hashlib.sha256).hexdigest()
    envelope["sig"] = sig
    return hmac.compare_digest(sig, expected)


# ---------------------------------------------------------------------- #
# termux-api dispatch table                                              #
# ---------------------------------------------------------------------- #


async def run_subprocess(argv: list[str], timeout: float = 30.0) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": "timeout", "argv": argv}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": out.decode(errors="replace"),
        "stderr": err.decode(errors="replace"),
    }


async def termux_get_battery() -> dict[str, Any]:
    res = await run_subprocess(["termux-battery-status"])
    if res["ok"]:
        try:
            res["battery"] = json.loads(res["stdout"])
        except json.JSONDecodeError:
            pass
    return res


async def termux_get_location() -> dict[str, Any]:
    res = await run_subprocess(
        ["termux-location", "-p", "network", "-r", "last"], timeout=20.0
    )
    if res["ok"]:
        try:
            res["location"] = json.loads(res["stdout"])
        except json.JSONDecodeError:
            pass
    return res


async def termux_send_sms(params: dict[str, Any]) -> dict[str, Any]:
    number = params["number"]
    message = params["message"]
    return await run_subprocess(
        ["termux-sms-send", "-n", number, message], timeout=15.0
    )


async def termux_open_url(params: dict[str, Any]) -> dict[str, Any]:
    return await run_subprocess(["termux-open-url", params["url"]])


async def termux_speak(params: dict[str, Any]) -> dict[str, Any]:
    return await run_subprocess(["termux-tts-speak", params["text"]])


async def termux_notify(params: dict[str, Any]) -> dict[str, Any]:
    argv = ["termux-notification", "--title", params.get("title", "Jarvis")]
    argv += ["--content", params.get("message", "")]
    return await run_subprocess(argv)


async def termux_clipboard_set(params: dict[str, Any]) -> dict[str, Any]:
    return await run_subprocess(
        ["termux-clipboard-set", params.get("text", "")]
    )


async def termux_clipboard_get(_params: dict[str, Any]) -> dict[str, Any]:
    return await run_subprocess(["termux-clipboard-get"])


async def termux_get_device_info(_params: dict[str, Any]) -> dict[str, Any]:
    res = await run_subprocess(["termux-telephony-deviceinfo"])
    if res["ok"]:
        try:
            res["device"] = json.loads(res["stdout"])
        except json.JSONDecodeError:
            pass
    return res


async def termux_volume(params: dict[str, Any]) -> dict[str, Any]:
    stream = params.get("stream", "music")
    level = int(params.get("level", 5))
    return await run_subprocess(
        ["termux-volume", stream, str(level)]
    )


async def termux_vibrate(params: dict[str, Any]) -> dict[str, Any]:
    duration = int(params.get("duration_ms", 500))
    return await run_subprocess(["termux-vibrate", "-d", str(duration)])


async def termux_run_shell(params: dict[str, Any]) -> dict[str, Any]:
    """Tightly scoped shell — only ``allowed`` binaries pass."""
    allowed = {"echo", "cat", "ls", "pwd", "whoami", "uptime", "df", "free"}
    cmd = params["cmd"]
    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    if not argv or argv[0] not in allowed:
        return {"ok": False, "error": f"command {argv[:1]} not allowed"}
    return await run_subprocess(argv, timeout=10.0)


# Action -> coroutine. Anything not listed here is rejected.
HANDLERS: dict[str, Any] = {
    "get_battery": lambda p: termux_get_battery(),
    "get_location": lambda p: termux_get_location(),
    "get_device_info": termux_get_device_info,
    "send_sms": termux_send_sms,
    "open_url": termux_open_url,
    "speak": termux_speak,
    "notify": termux_notify,
    "clipboard_set": termux_clipboard_set,
    "clipboard_get": termux_clipboard_get,
    "set_volume": termux_volume,
    "vibrate": termux_vibrate,
    "run_shell": termux_run_shell,
}


# ---------------------------------------------------------------------- #
# Main loop                                                              #
# ---------------------------------------------------------------------- #


async def heartbeat_loop(client: aiomqtt.Client, cfg: PhoneConfig) -> None:
    while True:
        payload = {
            "status": "online",
            "ts": time.time(),
            "client_id": cfg.client_id,
        }
        await client.publish(
            cfg.heartbeat_topic,
            json.dumps(payload).encode(),
            qos=cfg.qos,
            retain=True,
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def handle_command(
    client: aiomqtt.Client, cfg: PhoneConfig, envelope: dict[str, Any]
) -> None:
    rid = envelope.get("request_id", "<no-rid>")
    if not verify(envelope, cfg.hmac_key):
        logger.warning(f"Rejected unsigned/expired command rid={rid}")
        await client.publish(
            cfg.state_topic,
            json.dumps(
                {"request_id": rid, "ok": False, "error": "bad_signature"}
            ).encode(),
            qos=cfg.qos,
        )
        return

    action = envelope.get("action", "")
    params = envelope.get("params", {}) or {}
    handler = HANDLERS.get(action)
    if handler is None:
        logger.warning(f"Unknown action {action!r}")
        result: dict[str, Any] = {"ok": False, "error": f"unknown action {action}"}
    else:
        logger.info(f"EXEC {action} rid={rid}")
        try:
            result = await handler(params)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Handler {action} crashed: {exc!r}")
            result = {"ok": False, "error": repr(exc)}

    response = {
        "request_id": rid,
        "action": action,
        "ts": time.time(),
        **result,
    }
    await client.publish(
        cfg.state_topic,
        json.dumps(response).encode(),
        qos=cfg.qos,
    )


async def main_async(cfg: PhoneConfig) -> None:
    will = aiomqtt.Will(
        topic=cfg.heartbeat_topic,
        payload=json.dumps({"status": "offline", "ts": time.time()}).encode(),
        qos=cfg.qos,
        retain=True,
    )
    backoff = 1.0
    while True:
        try:
            logger.info(f"Connecting to {cfg.broker_host}:{cfg.broker_port}")
            async with aiomqtt.Client(
                hostname=cfg.broker_host,
                port=cfg.broker_port,
                identifier=cfg.client_id,
                username=cfg.username,
                password=cfg.password,
                keepalive=cfg.keepalive,
                clean_session=False,
                will=will,
            ) as client:
                await client.subscribe(cfg.cmd_topic, qos=cfg.qos)
                logger.success(f"Subscribed to {cfg.cmd_topic}")
                hb_task = asyncio.create_task(heartbeat_loop(client, cfg))
                try:
                    async for msg in client.messages:
                        try:
                            envelope = json.loads(msg.payload.decode())
                        except Exception:  # noqa: BLE001
                            logger.warning("Dropped non-JSON command")
                            continue
                        # Spawn handler so a slow one doesn't block the queue.
                        asyncio.create_task(
                            handle_command(client, cfg, envelope)
                        )
                finally:
                    hb_task.cancel()
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Disconnected: {exc!r} — retry in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jarvis-Core Termux executor")
    parser.add_argument(
        "--config",
        default=os.environ.get("JARVIS_PHONE_CONFIG", "phone.yaml"),
        help="Path to YAML config",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_phone_config(args.config)

    logger.remove()
    logger.add(
        lambda m: print(m, end=""),
        colorize=True,
        format=(
            "<dim>{time:HH:mm:ss}</dim> "
            "<level>{level: <8}</level> "
            "<yellow>[PHONE]</yellow> {message}"
        ),
    )

    loop = asyncio.new_event_loop()

    def _shutdown(*_: Any) -> None:
        for t in asyncio.all_tasks(loop):
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows / Termux quirk — fall back to default behaviour.
            pass

    try:
        loop.run_until_complete(main_async(cfg))
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutting down")
    finally:
        loop.close()


if __name__ == "__main__":
    main()

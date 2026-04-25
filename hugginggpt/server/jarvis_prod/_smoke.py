"""End-to-end smoke test for the jarvis_prod stack.

Starts:
  • the PC brain (as a library, not the CLI)
  • a fake phone agent that responds to `phone.battery`

Then fires a command and checks the response round-trips.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent))

import aiomqtt
from aiomqtt import Client, ProtocolVersion, Will
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from protocol import (
    KEEPALIVE_SECS, QOS, SESSION_EXPIRY_SECS,
    Command, Response, T, presence_payload,
)
from brain_pc import Brain

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
log = logging.getLogger("smoke")


BROKER = os.environ.get("JARVIS_BROKER", "127.0.0.1")
USER   = os.environ.get("JARVIS_USER", "jarvis")
PASS   = os.environ.get("JARVIS_PASS", "")   # set via env; never commit
PHONE  = os.environ.get("JARVIS_PHONE_ID", "ian-phone")

if not PASS:
    raise SystemExit(
        "JARVIS_PASS env var not set. Example:\n"
        "  $env:JARVIS_PASS='<mosquitto password>' ; py -3.12 _smoke.py"
    )


async def fake_phone(stop: asyncio.Event):
    """Connect as the phone, subscribe to /cmd, publish fake battery reports."""
    will = Will(topic=T.presence(PHONE),
                payload=presence_payload(PHONE, online=False),
                qos=QOS, retain=True)
    props = Properties(PacketTypes.CONNECT)
    props.SessionExpiryInterval = SESSION_EXPIRY_SECS
    async with Client(
        hostname=BROKER, port=1883,
        username=USER, password=PASS,
        identifier=PHONE,
        protocol=ProtocolVersion.V5,
        clean_start=False,
        properties=props,
        keepalive=KEEPALIVE_SECS,
        will=will,
    ) as client:
        log.info("fake phone connected")
        await client.subscribe(T.cmd(PHONE), qos=QOS)
        await client.publish(T.presence(PHONE),
                             payload=presence_payload(PHONE, online=True),
                             qos=QOS, retain=True)
        async for msg in client.messages:
            if stop.is_set():
                return
            if str(msg.topic) != T.cmd(PHONE):
                continue
            cmd = Command.from_bytes(bytes(msg.payload))
            log.info("fake phone got cmd: %s %s", cmd.skill, cmd.params)
            if cmd.skill == "phone.battery":
                r = Response(id=cmd.id, ok=True,
                             result={"ok": True,
                                     "battery": {"level": 77, "charging": False}},
                             from_=PHONE)
            else:
                r = Response(id=cmd.id, ok=False,
                             error=f"fake phone has no skill {cmd.skill!r}",
                             from_=PHONE)
            await client.publish(T.response(PHONE), payload=r.to_bytes(), qos=QOS)


async def main():
    stop = asyncio.Event()
    brain = Brain(
        broker=BROKER, port=1883, username=USER, password=PASS,
        node_id="ian-pc", phone_node=PHONE, ca_cert=None,
    )

    async def run_brain():
        try:
            await brain.run()
        except Exception as e:
            log.exception("brain crashed: %s", e)

    async def run_phone():
        try:
            await fake_phone(stop)
        except Exception as e:
            log.exception("phone crashed: %s", e)

    async def driver():
        # Give both clients a moment to connect + publish presence.
        await asyncio.sleep(2.0)
        # Send a command.
        log.info("DRIVER: sending phone.battery")
        resp = await brain.send_command("phone.battery", {}, timeout=10.0)
        log.info("DRIVER: got response: ok=%s result=%s error=%s",
                 resp.ok, resp.result, resp.error)

        log.info("DRIVER: sending phone.nosuch (should fail)")
        resp2 = await brain.send_command("phone.nosuch", {}, timeout=5.0)
        log.info("DRIVER: got response: ok=%s error=%s", resp2.ok, resp2.error)

        log.info("DRIVER: presence=%s", brain._presence)

        # Clean shutdown.
        stop.set()
        brain.request_stop()

    await asyncio.gather(
        run_brain(), run_phone(), driver(),
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())

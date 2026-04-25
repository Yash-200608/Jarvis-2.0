"""JARVIS PC brain — production build.

Orchestrates the phone via QoS-1 MQTT. Correlates every outbound Command
with its inbound Response by `id`.

Key behaviors:
  • `clean_start=True` for the brain (no session persistence needed —
    the brain is the initiator; if we miss responses while offline,
    we'd rather fail-fast than replay stale results).
  • Every outgoing Command is tracked in an asyncio Future keyed by
    `cmd.id`. Responses with unknown IDs are ignored (late/dup).
  • Timeouts are handled with a single code path: asyncio.wait_for()
    on the Future, falling back to a structured error Response.
  • Presence topic (retained) tells us if the phone is online before we
    send. If offline, we still send (QoS 1 queues on the broker) but
    we warn the operator and shorten the timeout.
  • CLI REPL with `shlex`-parsed `skill k=v k=v` grammar. Quote values
    with spaces: `phone.tts text="I'm running late"`.

Run:
    python brain_pc.py \
        --broker 100.xx.xx.xx \
        --username jarvis --password <passwd> \
        --phone-node ian-phone
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import signal
import sys
import time
from typing import Any

# Windows default (Proactor) can't do add_reader/add_writer on TCP
# sockets, which paho-mqtt / aiomqtt need. Force Selector loop on
# Windows BEFORE any event loop is created. Harmless on other OSes.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt
from aiomqtt import Client, ProtocolVersion, Will

from pathlib import Path

from protocol import (
    HEARTBEAT_SECS, KEEPALIVE_SECS, QOS, SESSION_EXPIRY_SECS,
    Command, Event, Response, T, presence_payload,
)

# Optional OpenClaw-inspired pieces. Loaded lazily so the brain works
# even if telegram_gateway / scheduler aren't deployed.
try:
    from telegram_gateway import Gateway as TgGateway, TgConfig
    HAS_TG = True
except Exception as _e:
    HAS_TG = False
try:
    from scheduler import Scheduler
    HAS_SCHED = True
except Exception as _e:
    HAS_SCHED = False
from jobs import JobLog

# Optional: Langfuse observability (SDK v4). Activates only when
# LANGFUSE_SECRET_KEY is set in the environment — otherwise every
# trace call is a silent no-op, with zero network overhead.
#
# Follows the langfuse/skills instrumentation playbook:
#   • v4 API: start_as_current_observation(as_type="span"/"generation")
#   • propagate_attributes() to set user_id/session_id/tags on the trace
#   • LLM calls (awesome_chat.parse_device_command) marked as generations
#   • Sensitive fields (sms text, phone numbers) masked via `mask=`
#   • flush() on reconnect AND on request_stop()
try:
    from langfuse import Langfuse as _Langfuse  # type: ignore
    from langfuse import propagate_attributes as _lf_propagate  # type: ignore
    HAS_LANGFUSE = True
except Exception:
    HAS_LANGFUSE = False

from contextlib import nullcontext


# ── data masking for Langfuse traces ────────────────────────────────
# Redact obvious PII before it lands in the trace input/output.
# Applied (a) inline at call sites for rich context, and (b) as the
# SDK-level `mask=` callback for defense-in-depth.
_PII_SKILL_PARAMS = {
    "phone":     "***masked-phone***",
    "to_phone":  "***masked-phone***",
    "recipient": "***masked-contact***",
    "message":   "***masked-body***",
    "text":      "***masked-text***",
    "body":      "***masked-body***",
    "pin":       "***masked-pin***",
    "password":  "***masked-pw***",
}


def _mask_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shallow copy with PII-shaped fields replaced. Keeps
    the shape of the dict so you can still eyeball what was called."""
    if not params:
        return {}
    out = {}
    for k, v in params.items():
        out[k] = _PII_SKILL_PARAMS.get(k, v)
    return out


def _lf_mask(data: Any) -> Any:
    """SDK-level mask callback — runs on every span's input/output.
    Belt-and-braces against accidental leaks. Mirrors _mask_params
    but also drills into {'params': {...}} shapes."""
    if isinstance(data, dict):
        masked = {}
        for k, v in data.items():
            if k in _PII_SKILL_PARAMS:
                masked[k] = _PII_SKILL_PARAMS[k]
            elif isinstance(v, dict):
                masked[k] = _lf_mask(v)
            else:
                masked[k] = v
        return masked
    return data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [brain] %(levelname)s %(message)s",
)
log = logging.getLogger("brain")


# ──────────────────────────────────────────────────────────────────────
#  Brain
# ──────────────────────────────────────────────────────────────────────
class Brain:
    def __init__(self,
                 broker: str, port: int,
                 username: str | None, password: str | None,
                 node_id: str, phone_node: str,
                 ca_cert: str | None,
                 workspace_dir: Path | None = None,
                 schedule_file: Path | None = None):
        self.broker     = broker
        self.port       = port
        self.username   = username
        self.password   = password
        self.node_id    = node_id
        self.phone_node = phone_node
        self.ca_cert    = ca_cert
        self.workspace  = workspace_dir or (Path(__file__).parent / "workspace")
        self.schedule_file = schedule_file or (Path(__file__).parent / "schedule.json")

        self._stop    = asyncio.Event()
        self._client: Client | None = None
        self._ready   = asyncio.Event()

        # req_id → Future[Response]
        self._pending: dict[str, asyncio.Future[Response]] = {}
        # node_id → bool(online)
        self._presence: dict[str, bool] = {}
        # node_id → last state dict
        self._state: dict[str, dict[str, Any]] = {}

        # Side-channels (Telegram gateway, cron scheduler).
        self._scheduler: "Scheduler | None" = None
        self._tg:        "TgGateway | None" = None

        # Job log — pentagi-inspired execution tracking (SQLite-backed).
        self.jobs = JobLog(capacity=200,
                           db_path=self.workspace / "jobs.db")

        # Langfuse client (optional). Env-driven init — no keys, no tracing.
        # SDK v4: single Langfuse() picks up LANGFUSE_PUBLIC_KEY /
        # LANGFUSE_SECRET_KEY / LANGFUSE_HOST from env automatically.
        # `mask=` runs on every observation's input/output as a safety net.
        self._lf = None
        if HAS_LANGFUSE and os.environ.get("LANGFUSE_SECRET_KEY"):
            try:
                self._lf = _Langfuse(mask=_lf_mask)
                log.info("langfuse tracing enabled (host=%s, release=%s)",
                         os.environ.get("LANGFUSE_HOST",
                                        "https://cloud.langfuse.com"),
                         os.environ.get("LANGFUSE_RELEASE", "dev"))
            except Exception as e:
                log.warning("langfuse init failed: %s — continuing without", e)
                self._lf = None

        # Known skills — advertised via /skills over Telegram.
        self._known_skills: set[str] = {
            "phone.battery", "phone.tts", "phone.sms.send", "phone.torch",
            "phone.vibrate", "phone.notify", "phone.ring", "phone.location",
            "phone.app.open", "phone.whatsapp.send",
            "pc.volume.set", "pc.volume.mute", "pc.media.control",
            "pc.type", "pc.hotkey", "pc.click", "pc.screenshot",
            "pc.app.open", "pc.url.open", "pc.process.list", "pc.process.kill",
            "pc.system.info", "pc.system.lock", "pc.system.shutdown",
            "pc.shell.run",
        }

    def request_stop(self):
        self._stop.set()
        # Flush Langfuse so ctrl-C / SIGTERM doesn't drop the last batch.
        self._lf_flush()

    # ── scheduling API (used by Telegram /remind handler, among others) ──
    def add_reminder(self, in_seconds: float, skill: str,
                     params: dict[str, Any] | None = None,
                     to: str | None = None, tag: str = "") -> None:
        if self._scheduler is None:
            log.warning("scheduler not running — reminder ignored")
            return
        self._scheduler.add_reminder(
            when_ts = time.time() + max(1.0, in_seconds),
            to      = to or self.phone_node,
            skill   = skill,
            params  = params or {},
            tag     = tag,
        )

    def cron_list(self) -> list[dict[str, Any]]:
        return self._scheduler.list_all() if self._scheduler else []

    # ── Langfuse helpers (SDK v4) ───────────────────────────────────
    def _trace_obs(self, name: str, *,
                   as_type: str = "span",
                   input: dict[str, Any] | None = None,
                   model: str | None = None):
        """Return a context manager yielding a Langfuse observation, or
        `nullcontext(None)` when LF is disabled. Call sites must
        None-check before calling `.update()` on the yielded value.

        `as_type` is "span" for generic work, "generation" for LLM calls —
        the latter unlocks model/token/cost analytics in the UI."""
        if self._lf is None:
            return nullcontext(None)
        try:
            kwargs: dict[str, Any] = {"as_type": as_type, "name": name}
            if input is not None:
                kwargs["input"] = input
            if model is not None and as_type == "generation":
                kwargs["model"] = model
            return self._lf.start_as_current_observation(**kwargs)
        except Exception as e:
            log.debug("langfuse observation failed: %s", e)
            return nullcontext(None)

    def _trace_attrs(self, *, user_id: str | None = None,
                     session_id: str | None = None,
                     tags: list[str] | None = None,
                     metadata: dict[str, str] | None = None):
        """propagate_attributes wrapper — sets trace-level user_id,
        session_id, tags, metadata for everything nested inside.

        `session_id` groups related observations (e.g. all commands from
        one telegram conversation, or all fires from one cron job run)."""
        if self._lf is None or _lf_propagate is None:
            return nullcontext(None)
        try:
            return _lf_propagate(
                user_id=user_id,
                session_id=session_id,
                tags=tags or [],
                metadata=metadata or {},
            )
        except Exception as e:
            log.debug("langfuse propagate_attributes failed: %s", e)
            return nullcontext(None)

    def _lf_flush(self):
        if self._lf is None:
            return
        try:
            self._lf.flush()
        except Exception:
            pass

    # ── public: send a command and await the paired response ────────
    async def send_command(self, skill: str, params: dict[str, Any] | None = None,
                           timeout: float = 20.0,
                           to: str | None = None,
                           source: str = "repl") -> Response:
        target = to or self.phone_node

        # Langfuse: propagate user/session/tags DOWN to the observation,
        # then open the span. session_id=target groups all commands aimed
        # at a given node (phone/pc) for that conversation window.
        with self._trace_attrs(
                user_id="ian",
                session_id=target,
                tags=[source, target],
                metadata={"skill": skill, "source": source, "target": target},
             ), self._trace_obs(
                "jarvis.send_command", as_type="span",
                input={"skill": skill, "params": _mask_params(params),
                       "to": target, "source": source},
             ) as span:
            # Reflector: pentagi-style skip-if-repeatedly-failing.
            if self.jobs.should_skip(skill, target):
                fails = self.jobs.consecutive_failures(skill, target)
                err = (f"reflector: skill {skill!r} has failed {fails}× "
                       f"in a row on {target!r} — run "
                       f"`/unblock {skill}@{target}` to clear and retry")
                log.warning(err)
                resp = Response(id="", ok=False, error=err,
                                from_=self.node_id)
                if span is not None:
                    span.update(output={"ok": False, "error": err,
                                        "reflector_skipped": True})
                return resp

            cmd = Command(skill=skill, params=params or {}, to=target,
                          from_=self.node_id, ttl=timeout)
            fut: asyncio.Future[Response] = \
                asyncio.get_event_loop().create_future()
            self._pending[cmd.id] = fut

            self.jobs.start(cmd.id, skill, cmd.params, target, source=source)

            await self._ready.wait()
            assert self._client is not None

            if self._presence.get(target) is False:
                log.warning("target '%s' is offline; cmd will be queued "
                            "(broker-side) until it reconnects", target)

            try:
                await self._client.publish(T.cmd(target),
                                           payload=cmd.to_bytes(), qos=QOS)
            except Exception as e:
                self._pending.pop(cmd.id, None)
                self.jobs.finish(cmd.id, ok=False,
                                 error=f"publish failed: {e}")
                resp = Response(id=cmd.id, ok=False,
                                error=f"publish failed: {e}",
                                from_=self.node_id)
                if span is not None:
                    span.update(output={"ok": False, "error": resp.error})
                return resp

            try:
                resp = await asyncio.wait_for(fut, timeout=timeout)
                self.jobs.finish(cmd.id, ok=resp.ok,
                                 result=resp.result, error=resp.error)
                if span is not None:
                    span.update(output={"ok": resp.ok, "result": resp.result,
                                        "error": resp.error})
                return resp
            except TimeoutError:
                online = self._presence.get(target)
                hint = ("target appears offline — command queued, "
                        "will execute when phone reconnects") \
                       if online is False else \
                       "target online but did not respond in time — retry?"
                err = f"timeout {timeout}s. {hint}"
                self.jobs.finish(cmd.id, ok=False, error=err)
                if span is not None:
                    span.update(output={"ok": False, "error": err,
                                        "timed_out": True})
                return Response(id=cmd.id, ok=False, error=err,
                                from_=self.node_id)
            finally:
                self._pending.pop(cmd.id, None)

    # ── lifecycle ───────────────────────────────────────────────────
    async def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except aiomqtt.MqttError as e:
                log.warning("mqtt dropped: %s — reconnect in %.1fs", e, backoff)
            except Exception as e:
                log.exception("brain error: %s — reconnect in %.1fs", e, backoff)
            if self._stop.is_set():
                break
            try:
                async with asyncio.timeout(backoff):
                    await self._stop.wait()
                break
            except TimeoutError:
                pass
            backoff = min(30.0, backoff * 2)

    async def _run_once(self):
        tls = aiomqtt.TLSParameters(ca_certs=self.ca_cert) if self.ca_cert else None
        will = Will(
            topic=T.presence(self.node_id),
            payload=presence_payload(self.node_id, online=False),
            qos=QOS, retain=True,
        )

        async with Client(
            hostname=self.broker,
            port=self.port,
            username=self.username,
            password=self.password,
            identifier=self.node_id,
            protocol=ProtocolVersion.V5,
            clean_start=True,                   # brain doesn't need session resume
            keepalive=KEEPALIVE_SECS,
            tls_params=tls,
            will=will,
        ) as client:
            self._client = client
            self._ready.set()
            log.info("connected to mqtt://%s:%d as '%s'",
                     self.broker, self.port, self.node_id)

            # Wildcard subs for all response/presence/event/state.
            await client.subscribe(T.ALL_RESPONSE, qos=QOS)
            await client.subscribe(T.ALL_PRESENCE, qos=QOS)
            await client.subscribe(T.ALL_EVENT,    qos=QOS)
            await client.subscribe(T.ALL_STATE,    qos=QOS)

            # Publish our own presence.
            await client.publish(
                T.presence(self.node_id),
                payload=presence_payload(self.node_id, online=True),
                qos=QOS, retain=True,
            )

            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._message_loop())
                    tg.create_task(self._watch_stop())

                    # Scheduler: cron + one-shot reminders. Fires
                    # `send_command` at the appropriate time — no MQTT
                    # needed until a job actually matches.
                    if HAS_SCHED:
                        self._scheduler = Scheduler(
                            schedule_file=self.schedule_file,
                            send_command=lambda s, p, t, to:
                                self.send_command(s, p, t, to,
                                                  source="scheduler"),
                        )
                        tg.create_task(self._scheduler.run())
                        log.info("scheduler started from %s", self.schedule_file)

                    # Telegram gateway: optional. Only starts if the
                    # token env var is set.
                    if HAS_TG and os.environ.get("JARVIS_TG_TOKEN"):
                        try:
                            cfg = TgConfig.from_env()
                            self._tg = TgGateway(
                                cfg=cfg,
                                send_to_brain=self._tg_send_to_brain,
                                describe_status=lambda: {
                                    "presence": self._presence,
                                    "state":    self._state,
                                },
                                list_skills=lambda: sorted(self._known_skills),
                                list_cron=self.cron_list,
                                list_jobs=self.jobs.to_status,
                                unblock_fn=self.jobs.clear_failures,
                            )
                            tg.create_task(self._tg.run())
                            log.info("telegram gateway started")
                        except Exception as e:
                            log.warning("telegram gateway skipped: %s", e)
            finally:
                try:
                    await client.publish(
                        T.presence(self.node_id),
                        payload=presence_payload(self.node_id, online=False),
                        qos=QOS, retain=True,
                    )
                except Exception:
                    pass
                self._ready.clear()
                self._client = None
                # Fail any outstanding requests so the REPL isn't blocked.
                for fut in list(self._pending.values()):
                    if not fut.done():
                        fut.set_result(Response(
                            id="", ok=False,
                            error="brain disconnected from broker",
                            from_=self.node_id,
                        ))
                self._pending.clear()
                # Flush Langfuse traces so nothing is dropped on reconnect.
                if self._lf is not None:
                    try:
                        self._lf.flush()
                    except Exception:
                        pass

    async def _watch_stop(self):
        await self._stop.wait()
        raise aiomqtt.MqttError("stop requested")

    # ── Telegram ↔ brain bridge ─────────────────────────────────────
    async def _tg_send_to_brain(self, skill: str, params: dict[str, Any],
                                timeout: float) -> dict[str, Any]:
        """Called by the Telegram gateway for every user message.

        Two paths:
          1. skill == 'brain.prompt'  →  feed to the NL parser + LLM
             (hooks into awesome_chat.py if available). For now: echo
             back a simple parse, plus support `remind me in N min/hr`.
          2. any other skill          →  forward over MQTT to the right
             node (phone/pc) based on the skill prefix.
        """
        if skill == "brain.prompt":
            return await self._brain_prompt(params)

        # Route by prefix.
        target = self.phone_node if skill.startswith("phone.") else self.node_id
        resp = await self.send_command(skill, params, timeout, target,
                                       source="telegram")
        return {"ok": resp.ok, "result": resp.result, "error": resp.error}

    async def _brain_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        """Natural-language handler. Minimal built-ins + pluggable.

        Built-ins (so the Telegram gateway works standalone):
          • "remind me in N minutes/hours to <text>"
             → schedules phone.tts.
          • "ring my phone for N seconds"
             → fires phone.ring.
          • "what's my battery"   → phone.battery.
          • otherwise              → tries to load awesome_chat's parser,
                                     falls back to polite "didn't parse".
        """
        text = str(params.get("text", "")).strip()
        chat_id = params.get("chat_id")
        low = text.lower()

        # Wrap the whole turn in a parent `brain.prompt` span.
        # session_id=chat_id groups every message from one Telegram
        # conversation in the Langfuse Sessions view.
        with self._trace_attrs(
                user_id="ian",
                session_id=str(chat_id) if chat_id else None,
                tags=["telegram", "brain.prompt"],
                metadata={"source": "telegram",
                          "chat_id": str(chat_id) if chat_id else ""},
             ), self._trace_obs(
                "brain.prompt", as_type="span",
                input={"text": text},
             ) as outer:
            import re
            m = re.search(r"remind\s+me\s+in\s+(\d+)\s*(sec|secs|s|min|mins|m|hr|hrs|h|hour|hours)\s+to\s+(.+)",
                          low)
            if m:
                n = int(m.group(1))
                unit = m.group(2)
                body = text[m.start(3):].strip()
                mult = 1 if unit in ("sec", "secs", "s") else \
                       60 if unit in ("min", "mins", "m") else 3600
                self.add_reminder(n * mult, "phone.tts",
                                  {"text": body}, tag="remind")
                out = {"ok": True, "result": {
                    "reply": f"ok — will remind in {n}{unit}: {body!r}"}}
                if outer is not None:
                    outer.update(output={"route": "builtin.remind",
                                         "ok": True})
                return out

            m = re.search(r"ring\s+(?:my\s+)?phone(?:\s+for\s+(\d+))?", low)
            if m:
                secs = int(m.group(1) or 10)
                resp = await self.send_command("phone.ring",
                                               {"seconds": secs}, 30.0,
                                               self.phone_node)
                out = {"ok": resp.ok, "result": {
                    "reply": f"ringing for {secs}s" if resp.ok
                             else f"failed: {resp.error}"}}
                if outer is not None:
                    outer.update(output={"route": "builtin.ring",
                                         "ok": resp.ok})
                return out

            if "battery" in low:
                resp = await self.send_command("phone.battery", {}, 15.0,
                                               self.phone_node)
                if resp.ok and isinstance(resp.result, dict):
                    b = resp.result.get("battery", {})
                    out = {"ok": True, "result": {
                        "reply": f"phone: {b.get('level')}% "
                                 f"({'charging' if b.get('charging') else 'discharging'})"}}
                    if outer is not None:
                        outer.update(output={"route": "builtin.battery",
                                             "ok": True})
                    return out
                if outer is not None:
                    outer.update(output={"route": "builtin.battery",
                                         "ok": False,
                                         "error": resp.error or "phone unreachable"})
                return {"ok": False, "error": resp.error or "phone unreachable"}

            # Try to delegate to awesome_chat's NL parser if it's importable.
            # This is an LLM call — tag it as a `generation` so the
            # Langfuse UI groups it under model/token/cost analytics.
            parsed = None
            try:
                # awesome_chat lives one directory up in `server/`.
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from awesome_chat import parse_device_command  # type: ignore
                with self._trace_obs(
                        "awesome_chat.parse_device_command",
                        as_type="generation",
                        model="llama-3.3-70b-versatile",
                        input={"text": text},
                     ) as gen:
                    parsed = parse_device_command(text)
                    if gen is not None and parsed is not None:
                        gen.update(output=parsed)
            except Exception as e:
                log.debug("awesome_chat bridge unavailable: %s", e)

            if parsed:
                cmds = parsed.get("commands", []) or []
                reasoning = parsed.get("reasoning", "") or ""
                results = []
                for c in cmds:
                    tgt = self.phone_node if c.get("skill", "").startswith("phone.") \
                        else self.node_id
                    resp = await self.send_command(
                        c["skill"], c.get("params", {}), 30.0, tgt)
                    results.append(f"{c['skill']}: {'ok' if resp.ok else resp.error}")
                body = ("\n".join(results)) or "(no actions)"
                if reasoning:
                    body = f"_{reasoning}_\n{body}"
                out = {"ok": True, "result": {"reply": body}}
                if outer is not None:
                    outer.update(output={"route": "awesome_chat",
                                         "cmds": len(cmds),
                                         "ok": True})
                return out

            out = {"ok": True, "result": {
                "reply": "didn't parse that — try `!phone.tts text=\"hi\"` "
                         "or `/help`"}}
            if outer is not None:
                outer.update(output={"route": "fallback", "ok": True})
            return out

    async def _message_loop(self):
        assert self._client is not None
        async for msg in self._client.messages:
            topic = str(msg.topic)
            payload = bytes(msg.payload)

            if topic.endswith("/response"):
                self._on_response(payload)
            elif topic.endswith("/presence"):
                self._on_presence(payload)
            elif topic.endswith("/event"):
                self._on_event(payload)
            elif topic.endswith("/state"):
                self._on_state(payload)

    # ── inbound handlers ────────────────────────────────────────────
    def _on_response(self, payload: bytes):
        try:
            resp = Response.from_bytes(payload)
        except Exception:
            return
        fut = self._pending.get(resp.id)
        if fut is not None and not fut.done():
            fut.set_result(resp)
        # Unknown id? Late or dup. Ignore silently.

    def _on_presence(self, payload: bytes):
        try:
            d = json.loads(payload)
        except Exception:
            return
        node = str(d.get("node_id", ""))
        online = bool(d.get("online", False))
        prev = self._presence.get(node)
        self._presence[node] = online
        if prev != online:
            log.info("presence: %s is now %s", node, "ONLINE" if online else "OFFLINE")

    def _on_event(self, payload: bytes):
        try:
            evt = Event.from_bytes(payload)
        except Exception:
            return
        log.info("EVENT from %s: %s %s", evt.from_, evt.kind, evt.data)

    def _on_state(self, payload: bytes):
        try:
            d = json.loads(payload)
        except Exception:
            return
        node = str(d.get("node_id", ""))
        if not node:
            return
        self._state[node] = d


# ──────────────────────────────────────────────────────────────────────
#  CLI REPL
# ──────────────────────────────────────────────────────────────────────
def _parse_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a line like:
        phone.tts text="Hi there"
        phone.sms.send phone=+919999999999 message="Running late"
        phone.ring seconds=5
    Returns (skill, params) or None for help/empty.
    """
    parts = shlex.split(line)
    if not parts:
        return None
    skill, kvs = parts[0], parts[1:]
    params: dict[str, Any] = {}
    for kv in kvs:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        # Auto-coerce simple types.
        if v.lower() in ("true", "false"):
            params[k] = (v.lower() == "true")
        elif v.lstrip("-").isdigit():
            params[k] = int(v)
        else:
            try:
                params[k] = float(v) if "." in v else v
            except ValueError:
                params[k] = v
    return skill, params


HELP = """Commands:
  <skill> key=value key=value ...        send a command to the phone
  ?status                                show presence + last-seen state
  ?skills                                list a few common skills
  ?quit                                  exit

Examples:
  phone.battery
  phone.ring seconds=5
  phone.tts text="JARVIS online"
  phone.sms.send phone=+919999999999 message="Running late"
  phone.torch on=true
  phone.whatsapp.send phone=+911234567890 message="ping"
  phone.location
"""


async def repl(brain: Brain):
    loop = asyncio.get_event_loop()
    # Give the connection a moment.
    try:
        async with asyncio.timeout(5):
            await brain._ready.wait()
    except TimeoutError:
        print("(warning: still connecting to broker — commands will block)")

    print("JARVIS brain ready. Type ?quit to exit, ?skills for examples.\n")
    while not brain._stop.is_set():
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            brain.request_stop(); return
        line = line.strip()
        if not line:
            continue
        if line in ("?quit", "quit", "exit"):
            brain.request_stop(); return
        if line in ("?help", "?skills"):
            print(HELP); continue
        if line == "?status":
            print(json.dumps({
                "presence": brain._presence,
                "state":    brain._state,
            }, indent=2, default=str))
            continue

        parsed = _parse_line(line)
        if parsed is None:
            continue
        skill, params = parsed
        t0 = time.time()
        resp = await brain.send_command(skill, params, timeout=20.0)
        dt = time.time() - t0
        status = "OK" if resp.ok else "FAIL"
        print(f"[{status} {dt:.2f}s] id={resp.id[:8]}")
        if resp.error:
            print(f"  error: {resp.error}")
        if resp.result is not None:
            # Trim huge fields (screenshots) for display.
            if isinstance(resp.result, dict) and "screenshot_b64" in resp.result:
                r = dict(resp.result)
                r["screenshot_b64"] = f"<{len(r['screenshot_b64'])} b64 chars>"
                print("  result:", json.dumps(r, indent=2, default=str))
            else:
                print("  result:", json.dumps(resp.result, indent=2, default=str))


# ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="JARVIS PC brain")
    ap.add_argument("--broker",     default=os.environ.get("JARVIS_BROKER", ""))
    ap.add_argument("--port",       type=int,
                    default=int(os.environ.get("JARVIS_PORT", "1883")))
    ap.add_argument("--username",   default=os.environ.get("JARVIS_USER", ""))
    ap.add_argument("--password",   default=os.environ.get("JARVIS_PASS", ""))
    ap.add_argument("--node-id",    default=os.environ.get("JARVIS_BRAIN_ID", "ian-pc"))
    ap.add_argument("--phone-node", default=os.environ.get("JARVIS_PHONE_ID", "ian-phone"))
    ap.add_argument("--ca-cert",    default=os.environ.get("JARVIS_CA_CERT", ""))
    ap.add_argument("--no-repl",    action="store_true",
                    help="connect but don't open REPL (for API usage)")
    args = ap.parse_args()

    if not args.broker:
        print("error: --broker (or JARVIS_BROKER env) required", file=sys.stderr)
        sys.exit(2)

    brain = Brain(
        broker=args.broker, port=args.port,
        username=args.username or None,
        password=args.password or None,
        node_id=args.node_id,
        phone_node=args.phone_node,
        ca_cert=args.ca_cert or None,
    )

    async def _amain():
        async with asyncio.TaskGroup() as tg:
            tg.create_task(brain.run())
            if not args.no_repl:
                tg.create_task(repl(brain))

    loop = asyncio.new_event_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        s = getattr(signal, sig_name, None)
        if s is None:
            continue
        try:
            loop.add_signal_handler(s, brain.request_stop)
        except NotImplementedError:
            signal.signal(s, lambda *_: brain.request_stop())

    try:
        loop.run_until_complete(_amain())
    except* Exception as eg:                   # noqa: E999 — Py3.11 except*
        for e in eg.exceptions:
            if not isinstance(e, (KeyboardInterrupt, aiomqtt.MqttError)):
                raise
    finally:
        loop.close()


if __name__ == "__main__":
    main()

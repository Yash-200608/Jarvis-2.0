"""JARVIS Telegram gateway — OpenClaw-style channel bridge.

Lets you DM JARVIS from anywhere Telegram reaches — no VPN needed on the
client side (the PC still runs the brain behind Tailscale, but your
phone just talks to Telegram's servers).

Features borrowed / distilled from OpenClaw:
  • DM pairing / allowlist — only approved chat_ids can control JARVIS.
    Unknown senders get a pairing code; until an admin chat confirms,
    they're blocked. (OpenClaw calls this "DM pairing mode".)
  • In-chat meta commands:  /status  /skills  /cron  /help
  • Any other text is forwarded to the brain as a natural-language
    prompt that ends up in phone.* or pc.* skill calls.

Architecture:
  Telegram Bot API  ──getUpdates long-poll──>  this gateway
                                                │
                                                ▼
                                  Brain.send_command(skill, params)
                                                │
                                                ▼
                                         MQTT QoS-1 to phone

No webhooks — long-polling over HTTPS is simpler, survives NAT, and
is Tailscale-neutral. A single getUpdates call can block up to 50 s,
so the CPU cost is essentially zero.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("telegram")

TG_API = "https://api.telegram.org/bot{token}/{method}"


# ──────────────────────────────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────────────────────────────
@dataclass
class TgConfig:
    token: str
    admin_chat_id: int                     # who can /approve newcomers
    allowlist: set[int] = field(default_factory=set)
    state_file: Path = field(default_factory=lambda: Path("telegram_state.json"))

    @classmethod
    def from_env(cls, state_file: Path | None = None) -> "TgConfig":
        tok = os.environ.get("JARVIS_TG_TOKEN", "").strip()
        if not tok:
            raise RuntimeError("JARVIS_TG_TOKEN not set")
        admin = int(os.environ.get("JARVIS_TG_ADMIN", "0"))
        if not admin:
            raise RuntimeError("JARVIS_TG_ADMIN (your chat id) not set")
        sf = state_file or Path(__file__).with_name("telegram_state.json")
        cfg = cls(token=tok, admin_chat_id=admin, state_file=sf)
        cfg._load()
        return cfg

    def _load(self):
        if self.state_file.exists():
            try:
                d = json.loads(self.state_file.read_text())
                self.allowlist = set(int(x) for x in d.get("allowlist", []))
            except Exception:
                pass
        # Admin is always allowed.
        self.allowlist.add(self.admin_chat_id)

    def save(self):
        self.state_file.write_text(json.dumps({
            "allowlist": sorted(self.allowlist),
        }, indent=2))


# ──────────────────────────────────────────────────────────────────────
#  Low-level Telegram Bot API (urllib — no external dep)
# ──────────────────────────────────────────────────────────────────────
async def tg_call(token: str, method: str,
                  params: dict[str, Any] | None = None,
                  timeout: float = 60.0) -> dict[str, Any]:
    """Thread-offloaded HTTPS call. Returns the parsed {'ok':..., 'result':...}."""
    def _do():
        url = TG_API.format(token=token, method=method)
        data = urllib.parse.urlencode(params or {}).encode() if params else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return await asyncio.get_event_loop().run_in_executor(None, _do)


async def tg_send(token: str, chat_id: int, text: str,
                  parse_mode: str = "Markdown") -> None:
    # Telegram caps messages at 4096 chars.
    for chunk in _chunk(text, 4000):
        await tg_call(token, "sendMessage", {
            "chat_id": chat_id, "text": chunk, "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }, timeout=15)


def _chunk(s: str, n: int) -> list[str]:
    return [s[i:i + n] for i in range(0, max(len(s), 1), n)] if s else [""]


# ──────────────────────────────────────────────────────────────────────
#  Gateway
# ──────────────────────────────────────────────────────────────────────
BrainHandler = Callable[[str, dict[str, Any], float], Awaitable[dict[str, Any]]]
# Signature: (skill, params, timeout) -> Response-like dict with 'ok','result','error'


class Gateway:
    """Long-polls Telegram; dispatches /meta commands or forwards to brain."""

    def __init__(self, cfg: TgConfig,
                 send_to_brain: BrainHandler,
                 describe_status: Callable[[], dict[str, Any]],
                 list_skills: Callable[[], list[str]],
                 list_cron: Callable[[], list[dict[str, Any]]] | None = None,
                 list_jobs: Callable[[], dict[str, Any]] | None = None,
                 unblock_fn: Callable[[str, str], None] | None = None):
        self.cfg            = cfg
        self.send_to_brain  = send_to_brain
        self.describe_status = describe_status
        self.list_skills    = list_skills
        self.list_cron      = list_cron or (lambda: [])
        self.list_jobs      = list_jobs or (lambda: {"recent": [], "consecutive_failures": {}})
        self.unblock_fn     = unblock_fn or (lambda s, t: None)
        self._stop          = asyncio.Event()
        self._offset        = 0       # Telegram update_id cursor
        self._pending_pair: dict[int, str] = {}   # chat_id -> pairing code

    def request_stop(self):
        self._stop.set()

    # ── main loop ──
    async def run(self):
        log.info("telegram gateway starting (admin=%d, allowlist=%s)",
                 self.cfg.admin_chat_id, self.cfg.allowlist)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._poll_once()
                backoff = 1.0
            except Exception as e:
                log.warning("telegram poll error: %s — backoff %.1fs", e, backoff)
                try:
                    async with asyncio.timeout(backoff):
                        await self._stop.wait()
                    return
                except TimeoutError:
                    pass
                backoff = min(30.0, backoff * 2)

    async def _poll_once(self):
        # Long-polling: server holds request up to 50 s if no updates.
        r = await tg_call(self.cfg.token, "getUpdates", {
            "offset":          self._offset,
            "timeout":         50,
            "allowed_updates": json.dumps(["message"]),
        }, timeout=60)
        if not r.get("ok"):
            raise RuntimeError(f"getUpdates: {r}")
        for upd in r.get("result", []):
            self._offset = upd["update_id"] + 1
            msg = upd.get("message")
            if not msg:
                continue
            asyncio.create_task(self._handle_msg(msg))

    async def _handle_msg(self, msg: dict[str, Any]):
        chat_id = int(msg["chat"]["id"])
        text = str(msg.get("text", "")).strip()
        if not text:
            return

        # ── pairing / allowlist gate ──
        if chat_id not in self.cfg.allowlist:
            await self._handle_unknown(chat_id, text, msg)
            return

        # ── admin-only: /approve <code> ──
        if chat_id == self.cfg.admin_chat_id and text.startswith("/approve"):
            await self._handle_approve(text)
            return

        # ── meta commands ──
        low = text.lower()
        if low in ("/start", "/help", "/h"):
            await tg_send(self.cfg.token, chat_id, HELP_TEXT); return
        if low == "/status":
            await self._cmd_status(chat_id); return
        if low == "/skills":
            await self._cmd_skills(chat_id); return
        if low == "/cron":
            await self._cmd_cron(chat_id); return
        if low == "/jobs":
            await self._cmd_jobs(chat_id); return
        if low.startswith("/unblock"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or "@" not in parts[1]:
                await tg_send(self.cfg.token, chat_id,
                              "usage: `/unblock <skill>@<target>`")
                return
            skill, _, target = parts[1].partition("@")
            self.unblock_fn(skill.strip(), target.strip())
            await tg_send(self.cfg.token, chat_id,
                          f"cleared failure counter for `{skill}@{target}`")
            return
        if low == "/allowlist":
            if chat_id == self.cfg.admin_chat_id:
                await tg_send(self.cfg.token, chat_id,
                              f"Approved chats: `{sorted(self.cfg.allowlist)}`")
            else:
                await tg_send(self.cfg.token, chat_id, "not allowed")
            return

        # ── direct skill: starts with `!` ──
        if text.startswith("!"):
            await self._cmd_direct(chat_id, text[1:].strip()); return

        # ── freeform prompt ──  (forwarded to brain's prompt handler)
        await self._cmd_prompt(chat_id, text)

    # ── pairing flow ──
    async def _handle_unknown(self, chat_id: int, text: str, msg: dict):
        code = self._pending_pair.get(chat_id)
        if code is None:
            code = f"{int(time.time()) % 1_000_000:06d}"
            self._pending_pair[chat_id] = code
            name = msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name", "?")
            # DM the admin with the pairing offer.
            await tg_send(self.cfg.token, self.cfg.admin_chat_id,
                          f"*Pairing request*\n"
                          f"From: `{name}` (chat `{chat_id}`)\n"
                          f"They said: {text[:200]!r}\n\n"
                          f"Approve with:  `/approve {code}`\n"
                          f"Deny:          ignore this message")
        # Tell the stranger we've got them.
        await tg_send(self.cfg.token, chat_id,
                      f"Not on the allowlist. A pairing request with code "
                      f"`{code}` has been sent to the admin.")

    async def _handle_approve(self, text: str):
        parts = text.split()
        if len(parts) < 2:
            await tg_send(self.cfg.token, self.cfg.admin_chat_id,
                          "usage: /approve <code>")
            return
        code = parts[1]
        approved: int | None = None
        for cid, c in list(self._pending_pair.items()):
            if c == code:
                approved = cid
                break
        if approved is None:
            await tg_send(self.cfg.token, self.cfg.admin_chat_id,
                          f"no pending request with code `{code}`")
            return
        self.cfg.allowlist.add(approved)
        self.cfg.save()
        self._pending_pair.pop(approved, None)
        await tg_send(self.cfg.token, self.cfg.admin_chat_id,
                      f"approved chat `{approved}`")
        await tg_send(self.cfg.token, approved,
                      "✓ You're approved. Try /help")

    # ── built-in commands ──
    async def _cmd_status(self, chat_id: int):
        st = self.describe_status()
        body = "```\n" + json.dumps(st, indent=2, default=str) + "\n```"
        await tg_send(self.cfg.token, chat_id, "*Status*\n" + body)

    async def _cmd_skills(self, chat_id: int):
        skills = self.list_skills()
        body = "\n".join(f"• `{s}`" for s in sorted(skills)) or "(none)"
        await tg_send(self.cfg.token, chat_id, f"*Skills*\n{body}")

    async def _cmd_cron(self, chat_id: int):
        jobs = self.list_cron()
        if not jobs:
            await tg_send(self.cfg.token, chat_id, "*Cron*\n(no jobs scheduled)")
            return
        lines = []
        for j in jobs:
            lines.append(f"• `{j.get('name','?')}` — `{j.get('cron','?')}` "
                         f"→ `{j.get('skill','?')}`")
        await tg_send(self.cfg.token, chat_id, "*Cron*\n" + "\n".join(lines))

    async def _cmd_jobs(self, chat_id: int):
        data = self.list_jobs()
        recent = data.get("recent", [])
        fails  = data.get("consecutive_failures", {})
        lines = ["*Recent jobs*"]
        if not recent:
            lines.append("(no recent activity)")
        for j in recent[-10:]:
            icon = "…" if j.get("ok") is None else ("✓" if j.get("ok") else "✗")
            dur = f" {j['dur_s']}s" if j.get("dur_s") is not None else ""
            src = j.get("src", "?")
            tail = f" — {j['error']}" if (not j.get("ok") and j.get("error")) else ""
            lines.append(f"{icon} `{j.get('skill','?')}` → `{j.get('to','?')}` [{src}]{dur}{tail}")
        if fails:
            lines.append("")
            lines.append("*Blocked by Reflector*")
            for k, n in fails.items():
                if n >= 3:
                    lines.append(f"• `{k}` ({n}× in a row)  — clear: `/unblock {k}`")
        await tg_send(self.cfg.token, chat_id, "\n".join(lines))

    async def _cmd_direct(self, chat_id: int, line: str):
        """`!phone.tts text="hi"` — shlex parse + forward to brain."""
        try:
            parts = shlex.split(line)
        except ValueError as e:
            await tg_send(self.cfg.token, chat_id, f"parse error: {e}"); return
        if not parts:
            await tg_send(self.cfg.token, chat_id, "empty command"); return
        skill, kvs = parts[0], parts[1:]
        params: dict[str, Any] = {}
        for kv in kvs:
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            if v.lower() in ("true", "false"):
                params[k] = (v.lower() == "true")
            elif v.lstrip("-").isdigit():
                params[k] = int(v)
            else:
                try: params[k] = float(v) if "." in v else v
                except ValueError: params[k] = v
        resp = await self.send_to_brain(skill, params, 20.0)
        ok = resp.get("ok", False)
        icon = "✓" if ok else "✗"
        body = resp.get("error") or json.dumps(resp.get("result"), default=str)[:1500]
        await tg_send(self.cfg.token, chat_id, f"{icon} `{skill}`\n```\n{body}\n```")

    async def _cmd_prompt(self, chat_id: int, text: str):
        """Freeform text. Treat it as a natural-language prompt — punt
        to the brain's prompt handler (registered as skill 'brain.prompt')."""
        resp = await self.send_to_brain("brain.prompt", {"text": text, "chat_id": chat_id}, 60.0)
        if resp.get("ok"):
            reply = str(resp.get("result", {}).get("reply", "(ok, no reply)"))
            await tg_send(self.cfg.token, chat_id, reply)
        else:
            await tg_send(self.cfg.token, chat_id,
                          f"✗ {resp.get('error', 'brain failed')}")


HELP_TEXT = """*JARVIS over Telegram*

Freeform prompts — I'll parse and act.
  • "ring my phone for 5 seconds"
  • "text mom I'm running late"
  • "what's my battery"

Slash commands:
  `/status`              presence + last state for each node
  `/skills`              all registered skills
  `/cron`                scheduled jobs
  `/jobs`                recent activity + Reflector-blocked calls
  `/unblock <skill>@<node>`  clear Reflector failure counter
  `/help`                this message

Direct skill invocation:
  `!phone.tts text="ready when you are"`
  `!phone.ring seconds=5`
  `!pc.volume.set level=20`

Admin-only:
  `/approve <code>`    approve a pairing request
  `/allowlist`         show approved chats
"""

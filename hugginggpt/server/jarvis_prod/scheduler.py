"""JARVIS scheduler — OpenClaw-style proactive triggers.

Reads `schedule.yaml` (or schedule.json if yaml unavailable) and fires
skills at cron times. Also supports one-shot reminders ("in 30 min").

YAML format:

    # Static cron jobs
    - name: morning_briefing
      cron: "30 7 * * *"        # mm hh dom mon dow  (crontab syntax)
      to:   ian-phone
      skill: phone.tts
      params:
        text: "Good morning Ian. 7:30 AM."

    # Ranges and names supported (via croniter):
    - name: workday_lunch
      cron: "0 13 * * MON-FRI"
      to:   ian-phone
      skill: phone.tts
      params:
        text: "Lunch — step away from the screen."

    # Heartbeat — NL prompt every 30 min (OpenClaw's HEARTBEAT.md pattern).
    # The brain's 'brain.prompt' handler decides what to do (or nothing).
    - name: heartbeat
      cron: "*/30 * * * *"
      to:   ian-pc
      skill: brain.prompt
      params:
        text: "heartbeat — anything due in the next hour?"

Cron matching is delegated to `croniter` — full crontab syntax: ranges
(1-5), names (MON-FRI, JAN-DEC), lists, steps, `L`/`#`, etc.

One-shot reminders are added in-memory via `add_reminder(when_ts, ...)`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from croniter import croniter

log = logging.getLogger("sched")


# ──────────────────────────────────────────────────────────────────────
#  Job / Reminder
# ──────────────────────────────────────────────────────────────────────
@dataclass
class Job:
    name:   str
    cron:   str
    to:     str
    skill:  str
    params: dict[str, Any] = field(default_factory=dict)

    def validate(self):
        if not croniter.is_valid(self.cron):
            raise ValueError(f"invalid cron expression: {self.cron!r}")

    def matches(self, now: dt.datetime) -> bool:
        # croniter.match is second-precision; we align ticks to minute
        # boundaries and zero the seconds so the match is stable.
        t = now.replace(second=0, microsecond=0)
        return croniter.match(self.cron, t)


@dataclass
class Reminder:
    when_ts: float       # epoch seconds
    to:      str
    skill:   str
    params:  dict[str, Any] = field(default_factory=dict)
    tag:     str = ""


# ──────────────────────────────────────────────────────────────────────
#  Scheduler
# ──────────────────────────────────────────────────────────────────────
BrainFn = Callable[[str, dict[str, Any], float, str | None], Awaitable[Any]]
# (skill, params, timeout, to) -> Response


class Scheduler:
    def __init__(self, schedule_file: Path,
                 send_command: BrainFn):
        self.file         = schedule_file
        self.send_command = send_command
        self.jobs:      list[Job]      = []
        self.reminders: list[Reminder] = []
        self._stop = asyncio.Event()
        self._last_fire_minute: int = -1

    def request_stop(self):
        self._stop.set()

    def load(self):
        self.jobs = []
        if not self.file.exists():
            log.info("no schedule file at %s — starting empty", self.file)
            return
        raw = self.file.read_text(encoding="utf-8")
        parsed: list[dict[str, Any]]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore
                parsed = yaml.safe_load(raw) or []
            except ImportError:
                log.error("schedule file is not JSON and pyyaml is missing")
                return
        for d in parsed or []:
            try:
                j = Job(
                    name   =str(d["name"]),
                    cron   =str(d["cron"]),
                    to     =str(d.get("to", "")),
                    skill  =str(d["skill"]),
                    params =dict(d.get("params") or {}),
                )
                j.validate()
                self.jobs.append(j)
            except Exception as e:
                log.warning("bad job %r: %s", d, e)
        log.info("loaded %d scheduled jobs from %s", len(self.jobs), self.file)

    def add_reminder(self, when_ts: float, to: str, skill: str,
                     params: dict[str, Any] | None = None,
                     tag: str = "") -> Reminder:
        r = Reminder(when_ts=when_ts, to=to, skill=skill,
                     params=params or {}, tag=tag)
        self.reminders.append(r)
        return r

    def list_all(self) -> list[dict[str, Any]]:
        out = []
        now_dt = dt.datetime.now()
        for j in self.jobs:
            try:
                nxt = croniter(j.cron, now_dt).get_next(dt.datetime)
                in_s = max(0, int((nxt - now_dt).total_seconds()))
            except Exception:
                in_s = -1
            out.append({"type": "cron", "name": j.name, "cron": j.cron,
                        "to": j.to, "skill": j.skill, "next_in_secs": in_s})
        now = time.time()
        for r in self.reminders:
            out.append({"type": "reminder",
                        "in_secs": max(0, int(r.when_ts - now)),
                        "to": r.to, "skill": r.skill, "tag": r.tag})
        return out

    # ── main loop ──
    async def run(self):
        self.load()
        while not self._stop.is_set():
            now = time.time()
            next_min = (int(now // 60) + 1) * 60
            wait = max(0.1, next_min - now)
            try:
                async with asyncio.timeout(wait):
                    await self._stop.wait()
                return
            except TimeoutError:
                pass
            await self._tick()

    async def _tick(self):
        now = time.time()
        now_dt = dt.datetime.fromtimestamp(now)
        minute = int(now // 60)
        if minute == self._last_fire_minute:
            return
        self._last_fire_minute = minute

        # Cron jobs
        for j in self.jobs:
            try:
                if j.matches(now_dt):
                    log.info("cron fire: %s → %s.%s", j.name, j.to, j.skill)
                    asyncio.create_task(
                        self._fire(j.skill, j.params, j.to, j.name))
            except Exception as e:
                log.warning("cron match error on %s: %s", j.name, e)

        # Reminders whose time has come
        fired: list[Reminder] = []
        for r in self.reminders:
            if r.when_ts <= now:
                fired.append(r)
        for r in fired:
            self.reminders.remove(r)
            log.info("reminder fire: %s → %s.%s", r.tag or "-", r.to, r.skill)
            asyncio.create_task(self._fire(r.skill, r.params, r.to,
                                           r.tag or "reminder"))

    async def _fire(self, skill: str, params: dict[str, Any],
                    to: str, label: str):
        try:
            resp = await self.send_command(skill, params, 30.0, to or None)
            ok = getattr(resp, "ok", False) if hasattr(resp, "ok") \
                else bool(resp.get("ok")) if isinstance(resp, dict) else False
            if not ok:
                err = getattr(resp, "error", None) or (resp.get("error") if isinstance(resp, dict) else "?")
                log.warning("job %s failed: %s", label, err)
        except Exception as e:
            log.exception("job %s crashed: %s", label, e)

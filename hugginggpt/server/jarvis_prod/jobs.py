"""JARVIS job log — pentagi-inspired execution tracking.

Every outbound Command is recorded as a Job, persisted to SQLite, so
`/jobs` over Telegram can show recent activity — and so the Reflector
can detect stuck-in-loop behavior (same tool call failing 3+ times in
a row → back off) even across restarts.

DB path: `workspace/jobs.db` (defaults, overridable). One table, two
indices. ~3µs to write a row, ~20µs to read recent().

The in-memory `_run_fail` counter is the hot path for `should_skip()`.
It's hydrated from SQLite on startup so a restart doesn't reset the
Reflector's memory.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jobs")


@dataclass
class Job:
    id:       str
    skill:    str
    params:   dict[str, Any]
    to:       str
    ts_sent:  float = field(default_factory=time.time)
    ts_done:  float | None = None
    ok:       bool | None  = None        # None = in flight
    result:   Any           = None
    error:    str | None    = None
    source:   str           = "repl"     # repl | telegram | scheduler | awesome_chat

    @property
    def duration(self) -> float | None:
        return (self.ts_done - self.ts_sent) if self.ts_done else None

    def summary(self) -> str:
        state = "..." if self.ok is None else ("OK" if self.ok else "FAIL")
        dur = f"{self.duration:.1f}s" if self.duration else "-"
        return f"[{state} {dur}] {self.skill} to={self.to} src={self.source}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    skill       TEXT NOT NULL,
    target      TEXT NOT NULL,
    params_json TEXT,
    ts_sent     REAL NOT NULL,
    ts_done     REAL,
    ok          INTEGER,
    result_json TEXT,
    error       TEXT,
    source      TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_ts_sent
    ON jobs (ts_sent DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_skill_target
    ON jobs (skill, target, ts_sent DESC);
"""


def _jdump(x: Any) -> str:
    try:
        return json.dumps(x, default=str, ensure_ascii=False)
    except Exception:
        return json.dumps(repr(x))


class JobLog:
    """SQLite-persisted log + running-failure counter."""

    def __init__(self, capacity: int = 200,
                 db_path: Path | str | None = None):
        self._capacity = capacity
        default = Path(__file__).parent / "workspace" / "jobs.db"
        self._db_path = Path(db_path) if db_path else default
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,          # autocommit
            check_same_thread=False,
        )
        self._db.executescript(_SCHEMA)
        self._by_id: dict[str, Job] = {}
        # (skill, to) -> consecutive-failure count
        self._run_fail: dict[tuple[str, str], int] = {}
        self._hydrate_run_fail()

    def _hydrate_run_fail(self):
        """Rebuild consecutive-failure counters from SQLite on startup.

        For each (skill, target), walk rows newest → oldest; count the
        leading streak of ok=0 rows (terminated by any ok=1)."""
        cur = self._db.execute(
            "SELECT DISTINCT skill, target FROM jobs "
            "WHERE ok IS NOT NULL"
        )
        for skill, target in cur.fetchall():
            rows = self._db.execute(
                "SELECT ok FROM jobs "
                "WHERE skill=? AND target=? AND ok IS NOT NULL "
                "ORDER BY ts_sent DESC LIMIT 20",
                (skill, target),
            ).fetchall()
            streak = 0
            for (ok,) in rows:
                if ok == 0:
                    streak += 1
                else:
                    break
            if streak:
                self._run_fail[(skill, target)] = streak
        if self._run_fail:
            log.info("hydrated %d consecutive-failure keys from jobs.db",
                     len(self._run_fail))

    # ── writes ──
    def start(self, id: str, skill: str, params: dict[str, Any],
              to: str, source: str = "repl") -> Job:
        j = Job(id=id, skill=skill, params=params, to=to, source=source)
        self._by_id[id] = j
        self._db.execute(
            "INSERT OR REPLACE INTO jobs "
            "(id, skill, target, params_json, ts_sent, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id, skill, to, _jdump(params), j.ts_sent, source),
        )
        return j

    def finish(self, id: str, ok: bool, result: Any = None,
               error: str | None = None) -> Job | None:
        j = self._by_id.get(id)
        if j is None:
            return None
        j.ts_done = time.time()
        j.ok      = ok
        j.result  = result
        j.error   = error
        self._db.execute(
            "UPDATE jobs SET ts_done=?, ok=?, result_json=?, error=? "
            "WHERE id=?",
            (j.ts_done, 1 if ok else 0, _jdump(result), error, id),
        )
        key = (j.skill, j.to)
        if ok:
            self._run_fail.pop(key, None)
        else:
            self._run_fail[key] = self._run_fail.get(key, 0) + 1
        return j

    # ── reads ──
    def consecutive_failures(self, skill: str, to: str) -> int:
        return self._run_fail.get((skill, to), 0)

    def should_skip(self, skill: str, to: str, threshold: int = 3) -> bool:
        """Reflector check — if a skill has failed N+ times in a row on
        this target, back off until the next manual run clears it."""
        return self.consecutive_failures(skill, to) >= threshold

    def clear_failures(self, skill: str | None = None, to: str | None = None):
        if skill is None and to is None:
            self._run_fail.clear(); return
        drop = [k for k in self._run_fail
                if (skill is None or k[0] == skill)
                and (to    is None or k[1] == to)]
        for k in drop:
            self._run_fail.pop(k, None)

    def recent(self, n: int = 10) -> list[Job]:
        rows = self._db.execute(
            "SELECT id, skill, target, params_json, ts_sent, ts_done, "
            "       ok, result_json, error, source "
            "FROM jobs ORDER BY ts_sent DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [Job(
            id=r[0], skill=r[1], to=r[2],
            params=json.loads(r[3]) if r[3] else {},
            ts_sent=r[4], ts_done=r[5],
            ok=(None if r[6] is None else bool(r[6])),
            result=(json.loads(r[7]) if r[7] else None),
            error=r[8], source=r[9] or "repl",
        ) for r in rows]

    def history(self, skill: str | None = None, to: str | None = None,
                limit: int = 50) -> list[Job]:
        """Filtered history — powers `/history <skill>` queries."""
        q = ("SELECT id, skill, target, params_json, ts_sent, ts_done, "
             "       ok, result_json, error, source "
             "FROM jobs WHERE 1=1")
        args: list[Any] = []
        if skill is not None:
            q += " AND skill=?"; args.append(skill)
        if to is not None:
            q += " AND target=?"; args.append(to)
        q += " ORDER BY ts_sent DESC LIMIT ?"; args.append(limit)
        rows = self._db.execute(q, tuple(args)).fetchall()
        return [Job(
            id=r[0], skill=r[1], to=r[2],
            params=json.loads(r[3]) if r[3] else {},
            ts_sent=r[4], ts_done=r[5],
            ok=(None if r[6] is None else bool(r[6])),
            result=(json.loads(r[7]) if r[7] else None),
            error=r[8], source=r[9] or "repl",
        ) for r in rows]

    def to_status(self) -> dict[str, Any]:
        recent = self.recent(10)
        return {
            "recent": [
                {"skill": j.skill, "to": j.to, "ok": j.ok,
                 "dur_s": round(j.duration, 2) if j.duration else None,
                 "error": j.error, "src": j.source}
                for j in recent
            ],
            "consecutive_failures": {
                f"{s}@{t}": n for (s, t), n in self._run_fail.items() if n > 0
            },
        }

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass

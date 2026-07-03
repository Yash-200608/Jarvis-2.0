"""Reflexion loop — Planner ⇄ Critic ⇄ Executor hand-off.

Flow per request
----------------
    sensor_event -> Planner (Llama 3) -> Plan(JSON)
                  -> Critic (Mistral) -> {approved, safety_score, notes}
                  -> Executor (PC or MQTT-Phone)
                  -> append to history.log + RAG memory

Circuit Breaker
---------------
The Critic is trusted to reject unsafe plans. If it rejects three plans in a
row for the same originating request, we:

1. Stop iterating — Jarvis does NOT keep trying to convince Mistral.
2. Append a ``NEED_ATTENTION.md`` file describing the impasse.
3. Publish an alert on ``jarvis/alerts`` so the phone can buzz the user.
4. Wait for a human to clear the flag by deleting the file.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .config import JarvisConfig
from .executor import ExecutionError, Executor
from .logging_setup import get_logger
from .memory import MemoryStore
from .ollama_client import OllamaClient, OllamaError
from jarvis_node_sdk.sandbox import is_inside_sandbox
from jarvis_node_sdk.sensors import SensorEvent
from jarvis_node_sdk.transport import MqttTransport

planner_log = get_logger("PLANNER")
critic_log = get_logger("CRITIC")
brain_log = get_logger("BRAIN")

PlanTarget = Literal["PC", "Phone"]


# ---------------------------------------------------------------------- #
# Typed Plan schema — both the Planner and Critic must produce/consume   #
# these shapes. Pydantic validates them before anything touches disk.    #
# ---------------------------------------------------------------------- #


class Plan(BaseModel):
    thought: str = Field(..., description="Chain-of-thought reasoning")
    tools: list[str] = Field(
        default_factory=list, description="Ordered tool names"
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    target: PlanTarget = "PC"


class CriticReport(BaseModel):
    approved: bool
    safety_score: int = Field(..., ge=0, le=10)
    violations: list[str] = Field(default_factory=list)
    revision_hint: str = ""


@dataclass
class ReflexionRecord:
    request_id: str
    request_text: str
    plans: list[Plan] = field(default_factory=list)
    critiques: list[CriticReport] = field(default_factory=list)
    final: Plan | None = None
    resolved: bool = False


# ---------------------------------------------------------------------- #
# Prompts                                                                #
# ---------------------------------------------------------------------- #

_PLANNER_SYSTEM = """You are the Planner of Jarvis-Core, a distributed \
assistant. Given a user request, relevant sensor context, and short-term \
history, produce a SINGLE JSON object — no prose around it — with exactly \
these keys:

  {
    "thought": "<short reasoning>",
    "tools":   ["<tool_1>", "<tool_2>"],
    "payload": { ... per-tool args, keyed by tool name or a single flat map },
    "target":  "PC" | "Phone"
  }

Available PC tools: write_file, read_file, list_dir, run_shell, pyautogui.
Available Phone tools: open_app, take_screenshot, send_sms, send_whatsapp,
  make_call, get_battery, get_device_info, set_volume, tap, swipe, type_text.

Rules:
- File paths MUST be relative; ALL file IO happens inside ./sandbox/.
- Pick the single best plan. Do not over-plan multi-stage flows; one atomic
  step per call — the brain will iterate.
- If the request is ambiguous, ask for clarification via the "notify" tool
  with payload {"message": "<question>", "target": "PC"}.
- NEVER include network shell commands that reach outside the sandbox."""


_CRITIC_SYSTEM = """You are the adversarial Critic of Jarvis-Core. Your job \
is to audit the Planner's JSON plan for:

- Security: any file path OUTSIDE ./sandbox/, shell commands that touch
  the host system, unauthorised network calls, unsigned cross-device traffic.
- Logic: missing arguments, plan that cannot satisfy the user's intent,
  contradictions with sensor context.
- Hardware safety: commands that could brick the phone, drain battery
  below 5%, or trigger irreversible actions (factory reset, wipe).

Return a SINGLE JSON object — no prose — with exactly these keys:

  {
    "approved":      true|false,
    "safety_score":  0..10,   (10 = flawless, 0 = catastrophic)
    "violations":    ["<short reason>", ...],
    "revision_hint": "<one-line suggestion for the planner>"
  }

Reject (approved=false) if safety_score < 8 OR any violation is present."""


# ---------------------------------------------------------------------- #
# Brain                                                                  #
# ---------------------------------------------------------------------- #


class Brain:
    """Coordinates sensors -> Planner -> Critic -> Executor."""

    def __init__(
        self,
        cfg: JarvisConfig,
        bus: MqttTransport,
        ollama: OllamaClient,
        executor: Executor,
        memory: MemoryStore | None,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._ollama = ollama
        self._executor = executor
        self._memory = memory

        self._history: list[str] = self._load_history()
        self._need_attention = (
            Path(cfg.brain.knowledge_graph).parent / "NEED_ATTENTION.md"
        )

    # ------------------------------------------------------------------ #
    # Public: consume sensor events                                      #
    # ------------------------------------------------------------------ #

    async def run(self, events: asyncio.Queue[SensorEvent]) -> None:
        """Main loop — pulls from the sensor queue indefinitely."""
        while True:
            event = await events.get()
            if self._need_attention.exists():
                brain_log.warning(
                    "NEED_ATTENTION.md present — refusing to process events "
                    "until a human clears it."
                )
                continue
            try:
                await self._handle_event(event)
            except Exception as exc:  # noqa: BLE001
                brain_log.exception(f"Brain error: {exc!r}")

    # ------------------------------------------------------------------ #
    # Per-event workflow                                                 #
    # ------------------------------------------------------------------ #

    async def _handle_event(self, event: SensorEvent) -> None:
        # Only promote "inbox" events into full Reflexion cycles — system
        # metrics just feed context; MQTT events from the phone are recorded
        # but not auto-acted upon (to avoid feedback loops).
        if event.source != "inbox":
            await self._record_context(event)
            return

        text = event.payload.get("text", "").strip()
        if not text:
            return

        record = ReflexionRecord(
            request_id=uuid.uuid4().hex, request_text=text
        )

        for attempt in range(1, self._cfg.brain.critic_rejection_threshold + 1):
            plan = await self._plan(text, previous=record)
            record.plans.append(plan)

            report = await self._critique(plan)
            record.critiques.append(report)

            if report.approved and report.safety_score >= self._cfg.brain.critic_min_safety_score:
                if not self._plan_passes_sandbox(plan):
                    # Belt-and-braces: even if Mistral said OK, our sandbox
                    # checker has the final word.
                    brain_log.warning(
                        "Plan passed Critic but failed sandbox check — rejecting"
                    )
                    continue
                record.final = plan
                await self._dispatch(record, plan)
                record.resolved = True
                break

            critic_log.warning(
                f"Plan {attempt}/{self._cfg.brain.critic_rejection_threshold} "
                f"rejected: score={report.safety_score} "
                f"violations={report.violations}"
            )

        if not record.resolved:
            await self._trip_circuit_breaker(record)

        self._append_history(record)

    # ------------------------------------------------------------------ #
    # Planner                                                            #
    # ------------------------------------------------------------------ #

    async def _plan(
        self, request_text: str, *, previous: ReflexionRecord
    ) -> Plan:
        recalled = []
        if self._memory is not None:
            try:
                recalled = await self._memory.recall(request_text, k=3)
            except Exception as exc:  # noqa: BLE001
                brain_log.warning(f"Memory recall failed: {exc!r}")

        prompt = self._build_planner_prompt(request_text, previous, recalled)
        planner_log.info(f"Planning: {request_text!r}")

        raw = await self._ollama.generate_json(
            self._cfg.ollama.planner_model,
            prompt,
            system=_PLANNER_SYSTEM,
        )
        try:
            return Plan.model_validate(raw)
        except ValidationError as exc:
            planner_log.error(f"Planner produced invalid JSON: {exc!r}")
            # Return a safe no-op so the Critic will reject it cleanly.
            return Plan(
                thought="planner returned malformed JSON",
                tools=["notify"],
                payload={
                    "message": "Planner produced invalid JSON; please rephrase"
                },
                target="PC",
            )

    def _build_planner_prompt(
        self,
        request: str,
        previous: ReflexionRecord,
        recalled: list[dict[str, Any]],
    ) -> str:
        lines = [f"REQUEST: {request}", ""]
        if self._history:
            lines.append("RECENT HISTORY:")
            lines.extend(f"- {h}" for h in self._history[-10:])
            lines.append("")
        if recalled:
            lines.append("LONG-TERM MEMORY HITS:")
            for r in recalled:
                lines.append(f"- {r['text'][:200]}")
            lines.append("")
        if previous.critiques:
            last = previous.critiques[-1]
            lines.append("PREVIOUS PLAN REJECTED:")
            lines.append(f"- violations: {last.violations}")
            lines.append(f"- hint: {last.revision_hint}")
            lines.append("")
        lines.append("Return JSON now.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Critic                                                             #
    # ------------------------------------------------------------------ #

    async def _critique(self, plan: Plan) -> CriticReport:
        critic_log.info(
            f"Auditing plan target={plan.target} tools={plan.tools}"
        )
        raw = await self._ollama.generate_json(
            self._cfg.ollama.critic_model,
            json.dumps(plan.model_dump(), separators=(",", ":")),
            system=_CRITIC_SYSTEM,
        )
        try:
            return CriticReport.model_validate(raw)
        except ValidationError as exc:
            critic_log.error(f"Critic produced invalid JSON: {exc!r}")
            return CriticReport(
                approved=False,
                safety_score=0,
                violations=["critic output malformed"],
                revision_hint="Critic JSON invalid — retry",
            )

    # ------------------------------------------------------------------ #
    # Dispatch                                                           #
    # ------------------------------------------------------------------ #

    async def _dispatch(self, record: ReflexionRecord, plan: Plan) -> None:
        for tool in plan.tools:
            # Payload may be a single flat map or keyed by tool name — handle both.
            params = plan.payload.get(tool) or plan.payload
            if not isinstance(params, dict):
                params = {"value": params}
            try:
                result = await self._executor.execute(plan.target, tool, params)
                brain_log.info(
                    f"✓ {plan.target}:{tool} ok={result.get('ok', True)}"
                )
            except (ExecutionError, Exception) as exc:  # noqa: BLE001
                brain_log.exception(f"Execution failed: {exc!r}")
                await self._bus.publish(
                    self._cfg.mqtt.topics.alerts,
                    {
                        "level": "error",
                        "request_id": record.request_id,
                        "tool": tool,
                        "error": repr(exc),
                    },
                )

    # ------------------------------------------------------------------ #
    # Sandbox belt-and-braces                                            #
    # ------------------------------------------------------------------ #

    def _plan_passes_sandbox(self, plan: Plan) -> bool:
        for tool in plan.tools:
            params = plan.payload.get(tool) or plan.payload
            if not isinstance(params, dict):
                continue
            for key in ("path", "cwd", "file", "src", "dst"):
                val = params.get(key)
                if val and not is_inside_sandbox(self._cfg.sandbox.root, val):
                    brain_log.warning(
                        f"Rejected plan — {tool!r} references {val!r} "
                        f"outside sandbox"
                    )
                    return False
        return True

    # ------------------------------------------------------------------ #
    # Circuit Breaker                                                    #
    # ------------------------------------------------------------------ #

    async def _trip_circuit_breaker(self, record: ReflexionRecord) -> None:
        brain_log.critical(
            f"Circuit breaker tripped for request {record.request_id!r}"
        )
        summary = [
            "# NEED_ATTENTION",
            "",
            f"- request_id: `{record.request_id}`",
            f"- request: {record.request_text}",
            f"- rejections: {len(record.critiques)}",
            "",
            "## Last critic report",
            "```json",
            json.dumps(
                record.critiques[-1].model_dump(), indent=2
            ) if record.critiques else "{}",
            "```",
            "",
            "## Last plan",
            "```json",
            json.dumps(
                record.plans[-1].model_dump(), indent=2
            ) if record.plans else "{}",
            "```",
            "",
            "Delete this file once you have resolved the issue — Jarvis-Core "
            "will refuse to process new events until it is gone.",
        ]
        self._need_attention.parent.mkdir(parents=True, exist_ok=True)
        self._need_attention.write_text("\n".join(summary), encoding="utf-8")

        await self._bus.publish(
            self._cfg.mqtt.topics.alerts,
            {
                "level": "critical",
                "code": "CIRCUIT_BREAKER",
                "request_id": record.request_id,
                "ts": time.time(),
            },
            retain=True,
        )

    # ------------------------------------------------------------------ #
    # History / memory                                                   #
    # ------------------------------------------------------------------ #

    def _load_history(self) -> list[str]:
        p = Path(self._cfg.brain.history_log)
        if not p.is_file():
            return []
        return [line.strip() for line in p.read_text("utf-8").splitlines() if line.strip()]

    def _append_history(self, record: ReflexionRecord) -> None:
        entry = {
            "ts": time.time(),
            "rid": record.request_id,
            "req": record.request_text,
            "final": record.final.model_dump() if record.final else None,
            "resolved": record.resolved,
        }
        line = json.dumps(entry, separators=(",", ":"))
        self._history.append(line)
        p = Path(self._cfg.brain.history_log)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    async def _record_context(self, event: SensorEvent) -> None:
        if self._memory is None:
            return
        try:
            await self._memory.remember(
                f"[{event.source}] {json.dumps(event.payload)[:500]}",
                kind=event.source,
            )
        except Exception as exc:  # noqa: BLE001
            brain_log.debug(f"memory.remember failed: {exc!r}")

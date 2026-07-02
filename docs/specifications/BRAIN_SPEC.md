# Brain Specification

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

## Purpose of This Document

This document defines what the **Brain** component is supposed to do, per the layered
diagram in the root [`CLAUDE.md`](../../CLAUDE.md), and reconciles that target against
what actually exists in the repository today. It is the specification future Claude Code
sessions should read before touching any of the three candidate "Brain" implementations
described below. It does not propose new code; it defines responsibilities, evaluates
existing partial implementations against those responsibilities, and states a
recommendation.

> **Terminology note:** this document evaluates candidate implementations against the Brain's responsibilities. For crisp, stable definitions of the vocabulary used throughout (Brain, Client, Node, Agent, Tool, Skill, Service, Memory, Orchestration, Context, Identity) and the invariants that must never be silently violated, see [`../brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md) — the terminology there and here is kept consistent by design.

Related documents:
- [`../brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md) — terminology and invariants this specification's vocabulary must stay consistent with.
- [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) — where the Brain sits in the overall Jarvis/Brain/MQTT layering.
- [`architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) — detail on the Agent Manager / orchestration responsibility defined here.
- [`architecture/MEMORY_ARCHITECTURE.md`](../architecture/MEMORY_ARCHITECTURE.md) — detail on the Memory Manager responsibility defined here.
- [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md) — detail on the permissions/security responsibility, including the root `core/` safety-bug finding referenced below.
- [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) — detail on tool routing / skill dispatch, including the missing `pc.*` executor.
- [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) — the phased plan (Phase 0 through Phase 6) that operationalizes the recommendation in this document.
- [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) — point-in-time status of each candidate.
- [`context/DECISION_LOG.md`](../context/DECISION_LOG.md) — where the ADR for "which island is canonical" should be recorded once made.

---

## 1. What CLAUDE.md Specifies

The root `CLAUDE.md` diagram places the Brain as the cognition layer, directly beneath
Jarvis and above the MQTT/Tailscale mesh:

```
JARVIS LAYER        ← lightweight, conversational shell
  │
  ▼
BRAIN LAYER         ← memory, reasoning, agent orchestration
  │
  ├── Memory Manager     (SQLite / PostgreSQL / VectorDB)
  ├── Context Manager
  ├── Agent Manager
  ├── Knowledge Layer
  └── Device Registry
  │
  ▼
MQTT (over Tailscale)
```

CLAUDE.md's design principles state plainly: **"Brain owns memory. Agents never access
databases directly."** and **"Jarvis stays lightweight... no orchestration logic."** This
is the load-bearing architectural rule for this document: whichever implementation is
promoted to "the Brain" must be the single place that owns memory, reasoning, and
orchestration state — not one of several places that each own a fragment of it.

### 1.1 Responsibility Breakdown

Decomposing CLAUDE.md's five boxes plus the cross-cutting concerns implied by "memory,
reasoning, agent orchestration" gives thirteen concrete responsibilities the Brain must
fulfill:

| # | Responsibility | CLAUDE.md source | What it means concretely |
|---|---|---|---|
| 1 | **Identity** | implicit (single Brain, "owns memory") | One addressable process/service that is the system's single source of truth — not three parallel processes each claiming the role. |
| 2 | **Memory** | Memory Manager box | Persistent storage of facts, conversation history, and embeddings across sessions and devices (SQLite / PostgreSQL / VectorDB per CLAUDE.md). |
| 3 | **Reasoning** | "memory, reasoning, agent orchestration" | A mechanism that turns a request into a considered response/plan, not a fixed lookup table of regex matches. |
| 4 | **Planning** | Agent Manager box (implied) | Decomposing a goal into ordered steps/tool calls before execution. |
| 5 | **Reflection** | implicit in "reasoning" + fault-tolerance principle | Self-checking output before or after acting; detecting repeated failure and stopping rather than looping forever. |
| 6 | **Permissions** | "Agents never access databases directly" + fault-tolerance | Gating what an agent/tool is allowed to do, and requiring confirmation for high-risk actions. |
| 7 | **Security** | Tailscale/MQTT principles + implicit | Authenticating nodes, scoping what each node can command, protecting credentials. |
| 8 | **Agent orchestration** | Agent Manager box | Coordinating one or more agents/tools to fulfill a request, tracking their state. |
| 9 | **Tool routing** | Agent Manager box (implied) | Mapping an intent/skill name to the correct executor on the correct node. |
| 10 | **Context management** | Context Manager box | Tracking what is currently relevant (active conversation, device state, recent actions) as distinct from long-term memory. |
| 11 | **Knowledge graph** | Knowledge Layer box | Structured, queryable relationships between entities (people, devices, facts), not just a flat log. |
| 12 | **Goal management** | Agent Manager box (implied) | Tracking outstanding goals/tasks across turns, not just single-shot request/response. |
| 13 | **Device registry** | Device Registry box | Knowing what nodes exist (PC, phone, Arduino, future ESP32/tablet nodes per CLAUDE.md's hardware inventory), their capabilities, and their live/offline state. |

None of the three existing Python islands in this repo fulfill all thirteen. Two of them
fulfill disjoint subsets. This is the central finding of this document.

---

## 2. Candidate Evaluation

Per the synthesis audit, there are two genuine partial-Brain candidates plus one
kernel-level dependency. The third island (the legacy `hugginggpt/server/awesome_chat.py`
stack) is a Flask/HuggingFace/Claude command router driven daily over ADB — it is in
real daily use but was not built against CLAUDE.md's Brain model at all and is out of
scope for this comparison; see [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md)
for its status.

### 2.1 Candidate A — `jarvis_core/core/brain.py` ("jarvis_core")

Location: `jarvis_core/core/brain.py`, alongside `jarvis_core/core/executor.py`,
`jarvis_core/core/memory.py`, `jarvis_core/core/ollama_client.py`,
`jarvis_core/core/scheduler.py`, `jarvis_core/core/sandbox.py`,
`jarvis_core/core/signing.py`.

**Architecture.** A Reflexion-style loop: Planner (Llama3 via local Ollama) proposes a
plan, Critic (Mistral via local Ollama) reviews it, Executor carries it out. A 3-strike
circuit breaker escalates to a human by writing `NEED_ATTENTION.md` if the loop fails
repeatedly. Memory is ChromaDB-backed RAG using `nomic-embed-text` embeddings
(`jarvis_core/core/memory.py`). A `knowledge_graph.md` file exists as a target artifact
for the Knowledge Layer responsibility.

**Responsibility coverage (structural, not runtime-proven):**

| Responsibility | Coverage | Notes |
|---|---|---|
| Reasoning | Strong (by design) | Planner/Critic split using two different models specifically so one cannot rubber-stamp the other. |
| Planning | Strong (by design) | Planner role is explicit and separate from execution. |
| Reflection | Strong (by design) | 3-strike circuit breaker + `NEED_ATTENTION.md` human-in-the-loop escalation is the closest thing in the repo to CLAUDE.md's fault-tolerance principle. |
| Permissions | Strong (by design) | `sandbox.safe_path()` is used as a single chokepoint by both the Executor and a post-Critic-approval re-check. |
| Memory | Present in design, unproven in practice | ChromaDB RAG via `memory.py`, but **no `brain/chroma/` directory has ever been created** — zero embeddings have ever been persisted. |
| Knowledge graph | Present in design, unproven in practice | `knowledge_graph.md` exists but is still template-only — the hourly `MaintenanceScheduler` that would populate it has never run. |
| Agent orchestration | Single-agent only | The Planner/Critic/Executor pipeline is linear, not a manager coordinating multiple concurrent agents. |
| Device registry | Absent | `jarvis_core/agent/` (phone_executor.py) targets only one node; no registry of PC/phone/Arduino/future nodes. |
| Context management | Weak/unproven | No evidence of session-to-session context tracking distinct from the RAG memory layer. |
| Tool routing | Present, narrow | `core/tools.py`-equivalent skill dispatch exists for the Executor's own action set, not a cross-node router. |
| Security | Present, unproven | `signing.py` exists for message signing but has never been exercised against live infrastructure. |
| Goal management | Weak | Single-request Reflexion loop; no persistent cross-turn goal tracking observed. |
| Identity | N/A | Never run as a standing service. |

**Runtime status: never run end-to-end.** Committed once in commit `5dfa65f` ("V1") and
never touched again through `4b32760` ("V2") and `fe41bf5` ("V2.1"). Concretely: no
`.env`, no `history.log`, no `logs/` directory, no `brain/chroma/` directory,
`knowledge_graph.md` still contains only its template header, and `NEED_ATTENTION.md` has
never been written by the scheduler. There is no observable evidence this code has ever
executed a full cycle against live Ollama models or a live MQTT broker.

**Verdict.** Structurally, this is the closest match in the repository to the full Brain
spec's reasoning/planning/reflection/permissions requirements — but it is a design on
paper, not a validated system. See Phase 2 of
[`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) for what "proven" would mean.

### 2.2 Candidate B — `hugginggpt/server/jarvis_prod/brain_pc.py` ("jarvis_prod")

Location: `hugginggpt/server/jarvis_prod/brain_pc.py`, alongside `agent_phone.py`,
`protocol.py`, `jobs.py`, `scheduler.py`, `telegram_gateway.py`,
`mosquitto.conf.example`, `_smoke.py`.

**Architecture.** An asyncio process using `aiomqtt` (MQTT v5, QoS 1) over a Mosquitto
broker, intended to run over Tailscale. It correlates outbound commands with inbound
responses, tracks phone presence/state topics, and dispatches to a fixed set of known
skills (`_known_skills` registry, referencing both `phone.*` and `pc.*` names). Its
`_brain_prompt` NL-handling path is regex-based, with a soft optional import of the
legacy stack's `awesome_chat.py` for natural-language parsing.

**Responsibility coverage:**

| Responsibility | Coverage | Notes |
|---|---|---|
| Agent orchestration | Present, real | Genuine request/response correlator across MQTT with QoS 1 delivery; asymmetric `clean_start` semantics tuned per role (Brain fails fast, phone queues while asleep). |
| Tool routing | Present, partial | `_known_skills` registry maps to `phone.*` skills (implemented in `agent_phone.py`) and `pc.*` skills (**referenced but never implemented — no receiving agent exists**). |
| Device registry | Minimal | Presence/state topics exist per node in `protocol.py`'s scheme, but only one real node (the phone) is ever addressed; nothing instantiates OPPO/Vivo/Arduino/future nodes. |
| Reasoning | Weak | `_brain_prompt`'s NL handling is regex built-ins, not a planning/reasoning engine; it is a command dispatcher, not a Brain in the CLAUDE.md sense. |
| Planning | Absent | No multi-step plan decomposition; each command maps directly to one skill call. |
| Reflection | Partial | `jobs.py`'s Reflector circuit breaker exists and correctly rehydrates its consecutive-failure state from SQLite on restart, but this is failure-counting for a heartbeat watchdog, not reasoning-level self-critique. |
| Permissions | Absent | No risk/confirmation gating layer of its own — this is exactly the gap root `core/` is meant to fill underneath it. |
| Memory | Absent | Only a SQLite `JobLog` for execution history exists (`jobs.py`); no semantic/long-term memory, no RAG, nothing CLAUDE.md's Memory Manager would recognize. |
| Knowledge graph | Absent | Nothing exists. |
| Context management | Minimal | Presence/state tracking only; no conversational context carried across turns. |
| Security | Partial | Runs over Tailscale (relies entirely on its encryption — no MQTT TLS configured; `mosquitto.conf.example`'s ACL is a single flat `jarvis/#` grant for one user, no per-node ACLs). |
| Goal management | Absent | No persistent goal/task tracking beyond the immediate command in flight. |
| Identity | Real but unvalidated | Runs as a standing asyncio process — the only candidate with a defined process lifecycle — but has never been deployed live. |

**Runtime status: smoke-tested, never live.** `_smoke.py` spins up a real Brain and a
fake phone agent through a real MQTT broker and passes — this is a genuine integration
test, the only one of its kind across all three islands. However, `jarvis_prod` has
**never had a live Tailscale run**: `mosquitto.conf.example` still uses the placeholder
IP `100.64.0.1`, and the system requires Python 3.12 (needs 3.11+ for `TaskGroup`/`except*`)
as the system interpreter, distinct from the legacy stack's Python 3.10 `jarvis_env` venv.

**Verdict.** This is really an **MQTT correlator + scheduler + dispatcher**, not a
reasoning engine. It is the only candidate that is actually deployable in the sense of
having a tested process lifecycle and transport layer, but it has zero memory layer and
no planning/reflection/permission logic of its own.

### 2.3 Supporting kernel — root `core/` (execution-safety layer, not a Brain candidate)

Location: root `core/` — `executor.py`, `safety.py`, `risk.py`, `tools.py`,
`validator.py`, `sandbox.py`, `idempotency.py`, `undo.py`, `state.py`, `retry.py`,
`config.py`, `memory.py` (unused dead code within this module), plus tests in
`core/tests/test_execution_safety.py`, `tests/test_adversarial_execution.py`, and
`tests/test_executor_safety.py` (565 lines of tests against 512 lines of implementation).

This is **not a third Brain candidate** — it implements none of memory, reasoning,
planning, or orchestration. It is a dormant, adversarially-tested action-safety kernel
(idempotency, sandboxing, undo, risk classification, confirmation-gating) that is
imported by nothing outside its own test files. It is listed here because it is the
correct foundation for responsibility #6 (Permissions) and #7 (Security) in the target
architecture below — but only after a confirmed live bug is fixed.

**Confirmed live bug:** in `core/executor.py`, `safety.apply_safety()` runs **before**
`risk.get_risk_level()`. `safety.apply_safety()` rewrites `delete_file` (high risk) to
`move_to_trash` (medium risk) before the risk check ever sees the original action name —
so a high-risk `delete_file` call is silently downgraded and executes with **zero
confirmation**. This has been reproduced by direct execution and is covered by three
currently-failing tests: `test_high_risk_action_requires_confirmation_before_safety_rewrite`,
`test_confirmation_bypass_attack_is_rejected`, and
`test_external_confirmation_does_not_bypass_safeguards`. Full detail is in
[`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md).

**Naming collision:** root `core/` and `jarvis_core/core/` are both importable as bare
`core` depending on `sys.path` order. `jarvis_core/tests/conftest.py` inserts
`jarvis_core/` onto `sys.path` specifically so its own imports resolve internally,
never touching root `core/` — but this is a latent footgun for any future refactor that
changes path ordering. Root `core/` must be renamed before it is wired into anything
production-facing.

---

## 3. Recommendation: Merge, Not Pick-One-Discard-One

Neither candidate satisfies the full Brain specification alone, and their gaps are
**complementary, not overlapping**:

- `jarvis_core` has the reasoning/planning/reflection/permissions architecture CLAUDE.md's
  Brain requires, but no proven transport, no proven memory, and has never run.
- `jarvis_prod` has the proven, tested transport/orchestration layer CLAUDE.md's Brain
  needs to actually communicate with nodes, but no reasoning engine and no memory layer.

The target Brain is therefore a **merge**:

```
Target Brain
├── Transport / orchestration core   ← from jarvis_prod (brain_pc.py + protocol.py)
│     proven deployable: real MQTT integration test (_smoke.py),
│     correct asymmetric clean_start semantics, Windows-correct asyncio wiring
│
├── Reasoning path                   ← from jarvis_core (Planner/Critic/Executor)
│     grafted onto the transport core as its planning/reflection engine —
│     NOT a competing brain process; replaces brain_pc.py's regex _brain_prompt
│
├── Memory Manager                   ← from jarvis_core (ChromaDB RAG, memory.py)
│     only after it has actually been exercised end-to-end at least once
│     (see Phase 2, roadmap/MASTER_ROADMAP.md) — currently zero embeddings ever persisted
│
└── Action-safety kernel             ← from root core/, POST safety-bug-fix and rename
      (idempotency, sandbox, undo, risk, confirmation-gating)
      sits underneath the merged Executor; TOOLS registry re-mapped from its
      generic desktop-agent surface (open_app, delete_file, send_sms, shutdown_device)
      onto Chimera's actual skill names (phone.*, pc.*)
```

This satisfies CLAUDE.md's "Brain owns memory, agents never access databases directly"
principle only once the merge is complete — today, memory is either unproven
(`jarvis_core`) or absent (`jarvis_prod`), so neither candidate alone should be described
as "the Brain" in any downstream document or code comment.

### 3.1 Non-negotiable preconditions before merge work starts

1. Fix the `core/executor.py` safety/risk ordering bug and get all 33 `core/` tests
   green. Do this before `core/` is wired into anything.
2. Rename root `core/` to eliminate the `sys.path` collision with `jarvis_core/core/`.
3. Run `jarvis_core`'s Reflexion loop through at least one full observed end-to-end
   cycle (live Ollama, live Mosquitto, a real `MaintenanceScheduler` pass producing
   real `history.log` / `knowledge_graph.md` / `brain/chroma/` content) before trusting
   its reasoning/memory layers as the basis for the merge.
4. Do not touch `hugginggpt/server/awesome_chat.py` (the legacy stack) during this
   work — it is the only system in daily use and is out of scope for the Brain merge
   until the merged Brain has demonstrated it can replace it for the currently-used
   command set.

Full phasing (Phase 0 through Phase 6, with exit criteria for each) is in
[`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md).

### 3.2 What "done" looks like

The merged Brain is considered to satisfy this specification when:

- It runs as a single standing process (or tightly-coupled process group) that is the
  system's one source of truth for memory — no other component queries a database or
  vector store directly (per CLAUDE.md's "Agents never access databases directly").
- Every responsibility in the table in Section 1 has both a named owning module
  (from the merge above) and at least one observed, logged instance of it firing in
  a live run — not just a passing unit test.
- The `pc.*` skill gap is closed: `brain_pc.py`'s `_known_skills`,
  `telegram_gateway.py`'s help text, and `schedule.example.json` currently reference
  `pc.system.lock`, `pc.shell.run`, etc. with no receiving agent; see
  [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) for the plan to build
  this on top of the (fixed, renamed) action-safety kernel.
- The Device Registry responsibility (currently absent in both candidates) is
  implemented against CLAUDE.md's actual hardware inventory: Main Desktop PC, Nothing
  Phone 3a Lite (active daily node via Termux/SSH), OPPO smartphone (spare), Vivo 1901
  (spare), Arduino UNO R4 WiFi (active), and the salvaged tablet components earmarked as
  a future display/voice terminal.

---

## 4. Open Questions

- TBD - confirm with user: target hosting model for the merged Brain (always-on
  desktop service vs. on-demand process) once Phase 3 of the roadmap begins.
- TBD - confirm with user: whether `jarvis_core`'s Ollama models (Llama3 for Planner,
  Mistral for Critic) are the final model choices, or placeholders chosen for local
  availability during prototyping.
- TBD - confirm with user: rotation status of the three previously-leaked secrets
  (Gmail app password, ElevenLabs key, GitHub PAT) flagged by the live README advisory
  — this audit could not confirm whether rotation has occurred.
- TBD - confirm with user: final target skill-name mapping when root `core/`'s
  TOOLS registry (`open_app`, `delete_file`, `send_sms`, `shutdown_device`, etc.) is
  re-mapped onto `phone.*` / `pc.*` namespaces.

# Agent Architecture

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

## Purpose and Scope

This document defines the target multi-agent architecture for the Chimera/JARVIS
Brain — the set of specialized agents the system will eventually run, how they
are supervised, and how they communicate. It also documents, in detail, the one
piece of code in this repository that is a genuine (if never-executed) prototype
of this pattern — `jarvis_core/core/brain.py` — and explains why the recommended
path is to **evolve that reflexion loop**, not discard it in favor of a
green-field rewrite.

This is a planning document. Nothing described here as "the target architecture"
is implemented today. Where a capability does not exist yet, this document says
so explicitly rather than describing it as if it were built.

Related documents:
- [`../vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md) — the AI Operating
  System ambition this architecture serves.
- [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) — product
  invariants (e.g. Brain owns memory, Jarvis stays lightweight).
- [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) — overall layering of
  Jarvis / Brain / MQTT / nodes.
- [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md) — the Memory Manager /
  RAG design that agents in this document read from and write to.
- [`SECURITY_MODEL.md`](SECURITY_MODEL.md) — the safety-kernel and
  confirmation-gating model referenced under Executor Safety below.
- [`TOOL_ROUTING.md`](TOOL_ROUTING.md) — how a Plan's `tools` list maps to
  concrete skill implementations (`phone.*`, `pc.*`, etc.).
- [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md) — the
  detailed spec for the Brain process this document's agents run inside of.
- [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) — phase
  sequencing; agent-architecture work is explicitly gated behind Phase 1-2
  (decide-the-brain, prove-jarvis_core-runs) per that roadmap.
- [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md) — repo-wide
  inventory this document assumes as background.
- [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) — where the
  "evolve jarvis_core, don't rewrite" decision recorded in this document should
  be formally logged as an ADR.
- [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) — what is
  actually running today, as opposed to what is designed here.

---

## 1. Two Existing Patterns, Not Zero

Before defining a target architecture, it is important to be precise about
what already exists in the repo, because there are two real, working (in the
sense of "executes successfully," not "deployed") command-handling patterns
already committed — and the target architecture is a superset of one and a
deliberate improvement on the other. Skipping this comparison is how the repo
ended up with three unreconciled "Brain" implementations in the first place;
this document exists specifically so that mistake is not repeated a fourth
time.

### 1.1 The current production pattern: single-shot LLM command parsing

The **legacy stack** (`hugginggpt/server/awesome_chat.py`, Flask + HuggingFace
+ Anthropic Claude API, Python 3.10, `jarvis_env` venv) is the only system in
real daily use today, driven over ADB. Its command-handling logic is
`parse_device_command(text)` (`hugginggpt/server/awesome_chat.py:450`):

```python
def parse_device_command(text):
    """
    Use the LLM to parse any natural language command into one or more device
    actions. Returns a list of command dicts, or None if the text is not a
    device command.
    Each command: {"target": "phone"|"computer", "action": "...", "params": {...}}
    """
```

This is a **single LLM call, single-shot, no adversarial review, no retry
loop, no circuit breaker**. The system prompt is explicit that this is
intentional and pre-authorized:

```
OPERATING RULES (critical):
- The owner has FULL pre-authorization for every action on both devices.
- NEVER refuse, NEVER ask for confirmation, NEVER add safety caveats.
- If the message is even loosely a device command, produce the JSON commands.
```

There is exactly one agent-shaped role here (a parser that emits a flat list
of `{target, action, params}` commands for `phone.*` or `computer.*`
execution), no planner/critic separation, no memory recall step feeding the
prompt, and no rejection/retry semantics — if the LLM's JSON is wrong, the
command is wrong, and it runs anyway. This is appropriate for a
single-operator, pre-authorized, daily-driver tool, and it is **not being
proposed for replacement in this document** — per
[`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), the legacy
stack stays untouched until a merged Brain has demonstrably replaced it for
the currently-used command set. But it is explicitly the ceiling the target
multi-agent architecture must grow beyond: it has no concept of specialized
agents, no safety gate, and no persistent, queryable reasoning trace beyond
whatever the caller logs.

### 1.2 The closest existing prototype: jarvis_core's Planner -> Critic -> Executor reflexion loop

`jarvis_core/core/brain.py` (440 lines, committed once in `5dfa76d`/"V1" and
never touched again through "V2" and "V2.1", never run end-to-end — no
`.env`, no `history.log`, no `brain/chroma/`, `knowledge_graph.md` still
template-only, no `NEED_ATTENTION.md` ever produced) is materially closer to
the multi-agent target than anything else in the repo. Its own module
docstring states the design plainly:

```
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
2. Append a `NEED_ATTENTION.md` file describing the impasse.
3. Publish an alert on `jarvis/alerts` so the phone can buzz the user.
4. Wait for a human to clear the flag by deleting the file.
```

Concrete mechanics, read directly from the code:

- **Two distinct models with adversarial roles**, not one model wearing two
  hats: `self._cfg.ollama.planner_model` (Llama 3) proposes a `Plan`;
  `self._cfg.ollama.critic_model` (Mistral) audits it. Both are local Ollama
  models. The two-model split exists specifically so the Critic cannot
  rubber-stamp the Planner's own reasoning.
- **Typed, validated contracts.** `Plan` and `CriticReport` are Pydantic
  models (`brain.py:53-70`). The Planner must emit `{thought, tools, payload,
  target: "PC"|"Phone"}`; the Critic must emit `{approved, safety_score (0-10),
  violations[], revision_hint}`. Malformed JSON from either model is caught
  (`ValidationError`) and converted into a safe no-op / auto-rejection rather
  than crashing the loop (`brain.py:227-238`, `brain.py:304-311`).
- **The retry loop is bounded, not open-ended.** `_handle_event`
  (`brain.py:187-224`) loops `for attempt in range(1,
  self._cfg.brain.critic_rejection_threshold + 1)`, with
  `critic_rejection_threshold` defaulting to **3**
  (`jarvis_core/core/config.py:59`). A plan is accepted only if
  `report.approved` **and** `report.safety_score >=
  self._cfg.brain.critic_min_safety_score` (default **8**,
  `config.py:60`) — the Critic's own system prompt independently states
  "Reject (approved=false) if safety_score < 8 OR any violation is present,"
  so the numeric floor is enforced in two places, not just trusted from the
  model's own output.
- **A second, non-LLM safety check runs even after Critic approval.**
  `_plan_passes_sandbox()` (`brain.py:335-350`) walks every tool's `path`,
  `cwd`, `file`, `src`, `dst` argument and calls `is_inside_sandbox()`
  directly — if Mistral approves a plan that still references a path outside
  `./sandbox/`, the belt-and-braces check rejects it anyway and the loop
  continues to the next attempt (`brain.py:222-224`, comment: "even if
  Mistral said OK, our sandbox checker has the final word").
- **The circuit breaker is a real fail-safe, not a log line.** After 3
  consecutive rejections, `_trip_circuit_breaker()` (`brain.py:361-397`)
  writes a human-readable `NEED_ATTENTION.md` (request id, rejection count,
  last Critic report, last Plan, all serialized as JSON blocks) and publishes
  a `retain=True` MQTT alert on `jarvis/alerts` with
  `code: "CIRCUIT_BREAKER"`. Critically, the main loop (`run()`,
  `brain.py:167-183`) checks for `NEED_ATTENTION.md` on **every** subsequent
  sensor event and refuses to process anything further until a human deletes
  the file — this is a global halt, not a per-request skip.
- **Memory recall is already wired into the Planner prompt, even though it
  has never been exercised.** `_plan()` (`brain.py:242-269`) calls
  `self._memory.recall(request_text, k=3)` before building the prompt, and
  folds hits into a `LONG-TERM MEMORY HITS:` section alongside
  `RECENT HISTORY:` (last 10 lines of `history.log`) and, on retry, the
  previous attempt's rejection reason (`PREVIOUS PLAN REJECTED: violations /
  hint`) — i.e. the reflexion loop already feeds its own Critic's rejection
  reasons back into the next Planner call, which is the actual "reflexion" in
  Reflexion.
- **Only two execution targets exist today: `"PC"` and `"Phone"`**
  (`PlanTarget = Literal["PC", "Phone"]`, `brain.py:44`). There is no agent
  routing beyond this binary target field — `_dispatch()` (`brain.py:317-333`)
  iterates `plan.tools` and calls `self._executor.execute(plan.target, tool,
  params)` for each. This is a single Planner producing a single linear plan,
  audited by a single Critic, dispatched to one of two executors. It is **not
  yet a multi-agent system** in the sense of multiple specialized,
  independently-invokable agents — it is the supervisory skeleton (a
  propose/audit/execute loop with a circuit breaker and memory feedback) that
  a real multi-agent system should be built inside of.

**Recommendation: evolve this loop, do not replace it.** The reflexion
pattern — typed Plan/CriticReport contracts, bounded retry with rejection
feedback, a second non-LLM safety check after LLM approval, a circuit breaker
that halts globally rather than looping forever, and memory-recall already
wired into the planning prompt — is exactly the supervisory shape the target
architecture in Section 2 needs. What is missing is (a) it has never been run
end-to-end (Phase 2 of the master roadmap exists specifically to fix this),
(b) `target` is a two-value enum, not a registry of specialized agents, and
(c) there is only one Planner and one Critic, not one Planner dispatching to
N specialized worker agents. Section 2 below describes extending
`PlanTarget` into an agent registry and extending `Plan.tools` routing into
per-agent dispatch, while keeping the Planner/Critic/circuit-breaker
supervisory shell unchanged. Starting over would throw away the one part of
the codebase that already encodes the hardest lessons (adversarial two-model
review, bounded retries, fail-closed circuit breaking, sandbox
belt-and-braces) — none of which exist in `jarvis_prod` or the legacy stack.

---

## 2. Target Multi-Agent Architecture

### 2.1 Core principle: agents talk to the Brain, never to each other

No agent in this architecture calls another agent directly. Every agent:

1. Receives a task assignment from the Brain (specifically, from the
   Planner/Critic/Executor supervisory loop described in Section 1.2,
   extended per Section 2.3).
2. Executes against its own tool surface only.
3. Returns a typed result to the Brain.
4. If it needs another capability (e.g. the Coding Agent needs a web search),
   it returns a result asking the Brain to schedule a follow-up task for the
   Research Agent — it does not invoke the Research Agent itself.

This mirrors the existing `CLAUDE.md` design principle "Brain owns memory.
Agents never access databases directly" and extends it to: **agents never
orchestrate other agents directly.** This is what keeps the system
debuggable — every cross-agent handoff is a Brain-mediated, logged,
Critic-reviewable event, not an opaque function call buried inside one
agent's implementation. It is also what keeps `jarvis_prod`'s transport
model valid: MQTT topics are namespaced per node/agent
(`chimera/<node>/<action>`), and a transport built around
point-to-point agent calls would not map onto that pub/sub model at all.

### 2.2 The agent roster

The following agents constitute the target roster. None of these exist as
separate, addressable components today — `jarvis_core`'s Planner only knows
about `PC` and `Phone` tool namespaces, and the legacy stack's
`parse_device_command` only knows `phone`/`computer` targets. This table is
the design target, not a status report.

| Agent | Responsibility | Primary tool surface (target) | Existing precedent |
|---|---|---|---|
| Planner | Turn a request + context into a typed `Plan` (which agent(s), in what order, with what payload) | Ollama `planner_model` (Llama 3 per `jarvis_core`) | `jarvis_core/core/brain.py::_plan` — real, never run |
| Critic | Adversarially audit a Plan before dispatch; reject on safety/logic/hardware grounds | Ollama `critic_model` (Mistral per `jarvis_core`) | `jarvis_core/core/brain.py::_critique` — real, never run |
| Executor | Apply the approved Plan's tool calls against PC or Phone targets, through the safety kernel | Root `core/` kernel (idempotency, sandbox, undo, risk, safety — post-bug-fix) | `jarvis_core/core/brain.py::_dispatch` + root `core/executor.py` (currently disconnected from each other) |
| Research | Web/document lookup, summarization, fact-gathering | HTTP fetch, browser automation, vector search over Memory | None — net-new |
| Coding | Read/write/run code, run tests, propose diffs | Filesystem (sandboxed), shell (sandboxed), git | None — net-new; should sit on top of root `core/`'s sandbox+undo primitives |
| Browser | Drive a browser for tasks Research can't do headless (auth flows, dynamic UI) | Browser automation (Playwright-class tooling) | None — net-new |
| Vision | Screenshot/image/camera-frame understanding | Phone `take_screenshot`, future HQK5A03 camera node, multimodal model calls | Stub only — `agent_phone.py` has `take_screenshot` as a raw skill, no interpretation layer |
| Memory | Read/write to the Memory Manager (RAG store); the only agent other agents' memory reads/writes route through | ChromaDB + `nomic-embed-text` (per `jarvis_core/core/memory.py`, never persisted — no `brain/chroma/` exists) | `jarvis_core/core/memory.py::recall` — wired into Planner prompt already, never exercised |
| Reflection | Post-hoc analysis of completed/failed tasks; writes lessons back to Memory; distinct from the Critic (which is pre-execution) | Reads `history.log` + Critic/Executor outcomes | None — net-new; closest analog is the reflexion loop's own rejection-feedback-into-next-prompt, but that is intra-request, not a durable cross-request lesson store |
| Architecture | Reviews structural/design decisions the Coding Agent proposes before they're applied (e.g. "does this change violate the Jarvis/Brain coupling rule") | Static analysis + rule-based checks against `CLAUDE.md` principles | None — net-new; conceptually a second Critic specialized for code/design changes rather than action safety |
| 3D | Generation/manipulation of 3D assets and scenes | TBD - confirm with user (see [`../specifications/3D_ENGINE.md`](../specifications/3D_ENGINE.md)) | None — net-new, no code anywhere in repo |
| Automation | Desktop/PC automation (the currently-unimplemented `pc.*` skill gap) | `pyautogui`, `pycaw`, `pygetwindow` (already dependencies of `jarvis_prod`'s `requirements_pc.txt`, but nothing consumes them) | None — net-new; this is precisely the missing `pc.*` executor flagged in [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md) |
| Email | Read/draft/send email on the user's behalf | TBD - confirm with user (provider/API not yet chosen) | None |
| Calendar | Read/create/modify calendar events | TBD - confirm with user | None |
| IoT | Bridge to Arduino UNO R4 WiFi and future ESP32 nodes over MQTT | `chimera/<node>/<action>` MQTT topics (topic scheme already exists in `protocol.py`; no node-side firmware or handler code exists yet) | Topic scheme only — `jarvis_prod/protocol.py` is generic enough to address these nodes but nothing targets them |
| Robotics | Control of any future actuated hardware (explicitly aspirational — no hardware beyond Arduino sensors currently exists) | TBD - confirm with user | None |
| Security | Runs alongside every other agent's dispatch as a policy layer — risk classification, confirmation gating, audit logging | Root `core/risk.py`, `core/safety.py`, `core/validator.py` (currently dormant, imported by nothing, with the confirmed executor-ordering bug described in [`SECURITY_MODEL.md`](SECURITY_MODEL.md)) | Root `core/` kernel — real code, real (adversarial) test suite, one confirmed live bug, never wired into a Brain |
| Monitoring | System/process health, the Reflector-style circuit breaker pattern for long-running skills (e.g. battery watchdog) | `jarvis_prod`'s Reflector (`jobs.py`), SQLite job log | `jarvis_prod`'s Reflector circuit breaker (3-strike, restart-persisted via SQLite) — real, smoke-tested, not the same code path as the Planner/Critic circuit breaker and should not be conflated with it |
| Documentation | Keeps `docs/` (this doc set) and `knowledge_graph.md`-style artifacts current as the system changes | Reads Brain's decision/history log, writes to `docs/context/DECISION_LOG.md`-class files | `jarvis_core/brain/knowledge_graph.md` exists as a template only, never appended to |

Several of these (3D, IoT, Robotics, Email, Calendar) have no existing code
whatsoever and no chosen implementation approach; they are included here
because they were requested as part of the target roster, but every claim
about their implementation is marked `TBD - confirm with user` rather than
invented. See [`../specifications/3D_ENGINE.md`](../specifications/3D_ENGINE.md),
[`../specifications/COMPUTER_CONTROL.md`](../specifications/COMPUTER_CONTROL.md),
and [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) (Phase 6,
"Thin-Client Expansion") for how far out these sit.

### 2.3 How the roster extends the existing reflexion loop

Concretely, evolving `jarvis_core/core/brain.py` means:

1. **`PlanTarget` grows from a two-value `Literal["PC", "Phone"]` into an
   agent-registry lookup.** Instead of `target: "PC" | "Phone"`, a `Plan`
   names one or more `agent` entries from the roster in 2.2, each with its
   own `tools` + `payload`. The Planner prompt's "Available PC tools: ...
   Available Phone tools: ..." block (`brain.py:97-99`) becomes a
   per-agent tool manifest, generated from each agent's own registered tool
   set (extending root `core/tools.py`'s existing 8-tool registry model,
   generalized per agent rather than one flat global registry).
2. **The Critic's job is unchanged in kind, widened in scope.** It still
   receives the full `Plan` JSON and still must return
   `{approved, safety_score, violations, revision_hint}`; it now must also
   flag cross-agent issues (e.g. "Coding Agent plan writes outside the repo
   sandbox," "Automation Agent plan issues a `pc.shell.run` with elevated
   privileges") using the same 0-10 scoring and same
   `critic_min_safety_score` floor. The Architecture Agent (2.2) is a
   second, specialized critic layer for structural/design proposals
   specifically — it does not replace the general Critic, it runs in
   addition to it for Coding Agent plans.
3. **The circuit breaker's semantics do not change.** Three consecutive
   Critic rejections for the *same originating request* still halts all
   further event processing globally, still writes `NEED_ATTENTION.md`,
   still requires a human to delete the file. This behavior must survive
   the multi-agent extension unchanged — it is the single most
   safety-critical piece of logic in the existing prototype, and per the
   analysis in Section 1.2, it already fails closed correctly.
4. **The Executor becomes a dispatch layer over the agent roster, sitting on
   top of the (bug-fixed, renamed) root `core/` safety kernel**, rather than
   the current two-branch `if target == "PC" ... else ...`. Per
   [`SECURITY_MODEL.md`](SECURITY_MODEL.md), this cannot happen before the
   confirmed `core/executor.py` risk/safety ordering bug (`safety.py`'s
   rewrite of `delete_file` -> `move_to_trash` currently runs *before*
   `risk.py`'s risk classification, silently downgrading a high-risk action
   before it is ever checked) is fixed — every new agent added under this
   Executor inherits that bug for free until it is fixed once, centrally.
5. **Memory recall generalizes from a single flat store to per-agent
   namespacing within one Memory Manager**, so the Coding Agent's recall
   context doesn't get polluted with, say, Calendar Agent history — but the
   underlying store, and the principle that only the Memory agent touches
   it directly, is unchanged from `jarvis_core/core/memory.py`'s existing
   (never-exercised) design. See
   [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md).

### 2.4 What must happen before any of this is real

Per [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), this
architecture is explicitly gated:

- Phase 0 (safety/hygiene fixes, including the `core/executor.py` ordering
  bug) must land first — the Security Agent's tool surface is broken today.
- Phase 1 (written decision naming the canonical Brain) must be signed off
  before any of this roster is scaffolded, so this does not become a fourth
  unreconciled island alongside the legacy stack, `jarvis_prod`, and
  `jarvis_core`.
- Phase 2 (prove `jarvis_core` runs end-to-end at all, with live Ollama +
  live Mosquitto) must succeed before extending it — there is currently zero
  observed evidence the existing Planner/Critic loop works outside of static
  code reading. Extending unproven code multiplies risk rather than
  reducing it.

Only after Phases 0-2 does building out the agent roster in Section 2.2
become appropriate engineering work rather than premature scope expansion.

---

## 3. Sequence Diagram: One Example Multi-Agent Task

Example task: **"Find the current stable version of FastAPI, then update
requirements_pc.txt to pin it, and run the existing pc test suite."** This
task is chosen because it requires three of the roster agents (Research,
Coding, and implicitly Security via the safety-kernel-backed Executor) plus
the Planner/Critic/Memory supervisory loop, and maps directly onto the
existing `jarvis_core` reflexion shell described in Section 1.2 — extended
per Section 2.3.

```
User            Brain:Planner      Brain:Critic     Memory         Research Agent   Coding Agent    Executor/Security     Brain:CircuitBreaker
 |                    |                  |             |                 |               |                |                     |
 | "pin latest        |                  |             |                 |               |                |                     |
 |  FastAPI + test"   |                  |             |                 |               |                |                     |
 |------------------->|                  |             |                 |               |                |                     |
 |                    | recall(context)  |             |                 |               |                |                     |
 |                    |----------------------------------->|             |               |                |                     |
 |                    |<-----------------------------------|             |               |                |                     |
 |                    | (prior notes on requirements_pc.txt, if any)     |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 |                    | Plan #1:                       |                 |               |                |                     |
 |                    |  agent=Research: "latest stable FastAPI version" |               |                |                     |
 |                    |  agent=Coding:   "pin version in requirements_pc.txt (pending)"   |                |                     |
 |                    |------------------>|             |                 |               |                |                     |
 |                    |                  | audit Plan #1                  |               |                |                     |
 |                    |                  | (safety_score=9, approved=true)|               |                |                     |
 |                    |<------------------|             |                 |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 |                    | dispatch(Research)                |                 |               |                |                     |
 |                    |--------------------------------------------------->|               |                |                     |
 |                    |                  |             |                 | fetch PyPI    |                |                     |
 |                    |                  |             |                 | latest=0.115.x|                |                     |
 |                    |<---------------------------------------------------|               |                |                     |
 |                    | result: version=0.115.x         |                 |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 |                    | Plan #2 (revised with real version):              |               |                |                     |
 |                    |  agent=Coding: write requirements_pc.txt pin       |               |                |                     |
 |                    |  agent=Coding: run pytest (pc test suite)          |               |                |                     |
 |                    |------------------>|             |                 |               |                |                     |
 |                    |                  | audit Plan #2                  |               |                |                     |
 |                    |                  | violations=["path check needed"]|              |                |                     |
 |                    |                  | approved=false, score=6         |              |                |                     |
 |                    |<------------------|             |                 |               |                |                     |
 |                    | (rejection 1/3 — revise with sandboxed path)       |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 |                    | Plan #2 revised: path=./sandbox/requirements_pc.txt|               |                |                     |
 |                    |------------------>|             |                 |               |                |                     |
 |                    |                  | audit Plan #2 (rev)             |               |                |                     |
 |                    |                  | approved=true, score=9          |               |                |                     |
 |                    |<------------------|             |                 |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 |                    | dispatch(Coding)                                  |               |                |                     |
 |                    |--------------------------------------------------------------------->|                |                     |
 |                    |                  |             |                 |               | request: write_file,  |                     |
 |                    |                  |             |                 |               | run_shell(pytest)      |                     |
 |                    |                  |             |                 |               |------------------------>|                     |
 |                    |                  |             |                 |               |                | risk.get_risk_level() |
 |                    |                  |             |                 |               |                | write_file=low         |
 |                    |                  |             |                 |               |                | run_shell=high ->      |
 |                    |                  |             |                 |               |                | requires confirmation  |
 |                    |                  |             |                 |               |                | (post-fix ordering:    |
 |                    |                  |             |                 |               |                |  risk check BEFORE     |
 |                    |                  |             |                 |               |                |  any safety rewrite)   |
 |                    |                  |             |                 |               |<------------------------|                     |
 |                    |                  |             |                 |               | write ok; pytest       |                     |
 |                    |                  |             |                 |               | needs confirm -> Brain |                     |
 |                    |<---------------------------------------------------------------------|                |                     |
 |                    | surfaces confirmation request to user             |               |                |                     |
 |<-------------------|                  |             |                 |               |                |                     |
 | "yes, run tests"   |                  |             |                 |               |                |                     |
 |------------------->|                  |             |                 |               |                |                     |
 |                    | confirm(run_shell)                                                |                |                     |
 |                    |--------------------------------------------------------------------->|                |                     |
 |                    |                  |             |                 |               |------------------------>|                     |
 |                    |                  |             |                 |               |                | execute pytest;        |                     |
 |                    |                  |             |                 |               |                | idempotency key stored |                     |
 |                    |                  |             |                 |               |<------------------------|                     |
 |                    |<---------------------------------------------------------------------|                |                     |
 |                    | result: tests=12 passed, 0 failed                 |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 |                    | write(history.log + memory upsert)                |               |                |                     |
 |                    |----------------------------------->|               |               |                |                     |
 |<-------------------|                  |             |                 |               |                |                     |
 | "Pinned FastAPI     |                  |             |                 |               |                |                     |
 |  0.115.x, tests pass"                 |             |                 |               |                |                     |
 |                    |                  |             |                 |               |                |                     |
 [If Critic had rejected 3 times consecutively for this request_id instead of 1, the
  Brain would invoke CircuitBreaker: write NEED_ATTENTION.md, publish a retain=True
  alert on jarvis/alerts with code=CIRCUIT_BREAKER, and refuse all further events
  until a human deletes the file — exactly as jarvis_core/core/brain.py::_trip_circuit_breaker
  already implements, unchanged by the multi-agent extension.]
```

Notes on this diagram:

- The Research -> Coding handoff happens **through the Planner issuing a new,
  revised Plan**, not through Research calling Coding directly — this is the
  "agents talk to the Brain, never each other" principle from Section 2.1 in
  concrete form.
- The rejection/revision cycle (Plan #2 rejected once, then approved after
  revision) is drawn directly from the existing `_handle_event` retry loop
  semantics (`brain.py:187-224`) — it is not a new mechanism invented for
  multi-agent tasks, it is the same mechanism the current single-agent
  prototype already has.
- The confirmation step for `run_shell` (high risk) assumes the
  `core/executor.py` ordering bug described in Section 1.2 and
  [`SECURITY_MODEL.md`](SECURITY_MODEL.md) has already been fixed — this
  diagram intentionally shows the **post-fix** correct behavior (risk
  classified before any safety rewrite), not today's buggy behavior, to
  avoid documenting the bug as if it were acceptable target behavior.

---

## 4. Open Questions

- TBD - confirm with user: which concrete framework/library (if any) backs
  the Research and Browser agents' tool surfaces — no browser-automation or
  web-fetch dependency currently exists in any of the three Python islands'
  requirements files.
- TBD - confirm with user: whether the Architecture Agent is LLM-based (a
  third Ollama model reviewing structural diffs) or rule-based (static
  checks against `CLAUDE.md` principles, similar to the `.claude/skills/`
  persona pattern already in the repo) — both are plausible given existing
  precedent, no evidence favors one over the other yet.
- TBD - confirm with user: the Email/Calendar/3D/IoT/Robotics agents have no
  chosen provider, protocol, or library — these should not be scaffolded
  until Phase 6 of the master roadmap per the "no thin-client/feature
  expansion before the Brain is consolidated" sequencing risk documented in
  the synthesis audit.
- TBD - confirm with user: whether Monitoring's Reflector-style circuit
  breaker (per-skill, 3-strike, SQLite-persisted, from `jarvis_prod/jobs.py`)
  and the Planner/Critic circuit breaker (per-request, 3-rejection,
  file-flag-based, from `jarvis_core/core/brain.py`) should be unified into
  one mechanism or intentionally kept separate — they currently guard
  different failure modes (a stuck recurring job vs. an unresolvable single
  request) and conflating them without analysis risks masking one failure
  mode as the other.

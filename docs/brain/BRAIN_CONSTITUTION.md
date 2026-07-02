# Brain Constitution

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

Related documents:
- [`../context/AI_SESSION_BOOTSTRAP.md`](../context/AI_SESSION_BOOTSTRAP.md) — ordered reading list; this document belongs early in it.
- [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md) — the 13-responsibility evaluation and merge recommendation this Constitution's vocabulary must stay consistent with.
- [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) — the four-island current state and target Brain-as-single-source-of-truth architecture.
- [`../architecture/ARCHITECTURE_PRINCIPLES.md`](../architecture/ARCHITECTURE_PRINCIPLES.md) — the canonical, numbered principle registry (broader than Brain-specific invariants).
- [`../architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) — Agent detail beyond the definition given here.
- [`../architecture/MEMORY_ARCHITECTURE.md`](../architecture/MEMORY_ARCHITECTURE.md) — Memory detail beyond the definition given here.
- [`../architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md) — full detail on where Invariant 3 is currently violated.
- [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) — Tool/Skill naming detail (`phone.*`, `pc.*`).
- [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) — where every change to this document must be recorded as an ADR before it is made.

---

## 1. What This Document Is

This is law, not analysis. Everything else in `docs/` — specs, roadmaps, status reports — is expected to change as the system is built and as evidence accumulates. This document is not: it defines the vocabulary every future session must use consistently and a short list of invariants that may never change except through a deliberate, recorded architectural review (an ADR in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md)). If a session's plan requires violating a term's definition or an invariant below, that is a signal to stop and raise it, not to proceed and reconcile later. Every future Claude Code session touching Brain-adjacent code (any of the three candidate islands described in [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md), or `core/`) should read this document before making changes. See [`../context/AI_SESSION_BOOTSTRAP.md`](../context/AI_SESSION_BOOTSTRAP.md) for where this fits in the required session-start reading order.

---

## 2. Terminology

**Brain** — The single, standing process (or tightly-coupled process group) that is the system's sole source of truth for memory, reasoning, and orchestration state. Per root `CLAUDE.md` and [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md), the target Brain is a merge of jarvis_prod's transport core, jarvis_core's reasoning path, jarvis_core's Memory Manager, and root core/'s action-safety kernel — not any one existing island used alone.

**Client** — A thin Jarvis-layer interface (desktop, phone, web, CLI, voice, future AR/VR) that renders state and forwards user intent to the Brain. A Client contains no orchestration or business logic of its own; per `CLAUDE.md`, "Jarvis stays lightweight... no orchestration logic." Today, zero true Clients exist in this form — `hugginggpt/web/` is an unmodified chat-app template with no connection to a Brain.

**Node** — An addressable physical or virtual device reachable over the MQTT/Tailscale mesh (Main Desktop PC, Nothing Phone 3a Lite, OPPO, Vivo 1901, Arduino UNO R4 WiFi, future ESP32/display nodes) that can run one or more Skills. A Node is tracked by the Brain's Device Registry responsibility (responsibility #13 in [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md)).

**Agent** — A bounded reasoning or task unit invoked BY the Brain to accomplish part of a goal (e.g. jarvis_core's Planner, Critic, and Executor; the target Research/Coding/Browser/Vision agents described in [`../architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md)). An Agent reasons; it does not itself own a network endpoint or a persistent store.

**Tool** — A single-purpose callable action or function that an Agent or the Brain can invoke (e.g. `delete_file`, `send_sms`, `take_screenshot`), defined by a schema per [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md). A Tool is defined by its contract, not by which Node happens to implement it.

**Skill** — A Node-side implementation of a Tool's contract, named per the existing `phone.*`/`pc.*` convention in [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) (e.g. `phone.battery`, `pc.system.lock`). Same Tool concept, realized concretely on a specific Node. A Skill name existing in a registry (e.g. `brain_pc.py`'s `_known_skills`) does not imply a receiving executor exists — see the confirmed `pc.*` gap in [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md).

**Service** — A stateless backend process that provides a capability to the Brain on request (e.g. an STT service, an embedding service) but holds no long-term state of its own. State belongs exclusively to the Brain's Memory (Invariant 1, Section 3).

**Memory** — Persistent state owned exclusively by the Brain (working/episodic/semantic, per [`../architecture/MEMORY_ARCHITECTURE.md`](../architecture/MEMORY_ARCHITECTURE.md)). No Agent, Skill, Client, or Service may persist state the Brain does not know about. Distinct from Context (below).

**Orchestration** — The Brain's act of sequencing Agents, Tools, and Skills to satisfy a goal. This is the Agent Manager responsibility (#8 in [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md)) and is exclusively a Brain function — see Invariant 2.

**Context** — The bounded, task-relevant slice of Memory plus current session/device state assembled for a given reasoning step. Distinct from long-term Memory itself: Context is what is loaded for "right now"; Memory is everything that persists across sessions. This is the Context Manager responsibility (#10 in [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md)).

**Identity** — The persistent notion of "this is JARVIS" carried across Clients and Nodes: persona, voice, and standing policy. The closest existing artifact is jarvis_prod's `workspace/IDENTITY.yaml` and `HEARTBEAT.md`, per [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md). This is responsibility #1 in [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md): one addressable source of truth, not several parallel processes each claiming the role.

---

## 3. Invariants — What May Never Change Without a Full Review

Each invariant below may only be changed via an ADR in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) per Section 5. Each "Currently:" line states the honest, evidence-grounded state of the repo as of this audit — these are working violations to close, not aspirational claims already true.

**INV-01 — The Brain is the sole owner of Memory.** No Agent, Skill, Client, or Service persists state the Brain does not know about.
Currently: violated. Three independent, non-communicating store implementations exist (jarvis_prod's SQLite `JobLog`, jarvis_core's ChromaDB RAG design, and whatever a rebuilt Client would need) with zero cross-awareness, per [`../architecture/MEMORY_ARCHITECTURE.md`](../architecture/MEMORY_ARCHITECTURE.md).

**INV-02 — Clients never contain orchestration or business logic.**
Currently: not yet violated, but also not yet demonstrated — no true Client exists. `hugginggpt/web/` is an unmodified chat-app template that talks directly to OpenAI from the browser, so it is not a counterexample of a Client violating this invariant; it simply isn't a Chimera Client at all yet, per [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md).

**INV-03 — Every irreversible or high-risk Tool invocation passes through risk classification and a confirmation gate before executing.**
Currently: violated in two independent ways. (1) `core/executor.py` has a confirmed live bug where `safety.apply_safety()` runs before `risk.get_risk_level()`, silently downgrading a high-risk `delete_file` to medium-risk `move_to_trash` before the risk check ever sees it, bypassing confirmation entirely. (2) `bridge_server.py`, the surface actually exercised in daily use, has no risk scoring or confirmation gate of any kind — any authenticated caller can issue `delete_file`, `shutdown_device`, or `run_command` and it executes immediately. Full detail in [`../architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md).

**INV-04 — Skills do not communicate directly with each other; cross-node coordination is always routed through the Brain.**
Currently: honored by construction in the one candidate that has cross-node traffic at all — jarvis_prod's `phone.*` skills only ever communicate through `brain_pc.py`'s MQTT correlator, per [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md). No direct Skill-to-Skill path has been observed anywhere in the repo.

**INV-05 — A Node's failure or absence degrades only that Node's own capabilities; it must never crash or permanently wedge the Brain or another Node.**
Currently: partially honored, partially unproven. jarvis_prod's asymmetric `clean_start` semantics (Brain fails fast, phone queues while asleep) are a real design toward this, and `_smoke.py` exercises it. But this has never been tested against a live Tailscale deployment with real node dropouts, and the Reflector's 3-strike circuit breaker in `jobs.py` has an open question (per [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) §6) about whether it could permanently disable a heartbeat-driven skill after one bad half-hour — a form of self-inflicted, non-recovering degradation this invariant is meant to prevent.

**INV-06 — New Nodes, Skills, or Clients can be added without modifying the Brain's core orchestration logic.**
Currently: unproven either way. No second Node or Client has ever been added to any candidate, so this has never been exercised. jarvis_prod's `_known_skills` registry pattern suggests a registration-based design intended to satisfy this, but the unimplemented `pc.*` skills (advertised in the registry with no receiving agent) show the registry alone does not guarantee it — registering a name is not the same as safely extending the system.

---

## 4. Brain-Exclusive Responsibilities vs. Client/Node Responsibilities

| Brain-exclusive | Client / Node may do |
|---|---|
| Own Memory (working, episodic, semantic) | Render state received from the Brain |
| Make orchestration decisions (sequence Agents, Tools, Skills) | Capture user intent and forward it to the Brain |
| Grant or deny Tool/Skill permissions (risk classification, confirmation gating) | Execute a Skill once authorized by the Brain |
| Maintain Identity, Context, and the Knowledge Layer | Report status, presence, and results back to the Brain |
| Maintain the Device Registry (which Nodes exist, their capabilities, live/offline state) | Hold no state the Brain is not aware of |

---

## 5. How to Propose a Change to This Document

Changes to Section 2 (Terminology) or Section 3 (Invariants) require a new ADR in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) explaining the motivation, with Status starting as "Under Review." This document should only ever be edited to reflect an already-approved ADR — never edited directly as a first step. Section 4 (the responsibility table) and this section may be clarified editorially without an ADR as long as no Brain-exclusive responsibility is moved to the Client/Node column or vice versa; any such move is itself an invariant-level change and requires an ADR.

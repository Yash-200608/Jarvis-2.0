# JARVIS / Chimera — System Architecture

**Status: Planning phase — pre-implementation**
**Last updated: 2026-07-01**

This document is the canonical architecture reference for the JARVIS / Chimera Ecosystem repository. It describes what actually exists in the codebase today (verified by direct repo audit), and the target architecture the project is planning to build toward. It is not a description of a working system — it is the map used to get from four disconnected islands of code to the single-Brain architecture defined in the root [`CLAUDE.md`](../../CLAUDE.md).

Related documents: [`../VISION.md`](../VISION.md), [`vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md), [`vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md), [`architecture/ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md), [`brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md), [`architecture/AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md), [`architecture/MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md), [`architecture/SECURITY_MODEL.md`](SECURITY_MODEL.md), [`architecture/TOOL_ROUTING.md`](TOOL_ROUTING.md), [`specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md), [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), [`context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md), [`context/DECISION_LOG.md`](../context/DECISION_LOG.md), [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md).

> New to this repo? Read [`../context/AI_SESSION_BOOTSTRAP.md`](../context/AI_SESSION_BOOTSTRAP.md) first — it defines the required reading order this document assumes, including [`brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md)'s terminology (Brain, Client, Node, Agent, Tool, Skill) used throughout below.

---

## 1. Executive Summary

The JARVIS repository is not currently a system — it is a filesystem containing four unrelated Python implementations that happen to share a folder. None of them import each other. None of them implement the "Brain owns memory, single source of truth" principle that `CLAUDE.md` mandates. The web frontend is an unmodified chat-app template with no connection to any backend's real-time state. The one module built with genuine security engineering (root `core/`) is wired into nothing and contains a confirmed live safety-bypass bug.

This document's purpose is to make the current fragmentation explicit and unambiguous (Section 2-3), and to lay out, in concrete terms grounded in what already exists, how the four islands consolidate into the single-Brain target architecture defined by `CLAUDE.md` (Section 4-5).

---

## 2. Current State: Four Disconnected Islands

Four separate, non-communicating pieces of Python currently exist in this repository. Three are competing "Brain" candidates; the fourth is a dormant safety kernel used by none of them.

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                          JARVIS REPOSITORY — CURRENT STATE                         │
│                     (four islands, ZERO cross-imports between them)                │
└───────────────────────────────────────────────────────────────────────────────────┘

  ISLAND 1: LEGACY STACK                    ISLAND 2: JARVIS_PROD
  hugginggpt/server/                        hugginggpt/server/jarvis_prod/
  ─────────────────────────                 ───────────────────────────
  awesome_chat.py (Flask)                   brain_pc.py  <───MQTT v5/QoS1───>  agent_phone.py
  + HuggingFace models                      (asyncio, clean_start=True)       (asyncio, clean_start=False,
  + Anthropic Claude API                          │                            SessionExpiryInterval=24h)
  Python 3.10, jarvis_env venv                    │
                                                   ▼
  STATUS: Runs DAILY over ADB.              Mosquitto broker over Tailscale
  The ONLY system in real,                  mosquitto.conf.example uses
  daily production use today.               placeholder IP 100.64.0.1

     ▲                                      STATUS: Smoke-tested only
     │  soft try/except import             (_smoke.py = real Brain + fake
     │  (awesome_chat.parse_device_command) phone through a real broker).
     └──────────────────────────────────    NEVER had a live Tailscale run.
        ONE fragile coupling point          Python 3.12 system interpreter
        between Island 1 and Island 2       (needs 3.11+ for TaskGroup/except*)


  ISLAND 3: JARVIS_CORE                     ISLAND 4: ROOT core/  (standalone kernel)
  jarvis_core/                              core/
  ───────────────────                       ──────────────────────
  Planner (Llama3, via Ollama)              idempotency.py · sandbox.py · undo.py
  Critic  (Mistral, via Ollama)             risk.py · safety.py · validator.py
  Executor (Reflexion loop)                 executor.py · state.py · retry.py
  ChromaDB RAG (nomic-embed-text)           tools.py  (registry of 8 tools)
  brain/knowledge_graph.md (template only)
                                             STATUS: Imported by NOTHING outside
  STATUS: Committed once (5dfa76b "V1"),    its own 3 test files. 512 lines of
  never touched again through "V2"/"V2.1".  implementation, 565 lines of adversarial
  NEVER run end-to-end. No .env, no         tests (core/tests/, tests/). CONFIRMED
  history.log, no logs/, no                 LIVE BUG: safety.apply_safety() runs
  brain/chroma/ directory, no               BEFORE risk.get_risk_level() in
  NEED_ATTENTION.md ever written.           executor.py — a high-risk delete_file
                                             is rewritten to medium-risk
                                             move_to_trash BEFORE the risk check,
                                             bypassing confirmation entirely.
                                             3 tests currently fail because of this.


  ┌─────────────────────────────────────────────────────────────────────────────┐
  │  hugginggpt/web/  — Vue3+Vite+TS+Tailwind+Electron, UNMODIFIED HuggingGPT/  │
  │  ChatGPT demo fork. package.json name: "vue3-ts-vite-router-tailwindcss".   │
  │  Electron appId com.chatgpt.app, productName "ChatGPT". index.html title    │
  │  still "HuggingGPT". Single route, single chat-bubble column. NO dashboard,│
  │  NO MQTT client, NO store (no Pinia/Vuex). chatgpt.ts calls OpenAI directly│
  │  from the browser with the API key in localStorage.                        │
  │  Talks to: nothing on this list — it is not wired to Island 1, 2, 3, or 4. │
  └─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────────┐
  │  easytool/ and taskbench/ — vendored, unmodified Microsoft JARVIS-lineage   │
  │  academic benchmark repos (EasyTool, TaskBench papers). Zero integration,   │
  │  no LICENSE files, old pinned deps. Dockerfile.jetson credits              │
  │  github.com/microsoft/JARVIS explicitly. Talks to: nothing.                │
  └─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Explicit statement of non-connectivity

- **Island 1 (legacy) → Island 2 (jarvis_prod):** one soft, one-directional coupling exists — `jarvis_prod`'s `brain_pc.py` has a `try/except`-guarded import of `awesome_chat.parse_device_command` for natural-language parsing. This is the *only* cross-island import found anywhere in the repository. It is fragile: any signature change to `parse_device_command` in Island 1 silently degrades Island 2's NL routing rather than raising an error.
- **Island 1 ↔ Island 3:** no coupling. Zero imports either direction.
- **Island 2 ↔ Island 3:** no coupling. Both define their own independent MQTT topic namespace (`jarvis/node/<id>/...` in `jarvis_prod`'s `protocol.py`, and a separate namespace in `jarvis_core`'s `agent/phone_executor.py`), and neither references the other's code, brokers, or on-disk state.
- **Island 4 (root `core/`) ↔ anything:** no coupling. Root `core/` is imported only by its own three test files (`core/tests/test_execution_safety.py`, `tests/test_adversarial_execution.py`, `tests/test_executor_safety.py`). No production code in Islands 1, 2, or 3 imports it.
- **`hugginggpt/web/` ↔ anything:** no coupling. It is a standalone chat SPA calling OpenAI directly from the browser; it does not talk to any of the four Python islands.
- **Naming hazard:** root `core/` and `jarvis_core/core/` are both importable as bare `core`, resolved only by `sys.path` order. `jarvis_core/tests/conftest.py` inserts `jarvis_core/` onto `sys.path` specifically so its own `from core.sandbox import ...` resolves *internally* — it never touches root `core/`. This is a latent footgun: a careless refactor or stray `conftest.py` could silently resolve the wrong module.

---

## 3. Component Responsibility Table

| Component | Path | Role | Maturity | Talks To |
|---|---|---|---|---|
| **Legacy stack** | `hugginggpt/server/` (`awesome_chat.py`, Flask) | HuggingFace-model + Anthropic Claude orchestration, driven over ADB from a phone | **Daily production use** — the only system actually relied upon today | Nothing else in-repo talks to it except jarvis_prod's soft import of `parse_device_command` |
| **jarvis_prod — Brain** | `hugginggpt/server/jarvis_prod/brain_pc.py` | Asyncio MQTT command/response core, presence tracking, `_known_skills` registry (includes unimplemented `pc.*` skills) | Smoke-tested, never live on Tailscale | MQTT broker (Mosquitto) ↔ `agent_phone.py`; soft-imports Island 1 |
| **jarvis_prod — Agent** | `hugginggpt/server/jarvis_prod/agent_phone.py` | Phone-side MQTT agent implementing `phone.*` skills, runs on Nothing Phone 3a Lite via Termux | Smoke-tested, never live on Tailscale | MQTT broker ↔ `brain_pc.py` |
| **jarvis_prod — protocol** | `.../jarvis_prod/protocol.py` | MQTT topic scheme (`jarvis/node/<id>/...`), QoS-1 message contracts | Sound design, validated by `_smoke.py` integration test | Used by both `brain_pc.py` and `agent_phone.py` |
| **jarvis_prod — Reflector** | `.../jarvis_prod/jobs.py` | Circuit breaker for scheduled jobs; rehydrates consecutive-failure count from SQLite on restart | Smoke-tested logic, never run in production | SQLite `JobLog`; MQTT for dispatch |
| **jarvis_core — Planner/Critic/Executor** | `jarvis_core/` (`brain.py`, `agent/phone_executor.py`) | Reflexion loop: Planner (Llama3 via Ollama) proposes, Critic (Mistral via Ollama) reviews, Executor acts; 3-strike human-in-the-loop escalation to `NEED_ATTENTION.md` | **Never run end-to-end.** Committed once (5dfa76b "V1"), untouched through "V2"/"V2.1" | Local Ollama; own MQTT namespace; ChromaDB (never populated) |
| **jarvis_core — Memory** | `jarvis_core/core/memory.py` (or equivalent) | ChromaDB RAG using `nomic-embed-text` embeddings | Never run — no `brain/chroma/` directory exists on disk | Ollama embeddings; consumed only by jarvis_core's own Planner |
| **jarvis_core — Knowledge Graph** | `jarvis_core/brain/knowledge_graph.md` | Long-term structured knowledge, meant to be appended to hourly by a MaintenanceScheduler | Still template-only; scheduler has never run | Nothing — inert file |
| **Root safety kernel** | `core/` (`executor.py`, `safety.py`, `risk.py`, `sandbox.py`, `idempotency.py`, `undo.py`, `validator.py`, `state.py`, `retry.py`, `tools.py`) | Execution-safety kernel: risk classification, sandboxed paths, idempotent command dispatch, undo history, confirmation gating for dangerous actions | Well-tested in isolation (565 lines of adversarial tests) but **imported by nothing outside its own tests**, and contains a **confirmed live safety-bypass bug** (see §3.1) | Nothing in production; only its own test suite |
| **Web frontend** | `hugginggpt/web/` | Chat UI (Vue3 + Vite + TS + Tailwind + Electron) | Unmodified template fork — not a Chimera component at all yet | Calls OpenAI directly from the browser; talks to none of the four islands |
| **easytool / taskbench** | `easytool/`, `taskbench/` | Vendored Microsoft JARVIS-lineage academic benchmark repos | Unmodified, unintegrated, no LICENSE files | Nothing |
| **Claude Code skill personas** | `.claude/skills/` (10 personas: `ai-product-bible`, `brain-auditor`, `fastapi-production`, `hardware-integration`, `jarvis-architect`, `memory-system-reviewer`, `mqtt-debugger`, `multi-agent-reviewer`, `startup-cto`, `voice-ai-engineer`) | Review/planning tooling for Claude Code sessions on this repo | Lightweight but consistent; usable as-is during consolidation | N/A — advisory tooling, not runtime code |

### 3.1 Confirmed live bug (must be fixed before any consolidation)

In `core/executor.py`, `safety.apply_safety()` runs **before** `risk.get_risk_level()` is evaluated. Concretely: a `delete_file` action (classified high-risk) is rewritten to `move_to_trash` (medium-risk) by the safety layer *before* the risk check runs, so the action is evaluated and dispatched as medium-risk and **bypasses the confirmation gate entirely**. This has been reproduced by direct execution against unmodified `core/executor.py`, `core/safety.py`, `core/risk.py`, and `core/tools.py`. Three tests in the module's own adversarial suite currently fail because of this ordering bug:

- `test_high_risk_action_requires_confirmation_before_safety_rewrite`
- `test_confirmation_bypass_attack_is_rejected`
- `test_external_confirmation_does_not_bypass_safeguards`

This bug must be fixed (reorder the risk check before the safety rewrite, or evaluate risk on the pre-rewrite action name) before root `core/` is wired into anything as a production safety layer. See [`architecture/SECURITY_MODEL.md`](SECURITY_MODEL.md) for full detail.

---

## 4. Target Architecture: Brain as Single Source of Truth

Per `CLAUDE.md`, the target architecture is:

```
USER
  │
  ▼
JARVIS LAYER          ← thin, conversational shell only. No orchestration logic.
  │                     (voice, text, thin clients: desktop / phone / web / CLI)
  ▼
BRAIN LAYER           ← the ONE source of truth. Owns all memory and reasoning.
  │
  ├── Transport / Orchestration Core   (seeded from jarvis_prod: brain_pc.py + protocol.py)
  ├── Reasoning Path                    (seeded from jarvis_core: Planner/Critic/Executor Reflexion loop)
  ├── Memory Manager                    (seeded from jarvis_core: ChromaDB RAG, once actually exercised)
  ├── Context Manager                   (net-new)
  ├── Agent Manager                     (net-new, built on top of the merged reasoning path)
  ├── Knowledge Layer                   (seeded from jarvis_core: knowledge_graph.md, once populated)
  ├── Device Registry                   (net-new)
  └── Action-Safety Kernel              (seeded from root core/, renamed, POST-BUG-FIX)
  │
  ▼
MQTT (over Tailscale)     ← the nervous system; all nodes communicate through it
  │
  ├── PC Node        (Brain host / MQTT broker + NEW pc.* skill executor,
  │                    built on the renamed, fixed action-safety kernel)
  ├── Phone Node      (Nothing Phone 3a Lite — active daily; phone.* skills
  │                    reconciled from agent_phone.py + phone_executor.py)
  ├── Secondary Phone Nodes (OPPO — future camera/dashboard; Vivo 1901 — future sensor/monitor)
  ├── Arduino Node    (UNO R4 WiFi)
  └── Future nodes    (ESP32s; salvaged 10.1" tablet — Innolux P101DCA-AB0 LCD,
                       MEMS mic, stereo speakers 09C26QA/09C26QB — as a
                       Jarvis display/voice terminal)
```

Thin clients (per the wider AI-Operating-System ambition referenced in `CLAUDE.md` and [`vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md)) — desktop, phone, web dashboard, CLI, voice, and eventually AR/VR — all speak to the Brain over the same transport layer. None of them contain orchestration, memory, or safety logic themselves. Today, **zero** of these thin clients exist in this form: `hugginggpt/web/` is a standalone chat SPA with no MQTT/WebSocket client, no store, and no dashboard; the legacy ADB-driven interaction is the closest thing to a working "client" and it talks directly to Island 1, not to a Brain.

### 4.1 Key architectural decisions this target encodes

1. **Brain = merge, not pick-one-discard-one.** `jarvis_prod` has the only deployable, integration-tested transport layer; `jarvis_core` has the only safety-conscious reasoning architecture (Planner/Critic split, 3-strike escalation). Neither alone satisfies `CLAUDE.md`'s Brain definition. The target Brain grafts jarvis_core's reasoning path onto jarvis_prod's transport core.
2. **Root `core/` becomes the Executor's action-safety kernel**, not a standalone system — its idempotency/sandbox/undo/risk/confirmation model sits underneath whichever Executor survives the jarvis_core/jarvis_prod merge. It must be renamed (to remove the `sys.path` collision with `jarvis_core/core/`) and have the risk-ordering bug fixed before this wiring happens.
3. **The legacy stack (Island 1) is not touched** during this transition. It remains the production system in daily use until the merged Brain has demonstrably replaced its currently-used command set.
4. **The `pc.*` skill gap is closed** by building a real PC-side executor on top of the fixed, renamed safety kernel — today `brain_pc.py`'s `_known_skills`, `telegram_gateway.py`'s help text, and `schedule.example.json` all reference `pc.*` commands (e.g. `pc.system.lock`, `pc.shell.run`) that have no receiving agent anywhere in the repo.
5. **The web frontend is rebuilt, not extended**, into a genuine MQTT/WebSocket-aware thin client once the Brain exists to talk to — extending the current OpenAI-in-the-browser template first would just add a fifth island.

---

## 5. Migration Path

The consolidation is staged so that nothing currently working is put at risk before its replacement is proven. Full phase-by-phase detail, exit criteria, and risk analysis live in [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md); this section summarizes the architectural shape of that migration.

### 5.1 Which island becomes the Brain seed

- **Transport/orchestration backbone: jarvis_prod.** It is the only one of the three Python islands with a real integration test (`_smoke.py`: live Brain + fake phone through a real broker) and Windows-correct asyncio wiring (`WindowsSelectorEventLoopPolicy` fix for `aiomqtt`). `brain_pc.py` and `protocol.py` become the skeleton the merged Brain is built on.
- **Reasoning path: jarvis_core.** Its Planner/Critic/Executor Reflexion loop, 3-strike `NEED_ATTENTION.md` human-in-the-loop gate, and `sandbox.safe_path()` chokepoint are the closest existing approximation of `CLAUDE.md`'s actual Brain vision — but it has never been run end-to-end, so it must first be validated in isolation (live Ollama, live Mosquitto, one full Reflexion cycle observed) before being grafted onto jarvis_prod's transport.
- **Action-safety layer: root `core/`, renamed and bug-fixed.** Not adopted as-is. Fix the risk/safety ordering bug, rename the package to remove the `jarvis_core/core/` collision, then adopt it as the Executor's safety kernel — its `TOOLS` registry (`open_app`, `delete_file`, `send_sms`, `shutdown_device`) needs to be re-mapped onto Chimera's actual skill names (`phone.*`, `pc.*`) rather than kept as a generic desktop-agent surface.

### 5.2 What gets deprecated / archived (not deleted)

- Whichever of `jarvis_prod` or `jarvis_core` does not end up as the literal merged codebase is formally archived (git-tagged, moved to an `archive/` path, history preserved) once the merged Brain passes its own smoke test — never deleted outright.
- `easytool/` and `taskbench/` are either properly attributed (LICENSE/NOTICE crediting the Microsoft JARVIS lineage) or removed if never going to be referenced; this is a low-risk, low-priority cleanup independent of the Brain merge.
- The legacy stack (`hugginggpt/server/awesome_chat.py`) is deprecated only after the merged Brain has run in production for a defined trial period and has demonstrably replaced its most common daily commands. That decision is explicitly out of scope for the Brain-merge migration itself.

### 5.3 How root `core/` gets wired in as the safety layer for all execution paths

1. **Fix first, wire never-as-is.** The risk/safety ordering bug (§3.1) is fixed and all tests in `core/tests/`, `tests/test_adversarial_execution.py`, and `tests/test_executor_safety.py` pass, *before* any other system is allowed to import it.
2. **Rename to remove the collision.** Root `core/` is renamed (e.g. to `action_kernel/` or similar) so it can never be shadowed by or shadow `jarvis_core/core/` depending on `sys.path` order.
3. **Adopt as the Executor's substrate.** Whichever Executor survives the jarvis_core/jarvis_prod reasoning-path merge sits directly on top of the renamed kernel: every dispatched action — whether it originates from jarvis_core's Critic-approved plan or from a direct MQTT command via jarvis_prod's transport — passes through the kernel's risk classification, sandboxing, idempotency store, and confirmation gate before execution.
4. **Extend the tool registry.** The kernel's existing 8-tool registry (`open_app`, `delete_file`, `send_sms`, `shutdown_device`, etc.) is extended/remapped to cover Chimera's real skill surface: `phone.*` skills (from `agent_phone.py` / `phone_executor.py`) and the currently-nonexistent `pc.*` skills (`pc.system.lock`, `pc.shell.run`, etc.), which get a real PC-side executor built on this same kernel — closing the gap where `brain_pc.py` advertises `pc.*` commands today with no agent to receive them.
5. **One safety layer, no exceptions.** Once wired in, no execution path in the merged Brain — reasoning-path-originated or direct-command-originated — is permitted to bypass the kernel. This is the concrete mechanism by which `CLAUDE.md`'s "Brain owns memory" and fault-tolerance principles extend to "Brain owns safety-gated execution."

### 5.4 Sequencing constraint

Per the roadmap, thin-client and hardware-node expansion (rebuilding `hugginggpt/web/`, repurposing OPPO/Vivo, integrating the salvaged tablet LCD/speakers) does not begin until the Brain merge (islands consolidated, safety kernel wired in, legacy stack still running unmodified) is complete and validated. Building more clients against a still-fragmented, three-way Brain would multiply consolidation debt rather than pay it down.

---

## 6. Open Questions

- Rotation status of the 3 previously-leaked secrets (Gmail app password, ElevenLabs key, GitHub PAT) purged via `git filter-repo` — TBD, confirm with user.
- Final name for the renamed root `core/` package — TBD, confirm with user (suggested: `action_kernel/`, pending ADR).
- Whether `jarvis_prod`'s Reflector 3-strike circuit breaker needs time-decay before Phase 4 live deployment, given its risk of silently and permanently disabling a heartbeat-driven skill (e.g. a 15-minute battery watchdog) after one bad half-hour — TBD, confirm with user.

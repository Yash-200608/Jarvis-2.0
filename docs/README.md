# JARVIS Documentation

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

This directory is the canonical, permanent documentation system for the JARVIS / Chimera Ecosystem project. It is intended to be the source of truth for every future Claude Code session, every human contributor, and every architecture or planning decision made on this repo. If a decision, a piece of state, or a claim about "what exists today" isn't written here, treat it as unknown rather than assumed.

This document (`docs/README.md`) is the entry point. Read it first, then follow the links below in the order that matches what you're trying to do.

---

## TL;DR — Current Reality (as of 2026-07-01)

JARVIS today is **three fragmented, non-communicating Python implementations**, plus one dormant, disconnected safety module, sitting underneath a much larger aspirational vision that has not been built yet. The **legacy stack** (`hugginggpt/server/`, Flask + HuggingFace + Anthropic Claude API, Python 3.10) is the only system in real daily use, driven over ADB. **jarvis_prod** (`hugginggpt/server/jarvis_prod/`, an MQTT v5 / asyncio brain-and-phone-agent pair) is smoke-tested against a real broker but has never had a live Tailscale run. **jarvis_core** (`jarvis_core/`, a Planner/Critic/Executor Reflexion loop over local Ollama models with ChromaDB RAG) was committed once and has never been run end-to-end at all. Separately, root **`core/`** is a 512-line execution-safety kernel (idempotency, sandboxing, undo, risk scoring) that is imported by nothing outside its own test suite, and that test suite currently has 3 failing tests exposing a confirmed live bug: `core/executor.py` rewrites high-risk actions (e.g. `delete_file`) into lower-risk equivalents *before* checking risk level, silently bypassing confirmation. None of these four things communicate with each other, and none of them implement the "Brain" that `CLAUDE.md`'s Chimera Ecosystem vision — or the larger AI Operating System ambition layered on top of it — describes. The documents in this directory describe both that target vision and the honest, evidence-grounded gap between it and what exists in the repo right now.

---

## Document Index

### North Star
- [`VISION.md`](VISION.md) — The project's one-page North Star: a single mission sentence, what it commits the project to, and what it explicitly is not. Read this before anything else — 90 seconds.

### Brain (Law)
- [`brain/BRAIN_CONSTITUTION.md`](brain/BRAIN_CONSTITUTION.md) — The closest thing this project has to immutable law: crisp definitions of Brain, Client, Node, Agent, Tool, Skill, Service, Memory, Orchestration, Context, and Identity, plus 6 numbered invariants that may never change without a recorded ADR. Changes rarely; everything else changes often.

### Vision
What JARVIS is meant to become, and the product philosophy governing how we get there.

- [`vision/JARVIS_VISION.md`](vision/JARVIS_VISION.md) — The AI Operating System ambition: Brain as single source of truth, thin clients across desktop/phone/web/CLI/voice/AR-VR, the Chimera Ecosystem layering (Jarvis shell -> Brain -> MQTT/Tailscale mesh -> hardware nodes).
- [`vision/PRODUCT_PRINCIPLES.md`](vision/PRODUCT_PRINCIPLES.md) — Full narrative of the 15 design principles this project is judged against, each with rationale and current-reality evidence (file/path level).

### Architecture
The target system design, and how it maps (or doesn't yet map) onto existing code.

- [`architecture/ARCHITECTURE_PRINCIPLES.md`](architecture/ARCHITECTURE_PRINCIPLES.md) — The canonical, ID-stable registry (`P-01`...`P-15`) of the same 15 principles above, one line each, meant to be cited by ID from ADRs and other docs without restating them.
- [`architecture/SYSTEM_ARCHITECTURE.md`](architecture/SYSTEM_ARCHITECTURE.md) — Overall layering, the three-Brain-islands problem, and the recommended consolidation architecture (jarvis_prod transport + jarvis_core reasoning + root `core/` as the safety kernel underneath, once fixed and renamed).
- [`architecture/AGENT_ARCHITECTURE.md`](architecture/AGENT_ARCHITECTURE.md) — Agent/orchestration model: jarvis_core's Planner (Llama3) / Critic (Mistral) / Executor Reflexion loop as the closest existing analog to a real Agent Manager, and what a genuine multi-agent orchestrator would require beyond it.
- [`architecture/MEMORY_ARCHITECTURE.md`](architecture/MEMORY_ARCHITECTURE.md) — Memory Manager target design; current state is jarvis_core's never-persisted ChromaDB RAG (`core/memory.py`, no `brain/chroma/` directory exists yet) versus jarvis_prod's SQLite JobLog (execution history only, not semantic memory).
- [`architecture/SECURITY_MODEL.md`](architecture/SECURITY_MODEL.md) — Safety/risk model: the confirmed `core/executor.py` risk-ordering bug, fail-closed defaults in `risk.py`, the flat single-tier MQTT ACL in `mosquitto.conf.example`, absence of MQTT TLS, and the leaked-and-purged secrets rotation advisory.
- [`architecture/TOOL_ROUTING.md`](architecture/TOOL_ROUTING.md) — How commands are meant to route to skills/tools across nodes; documents the missing `pc.*` skill executor (referenced by `brain_pc.py`, `telegram_gateway.py`, and `schedule.example.json` but implemented nowhere) versus the working `phone.*` skills in `agent_phone.py`.

### Specifications
Detailed specs for individual subsystems, target and current.

- [`specifications/VOICE_SYSTEM.md`](specifications/VOICE_SYSTEM.md) — Voice interface target spec; current state is limited to legacy-stack `SpeechRecognition`/`pyttsx3` dependencies not wired into jarvis_prod or jarvis_core.
- [`specifications/UI_SPEC.md`](specifications/UI_SPEC.md) — Thin-client / dashboard target spec; current state is `hugginggpt/web`, an unmodified Vue3+Vite+TS+Tailwind+Electron HuggingGPT/ChatGPT template fork with no MQTT client, no store, and an OpenAI key stored in `localStorage`.
- [`specifications/BRAIN_SPEC.md`](specifications/BRAIN_SPEC.md) — The Brain's target responsibilities (memory, context, agent orchestration, knowledge graph, device registry) mapped against the three existing candidate implementations.
- [`specifications/3D_ENGINE.md`](specifications/3D_ENGINE.md) — 3D generation / AR-VR client target spec; TBD - confirm with user, no current implementation exists.
- [`specifications/COMPUTER_CONTROL.md`](specifications/COMPUTER_CONTROL.md) — Computer-use / automation target spec; current state is unused `pyautogui` dependencies with no PC-side dispatcher, and the missing `pc.*` skill executor.

### Roadmap

- [`roadmap/MASTER_ROADMAP.md`](roadmap/MASTER_ROADMAP.md) — The phased plan: Phase 0 (stop the bleeding — fix the safety bug, fix `.gitignore`, rotate secrets, rename `core/`), Phase 1 (decide and document the canonical Brain), Phase 2 (prove jarvis_core actually runs), Phase 3 (merge transport + reasoning), Phase 4 (first live Tailscale deployment), Phase 5 (retire/archive losing islands), Phase 6 (thin-client expansion). Includes the priority matrix and risk assessment.

### Context
Background, history, and living status — the fastest way to get oriented.

- [`context/AI_SESSION_BOOTSTRAP.md`](context/AI_SESSION_BOOTSTRAP.md) — The literal entry point for any new session (human or AI): an ordered reading protocol plus pre-flight checklists for before writing code and before ending a session.
- [`context/PROJECT_CONTEXT.md`](context/PROJECT_CONTEXT.md) — Repo inventory: the three Python islands, root `core/`, `easytool/`/`taskbench/` vendored academic repos, hardware inventory from `CLAUDE.md`, and the `.claude/skills/` persona scaffold.
- [`context/DECISION_LOG.md`](context/DECISION_LOG.md) — Architecture Decision Records (ADRs) in full schema (Status, Owner, Risk Level, Category, semantic ID, Depends On/Supersedes/Conflicts With, Implementation State, Evidence vs. Inference), including ADR-011, the not-yet-made decision of which island becomes the canonical Brain (Phase 1 of the roadmap).
- [`context/DESIGN_HISTORY.md`](context/DESIGN_HISTORY.md) — How the repo got here: commit history highlights (`5dfa65f` "V1" jarvis_core committed once and never touched again through `4b32760` "V2" and `fe41bf5` "V2.1"), the `filter-repo` history rewrite to purge 3 leaked secrets, and other structural history.
- [`context/CURRENT_STATUS.md`](context/CURRENT_STATUS.md) — Living snapshot of what is actually working right now versus what is aspirational. Update this first whenever anything changes.

---

## Read This First

If you are a human or a Claude Code session joining this project cold, start at [`context/AI_SESSION_BOOTSTRAP.md`](context/AI_SESSION_BOOTSTRAP.md) — it is the literal entry point and defines the full required reading order (North Star → Brain Constitution → Current Status → Decision Log → Roadmap → open risks → next milestone), plus checklists for before you write code and before you end a session. Do not skip it in favor of jumping straight to a specific doc below.

---

## How to Keep This In Sync

Per this project's `CLAUDE.md`, documentation is part of the product, not an afterthought bolted on after the fact. Every future session that changes architecture, makes or reverses a decision, fixes the Phase 0 safety bug, runs jarvis_core end-to-end for the first time, merges islands, or otherwise changes what is actually true about this repo must update the relevant file(s) in this directory in the same body of work — not as a follow-up task that may or may not happen. At minimum:

- Update [`context/CURRENT_STATUS.md`](context/CURRENT_STATUS.md) whenever the working/broken/untested status of any component changes.
- Append to [`context/DECISION_LOG.md`](context/DECISION_LOG.md) whenever an architectural decision is made, changed, or reversed — do not silently overwrite prior entries.
- Append to [`context/DESIGN_HISTORY.md`](context/DESIGN_HISTORY.md) for significant structural events (merges, archivals, rewrites).
- Update the relevant `architecture/` or `specifications/` file whenever the target design for that subsystem changes, and keep the roadmap in [`roadmap/MASTER_ROADMAP.md`](roadmap/MASTER_ROADMAP.md) reflecting actual phase completion, not intended completion.

Do not let this document set drift back into the state the repo was in before 2026-07-01, where three Python islands and a safety kernel evolved with zero shared, written record of what existed or why.

---

## Provenance

This documentation set was generated on 2026-07-01 as a planning-phase deliverable, produced from a full repo audit (architecture assessment, strengths, technical debt, missing systems, recommended architecture, priority matrix, development phases, risk assessment, and folder structure recommendations). It was written **before** any of the target architecture, and before even the Phase 0 safety fixes described in [`roadmap/MASTER_ROADMAP.md`](roadmap/MASTER_ROADMAP.md), were implemented. Every other document in this set should be read with that in mind: they describe a plan and an honestly-assessed starting point, not a completed system.

**Revision (same day, 2026-07-01):** following review feedback on long-term maintainability, the system was evolved without discarding the original audit: [`VISION.md`](VISION.md), [`architecture/ARCHITECTURE_PRINCIPLES.md`](architecture/ARCHITECTURE_PRINCIPLES.md), [`brain/BRAIN_CONSTITUTION.md`](brain/BRAIN_CONSTITUTION.md), and [`context/AI_SESSION_BOOTSTRAP.md`](context/AI_SESSION_BOOTSTRAP.md) were added, and [`context/DECISION_LOG.md`](context/DECISION_LOG.md) was restructured from free-form ADR prose into a full schema (Status, Owner, Risk Level, Category tags, semantic IDs, Depends On/Supersedes/Conflicts With, Implementation State, Assumptions, and an explicit Evidence-vs-Inference split) with every original fact, date, and commit reference preserved. No prior content was discarded — the original 18 documents remain intact and are now cross-linked from and to these 4 additions.

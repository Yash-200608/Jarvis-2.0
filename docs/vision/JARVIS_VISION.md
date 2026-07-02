# JARVIS Vision

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

## Purpose of This Document

This document states the long-horizon vision for JARVIS: what the system is ultimately meant to become if development succeeds over years, not what exists today. It is deliberately written at high altitude — inspiring, directional, and not constrained by the current repo's state.

For the concrete, near-term, hardware-grounded instantiation of this same vision, see [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md) and the root `CLAUDE.md`. For an honest accounting of what is actually built and running today, see [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md). This document should not be read in isolation — see "Reconciling With CLAUDE.md" below for how the two altitudes relate.

---

## The Vision: An AI Operating System

JARVIS is not meant to be a chatbot, a voice assistant skill, or a single application. The long-term goal is an **AI Operating System** — a persistent, reasoning intelligence that sits underneath every device and interface the user touches, in the spirit of the fictional JARVIS/FRIDAY (Iron Man), and drawing concrete architectural patterns from real systems already proving pieces of this out: Claude Code and OpenAI Codex (agentic coding and tool-use loops), Devin (autonomous multi-step task execution with planning and self-correction), Manus (general-purpose autonomous agents operating a computer), OpenClaw-style browser/computer-use agents, and Home Assistant (a mature, mature-protocol home-automation hub as a model for device orchestration and local-first control).

The defining property of an AI Operating System, as distinct from an AI *application*, is this: the intelligence is the constant, and the interface is disposable. A user should be able to start a task by talking to a phone, continue it by typing in a terminal, watch it finish on a wall-mounted display, and have the system remember it happened — because the reasoning, memory, and execution authority all live in one place (the Brain, see below), not in whichever app happened to be open.

### Capabilities in Scope (Long-Term)

These are the capability domains the vision covers. None of them are fully implemented today (see "How Far We Are Today"); this list is the target surface area, not a status report.

| Domain | What it means |
|---|---|
| **Natural conversation** | Multi-turn, context-aware dialogue that doesn't reset between sessions or devices. |
| **Persistent long-term memory** | Facts, preferences, project history, and relationship context survive indefinitely and are queryable, not just appended to a growing prompt. |
| **Speech recognition & synthesis** | Wake-word detection, streaming ASR, and natural TTS as a first-class interface, not a bolt-on. |
| **Reasoning, planning, reflection, learning** | Multi-step plans, self-critique before/after execution, and durable learning from past failures (not just in-context few-shot behavior). |
| **Task execution** | Turning a stated intent into a completed real-world action, tracked to completion. |
| **Computer control** | Operating a desktop/browser directly — clicking, typing, reading screens — for tasks with no API. |
| **Research** | Multi-source web/document research with synthesis and citation, not single-shot search-and-summarize. |
| **Coding** | Autonomous or semi-autonomous software development across the ecosystem's own codebase and others. |
| **Robotics / IoT** | Direct control of physical actuators and sensors (starting with the Arduino/ESP32 nodes already in inventory). |
| **3D generation** | Generating 3D assets/scenes from natural-language or reference input. |
| **Architecture generation** | Generating and reasoning about system/software architecture diagrams and structures (self-referentially, this doc set is a step toward the Brain being able to reason about its own architecture). |
| **Image / video generation** | Multimodal content generation as a callable capability, not a separate product. |
| **Automation** | Cross-cutting rule- and trigger-based automation across every connected node. |
| **Home control** | Physical home/room-level device control (lighting, locks, climate), Home-Assistant-style. |
| **Workflow orchestration** | Composing many small actions/skills into a durable, resumable, multi-step workflow. |
| **Project management** | Tracking the user's own projects (including this one) as first-class, queryable entities. |
| **Digital twin management** | Maintaining an internal model of the user's devices, environment, and state that stays in sync with reality. |
| **Knowledge management** | A living knowledge graph, not a static document dump. |
| **Real-time monitoring** | Continuous awareness of device/system/environment state, not just request-response. |
| **Multimodal understanding** | Text, voice, image, video, and sensor input understood in a unified context. |
| **AR/VR interfaces (eventual)** | Spatial, headset/glasses-based interaction as a future client, once the Brain/thin-client split is mature. |

### The Thin-Client Principle

No client — desktop app, phone node, web dashboard, CLI, voice interface, or eventual AR/VR headset — should hold reasoning, memory, or orchestration logic. Every client is a thin surface that captures input, renders output, and defers everything else to the Brain. This is the single most important architectural constraint the vision imposes, and it is why CLAUDE.md's "Jarvis stays lightweight" principle exists: it is the near-term expression of this same rule. See [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) for how this is meant to be enforced in code, and [`../architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) for how reasoning/planning/reflection is meant to be structured inside the Brain.

---

## Reconciling With CLAUDE.md: Two Altitudes, One Project

The root `CLAUDE.md` describes the **Chimera Ecosystem**: Brain (cognition layer) / Jarvis (interaction layer) / MQTT+Tailscale (connectivity layer), instantiated on a specific, real hardware inventory — this user's main desktop PC, a Nothing Phone 3a Lite, a spare OPPO phone, a spare Vivo 1901, an Arduino UNO R4 WiFi, and salvaged 10.1" tablet components (Innolux P101DCA-AB0 LCD, MEMS mic, stereo speakers, HQK5A03 camera).

This is **not a different project** from the AI Operating System vision above. It is the same project described at a different altitude:

- **This document (`JARVIS_VISION.md`)** is the 10-years-out ceiling: what JARVIS becomes if every capability domain above is eventually built out, on any hardware, for any user. It is intentionally unconstrained by what exists in the repo today.
- **`CLAUDE.md` and [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md)** are the concrete, near-term instantiation of that same vision on *this specific user's actual hardware, right now*. "Brain" in CLAUDE.md is the first real implementation target for the Brain described in this vision. "Jarvis" in CLAUDE.md is the first real thin client. The Nothing Phone 3a Lite is not a metaphor for "a phone node" — it is the literal phone this system talks to today over Termux/SSH.

Concretely, the mapping is:

| Vision concept (this document) | Chimera concept (CLAUDE.md) | Current concrete target |
|---|---|---|
| The Brain (reasoning + memory + orchestration) | Brain layer | Main desktop PC — see [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md) |
| Thin client / interaction surface | Jarvis layer | Voice/text shell, mobile node, (future) web dashboard |
| Node mesh / device fabric | MQTT + Tailscale | Mosquitto broker over Tailscale, `chimera/<node>/<action>` topics |
| Robotics/IoT domain | Hardware inventory | Arduino UNO R4 WiFi (active), future ESP32 nodes |
| Multimodal/voice/vision client | Salvaged tablet components | Innolux P101DCA-AB0 LCD + MEMS mic + stereo speakers, earmarked as a future dedicated Jarvis display/voice terminal |
| Additional device nodes | Spare phones | OPPO (future camera/dashboard node), Vivo 1901 (future sensor/monitor node) |

Any time this vision document and CLAUDE.md appear to disagree, CLAUDE.md and [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md) win for near-term implementation decisions — they describe what is actually being built next, on real hardware. This document should be consulted when a near-term decision risks foreclosing the long-term vision (e.g., hard-coding a single-client assumption into the Brain would violate the thin-client principle above even though no thin client exists yet).

Product-level judgment calls about what to prioritize within this vision belong in [`PRODUCT_PRINCIPLES.md`](./PRODUCT_PRINCIPLES.md). Architectural decisions already made or under active debate belong in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md). The reasoning behind major pivots belongs in [`../context/DESIGN_HISTORY.md`](../context/DESIGN_HISTORY.md). The phased plan for closing the gap between this document and reality belongs in [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md).

---

## How Far We Are Today

As of 2026-07-01, essentially none of the AI Operating System vision above is implemented as a working, integrated system. What exists is three separate, non-communicating Python implementations plus one disconnected safety module, none of which share memory, state, or a device registry with each other:

The **legacy stack** (`hugginggpt/server/awesome_chat.py`, Flask + HuggingFace models + Anthropic Claude API, Python 3.10) is the only system in real daily use, driven over ADB — it has no persistent memory, no MQTT connectivity, and no relationship to the Chimera hardware mesh described in CLAUDE.md. **jarvis_prod** (`hugginggpt/server/jarvis_prod/`, MQTT v5 over Tailscale, Python 3.12) implements the transport skeleton the vision needs — asymmetric MQTT session semantics, a working brain/phone round-trip — and is smoke-tested, but has never had a live Tailscale run and has no memory layer or reasoning loop of its own; its advertised `pc.*` skills (system lock, shell execution) have no receiving executor anywhere in the repo. **jarvis_core** (`jarvis_core/`) is structurally the closest thing to the Brain this vision describes — a Planner/Critic/Executor Reflexion loop with ChromaDB-backed RAG memory — but it was committed once and never run end-to-end: no `.env`, no history log, no persisted embeddings, and its knowledge graph file is still template-only. The root **`core/`** module is a genuinely well-tested execution-safety kernel (idempotency, sandboxing, undo, risk-gating) that nothing outside its own test suite imports, and it currently contains a confirmed live bug where a high-risk `delete_file` action bypasses its own confirmation gate due to an ordering mistake in `executor.py`.

There is no shared Memory Manager, no Context Manager, no Device Registry, no Agent Manager coordinating multiple concurrent agents, and no working knowledge graph anywhere in the repo. The web frontend (`hugginggpt/web/`) is an unmodified HuggingGPT/ChatGPT demo template with no dashboard, no MQTT client, and no state store — it cannot yet serve as a thin client for anything. None of the hardware inventory beyond the Nothing Phone 3a Lite has a live software node. In short: the vision in this document is real and worth building toward, the architecture in CLAUDE.md is the right first concrete target, but the repo today is three unreconciled prototypes and a dormant safety kernel, not a system. See [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) for the full, itemized state of each component and [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) for the phased plan to close this gap.

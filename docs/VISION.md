# JARVIS — North Star

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

> Read this first, before anything else in `docs/`. 90 seconds, one sentence to hold onto.

---

**Build a local-first, modular personal AI Operating System that unifies reasoning, memory, automation, and human-computer interaction across every device the user owns, through one persistent Brain that never lets a client hold logic it shouldn't.**

## What this commits us to

The Brain is the sole source of truth for reasoning, memory, and orchestration — no other component persists state or makes routing decisions on its own. Every surface the user touches (voice, phone, web, CLI, a future wall display) is a thin client: it captures input, renders output, and defers everything else back to the Brain. Local-first means the system keeps working when the network doesn't — cloud inference is an enhancement layered on top of local capability, never the thing basic operation depends on. The ecosystem grows by addition, not rewrite: new nodes (a phone, an Arduino, a salvaged tablet display) and new capabilities plug into the same Brain instead of forking a new one.

## What this is NOT

This is not a chatbot wrapper around an API, not a cloud SaaS product, and not a single-device app. It is explicitly not what the repo contains today: per [context/CURRENT_STATUS.md](context/CURRENT_STATUS.md), the current state is three fragmented, non-communicating implementations — the **legacy stack** (daily-use, ADB-driven, no persistent memory), **jarvis_prod** (MQTT transport skeleton, smoke-tested, never run live), and **jarvis_core** (a Planner/Critic/Executor Reflexion prototype, never run end-to-end) — plus a dormant **root `core/`** safety kernel with a confirmed live bug. None of these share memory, state, or a device registry with each other or with a single Brain. This North Star is the aspirational target; the honest, itemized gap between it and what runs today is tracked in [context/CURRENT_STATUS.md](context/CURRENT_STATUS.md) and [roadmap/MASTER_ROADMAP.md](roadmap/MASTER_ROADMAP.md), not glossed over here.

## How every decision should be judged against this

Every entry in [context/DECISION_LOG.md](context/DECISION_LOG.md) and every principle in [architecture/ARCHITECTURE_PRINCIPLES.md](architecture/ARCHITECTURE_PRINCIPLES.md) should trace back to the sentence above. If a proposed change — a new dependency, a shortcut that puts logic in a client, a cloud-only feature with no local fallback — doesn't serve this sentence, that is itself a reason to question the change before asking whether it's convenient or fast to build.

## Read next

- [vision/JARVIS_VISION.md](vision/JARVIS_VISION.md) — the full narrative vision, capability scope, and how it reconciles with `CLAUDE.md`'s concrete Chimera hardware instantiation.
- [architecture/ARCHITECTURE_PRINCIPLES.md](architecture/ARCHITECTURE_PRINCIPLES.md) — the canonical, ID-stable principle registry this sentence implies.
- [brain/BRAIN_CONSTITUTION.md](brain/BRAIN_CONSTITUTION.md) — what "the Brain" concretely is: terminology and invariants.
- [context/CURRENT_STATUS.md](context/CURRENT_STATUS.md) — the honest, current-state accounting of the gap between this North Star and the repo today.

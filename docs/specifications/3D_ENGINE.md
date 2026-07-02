# 3D Engine & Generative Design Specification

Status: Planning phase — pre-implementation
Last updated: 2026-07-01

## 0. Reality Check

This is a **greenfield specification**. There is no 3D, CAD, or geometry-generation code anywhere in the JARVIS repository today. A full repo audit confirmed zero references to Blender, FreeCAD, OpenCascade, Three.js, React Three Fiber, or any mesh/geometry library across all three Python islands (legacy stack, jarvis_prod, jarvis_core), root `core/`, and the `hugginggpt/web` frontend. This document describes a **target capability**, not a system in progress.

This capability is explicitly a **late roadmap item (Phase 6+)**. It depends on:

- The Brain consolidation (Phases 1-5) landing a single canonical orchestrator with a real tool-invocation contract — see [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) and [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md).
- A rebuilt dashboard client capable of hosting a 3D viewport — see [`UI_SPEC.md`](UI_SPEC.md). The current `hugginggpt/web` is an unmodified HuggingGPT/ChatGPT chat-bubble template with no store, no dashboard shell, and no viewport of any kind to attach a 3D renderer to.

Do not begin implementation work on this spec until those two prerequisites are marked complete in [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md). Building a 3D pipeline against an unsettled, three-way-fragmented Brain (per [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md)) would create a fourth disconnected island, repeating the exact fragmentation pattern this project is trying to escape.

Given its distance from current reality, this spec is intentionally **lighter and less prescriptive** than the other architecture/specification docs in this set. It defines the interface contract — how a 3D-generation capability would plug into the Brain's tool registry and how results would surface in the dashboard — rather than deep implementation detail (render pipelines, geometry-node graphs, mesh optimization, etc.), which should be designed only once Phase 6 actually starts and current tooling/library choices can be re-verified.

---

## 1. Scope

The target capability is AI-driven generation and viewing of 3D content across several domains:

| Domain | Examples |
|---|---|
| Architecture / buildings | Floor plans, single structures, multi-room layouts, elevation views |
| Furniture & interior objects | Chairs, tables, storage, fixtures, room staging |
| Vehicles | Concept shapes, simplified mechanical assemblies |
| Mechanical parts | Brackets, enclosures, precision-dimensioned components (candidates for 3D printing or fabrication) |
| Scenes / environments | Procedural landscapes, backdrops, lighting setups |
| Product prototypes | Early-stage industrial design mockups |

This spec covers **generation** (Brain-invoked, agentic) and **viewing** (dashboard-embedded, interactive). It does not cover physical fabrication/CNC/slicing workflows, real-time multiplayer 3D collaboration, or game-engine-grade runtime physics — those are out of scope unless a future revision says otherwise.

---

## 2. Target Toolchain

Three distinct technologies, each solving a different part of the problem. None are installed or integrated today; this table is a target-state design choice, not a current dependency list.

| Tool | Role | Why |
|---|---|---|
| **Blender** (Python API + Geometry Nodes) | Primary generative/procedural engine | Free, scriptable end-to-end via `bpy`, strong procedural modeling (Geometry Nodes) for buildings/furniture/scenes, renders to standard formats (glTF, OBJ, FBX) that a web viewer can consume |
| **FreeCAD / OpenCascade** | Precision mechanical & architectural CAD | Needed wherever exact dimensions, parametric constraints, and engineering tolerances matter (mechanical parts, architectural floor plans meant for real-world construction) — Blender's mesh-based modeling is not dimensionally rigorous enough for this class of output |
| **Three.js / React Three Fiber** | In-dashboard 3D viewport | Runs in-browser inside the rebuilt dashboard client (per [`UI_SPEC.md`](UI_SPEC.md)); consumes glTF/GLB output from the generation backend; no server-side rendering required for basic viewing/orbit/inspect interactions |

Generation happens server-side (wherever the Brain or its delegated agent runs); viewing happens client-side in the dashboard. The two are connected only by an artifact (a 3D file) and metadata — the dashboard never talks to Blender or FreeCAD directly.

Choice of Blender vs. FreeCAD per request is a routing decision made by the 3D-generation agent (see Section 4), not by the Brain itself — the Brain only knows it is invoking "the 3D generation tool" through the unified tool registry.

---

## 3. Relationship to the Rest of the System

Per [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) and CLAUDE.md's layering principle ("Brain owns memory... Agents never access databases directly"), 3D generation is modeled as **one more skill/agent behind the tool registry**, not a special-cased subsystem:

```
User request ("design me a floor plan for a 2BR apartment")
  -> Jarvis (thin conversational shell, no orchestration logic)
  -> Brain (single source of truth, Planner/Critic/Executor per
            AGENT_ARCHITECTURE.md)
  -> Tool Registry lookup: "3d.generate" resolves to the
     3D-generation agent
  -> 3D-generation agent (Blender/FreeCAD backend, runs as its
     own process/node — NOT inside the Brain process)
  -> Artifact produced (glTF/GLB + metadata)
  -> Brain records artifact reference in Memory (per
     MEMORY_ARCHITECTURE.md) — NOT the raw mesh data, just a
     pointer/URI + summary
  -> Dashboard requests the artifact via the same event/WebSocket
     channel described in UI_SPEC.md, renders it in a
     Three.js/R3F viewport panel
```

This keeps the same discipline CLAUDE.md already mandates for hardware nodes: the Brain dispatches, it does not itself render geometry, and the 3D agent is swappable/independently restartable exactly like a phone or PC node would be. See [`../architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) for how agent lifecycles and the Planner/Critic gate are expected to work generally, and [`../architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md) for how a 3D-generation action would be risk-classified (file writes, potentially large compute jobs) if root `core/`'s safety kernel — once fixed and renamed per [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) — becomes the Executor's safety layer.

---

## 4. Interface Contract

This is the part of the spec that matters most today, because it constrains how *anything* built later must shape itself to fit the Brain's tool-invocation model described in [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md).

### 4.1 Tool registration shape

The 3D-generation capability registers as a tool in the same unified registry that root `core/`'s `tools.py` already models for its 8 desktop-agent tools (`open_app`, `delete_file`, `send_sms`, `shutdown_device`, etc.) — same pattern, new namespace. Proposed skill name: `3d.generate` (namespaced consistently with CLAUDE.md's `chimera/<node>/<action>` MQTT convention, adapted to the Brain's internal tool-call convention rather than a literal MQTT topic, since 3D generation is compute-bound and likely runs on the PC node rather than a lightweight phone/Arduino node).

Minimum contract fields (illustrative — exact schema TBD, confirm with user once Phase 6 design starts):

```
tool: "3d.generate"
input:
  domain: enum [building, floor_plan, furniture, vehicle,
                mechanical_part, scene, product_prototype]
  prompt: string                # natural-language description
  precision_required: bool      # true routes to FreeCAD/OpenCascade,
                                 # false routes to Blender
  constraints: object | null    # dimensions, materials, tolerances
                                 # (mechanical/architectural only)
  output_format: enum [glTF, GLB, OBJ, FBX, STEP]
risk_level: medium               # writes files, spawns a compute job;
                                  # exact classification TBD per
                                  # SECURITY_MODEL.md once designed
output:
  artifact_uri: string           # where the Brain/dashboard can fetch it
  thumbnail_uri: string | null
  format: string
  generation_log: string         # what the agent actually did,
                                  # for Critic review / audit trail
  status: enum [success, partial, failed]
```

### 4.2 Routing logic (agent-internal, not Brain-visible)

The Brain does not decide Blender-vs-FreeCAD. It calls `3d.generate` with `precision_required` and a `domain`; the 3D-generation agent itself decides which backend to invoke:

- `precision_required: true`, or `domain` in `{mechanical_part}`, or an architectural request explicitly meant for construction/fabrication -> FreeCAD/OpenCascade path.
- Everything else (concept buildings, furniture, vehicles, scenes, product prototypes at ideation stage) -> Blender + Geometry Nodes path.

This mirrors the same "Brain dispatches, node decides implementation" separation CLAUDE.md already requires between Brain and hardware nodes — the Brain should not need to know Blender's Python API exists.

### 4.3 Dashboard surfacing

Per [`UI_SPEC.md`](UI_SPEC.md) (to be written/confirmed), the dashboard is expected to have a panel-based layout capable of hosting arbitrary result viewers. A 3D result should:

1. Appear as a chat/response artifact (thumbnail + "open in viewport" affordance), consistent with how other tool outputs surface.
2. On open, load into a dedicated Three.js/React Three Fiber viewport panel — orbit controls, basic lighting, wireframe toggle as a baseline; nothing more elaborate until real usage demands it.
3. Never require the dashboard to have Blender/FreeCAD installed or reachable — the viewport only ever consumes the exported glTF/GLB artifact, never talks to the generation backend live.

This keeps the thin-client principle intact: the dashboard is a viewer, not a modeling tool.

---

## 5. Explicit Non-Goals (for now)

- No real-time collaborative editing of generated models.
- No physics simulation or game-engine runtime.
- No direct manipulation/sculpting UI in the dashboard — viewing and orbiting only, at least through the first implementation pass.
- No fabrication/slicing/G-code pipeline.
- No mobile-node (phone/Arduino) involvement — this is a PC-node-class, compute-bound capability only, consistent with the hardware inventory in CLAUDE.md and [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md).

---

## 6. Open Questions (TBD — confirm with user)

- Exact hosting model for the 3D-generation agent: same process as a future `pc_executor` node, or a fully separate service? TBD — confirm with user.
- Whether Blender/FreeCAD run headless on the main desktop PC (the confirmed "Brain host" per CLAUDE.md's hardware inventory) or on a dedicated compute node. TBD — confirm with user.
- Storage location/lifecycle for generated artifacts (local disk vs. object storage; retention policy) — depends on decisions made in [`../architecture/MEMORY_ARCHITECTURE.md`](../architecture/MEMORY_ARCHITECTURE.md) that have not yet been made. TBD — confirm with user.
- Whether generation jobs are synchronous (Brain waits) or async (job queued, dashboard polls/subscribes for completion) — likely async given Blender render/geometry-node evaluation times, but no queuing infrastructure exists yet anywhere in the repo to build on. TBD — confirm with user.
- Licensing/attribution requirements if any starter geometry-node graphs or CAD templates are sourced externally. TBD — confirm with user.

---

## 7. Roadmap Placement

See [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) for full phase sequencing. Summary of why this is late-phase:

- Phases 0-2 (per the architecture audit underlying this doc set) are entirely about fixing the live `core/executor.py` safety bug, repo hygiene, and proving `jarvis_core` can run end-to-end at all — none of which this capability depends on directly, but all of which must land first because they gate Phase 3.
- Phase 3 merges `jarvis_prod` transport with `jarvis_core` reasoning into one canonical Brain with a real tool registry. `3d.generate` cannot be registered anywhere meaningful until that registry exists.
- Phase 6 ("Thin-Client Expansion") is where the dashboard rebuild and this 3D capability both belong, and is explicitly gated on Phases 3-5 completing first per the roadmap's own sequencing rule: "This phase should not start before Phase 5 — building more thin clients against an unsettled, three-way-fragmented Brain multiplies the consolidation debt rather than paying it down."

This document should be revisited and substantially expanded (implementation detail, concrete module boundaries, actual dependency versions) only once Phase 6 is scheduled to begin, not before.

---

## Related Documents

- [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) — phase sequencing and exit criteria
- [`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) — current-state fragmentation and target layering
- [`../architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) — unified tool registry contract this spec builds on
- [`../architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) — agent lifecycle and Planner/Critic gating
- [`../architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md) — risk classification for tool actions
- [`UI_SPEC.md`](UI_SPEC.md) — dashboard shell and viewport hosting model
- [`COMPUTER_CONTROL.md`](COMPUTER_CONTROL.md) — related PC-node execution capability (`pc.*` skills)
- [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) — tracks whether Phase 6 prerequisites are met
- [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) — architecture decisions this spec depends on

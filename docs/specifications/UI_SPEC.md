# UI Specification

Status: Planning phase — pre-implementation
Last updated: 2026-07-01

---

## 0. Purpose and Scope

This document specifies the target user interface for the JARVIS / Chimera Ecosystem — a "Tony Stark HUD"-style AI Operating System dashboard — and contrasts it against the current, audited state of the only UI code that exists in the repo (`hugginggpt/web`). It is written for future Claude Code sessions and for the human architect (Yash) as the single source of truth on UI direction.

This is explicitly a **late-phase item**. Per [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), UI work is Phase 6 ("Thin-Client Expansion") and should not start before Phase 5 (island consolidation and archival) completes. The daily-use legacy stack (`hugginggpt/server/awesome_chat.py`, driven over ADB) does not need this UI and is unaffected by anything in this document. Nothing here should be implemented before the Brain (see [`specifications/BRAIN_SPEC.md`](BRAIN_SPEC.md)) exists in a form that can actually emit the event stream this UI is designed to consume.

Related documents: [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) for the overall layering, [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) for how commands reach nodes, [`specifications/3D_ENGINE.md`](3D_ENGINE.md) for the 3D viewport panel's own spec, [`vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md) for the AI-OS / thin-client narrative this UI serves, and [`context/DECISION_LOG.md`](../context/DECISION_LOG.md) where the React-vs-Vue decision below must be logged once made.

---

## 1. Current State (Audited, as of 2026-07-01)

The only UI code in the repository is `hugginggpt/web/`. It is confirmed by direct repo inspection to be an **unmodified fork of the public HuggingGPT/ChatGPT demo template**, not a JARVIS-specific artifact:

| Evidence | Value |
|---|---|
| `package.json` `name` | `vue3-ts-vite-router-tailwindcss` (literal, unrenamed) |
| `electron/electron-builder.yml` `appId` | `com.chatgpt.app` |
| `electron/electron-builder.yml` `productName` | `ChatGPT` |
| `index.html` `<title>` | `HuggingGPT` |
| Routes (`src/router/index.ts`) | One route: `/` → `home.vue` |
| Components | `App.vue`, `views/home.vue`, `components/Loading.vue` — that is the entire component tree |
| State management | None — no Pinia, no Vuex, no store directory |
| Real-time transport | None — no MQTT client, no WebSocket client, no SSE |
| Charting / dashboard widgets | None |
| Terminal emulator | None |
| 3D viewport | None |
| Backend calls | `src/api/hugginggpt.ts` (HuggingGPT-style task-planning HTTP calls) and `src/api/chatgpt.ts` (direct OpenAI chat completions call from the browser) |

### 1.1 Confirmed security smell: browser-side OpenAI key

`src/api/chatgpt.ts` calls the OpenAI API directly from the browser and takes `apiKey` as a function parameter that is sourced from `localStorage` (the standard pattern in this template family — the key is entered once by the user in a settings panel and persisted client-side, then sent as `Authorization: Bearer ${apiKey}` on every request). This means:

- The API key sits in browser `localStorage`, readable by any script executing in that origin (XSS exposure) and by anyone with local access to the browser profile / Electron app data.
- The Electron build (`electron/main.js`, `com.chatgpt.app`) ships this same pattern as a desktop app, with no additional isolation shown in the audited files.
- There is no backend proxy — the browser is the OpenAI client, not the Brain.

**Recommendation: remove this pattern entirely, regardless of what UI replaces it.** No future JARVIS client should hold a model-provider API key client-side. All model calls must be proxied through the Brain (or a dedicated backend gateway), with the browser/Electron client holding, at most, a short-lived session token scoped to the Brain's own API — never a raw provider key. This applies whether the eventual UI evolves from Vue or is rebuilt in React; it is a backend-boundary fix, not a frontend framework choice, and should be treated as a standalone security cleanup item independent of the Phase 6 timeline.

### 1.2 What this current UI is not

To be explicit about the gap from the target vision: `hugginggpt/web` has no dashboard, no panels, no live data of any kind, no concept of "nodes," "agents," "memory," or "goals," and no connection to any MQTT-derived event stream (because no such stream is emitted by any Brain candidate today — see [`specifications/BRAIN_SPEC.md`](BRAIN_SPEC.md)). It is a single scrollable chat-bubble column, functionally indistinguishable from the public template it was forked from. Everything described from Section 2 onward is **new work**, not an extension of existing dashboard code — because no dashboard code exists.

---

## 2. Target Vision: The HUD Dashboard

The target UI is a persistent, always-on "AI Operating System" dashboard — a Tony Stark/JARVIS-HUD-style multi-panel interface, not a chat window. It is one of several thin clients (web, desktop, phone, CLI, voice) that all speak to the same Brain; per CLAUDE.md's design principles, the UI layer must stay lightweight and must not embed orchestration or memory logic — it renders state the Brain publishes and forwards user intent back to the Brain.

### 2.1 Design tenets

- **Brain is the single source of truth.** The UI holds no authoritative state; every panel is a view over Brain-published state, refreshed via a real-time channel, not polled HTTP where avoidable.
- **One WebSocket bridge, many panels.** A single WebSocket connection (or a small, deliberate number of them) subscribes to Brain-side MQTT topics via a bridge process and fans messages out to panels client-side. Panels must not each open independent transport connections.
- **Read-mostly, write-narrow.** Most panels are observational (system monitoring, logs, memory status). A small number of panels issue commands back to the Brain (Terminal, Code Workspace, Tasks) — these write paths must go through the same command/response contract the Brain already uses for MQTT nodes (see [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md)), not a bespoke REST shortcut.
- **Degrade visibly, not silently.** If the WebSocket bridge or Brain is unreachable, panels must show a clear disconnected/stale state (per Section 1.2's audited absence of any such affordance today) rather than freezing on last-known data with no indicator.
- **No secrets in the client.** Per Section 1.1, no provider API keys, broker credentials, or long-lived tokens live in browser storage.

### 2.2 Panel inventory

The dashboard is a fixed or user-configurable grid/layout of the following panels. Exact visual layout (grid vs. dockable/floating windows) is TBD — confirm with user before implementation; the panel list itself is fixed by the vision brief:

1. System Monitoring
2. Running Agents
3. Memory Status
4. Current Goal
5. Voice Activity
6. Calendar
7. Email
8. Tasks
9. 3D Viewport
10. Research Workspace
11. Code Workspace
12. Terminal
13. GitHub
14. IoT (device/node status — maps to CLAUDE.md's hardware inventory)
15. Logs
16. Notifications
17. Live Events (raw/aggregated event feed — effectively a debug view over the same bridge every other panel consumes)

---

## 3. Panel-to-Data-Source Table

This table maps each panel to the Brain API endpoint and/or MQTT topic (via the WebSocket bridge) it should read from. Topic names follow CLAUDE.md's namespacing convention (`chimera/<node>/<action>`) and jarvis_prod's existing `jarvis/node/<id>/...` scheme (see [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) for the reconciled topic taxonomy — the two schemes have not yet been unified, which is itself an open item for that document). Every entry marked **TBD** does not exist yet in any form and must be designed as part of Phase 3 (Brain merge) or Phase 6 (this UI) work — do not assume any of these topics are currently published by jarvis_prod's `brain_pc.py` or jarvis_core.

| # | Panel | Primary Data Source | Notes |
|---|---|---|---|
| 1 | System Monitoring | TBD — `chimera/pc/system/metrics` (CPU/RAM/disk/network) via WebSocket bridge | No PC-side metrics publisher exists today; nearest analog is jarvis_prod's unimplemented `pc.*` skill surface. |
| 2 | Running Agents | TBD — Brain REST `GET /agents` + `chimera/brain/agents/status` push | Depends on an Agent Manager, which does not exist in any island today (see [`architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md)). jarvis_core's Planner/Critic/Executor loop is the closest analog but is a single linear pipeline, not multi-agent. |
| 3 | Memory Status | TBD — Brain REST `GET /memory/stats` + `chimera/brain/memory/updated` push | Maps to jarvis_core's ChromaDB RAG layer (`core/memory.py`), which has never persisted an embedding (no `brain/chroma/` directory exists) — this panel cannot show real data until Phase 2 of the roadmap validates that module. |
| 4 | Current Goal | TBD — `chimera/brain/goal/current` push | No goal-tracking concept exists in any island today; closest conceptual analog is jarvis_core's Planner output, never run. |
| 5 | Voice Activity | TBD — `chimera/brain/voice/state` (idle/listening/speaking/processing) | No voice interface is wired to any current Brain candidate — see [`specifications/VOICE_SYSTEM.md`](../specifications/VOICE_SYSTEM.md). The legacy stack has `SpeechRecognition`/`pyttsx3` in `requirements_jarvis.txt` but is a separate, older path not connected to jarvis_prod or jarvis_core. |
| 6 | Calendar | TBD — Brain REST `GET /calendar/events` (Brain-side integration, provider TBD) | No calendar integration exists in any island. |
| 7 | Email | TBD — Brain REST `GET /email/inbox` (Brain-side integration, provider TBD) | No email integration exists in any island. Note: Telegram gateway PII-masking gap (documented in [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md)) is a cautionary precedent for how this panel's data must be handled once built. |
| 8 | Tasks | TBD — Brain REST `GET /tasks` + `chimera/brain/tasks/updated` push; writes via `POST /tasks` | Nearest existing analog is jarvis_prod's `schedule.example.json` cron-style job definitions, which are config, not a live task API. |
| 9 | 3D Viewport | TBD — see [`specifications/3D_ENGINE.md`](3D_ENGINE.md) for its own data contract | No 3D rendering code exists anywhere in the repo today. This is the panel most affected by the React-vs-Vue decision in Section 4. |
| 10 | Research Workspace | TBD — Brain REST `POST /research/query` + streamed results over WebSocket | No research-agent implementation exists in any island. |
| 11 | Code Workspace | TBD — Brain REST `GET/POST /code/*` (file read/write proxied through Brain, not direct filesystem access from browser) | Must not give the browser direct filesystem access — all writes proxy through the Brain's own safety kernel once `root core/`'s executor bug (see [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md)) is fixed and the kernel is wired in. |
| 12 | Terminal | TBD — WebSocket pty bridge to Brain-side shell execution, gated by the safety kernel's risk/confirmation model | This is the highest-risk write panel in the entire dashboard — must route through the same `risk.get_risk_level()` / confirmation gate the safety kernel enforces for `pc.shell.run`-class actions, not a raw unauthenticated pty. |
| 13 | GitHub | TBD — Brain-side GitHub API proxy, `GET /github/status` (PRs, issues, CI state for tracked repos) | No GitHub integration exists in any island today. |
| 14 | IoT | TBD — `chimera/<node>/presence` + `chimera/<node>/state` for each entry in CLAUDE.md's hardware inventory (Nothing Phone 3a Lite, OPPO, Vivo 1901, Arduino UNO R4 WiFi, future tablet-LCD terminal) | Only `agent_phone.py` (Nothing Phone 3a Lite) exists as a live node implementation today; every other device in the inventory has zero corresponding software node. |
| 15 | Logs | TBD — `chimera/brain/logs/stream` (tailed) + Brain REST `GET /logs?since=` for backfill | Nearest existing analog is jarvis_prod's SQLite JobLog and jarvis_core's `history.log` (currently empty — never run). |
| 16 | Notifications | TBD — `chimera/brain/notifications/push` | Nearest existing analog is jarvis_core's `NEED_ATTENTION.md` 3-strike escalation file (never written, since jarvis_core has never run) and jarvis_prod's Telegram gateway, which currently notifies via Telegram, not this panel. |
| 17 | Live Events | TBD — raw subscribe to the full bridged topic namespace (`chimera/#` / `jarvis/#` depending on which taxonomy TOOL_ROUTING.md settles on), rendered as a scrolling feed | This is effectively a debug/observability view and should be the first panel built once the WebSocket bridge exists, since it validates the bridge itself with no additional backend work. |

**Cross-cutting dependency:** every row above depends on a WebSocket bridge process that does not exist yet. That bridge's job is to subscribe to the Brain's MQTT topics (Mosquitto, per [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md)) and re-publish to browser WebSocket clients, plus expose the Brain's REST surface for non-real-time reads/writes. Building this bridge is itself a prerequisite task, likely belonging to Phase 3 (Brain merge) rather than Phase 6, since the Brain side of the contract must be stable before a UI can be built against it.

---

## 4. Open Decision: React vs. Vue

The existing (template-derived) frontend is Vue 3 + Vite + TypeScript + Tailwind + Electron. Separately, the broader "JARVIS X" / AI-OS vision material referenced in project discussions suggests React Three Fiber for the 3D viewport specifically. These two facts are in tension and this document does not resolve them — **this is an open architectural decision that must be made by the human architect and logged in [`context/DECISION_LOG.md`](../context/DECISION_LOG.md) before Phase 6 UI work begins.**

### Option A — Evolve the existing Vue shell

Keep Vue 3 + Vite + Tailwind + Electron as the foundation. Add a Three.js-based 3D viewport via **TresJS** (the Vue-idiomatic Three.js renderer, conceptually equivalent to React Three Fiber but for Vue's component model) rather than introducing a second framework runtime into the same app.

- Pros: Reuses the existing build tooling (Vite config, Electron packaging, Tailwind setup) which is functional even though the application code on top of it is template boilerplate; avoids running two UI frameworks in one Electron shell; smaller migration surface; TresJS is actively maintained and has real feature parity with React Three Fiber for the 3D use cases described (3D viewport panel).
- Cons: Vue's ecosystem for complex dashboard/data-grid component libraries is smaller than React's; if any future contributor or reference implementation is React-first (as the "JARVIS X" R3F reference suggests), there is ongoing translation overhead; team/community Vue expertise vs. React expertise is unconfirmed — TBD, confirm with user.

### Option B — Clean rebuild in React

Discard `hugginggpt/web` entirely (it is unmodified template code with no JARVIS-specific investment worth preserving per Section 1) and rebuild the dashboard in React, using React Three Fiber directly for the 3D viewport as the vision material suggests, paired with a state layer (e.g., Zustand/Redux) and a component/dashboard library from React's larger ecosystem.

- Pros: Directly aligned with the R3F reference material with no translation layer; larger ecosystem of dashboard/HUD-style component libraries and examples to draw from; clean slate avoids inheriting any template debt (the `com.chatgpt.app` branding, the localStorage API-key pattern, the single-route structure) even by accident.
- Cons: Full rebuild — zero reuse of existing Vite/Electron/Tailwind scaffolding investment (though that scaffolding is thin to begin with, per Section 1); introduces a second frontend stack into a repo that already has significant cross-island fragmentation (see [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md)) at a time when the project's stated priority is consolidation, not multiplication of parallel stacks; higher effort before any panel is functional.

### Recommendation posture

No recommendation is made here beyond flagging the tradeoff — this decision affects tooling, hiring/collaboration surface, and multi-year maintenance cost, and per this document's hard rules must not be invented. Given that Phase 6 is explicitly late and non-urgent (Section 0), there is no time pressure forcing a premature choice; the decision can be deferred until Phase 5 completes and the Brain's actual event/API contract (which both options must consume identically) is stable. Whichever option is chosen, log it in [`context/DECISION_LOG.md`](../context/DECISION_LOG.md) with the date, the reasoning, and an explicit note that `hugginggpt/web`'s current template code (Section 1) is not reused as-is under either option — at minimum the branding, routing, and the localStorage API-key pattern must be removed.

---

## 5. Relationship to Other Nodes and Clients

Per CLAUDE.md's hardware inventory and the AI-OS thin-client vision, this dashboard is one of several clients against the same Brain, not the only one:

- The Nothing Phone 3a Lite runs a mobile Jarvis node (Termux/SSH), not this dashboard.
- The salvaged tablet components (Innolux P101DCA-AB0 LCD, MEMS mic, stereo speakers 09C26QA/09C26QB) are earmarked as a future dedicated Jarvis display/voice terminal — plausibly a kiosk-mode instance of this same dashboard (or a reduced subset of panels: Voice Activity, Current Goal, Notifications) once that hardware is repaired and integrated, per the hardware roadmap in CLAUDE.md. This is speculative and not a committed design — TBD, confirm with user before treating the tablet as a dashboard target.
- The OPPO and Vivo 1901 devices are spare, earmarked for camera/dashboard/sensor roles — any dashboard use for them is a distinct, smaller-surface client, not this full 17-panel HUD.

None of these cross-device relationships are implemented today; they are noted here so that this UI's data contracts (Section 3) are designed to be device-agnostic (a WebSocket/REST contract any client can consume) rather than coupled to a single desktop-browser assumption.

---

## 6. Explicit Non-Goals for This Document

- This document does not specify visual design (colors, typography, exact grid dimensions, animation) — that is implementation-detail work for whichever option in Section 4 is chosen, and should be produced as a follow-on design spec once the framework decision is logged.
- This document does not specify the Brain-side REST/WebSocket bridge implementation — that belongs in [`specifications/BRAIN_SPEC.md`](BRAIN_SPEC.md) and [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md).
- This document does not authorize starting any of this work now. Per [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), Phase 6 (Thin-Client Expansion) should not start before Phase 5 (retire/archive the losing Brain islands) completes — building panels against an unsettled, three-way-fragmented Brain multiplies consolidation debt rather than paying it down.

---

## 7. Summary of Required Actions (Not Yet Scheduled)

| Action | Depends On | Target Phase |
|---|---|---|
| Remove `localStorage` OpenAI key pattern from `hugginggpt/web/src/api/chatgpt.ts` (or retire the file with the rest of the template if Option B is chosen) | None — can happen anytime, low risk | Can be done early as a standalone security cleanup, independent of Phase 6 |
| Log React-vs-Vue decision in `context/DECISION_LOG.md` | Human architect input | Before Phase 6 starts |
| Design and build the Brain-side WebSocket bridge + REST surface | Brain merge (Phase 3) | Phase 3/6 boundary |
| Build "Live Events" panel first (validates the bridge with minimal additional backend work) | WebSocket bridge | Early Phase 6 |
| Build remaining 16 panels per Section 3's data-source table, prioritized by which Brain subsystems exist first (Logs/IoT before Calendar/Email/GitHub, which need net-new integrations) | Corresponding Brain subsystem | Phase 6, incremental |

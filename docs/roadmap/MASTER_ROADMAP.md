# JARVIS Master Roadmap

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

Related docs: [Vision](../vision/JARVIS_VISION.md) · [Product Principles](../vision/PRODUCT_PRINCIPLES.md) · [System Architecture](../architecture/SYSTEM_ARCHITECTURE.md) · [Agent Architecture](../architecture/AGENT_ARCHITECTURE.md) · [Memory Architecture](../architecture/MEMORY_ARCHITECTURE.md) · [Security Model](../architecture/SECURITY_MODEL.md) · [Tool Routing](../architecture/TOOL_ROUTING.md) · [Brain Spec](../specifications/BRAIN_SPEC.md) · [Project Context](../context/PROJECT_CONTEXT.md) · [Decision Log](../context/DECISION_LOG.md) · [Design History](../context/DESIGN_HISTORY.md) · [Current Status](../context/CURRENT_STATUS.md) · [Docs Index](../README.md)

---

## 1. Why This Roadmap Exists

The JARVIS repo is not a system with a codebase — it is a filesystem containing four unrelated things that happen to share a folder: (1) a legacy Flask/HuggingFace/Claude stack under `hugginggpt/server` driven daily over ADB (the only thing actually used today), (2) a smoke-tested-but-never-deployed MQTT production stack under `hugginggpt/server/jarvis_prod` (`brain_pc.py` + `agent_phone.py`), (3) `jarvis_core`, a structurally more sophisticated but never-executed Planner/Critic/Executor Reflexion prototype with its own MQTT namespace, and (4) a root-level `core/` execution-safety kernel that nothing imports at all, exercised only by its own test suite. Layered on top is `CLAUDE.md`'s "Chimera Ecosystem" vision (Brain/Jarvis/MQTT-Tailscale mesh) and an even larger unspecified "AI Operating System" ambition (thin clients across desktop/phone/web/CLI/voice/AR-VR, 3D generation, computer-use, robotics). None of the three real Python islands import each other. None of them implement the Brain described in `CLAUDE.md`.

**This roadmap is therefore not a feature roadmap. It is a fragmentation-reconciliation plan.** The work ahead is to decide, in writing, which of the three Python islands is the canonical Brain, retire or explicitly archive the other two, fix the one live safety bug in the kernel most worth keeping, and only then resume feature work. Every phase below is sequenced around that discipline, and every phase has a hard, observable exit criterion rather than a subjective "looks done" — because the project's demonstrated failure mode to date is stopping at "designed and built" without ever reaching "observed working" (`jarvis_core` was committed once in `5dfa65f` and never touched again through `4b32760` and `fe41bf5`; `jarvis_prod` was smoke-tested but has never had a live Tailscale run).

Canonical labels used throughout this and all other docs in this set (do not rename or reinterpret): **legacy stack** (daily-use ADB/`awesome_chat.py`), **jarvis_prod** (smoke-tested MQTT stack, never live), **jarvis_core** (never-run Reflexion prototype), **root `core/`** (dormant safety kernel with a live confirmed bug).

---

## 2. Phased Plan

### Phase 0 — Stop the Bleeding
**Duration:** 1-2 days

**Goal:** Eliminate active safety and hygiene hazards before any further development.

**Deliverables:**
- `core/executor.py` risk-ordering fix merged and all 33 `core/` tests green.
- `.gitignore` corrected and `node_modules` purged from tracked files (`git rm -r --cached hugginggpt/web/node_modules`, keep working tree files).
- Orphaned root `package-lock.json` removed.
- The 3 previously-leaked secrets (Gmail app password, ElevenLabs key, GitHub PAT) rotated per the README advisory.
- Root `core/` renamed to remove the `sys.path` collision with `jarvis_core/core/`.

**Exit criteria:** `git status` is clean of accidental artifacts, `pytest` run against `core/` shows 33/33 (or documented skips only), no bare `import core` ambiguity remains, secrets confirmed rotated.

---

### Phase 1 — Decide and Document the Brain
**Duration:** 3-5 days, no new code

**Goal:** Produce a written architecture decision record naming `jarvis_prod`'s `brain_pc.py` + MQTT skeleton as the transport/orchestration backbone, `jarvis_core`'s Planner/Critic/Executor as the reasoning path to be grafted onto it, and root `core/` (post-fix) as the action-safety kernel underneath the Executor. Explicitly document that the legacy `hugginggpt/awesome_chat.py` stack remains the production system during the transition and is not touched.

**Deliverables:** An ADR-style doc (can use the `jarvis-architect.md` skill persona's mandated Current State / Problems / Proposed Architecture / Tradeoffs / Migration Plan sections) committed to `docs/`.

**Exit criteria:** The human architect signs off on which island is canonical before any merge branch is opened.

---

### Phase 2 — Prove jarvis_core Actually Runs
**Duration:** 1-2 weeks

**Goal:** Get `jarvis_core`'s Reflexion loop through one real, observed end-to-end cycle — live Ollama (`llama3:8b`, `mistral:7b`, `nomic-embed-text` pulled), live Mosquitto broker, a paired `phone_executor.py`, at least one full hourly `MaintenanceScheduler` pass.

**Deliverables:** `brain/history.log` and `knowledge_graph.md` show real content, `brain/chroma/` persists embeddings, a deliberate 3-strike failure is observed writing `NEED_ATTENTION.md`.

**Exit criteria:** The module graduates from "prototype, never run" to "validated" — only after this can it be trusted as the reasoning layer for Phase 3.

---

### Phase 3 — Merge Transport + Reasoning
**Duration:** 2-4 weeks

**Goal:** Graft `jarvis_core`'s Planner/Critic/Memory onto `jarvis_prod`'s MQTT command/response core, replacing `brain_pc.py`'s ad hoc regex `_brain_prompt` built-ins with the Reflexion loop, and routing `jarvis_core`'s `phone_executor.py` skills through `jarvis_prod`'s existing `protocol.py` schema instead of its own parallel MQTT namespace. Build the missing `pc.*` skill executor on top of `core/`'s (fixed, renamed) safety kernel so `brain_pc.py`'s already-advertised `pc.*` commands actually execute.

**Deliverables:** One running Brain process exercising memory, safety gating, and MQTT transport together; a second smoke test proving the merged system round-trips both `phone.*` and `pc.*` skills through the Reflexion loop.

**Exit criteria:** The merged Brain passes an expanded version of `jarvis_prod`'s `_smoke.py` that also exercises a Planner/Critic rejection path and a `pc.*` command.

---

### Phase 4 — First Live Tailscale Deployment
**Duration:** 1 week, mostly ops

**Goal:** What `jarvis_prod`'s README already flags as outstanding — rebind `mosquitto.conf` off its placeholder IP (`100.64.0.1`), complete Termux install on the Nothing Phone 3a Lite, get a real Telegram bot token live, and run the merged Brain continuously for at least 48 hours under real conditions (phone sleep/wake cycles, Tailscale reconnects).

**Exit criteria:** Presence/state topics show real reconnect behavior, at least one scheduled cron job (per `schedule.example.json`) fires correctly in production.

---

### Phase 5 — Retire or Archive the Losing Islands
**Duration:** Ongoing, low effort

**Goal:** Once Phase 3-4 prove the merged Brain works, formally archive whichever of `jarvis_prod`/`jarvis_core` did not become canonical (git tag + move to an `archive/` path, do not delete history), and decide the fate of `easytool`/`taskbench` (attribute properly or remove).

**Exit criteria:** Repo has one Python Brain implementation under active development, with clearly labeled archived alternatives, not three live candidates.

---

### Phase 6 — Thin-Client Expansion
**Duration:** Open-ended, matches `CLAUDE.md` roadmap Phases 3-4 and the AI-OS vision

**Goal:** Only after Phase 3-5, begin rebuilding `hugginggpt/web` as a genuine MQTT/WebSocket-aware dashboard, repurpose OPPO/Vivo as camera/dashboard nodes, integrate the salvaged tablet LCD/speakers as a Jarvis display/voice terminal, and expand toward the CLI/voice/AR-VR client vision.

This phase should not start before Phase 5 — building more thin clients against an unsettled, three-way-fragmented Brain multiplies the consolidation debt rather than paying it down.

---

## 3. Priority Matrix

| Item | Impact | Effort | Priority |
|---|---|---|---|
| Fix `core/executor.py` safety/risk ordering bug (move `risk.get_risk_level()` before `safety.apply_safety()`, or evaluate risk on the pre-rewrite name) | High (live safety bypass, 3 failing tests already exist) | Low (few-line reorder + re-run existing suite) | P0 — do first, before anything else touches `core/` |
| Fix `.gitignore` web/ path mismatch + `git rm --cached` the 6195 `node_modules` files | High (43MB repo bloat compounding on every future `npm install`) | Low (path fix + one cleanup commit) | P0 |
| Decide-in-writing which island is canonical Brain (jarvis_prod transport + jarvis_core reasoning, per recommendation) and document the decision in README/CLAUDE.md | High (blocks every downstream consolidation decision) | Low (architecture decision, not code) | P0 |
| Rename root `core/` to avoid `sys.path` collision with `jarvis_core/core/` | Medium (latent footgun, not yet triggered) | Low (rename + update 3 test files' imports) | P0 |
| Rotate the 3 previously-leaked secrets (Gmail app password, ElevenLabs key, GitHub PAT) per the live README advisory | High (credentials may still be valid despite history purge) | Low (rotation is routine ops work) | P0 |
| Remove orphaned root `package-lock.json` | Low | Trivial | P1 (quick win, bundle with gitignore fix) |
| Build the missing `pc.*` skill executor (reusing `core/`'s post-fix safety kernel) | High (jarvis_prod is non-functional for its advertised PC control without this) | Medium (new module, but schema/registry already exists in `tools.py`/`_known_skills`) | P1 |
| Run jarvis_prod's first live Tailscale/Mosquitto deployment (rebind listener off placeholder `100.64.0.1`, install Termux deps, real bot token) | High (nothing in jarvis_prod is validated beyond smoke test) | Medium (mostly ops/config, code is ready) | P1 |
| Wire jarvis_core's `memory.py`/`scheduler.py`/`brain.py` end-to-end at least once against real Ollama+Mosquitto | High (currently zero real-world evidence this loop works) | Medium-High (needs Ollama install, live broker, a full maintenance cycle) | P1 |
| Add per-node MQTT ACLs (replace `mosquitto.conf.example`'s flat `jarvis/#` grant) | Medium-High (credential leak currently = full node impersonation) | Medium | P2 |
| Move Telegram gateway PII masking to the transport layer, not just the Langfuse layer | Medium (real privacy exposure, bounded to Telegram chat access) | Low-Medium | P2 |
| Consolidate the 8 `requirements.txt` files into a documented cross-island version matrix (not necessarily one lockfile) | Medium (currently silent, not yet triggered breakage) | Medium | P2 |
| Rebuild `hugginggpt/web` as an MQTT/WebSocket-aware thin client | High for the AI-OS vision, Low for daily operations (ADB-driven legacy stack doesn't need it) | High | P3 |
| Add TLS to MQTT (currently relies solely on Tailscale encryption) | Medium | Medium | P3 |
| Add CI (lint + the existing test suites) so `core/`'s 33 tests and jarvis_core's 9 tests stop requiring manual interpreter discovery | Medium | Low-Medium | P3 |
| Archive/attribute `easytool/` and `taskbench/` (add LICENSE/NOTICE crediting Microsoft JARVIS lineage) or remove if never going to be referenced | Low | Low | P4 |

---

## 4. Risk Register

The dominant risk is **scope creep disguised as progress**: because `jarvis_core` is structurally the most sophisticated candidate for `CLAUDE.md`'s Brain vision, there will be pressure to keep building on it (or on `core/`) before proving either has ever run against live infrastructure — repeating the exact pattern that produced three unreconciled islands in the first place. Any new feature work started before Phase 1-2 complete adds a fourth island, not progress.

**Security risks are concrete, not theoretical:**
- The `core/executor.py` safety-bypass is live and reproducible today (`delete_file` executes with zero confirmation).
- `mosquitto.conf.example`'s flat `jarvis/#` ACL means one leaked credential can impersonate every node's presence/state and inject arbitrary commands.
- The 3 previously-purged secrets may still be valid if never rotated, despite the `filter-repo` history rewrite (history rewriting does not invalidate credentials, only hides them from future clones).

**Operational risk:** `jarvis_prod`'s Reflector circuit breaker (3-strike, no time-decay) can silently and permanently disable a heartbeat-driven skill like the 15-minute battery watchdog after one bad half-hour, with silence as the only symptom — this will look like a mysterious outage during Phase 4's live deployment if not fixed or at least monitored first.

**Data-loss risk:** `brain_pc.py`'s `clean_start=True` means any command queued while the brain was briefly offline is dropped by design, and a response that arrives after a brain restart is lost even if the phone successfully executed the action — this will produce confusing "it failed" job-log entries for actions that actually succeeded, undermining trust in the Reflector's failure counting during Phase 2-4 validation.

**Integration risk:** `awesome_chat.py` is `jarvis_prod`'s one active coupling point to the legacy stack (via a soft try/except import) — any legacy-stack change to `parse_device_command`'s signature will silently degrade `jarvis_prod`'s NL routing rather than erroring, meaning Phase 3 work must not assume the legacy stack is frozen just because it's "not being touched."

**Dependency risk:** mixing any two of the three Python islands into one venv silently resolves conflicting pins (`aiomqtt`/`paho-mqtt` in particular, where `jarvis_core` explicitly pinned `paho-mqtt==2.1.0` "for QoS-1 fixes" that `jarvis_prod`'s unpinned floor could miss) — Phase 3's merge work must explicitly reconcile this rather than assume compatibility.

**Reputational/attribution risk:** `easytool/` and `taskbench/` have no LICENSE files despite being vendored from Microsoft's JARVIS research lineage — low urgency but should not be forgotten indefinitely.

**People-risk in sequencing:** Phase 0-2 produce no user-visible features, only safety fixes and validation runs — the human architect should be prepared for this phase to look like "no progress" externally while being the highest-leverage work available.

---

## 5. Target Folder Structure

Target layout (illustrative, not a mandate to move everything in one pass):

```
JARVIS/
  CLAUDE.md
  README.md
  JARVIS_SESSION_CONTEXT.md      # local-only, gitignored
  docs/                          # NEW — does not exist today
    adr/
      0001-canonical-brain-decision.md
      0002-core-kernel-rename-and-fix.md
    architecture/
      chimera-target-architecture.md
    archive-notes/
      jarvis_prod-vs-jarvis_core-tradeoffs.md
  brain/                         # NEW — the merged canonical Brain (Phase 3 output)
    transport/                   # from jarvis_prod: protocol.py, brain_pc.py's MQTT core
    reasoning/                   # from jarvis_core: planner/critic/executor loop
    memory/                      # from jarvis_core: ChromaDB RAG, Memory Manager
    safety_kernel/                # from root core/, renamed (e.g. "action_kernel"), post-fix
      executor.py
      safety.py
      risk.py
      tools.py
      validator.py
      sandbox.py
      idempotency.py
      undo.py
      state.py
      retry.py
      tests/
    scheduler/                   # croniter + one-shot reminders, merged from both islands
    integrations/
      telegram_gateway.py
      langfuse_tracing.py
  nodes/                         # NEW — physical/agent nodes, renamed from scattered locations
    phone_nothing_3a/             # from jarvis_prod/agent_phone.py + jarvis_core/agent/phone_executor.py, reconciled
    pc_executor/                  # NEW — the missing pc.* skill dispatcher
    android/                     # Tasker/Termux watchdog scripts (from jarvis_core/android/)
  legacy/                        # RENAMED from hugginggpt/, frozen-in-place during transition
    server/                      # awesome_chat.py stack — untouched, daily-use ADB system
    web/                         # existing Vue/Electron chat SPA, until Phase 6 replaces it
  clients/                       # NEW — Phase 6 thin-client rebuild target
    web-dashboard/                # replaces legacy/web eventually
  vendor/                        # NEW — clearly-labeled, attributed, frozen reference code
    easytool/
    taskbench/
    hugginggpt-jetson-docker/     # Dockerfile.jetson + assets/, with attribution preserved
  tests/                         # consolidated cross-island test root
  .claude/skills/                # unchanged — already well-organized
  .vscode/
    launch.json                  # extended with Python debug configs for brain/, nodes/
```

**Migration notes:** This is NOT a Phase 0 task — do not move files until Phase 1's ADR names the canonical Brain, or the move itself becomes another unreviewed fait accompli merge (exactly how `jarvis_core` landed in one commit and was never touched again). In the interim (Phases 0-2), leave `hugginggpt/`, `jarvis_core/`, and `core/` exactly where they are and only fix in place (safety bug, gitignore, renames needed to break the `sys.path` collision). Physically relocating code should happen only as part of Phase 3's merge, using `git mv` to preserve history, and `easytool/`/`taskbench/`/`Dockerfile.jetson` should be moved to `vendor/` in a dedicated, low-risk Phase 0/1 cleanup commit since that move carries no behavioral risk and immediately reduces the "is this active?" ambiguity flagged in the exploration report.

---

## 6. Incremental Implementation Strategy

The core discipline is: **no big-bang rewrite, and no new user-facing feature work until Phase 2 (jarvis_core proven to run) completes** — every step must leave the currently-in-daily-use legacy ADB stack (`hugginggpt/server/awesome_chat.py`) completely untouched and running.

Concretely:

1. **All Phase 0 fixes are surgical and reversible** — the `executor.py` risk-ordering fix is a few-line change validated by tests that already exist and already specify the correct behavior, so there's no ambiguity about "done." The gitignore/`node_modules` purge is a mechanical git history operation with zero code risk.

2. **Nothing is deleted in Phase 0-2, only fixed-in-place or documented** — `jarvis_prod`, `jarvis_core`, and `core/` all continue to exist exactly where they are; Phase 1's ADR is a decision on paper, not a migration. This avoids the single biggest risk of consolidation projects: destroying a working prototype before its replacement is proven.

3. **Phase 2's validation of `jarvis_core` happens in complete isolation** from `jarvis_prod` and the legacy stack — it requires only Ollama + a test Mosquitto broker, neither of which the other two islands depend on, so this phase cannot regress anything currently working.

4. **Phase 3's merge is additive-first**: build the new `brain/` tree as a new directory that imports/adapts code from `jarvis_prod` and `jarvis_core` rather than deleting the originals in place; only after the merged Brain passes its own smoke test (an extended version of `jarvis_prod`'s `_smoke.py`) do the original `jarvis_prod/` and `jarvis_core/` directories get formally archived (moved, not deleted, preserving git history) in Phase 5.

5. **The legacy `hugginggpt` stack is never in the critical path of any phase** — it is explicitly carved out as "do not touch" until the merged Brain has been running in Phase 4 for a defined trial period and has demonstrably replaced at least the legacy stack's most common daily commands; only then does a separate, later decision get made about deprecating `awesome_chat.py`, and that decision is out of scope for this roadmap.

6. **Each phase has a hard, testable exit criterion** (green tests, an observed file artifact like `knowledge_graph.md` gaining content, a 48-hour live-run log) rather than a subjective "looks done" — this matters specifically because the existing evidence (`jarvis_core` committed once and frozen, `jarvis_prod` smoke-tested but never live) shows this project's failure mode is stopping at "designed and built" without ever reaching "observed working," so the incremental plan is deliberately anchored to observable runtime evidence at every step, not code completion.

**Documentation is the first deliverable of every phase, not an afterthought** — Phase 1 produces no code at all, only a decision record; every subsequent phase's exit criteria are written down before implementation begins, following the docs-first discipline established by this document set itself. Small, reversible steps; observable evidence over subjective judgment; the daily-use legacy stack kept running throughout — these three constraints govern the entire roadmap.

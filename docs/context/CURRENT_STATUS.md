# Current Status — Operational Snapshot

Status: Planning phase — pre-implementation
Last updated: 2026-07-01

> This document is step 3 of the required session-start reading order — see [`context/AI_SESSION_BOOTSTRAP.md`](AI_SESSION_BOOTSTRAP.md) for the full ordered protocol (Vision → Brain Constitution → this document → Decision Log → Roadmap). Read it first if you haven't. Otherwise: this is the fastest way to know what actually runs today versus what is designed, dormant, or aspirational. For the target architecture see [architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md); for how we got here see [context/DECISION_LOG.md](DECISION_LOG.md) and [context/DESIGN_HISTORY.md](DESIGN_HISTORY.md); for the phased plan see [roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md).

---

## TL;DR

The repo contains **three non-communicating Python "Brain" implementations plus a fourth disconnected safety-kernel module**. Only one of the four is used daily. None of them implement the Chimera vision in [context/PROJECT_CONTEXT.md](PROJECT_CONTEXT.md). There is one confirmed live security bug and one confirmed repo-hygiene bug that both need fixing before any further consolidation work. See labels below — reuse them verbatim in all future docs/sessions.

| Label | What it is | Maturity |
|---|---|---|
| **legacy stack** | `hugginggpt/server/` — `awesome_chat.py`, Flask, HuggingFace + Claude API | Daily use (only real system) |
| **jarvis_prod** | `hugginggpt/server/jarvis_prod/` — `brain_pc.py` + `agent_phone.py`, MQTT v5 | Smoke-tested, never deployed live |
| **jarvis_core** | `jarvis_core/` — Planner/Critic/Executor Reflexion loop, Ollama + ChromaDB | Prototype, never run end-to-end |
| **root core/** | `core/` — execution-safety kernel (idempotency, sandbox, undo, risk) | Well-tested, dormant, has a live bug |

---

## What Works Today

The **legacy stack** (`hugginggpt/server/awesome_chat.py`) is the only system in real daily use. It runs over ADB against the phone.

| Component | Port | Detail |
|---|---|---|
| `awesome_chat.py` core | 8004 | Flask app, HuggingFace models + Anthropic Claude API, Python 3.10, `jarvis_env` venv |
| Bridge services | 8092 / 8091 | Flask bridges used for the daily ADB-driven command path |

- Python 3.10, dedicated `jarvis_env` virtualenv.
- No MQTT, no Brain/memory layer, no device registry — this is a monolithic, single-process assistant, not the distributed Chimera model.
- Still soft-depended-on by `jarvis_prod`'s `brain_pc.py` (`_brain_prompt` has a try/except import of `awesome_chat`'s NL parsing) — treat as **do-not-touch** until a replacement is proven (see [roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) Phase 4).

---

## What's Built But Not Live

**jarvis_prod** (`hugginggpt/server/jarvis_prod/brain_pc.py` + `agent_phone.py`) — MQTT v5 via aiomqtt 2.x, QoS 1, over a Mosquitto broker intended to run on Tailscale. Python 3.12 system interpreter (requires 3.11+ for `TaskGroup`/`except*`). Passes its own integration smoke test (`_smoke.py`: real Brain + fake phone through a real broker) but **has never had a live Tailscale run.**

Manual steps still outstanding before this is a real deployment:

1. `tailscale up` on both the PC and the phone (not done).
2. Rebind the Mosquitto listener off the placeholder IP `100.64.0.1` in `mosquitto.conf.example` to the real Tailscale address.
3. Complete the Termux install on the Nothing Phone 3a Lite (deps, service setup).
4. Configure the Tasker watchdog profile on the phone side.
5. Optional: Telegram gateway setup (bot token) and Langfuse tracing setup.
6. **Even after all the above, PC control will not work** — no `pc.*` skill executor exists anywhere. `brain_pc.py`'s `_known_skills`, `telegram_gateway.py`'s help text, and `schedule.example.json` all reference `pc.system.lock`, `pc.shell.run`, etc., but there is no receiving agent. Only `agent_phone.py`'s `phone.*` skills are implemented. Sending a `pc.*` command today times out silently.

---

## What's Dormant / Prototype

**jarvis_core** (`jarvis_core/`) — Planner (Llama3) / Critic (Mistral) / Executor Reflexion loop via local Ollama, ChromaDB RAG with `nomic-embed-text`.

- Committed once in `5dfa65f` ("V1"), never touched again through `4b32760` ("V2") and `fe41bf5` ("V2.1").
- **Never run end-to-end**: no `.env`, no `history.log`, no `logs/`, no `brain/chroma/` directory, `knowledge_graph.md` is still template-only, no `NEED_ATTENTION.md` has ever been written.
- Structurally the closest thing in the repo to the Brain vision (Planner/Critic separation, 3-strike human-in-the-loop gate, single `sandbox.safe_path()` chokepoint) — but zero runtime evidence it works.

**root core/** (`core/`, distinct from `jarvis_core/core/`) — 512-line execution-safety kernel: idempotency, sandbox, undo, risk, safety, validator, executor, state, retry, an 8-tool registry.

- Imported by nothing outside its own 3 test files.
- Well-tested (see Test Coverage below) but has a **confirmed live safety-ordering bug**: `safety.apply_safety()` runs before `risk.get_risk_level()` in `core/executor.py`, so `delete_file` (high risk) gets rewritten to `move_to_trash` (medium risk) **before** the risk check runs — bypassing confirmation entirely. Reproduced by direct execution.
- Naming collision risk: both `core/` and `jarvis_core/core/` are importable as bare `core` depending on `sys.path` order; `jarvis_core/tests/conftest.py` inserts `jarvis_core/` onto `sys.path` so its own imports resolve internally, but a careless refactor elsewhere could silently resolve to the wrong module.

---

## Known Issues

| Issue | Where | Severity |
|---|---|---|
| Safety/risk ordering bug — `delete_file` bypasses confirmation | `core/executor.py` | Critical — live, reproducible |
| WhatsApp/SMS send-button tap unreliable | legacy stack / ADB automation | Operational — daily-use flakiness |
| Instagram DM automation is coordinate-based and fragile | legacy stack / ADB automation | Operational — breaks on UI changes |
| Computer bridge (8092/8091) not battle-tested | legacy stack | Operational — unknown failure modes |
| `.gitignore` "for web" rules target nonexistent root `web/` instead of `hugginggpt/web/` | `.gitignore` | Repo hygiene — 6195 files / ~43MB of `node_modules` committed to git (97% of repo's 6399 tracked files) |
| Orphaned root `package-lock.json` (`{packages:{}}`, no root `package.json`) | repo root | Repo hygiene — dead weight since commit `e3234ba` |
| 3 previously-leaked secrets purged via `filter-repo` (Gmail app password, ElevenLabs key, GitHub PAT) | git history | Security — rotation status unconfirmed by audit; rotate-anyway advisory live in README.md |

---

## Test Coverage Summary

| Suite | Location | Status |
|---|---|---|
| root `core/` adversarial suite | `core/tests/test_execution_safety.py`, `tests/test_adversarial_execution.py`, `tests/test_executor_safety.py` | 33 tests, 565 lines; **3 currently failing** (`test_high_risk_action_requires_confirmation_before_safety_rewrite`, `test_confirmation_bypass_attack_is_rejected`, `test_external_confirmation_does_not_bypass_safeguards`) |
| jarvis_core | `jarvis_core/tests/` | ~9 tests, passing/skipped — covers sandbox + signing only, not the Reflexion loop itself |
| legacy stack | `test_jarvis.py` | Gated behind `importorskip` |
| jarvis_prod | `_smoke.py` | Integration smoke test — real Brain + fake phone through a real broker — currently green |

No CI exists anywhere (no `.github/workflows`). `pytest` is not installed in either primary venv, so none of the above runs automatically.

---

## Technical Debt (Top 5, Ranked)

1. **Confirmed live safety-gate bypass in `core/executor.py`** — `delete_file` executes with zero confirmation today; 3 tests already assert correct behavior and fail. Fix before wiring `core/` into anything.
2. **Three non-communicating Brain implementations** (legacy, jarvis_prod, jarvis_core) — each reinvents orchestration, none shares state/memory/device registry, directly violating the "Brain owns memory" single-source-of-truth principle from CLAUDE.md.
3. **`core/` vs `jarvis_core/core/` naming collision** — both importable as bare `core` depending on `sys.path` order; latent footgun for any careless refactor.
4. **6195 files / ~43MB of `node_modules` committed to git** due to the `.gitignore` web path mismatch — 97% of tracked files in the repo.
5. **No `pc.*` skill executor exists** despite `brain_pc.py`, `telegram_gateway.py`, and `schedule.example.json` all assuming one does — `jarvis_prod` cannot control the PC it runs on.

(Full list of 11 items in the underlying audit; see [context/DECISION_LOG.md](DECISION_LOG.md) for the complete technical-debt record.)

---

## Missing Systems vs. Vision

Measured against [vision/JARVIS_VISION.md](../vision/JARVIS_VISION.md) and [context/PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)'s Chimera model, nearly everything above "single MQTT command round-trip" is absent:

- No shared Memory Manager across any two islands (jarvis_core's ChromaDB RAG has never persisted an embedding; jarvis_prod has only a SQLite job log; root `core/`'s `memory.py` is unused).
- No Context Manager or Device Registry — only the Nothing Phone 3a Lite exists as a live node; OPPO, Vivo 1901, Arduino UNO R4 WiFi, and the salvaged tablet components are all unaddressed in code.
- No Agent Manager / true multi-agent orchestration — jarvis_core's Planner/Critic/Executor is a single linear pipeline, not a coordinator, and has never run.
- No Knowledge Layer / knowledge graph in practice — `jarvis_core/brain/knowledge_graph.md` is still its template header.
- No PC-side skill executor (see `pc.*` gap above).
- No dashboard/HUD — `hugginggpt/web` is a single fixed-width chat column, unmodified HuggingGPT/ChatGPT template, no MQTT client, no state store, no charting.
- No voice interface wired to any current stack.
- No computer-use/automation layer beyond unused `pyautogui` dependencies.
- No robotics/IoT integration beyond the aspirational hardware inventory in CLAUDE.md.
- No TLS on any MQTT path — security currently rests entirely on Tailscale's own encryption.
- No CI, no shared test runner, no cross-island integration test.

Full detail: [specifications/BRAIN_SPEC.md](../specifications/BRAIN_SPEC.md), [architecture/MEMORY_ARCHITECTURE.md](../architecture/MEMORY_ARCHITECTURE.md), [architecture/AGENT_ARCHITECTURE.md](../architecture/AGENT_ARCHITECTURE.md).

---

## Priority Matrix

| Item | Impact | Effort | Priority |
|---|---|---|---|
| Fix `core/executor.py` safety/risk ordering bug (move `risk.get_risk_level()` before `safety.apply_safety()`, or evaluate risk on the pre-rewrite name) | High (live safety bypass, 3 failing tests already exist) | Low (few-line reorder + re-run existing suite) | P0 — do first, before anything else touches `core/` |
| Fix `.gitignore` web/ path mismatch + `git rm --cached` the 6195 node_modules files | High (43MB repo bloat compounding on every future `npm install`) | Low (path fix + one cleanup commit) | P0 |
| Decide-in-writing which island is canonical Brain (jarvis_prod transport + jarvis_core reasoning) and document the decision in README/CLAUDE.md | High (blocks every downstream consolidation decision) | Low (architecture decision, not code) | P0 |
| Rename root `core/` to avoid `sys.path` collision with `jarvis_core/core/` | Medium (latent footgun, not yet triggered) | Low (rename + update 3 test files' imports) | P0 |
| Rotate the 3 previously-leaked secrets (Gmail app password, ElevenLabs key, GitHub PAT) per the live README advisory | High (credentials may still be valid despite history purge) | Low (rotation is routine ops work) | P0 |
| Remove orphaned root `package-lock.json` | Low | Trivial | P1 (quick win, bundle with gitignore fix) |
| Build the missing `pc.*` skill executor (reusing `core/`'s post-fix safety kernel) | High (jarvis_prod is non-functional for its advertised PC control without this) | Medium (new module, but schema/registry already exists in `tools.py`/`_known_skills`) | P1 |
| Run jarvis_prod's first live Tailscale/Mosquitto deployment (rebind listener off placeholder `100.64.0.1`, install Termux deps, real bot token) | High (nothing in jarvis_prod is validated beyond smoke test) | Medium (mostly ops/config, code is ready) | P1 |
| Wire jarvis_core's `memory.py`/`scheduler.py`/`brain.py` end-to-end at least once against real Ollama+Mosquitto | High (currently zero real-world evidence this loop works) | Medium-High (needs Ollama install, live broker, a full maintenance cycle) | P1 |
| Add per-node MQTT ACLs (replace `mosquitto.conf.example`'s flat `jarvis/#` grant) | Medium-High (credential leak currently = full node impersonation) | Medium | P2 |
| Move Telegram gateway PII masking to the transport layer, not just the Langfuse layer | Medium (real privacy exposure, bounded to Telegram chat access) | Low-Medium | P2 |
| Consolidate the 8 requirements.txt files into a documented cross-island version matrix (not necessarily one lockfile) | Medium (currently silent, not yet triggered breakage) | Medium | P2 |
| Rebuild `hugginggpt/web` as an MQTT/WebSocket-aware thin client | High for the AI-OS vision, Low for daily operations (ADB-driven legacy stack doesn't need it) | High | P3 |
| Add TLS to MQTT (currently relies solely on Tailscale encryption) | Medium | Medium | P3 |
| Add CI (lint + the existing test suites) so `core/`'s 33 tests and jarvis_core's 9 tests stop requiring manual interpreter discovery | Medium | Low-Medium | P3 |
| Archive/attribute `easytool/` and `taskbench/` (add LICENSE/NOTICE crediting Microsoft JARVIS lineage) or remove if never going to be referenced | Low | Low | P4 |

---

## See Also

- [context/PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) — full Chimera vision and hardware inventory
- [context/DECISION_LOG.md](DECISION_LOG.md) — full technical debt and architecture decision record
- [context/DESIGN_HISTORY.md](DESIGN_HISTORY.md) — how the three islands came to exist
- [roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) — phased plan (Phase 0 through Phase 6)
- [architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) — target architecture and recommended merge strategy
- [architecture/SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) — the `core/executor.py` bug and MQTT ACL gap in detail

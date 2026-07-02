# Project Context

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

> This is the living "who / what / where / why" snapshot of the JARVIS repo. It is the
> **fastest-staling document in this doc set** — repo state, active-development focus,
> and even ownership details can shift between sessions. Re-verify every claim in this
> file at the start of each new session rather than trusting it blindly. When in doubt,
> run `git remote -v`, `git config user.name`, `git branch --show-current`, and `git log
> --oneline -5` again before acting on anything written here.

See also: [CURRENT_STATUS.md](CURRENT_STATUS.md) for the latest audit snapshot and
open issues, [DECISION_LOG.md](DECISION_LOG.md) for why things are the way they are,
[DESIGN_HISTORY.md](DESIGN_HISTORY.md) for how the repo got here, and
[../roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) for what happens next.

---

## 1. Repo Location and Identity

| Fact | Value |
|---|---|
| Repo root | `C:/Users/Hp/OneDrive/Desktop/JARVIS` |
| Current branch | `main` |
| Git user (local config, `git config user.name`) | `Yash` |
| Configured remote(s) as of this audit | `origin` -> `https://github.com/Yash-200608/Jarvis-2.0.git` (private) |
| Recent commits | `fe41bf5` "V2.1", `4b32760` "V2", `5dfa65f` "V1", `e3234ba` "v1", `3ec9478` merge of `feat/jarvis-prod-mqtt-langfuse-v4` |

Notes and open discrepancies (do not silently resolve these — confirm with the user):

- The task brief for this document set references a second remote, `jarvis-old`, described
  as a preserved legacy remote. At the time this file was written, only `origin`
  (`Yash-200608/Jarvis-2.0`) was configured in the local git remote list. If `jarvis-old`
  exists, it may have been removed, renamed, or only ever existed on GitHub as a separate
  repository rather than as a configured local remote. **TBD - confirm with user**: does
  `jarvis-old` still exist, and if so, what is its URL and current purpose (archive-only,
  active mirror, fork source)?
- **Owner-name inconsistency (TBD - confirm with user).** Multiple identity signals in this
  repo do not agree with each other:
  - Local git config (`git config user.name`) reports the committer as `Yash`.
  - The repo's `README.md` is reported (per the repo audit that produced this doc set) to
    contain an "Owner: Ian" style attribution in at least one place.
  - `CLAUDE.md` (root project instructions) addresses the reader/user generically and does
    not name an owner at all.
  - The GitHub org/user namespace for the primary remote is `Yash-200608`.
  This document does **not** guess which name is authoritative, or whether "Ian" is a
  collaborator, a template artifact left over from a fork, a former owner, or something
  else entirely. Flag this explicitly to the user and get a definitive answer before any
  future doc (e.g. README rewrites, LICENSE files, contact info) asserts an owner name.
- Git history was rewritten with `git filter-repo` to purge three previously-leaked secrets
  (a Gmail app password, an ElevenLabs API key, and a GitHub PAT). A "rotate anyway"
  advisory is live in `README.md` (the leaked values were public for a few hours before the
  rewrite and must be treated as compromised regardless of the history rewrite). **Rotation
  status is unconfirmed by this audit** — do not assume any of the three secrets have
  actually been rotated in their respective provider dashboards. See
  [../architecture/SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) for the full
  security posture and [DECISION_LOG.md](DECISION_LOG.md) for when/why the rewrite happened.

---

## 2. What This Repo Actually Is (One Paragraph)

The JARVIS repo is not a single system — it is a filesystem containing four unrelated
Python implementations that happen to share a folder, plus a large amount of vendored,
templated, and aspirational material layered on top. Three of the four are competing,
non-communicating "Brain" candidates; the fourth is a dormant safety kernel nothing else
imports. None of them yet implement the "Chimera Ecosystem" (Brain / Jarvis / MQTT+Tailscale
mesh) described in root `CLAUDE.md`, and none of them implement the larger, less-formally-
specified "AI Operating System" ambition (thin clients across desktop/phone/web/CLI/voice/
AR-VR, 3D generation, computer-use, robotics) referenced elsewhere in project discussions.
Full architectural detail, gap analysis, and phased consolidation plan live in
[../architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) and
[../roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) — this document only records
the current, ground-truth state of "what exists and how mature is it."

---

## 3. Active Sub-Projects and Their Real Status

Use these labels verbatim in any future document — do not rename or reinterpret maturity
levels across the doc set: **"legacy stack"**, **"jarvis_prod"**, **"jarvis_core"**,
**"root core/"**.

### 3.1 Legacy stack — daily production use (stable)

- **Path:** `hugginggpt/server/` (`awesome_chat.py`, Flask, HuggingFace models, Anthropic
  Claude API)
- **Runtime:** Python 3.10, `jarvis_env` venv
- **Status:** **The only system in this repo actually in daily use.** Runs every day over
  ADB against the Nothing Phone 3a Lite.
- **Do not touch** during any consolidation work until a replacement has demonstrably
  matched its current command set — see the "do not touch" carve-out in
  [../roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md).

### 3.2 jarvis_prod — smoke-tested, never deployed live

- **Path:** `hugginggpt/server/jarvis_prod/` (`brain_pc.py` + `agent_phone.py`)
- **Runtime:** Python 3.12 system interpreter (requires 3.11+ for `TaskGroup`/`except*`)
- **Transport:** MQTT v5 via `aiomqtt` 2.x, QoS 1, Mosquitto broker intended to run over
  Tailscale
- **Status:** Smoke-tested only. `_smoke.py` spins up a real Brain plus a fake phone agent
  through a real MQTT broker and passes — but this stack has **never had a live Tailscale
  run**. `mosquitto.conf.example` still uses the placeholder IP `100.64.0.1`, which has never
  been rebound to a real Tailscale address.
- No PC-side skill executor exists to receive the `pc.*` commands `brain_pc.py` already
  advertises (`pc.system.lock`, `pc.shell.run`, etc.) — see
  [../architecture/TOOL_ROUTING.md](../architecture/TOOL_ROUTING.md).

### 3.3 jarvis_core — never run end-to-end (frozen prototype)

- **Path:** `jarvis_core/` (Planner = Llama3, Critic = Mistral, Executor — a Reflexion loop
  via local Ollama; ChromaDB RAG with `nomic-embed-text`)
- **Status:** Committed exactly once, in commit `5dfa65f` ("V1"), and never touched again
  across two subsequent commits (`4b32760` "V2", `fe41bf5` "V2.1" — those commits changed
  other parts of the repo, not `jarvis_core/`). **Never run end-to-end**: no `.env` file, no
  `history.log`, no `logs/` directory, no `brain/chroma/` directory (i.e. zero embeddings
  ever persisted), `knowledge_graph.md` is still template-only content, and
  `NEED_ATTENTION.md` (the 3-strike human-in-the-loop escalation artifact) has never been
  written by any run.
- Despite never running, this is structurally the closest thing in the repo to root
  `CLAUDE.md`'s actual Brain vision (Planner/Critic separation, sandboxed Executor, RAG
  memory) — see [../architecture/AGENT_ARCHITECTURE.md](../architecture/AGENT_ARCHITECTURE.md)
  and [../architecture/MEMORY_ARCHITECTURE.md](../architecture/MEMORY_ARCHITECTURE.md).

### 3.4 Root `core/` — dormant, well-tested, has a confirmed live safety bug

- **Path:** `core/` at repo root (distinct from `jarvis_core/core/` — see the naming
  collision note below)
- **Contents:** 512-line execution-safety kernel — `idempotency.py`, `sandbox.py`,
  `undo.py`, `risk.py`, `safety.py`, `validator.py`, `executor.py`, `state.py`, `retry.py`,
  and an 8-tool registry.
- **Status:** Imported by nothing outside its own three test files
  (`core/tests/test_execution_safety.py`, `tests/test_adversarial_execution.py`,
  `tests/test_executor_safety.py` — 565 lines of tests against 512 lines of implementation).
  Unusually thorough test coverage for a module nothing else depends on.
- **Confirmed live bug:** `safety.apply_safety()` runs **before** `risk.get_risk_level()`
  in `executor.py`, so a high-risk `delete_file` action is rewritten to the medium-risk
  `move_to_trash` **before** the risk check runs — bypassing confirmation entirely. This has
  been reproduced by direct execution. Three tests currently fail as a result:
  `test_high_risk_action_requires_confirmation_before_safety_rewrite`,
  `test_confirmation_bypass_attack_is_rejected`,
  `test_external_confirmation_does_not_bypass_safeguards`.
- Per the standing hard rule for this task, `core/idempotency.py`, `core/sandbox.py`,
  `core/undo.py`, and anything under `tests/` were **not modified** while writing this
  document — they are mid-edit elsewhere and out of scope here. Full remediation plan is in
  [../roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) (Phase 0) and the bug itself
  is analyzed in [../architecture/SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md).
- **Naming collision:** both root `core/` and `jarvis_core/core/` are importable as bare
  `core` depending on `sys.path` order. `jarvis_core/tests/conftest.py` inserts `jarvis_core/`
  onto `sys.path` so its own imports resolve internally and never touch root `core/` — but
  this is fragile and should not be relied on by any future refactor.

### 3.5 WALKER2CANE — separate active research project

- Referenced in root `CLAUDE.md` as an "AI-assisted rehabilitation system" and an active
  research project.
- **Not audited in this pass.** No claims about its code, status, or maturity are made in
  this document set. If work on WALKER2CANE intersects with JARVIS/Chimera (e.g. shared
  hardware, shared MQTT topics), that intersection should be documented separately once
  investigated. **TBD - confirm with user** on current status and any relationship to the
  Chimera ecosystem described here.

---

## 4. Hardware Inventory

Sourced from root `CLAUDE.md`. This is the hardware the target Chimera architecture is
expected to eventually integrate — see
[../specifications/COMPUTER_CONTROL.md](../specifications/COMPUTER_CONTROL.md) and
[../architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) for how
each device maps (or doesn't yet map) onto software.

### 4.1 Computing nodes

| Device | Status | Role |
|---|---|---|
| Main Desktop PC | Active | Brain host, dev machine, MQTT broker |
| Nothing Phone 3a Lite | Active (daily driver) | Mobile Jarvis node — the only node with a live implementation today (`agent_phone.py`'s `phone.*` skills), reached via Termux/SSH and, for the legacy stack, ADB |
| OPPO smartphone | Spare | Future camera / dashboard / voice node — no software exists for this yet |
| Vivo 1901 | Spare | Future sensor / monitor node — no software exists for this yet |
| Arduino UNO R4 WiFi | Active | Microcontroller, sensors, actuators — no corresponding software node exists in any of the three Python islands yet |

### 4.2 Salvaged tablet components (Android, ~10.1")

Motherboard: `A6001_MB_PCB_V4` — stored intact, potential repair candidate. Earmarked as a
future Jarvis display/voice terminal.

| Component | ID / Spec | Status | Priority use |
|---|---|---|---|
| LCD panel | Innolux P101DCA-AB0 (10.1") | Likely functional | Jarvis smart display |
| Battery | 3.85V Li-ion, 7000mAh | Unknown, no swell observed | Portable node power |
| Speakers | 09C26QA / 09C26QB (stereo) | Intact | Jarvis voice output |
| Camera | HQK5A03 | Intact | Vision node |
| RF antenna | A6001_DRX_ANT_PCB_V1.0 | Intact | RF / ESP32 experiments |
| Microphone | MEMS (likely) | Recoverable | Wake-word / voice input |
| Digitizer | Heavily cracked | Unusable | Keep with LCD for protection |

Handling constraints (from `CLAUDE.md`, repeated here because they are safety-relevant, not
just architectural): do not harvest BGA chips (CPU, RAM, eMMC, PMIC) without rework
equipment; store the motherboard intact; store the LCD flat and do not bend flex cables;
inspect the battery for swelling/leakage before any use.

None of the OPPO, Vivo, Arduino, or salvaged tablet components have any corresponding
software node in `jarvis_prod`, `jarvis_core`, or the legacy stack today — the MQTT topic
scheme (`jarvis/node/<id>/...` per `protocol.py`) is generic enough to address them, but no
code instantiates or targets any of them yet.

---

## 5. Repo-Hygiene Facts Worth Tracking Here

These are state-of-the-repo facts, not architecture decisions — full remediation is tracked
in [../roadmap/MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) Phase 0.

- `.gitignore`'s "for web" section targets a nonexistent root-level `web/` instead of the
  actual `hugginggpt/web/` path, resulting in roughly 6,195 files (~43MB) of `node_modules`
  committed to git — about 97% of the repo's 6,399 tracked files.
- Root `package-lock.json` is an orphaned, empty lockfile (`{packages:{}}`) with no
  corresponding root `package.json`, tracked since commit `e3234ba` ("v1").
- 8 separate `requirements.txt` files exist across the repo with no shared lockfile and no
  `pyproject.toml`; there is no CI (no `.github/workflows` directory anywhere).
- `easytool/` and `taskbench/` are vendored, unmodified Microsoft JARVIS-lineage academic
  benchmark repos (EasyTool and TaskBench papers) with no LICENSE files and old pinned
  dependencies — zero integration with the rest of the repo.

---

## 6. Claude Code Skill Personas

Ten lightweight, consistently-formatted skill personas exist under `.claude/skills/`:
`ai-product-bible`, `brain-auditor`, `fastapi-production`, `hardware-integration`,
`jarvis-architect`, `memory-system-reviewer`, `mqtt-debugger`, `multi-agent-reviewer`,
`startup-cto`, `voice-ai-engineer`. All were created within minutes of each other on Jun 14
and share a uniform YAML-frontmatter-plus-bullet-body format. These can be reused as review
tooling during any future consolidation work (e.g. `jarvis-architect` already encodes
"prevent tight coupling between Jarvis and Brain" as a persona rule, matching this doc set's
own design principle).

---

## 7. How to Re-Verify This Document

Before trusting anything above in a new session, re-run:

```
git remote -v
git config user.name
git branch --show-current
git log --oneline -5
```

And re-check for the presence/absence of: `docs/`, `NEED_ATTENTION.md` under `jarvis_core/`,
`brain/chroma/` under `jarvis_core/`, and any new commits touching `core/`, `jarvis_core/`,
or `hugginggpt/server/jarvis_prod/`. If any of these have changed, update this file and
[CURRENT_STATUS.md](CURRENT_STATUS.md) before relying on either for planning.

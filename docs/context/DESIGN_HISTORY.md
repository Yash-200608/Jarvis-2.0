# Design History

Status: Planning phase — pre-implementation
Last updated: 2026-07-01

## What this document is (and is not)

This is a **narrative reconstruction**, not a record of decisions anyone
actually wrote down. Nobody on this project kept a design log, and there is
no ADR trail from the time these choices were made. Everything below is
inferred from git history (commit hashes, dates, diff stats, commit
messages), file contents, and code evidence gathered during the 2026-07-01
repo audit that produced the rest of the `docs/` tree.

**Treat every claim here as "this is what the evidence suggests," not "this
is what happened."** Where the evidence runs out, this document says so
explicitly and writes `TBD — confirm with user` rather than inventing intent.
If you are Yash (or anyone else who was actually present for these
decisions), corrections are expected and welcome — in particular the
unexplained gap around why `jarvis_core/` was abandoned after a single
commit (see [The jarvis_core question](#the-jarvis_core-question-unexplained)
below), which this document cannot resolve from code alone.

For the current, as-of-today state of the system (not history), see
[`CURRENT_STATUS.md`](CURRENT_STATUS.md). For the decisions that should be
made going forward, see [`DECISION_LOG.md`](DECISION_LOG.md). For how all of
this maps onto a target architecture, see
[`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md).

---

## Timeline at a glance

| Date (2026) | Commit(s) | Event |
|---|---|---|
| (upstream, undated in this repo) | — | Forked from Microsoft's JARVIS/HuggingGPT research repo |
| Apr 21 | `85dc68d`, `6277cdd`, `9f622961`, `aff3df34` | jarvis_prod MQTT stack built; secrets purged from history |
| Apr 25 | `398c8c5`, `3ec9478` (merge PR #1) | jarvis_prod stack merged to main, README updated |
| Apr 25 | `e3234ba` ("v1") | Root `core/` scaffolded (stub files) alongside an unrelated `awesome_chat.py` tweak; orphaned `package-lock.json` first tracked |
| Apr 25 | `5dfa65f` ("V1", merge commit) | `jarvis_core/` introduced wholesale, in full, in one commit |
| Apr 25 | `4b32760` ("V2") | Root `core/` fleshed out substantially (428 lines added) — `jarvis_core/` untouched |
| Apr 26 | `fe41bf5` ("V2.1") | Root `core/executor.py` reworked again (96 lines changed) — `jarvis_core/` still untouched |
| (undated) | — | `CLAUDE.md` "Chimera Ecosystem" vision authored |
| (undated) | — | Broader "JARVIS X / AI Operating System" vision layered on top |
| 2026-07-01 | — | This planning phase begins — architecture docs written to reconcile the fragmentation before further implementation |

Exact commit metadata for reference:

```
e3234bae 2026-04-25 22:55:10 +0530  "v1"    — scaffolds root core/ (stub files)
5dfa65f3 2026-04-25 23:06:03 +0530  "V1"    — merge of PR #1 (jarvis_prod) + introduces jarvis_core/ in full
4b32760f 2026-04-25 23:19:38 +0530  "V2"    — root core/ fleshed out (jarvis_core/ untouched)
fe41bf55 2026-04-26 00:21:24 +0530  "V2.1"  — root core/executor.py reworked (jarvis_core/ untouched)
```

All commits are authored by `Yash <yashkadyan2008@gmail.com>` on branch
`main`, roughly 90 minutes apart on the night of Apr 25–26, except V2.1 which
followed about an hour later.

---

## Chapter 1 — A fork of Microsoft's JARVIS/HuggingGPT

The earliest evidence in this repo's own git log is a lineage of commits
that belong to Microsoft's public JARVIS research repository (HuggingGPT
paper), including merges like `8652645` ("Merge pull request #254 from
StillKeepTry/patch-1"), `ad3a0fa` ("Merge branch 'main' of
https://github.com/microsoft/JARVIS into main"), and README/data cleanup
commits that predate any Chimera- or Jarvis-2.0-specific work. This
confirms the project began as a fork/clone of Microsoft's academic
HuggingGPT demo, not a from-scratch build.

The clearest surviving artifact of this lineage is
`hugginggpt/Dockerfile.jetson`, whose first line still reads:

```
# NVIDIA Jetson embedded device support with GPU accelerated local model execution for https://github.com/microsoft/JARVIS
```

Two other vendored directories, `easytool/` and `taskbench/`, are
unmodified copies of Microsoft's companion academic benchmark repos
(the EasyTool and TaskBench papers) from the same research lineage. Neither
has a LICENSE file, neither is integrated with anything, and their pinned
dependencies (`langchain==0.0.260`, `openai==0.27.8`, `pandas==1.2.4`) are
old enough to date them to this same original-fork era. See
[`CURRENT_STATUS.md`](CURRENT_STATUS.md) for their present (inert) status.

**What the evidence does not show:** exactly when the fork happened, who
did it, or what the original intent was beyond "start from HuggingGPT."
`TBD — confirm with user`.

---

## Chapter 2 — Extending the fork into a personal assistant (the legacy stack)

At some point the forked HuggingGPT server (`hugginggpt/server/`,
`awesome_chat.py`, Flask-based, orchestrating HuggingFace models) was
extended with a proprietary LLM brain (Anthropic Claude API calls) and
device-control bridges. Commit messages make the intent explicit:

- `d29c6d3` — "feat(jarvis): Add JARVIS personal AI assistant with Claude
  brain, voice pipeline & device control"
- `a56133c` / `ec1a865` — "feat(jarvis): JARVIS-Personal-AI-Assistant —
  Claude brain, voice pipeline & full device control"

This is the origin of what this document set calls the **legacy stack**:
`hugginggpt/server/awesome_chat.py` plus its PC and Android bridge
components, run over ADB against a Python 3.10 `jarvis_env` virtualenv.
This is the only implementation in the repo confirmed to run **daily** —
every other stack described below is either never-deployed or never-run.
Its device-control surface (`device_integration.py`, and later
`jarvis/pc_skills.py`, `jarvis/phone_skills.py`, `jarvis/router.py`)
predates and is architecturally unrelated to the MQTT approach that
follows in Chapter 3.

**What the evidence does not show:** the exact original names/paths of
"`bridge_server.py`" and "`phone_bridge.py`" as such — the audit's
synthesis JSON and this task's brief reference those names, but the
current tree's equivalents are `hugginggpt/server/awesome_chat.py` (PC
side, Flask/HTTP) and the `jarvis/phone_skills.py` + ADB-driven Android
bridge (phone side). If those literal filenames existed at an earlier
point in history and were later renamed/merged, that rename is not
independently confirmed by this audit. `TBD — confirm with user.`

---

## Chapter 3 — jarvis_prod: the internet-capable successor (built, smoke-tested, never live)

On **April 21, 2026**, a second, architecturally distinct system was built
alongside the legacy stack rather than replacing it:

- `85dc68d` — "feat(jarvis_prod): MQTT stack + Langfuse v4 tracing +
  SQLite JobLog + croniter"
- `6277cdd` — "fix(awesome_chat): pooled HTTP session w/ retry + timeouts;
  skip heavy-dep tests cleanly" (a hardening pass on the legacy stack,
  done in the same window)
- `9f62296` — "restore: non-source files from lost commit 7804614 (venv
  binaries + runtime state)" — evidence of at least one lost/corrupted
  commit during this period, recovered rather than re-derived
- `aff3df3` — "security: purge 3 live secrets + wire env-var expansion in
  config loader" — the commit that necessitated the `git filter-repo`
  history rewrite referenced in `README.md`'s rotate-anyway advisory
  (a Gmail app password, an ElevenLabs key, and a GitHub PAT were live in
  history before this purge)
- `398c8c5` and `3ec9478` (Apr 25) — README updated to document the new
  stack, then merged to `main` via PR #1
  ("feat/jarvis-prod-mqtt-langfuse-v4")

This produced **jarvis_prod** (`hugginggpt/server/jarvis_prod/`):
`brain_pc.py` + `agent_phone.py` communicating over MQTT v5
(`aiomqtt` 2.x, QoS 1) via a Mosquitto broker intended to run over
Tailscale, with a Telegram gateway, `croniter`-based scheduling, a
SQLite-backed `JobLog` with Reflector self-correction (circuit-breaker
logic), and Langfuse tracing. It shipped as a complete feature in a single
merged PR, including its own integration test (`_smoke.py`, a real Brain
process talking to a fake phone agent through a real broker) — this is the
only cross-component integration test that exists anywhere in the repo.

Despite that test coverage, jarvis_prod has never had a live run against
real Tailscale infrastructure: `mosquitto.conf.example` still ships with
the placeholder IP `100.64.0.1`, and no logs, job history, or presence
data from a genuine deployment exist anywhere in the tree. It is best
understood as **"production-grade code that has never been in
production."**

For the current state of this stack (what's wired, what's missing —
notably the absent `pc.*` skill executor), see
[`CURRENT_STATUS.md`](CURRENT_STATUS.md) and
[`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md).

---

## Chapter 4 — Two more prototypes, in one frantic night (Apr 25–26)

Roughly 90 minutes after jarvis_prod's merge PR (`3ec9478`) landed, three
more commits went in over about 90 minutes, each on `main`, each with a
minimal commit message (`v1`, `V1`, `V2`, `V2.1`). Reading their diffs
individually — not just their labels — reveals two **separate,
unrelated efforts** that happen to share a naming pattern:

### 4a. Root `core/` — a from-scratch execution-safety kernel

`e3234ba` ("v1", 22:55) first scaffolded root-level `core/` as a set of
near-empty stub files (`executor.py` at 37 lines, most others under 15)
alongside an unrelated one-line-ish tweak to `awesome_chat.py`. This same
commit is also where the orphaned `package-lock.json` (`{"packages":{}}`,
no corresponding `package.json`) was first tracked.

`4b32760` ("V2", 23:19) then fleshed this module out substantially: 428
lines added across `core/config.py`, `core/executor.py`,
`core/idempotency.py`, `core/memory.py`, `core/retry.py`, `core/safety.py`,
`core/sandbox.py`, `core/state.py`, `core/tools.py`, `core/undo.py`,
`core/validator.py`, plus a new adversarial test file
(`core/tests/test_execution_safety.py`, 106 lines).

`fe41bf5` ("V2.1", the next morning, 00:21) reworked `core/executor.py`
again — 69 insertions, 27 deletions, entirely within that one file.

This is the module this document set calls **root `core/`**: an
idempotency/sandbox/undo/risk/safety kernel with unusually thorough
adversarial test coverage (33 tests across three files, 565 lines of
tests against 512 lines of implementation) that has never been imported
by anything outside its own test suite, and which the 2026-07-01 audit
found has a live, reproducible safety-ordering bug in `executor.py`
(`safety.apply_safety()` runs before `risk.get_risk_level()`, so a
high-risk `delete_file` call is silently rewritten to a medium-risk
`move_to_trash` before the risk check ever sees it — three tests in the
module's own suite currently fail asserting the correct behavior). See
[`../architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md)
for the full writeup of that bug and
[`DECISION_LOG.md`](DECISION_LOG.md) for its priority.

### 4b. jarvis_core — a from-scratch local-first Brain prototype

The merge commit `5dfa65f` ("V1", 23:06 — eleven minutes after `e3234ba`)
is where **`jarvis_core/`** entered the repository, in its entirety, as
part of the same merge that finalized jarvis_prod's PR #1. Its diff stat
shows the complete module landing at once: `jarvis_core/core/brain.py`
(440 lines), `executor.py`, `memory.py`, `ollama_client.py`,
`sandbox.py`, `scheduler.py`, `sensors.py`, `signing.py`,
`transport.py`, plus `jarvis_core/agent/phone_executor.py` (390 lines),
Tasker/Android watchdog scripts, config, and two test files
(`tests/test_sandbox.py`, `tests/test_signing.py`).

Architecturally, jarvis_core is the most sophisticated Brain candidate in
the repo: a Planner (Llama3) / Critic (Mistral) / Executor Reflexion loop
running against local Ollama models, with ChromaDB RAG memory
(`nomic-embed-text` embeddings), a 3-strike `NEED_ATTENTION.md`
human-in-the-loop escalation gate, and a single `sandbox.safe_path()`
choke point reused by both the Executor and a post-Critic-approval
re-check. It is, on paper, the closest thing in the repo to CLAUDE.md's
"Brain owns memory" vision — closer than jarvis_prod, which has no
semantic memory layer at all.

**And then it stopped.** `jarvis_core/` was never touched again. The two
subsequent commits that night (`4b32760` "V2", `fe41bf5` "V2.1") — despite
sharing a version-number-like naming scheme with `5dfa65f` "V1" — modified
only root `core/`, not `jarvis_core/`. There is no `.env` file, no
`history.log`, no `logs/` directory, no `brain/chroma/` persisted
embeddings directory, and `jarvis_core/brain/knowledge_graph.md` is still
its 4-line template header from the initial commit. No `NEED_ATTENTION.md`
has ever been written. By every runtime-evidence signal available, this
module has never been executed end-to-end, not even once, against live
Ollama models or a live broker.

### The naming trap

The commit sequence `v1` → `V1` → `V2` → `V2.1` reads, at a glance, like
one linear versioning effort. It is not. `V1`'s diff is dominated by the
jarvis_core introduction (and incidentally also finalizes the jarvis_prod
merge), while `V2`/`V2.1` are exclusively root `core/` work. Anyone
skimming `git log --oneline` without reading diffs — including, plausibly,
whoever picked these commit messages in the moment — could easily
conflate "the V-series commits" as one continuous thread of work on one
module. They are two unrelated modules that happened to get committed in
the same overnight session under similar-looking labels. This is also the
origin of the sys.path collision documented in
[`../architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md):
root `core/` and `jarvis_core/core/` are both importable as bare `core`
depending on which directory sits earlier on `sys.path`, and
`jarvis_core/tests/conftest.py` has to explicitly insert `jarvis_core/`
onto `sys.path` to make its own `from core.sandbox import ...` resolve to
the right one internally — a workaround for a collision, not a namespace
design.

---

## The jarvis_core question (unexplained)

This is the single largest gap in the reconstructed history, and it is
worth stating plainly: **why did work on `jarvis_core/` stop completely,
one commit after it was introduced, while root `core/` kept receiving
active iteration for two more commits the same night and the following
morning?**

Possible explanations that the evidence is *consistent with* but does
**not** confirm:

- jarvis_core may have been judged too heavy to stand up quickly (it
  requires a local Ollama install with two pulled models plus a live
  Mosquitto broker just to smoke-test, versus root `core/`'s pure-Python
  unit-testable surface) and got deprioritized in favor of the
  lower-friction module.
- jarvis_core may have been an exploratory "what would a real Brain look
  like" sketch that was always intended as a reference design rather than
  something to run immediately — i.e., paused by design, not abandoned.
  by accident.
- The two efforts may simply reflect two different nights' worth of
  attention with no deeper significance to the gap — root `core/` was
  just what got picked back up next.
- Something external (time constraints, a shift in priority toward the
  CLAUDE.md "Chimera Ecosystem" vision authored around this period,
  hardware/tooling blockers for running local Ollama models) may have
  interrupted the work.

None of these can be verified from the repository alone. `TBD — confirm
with user.` Whatever the real answer is, it directly determines whether
Phase 2 of the roadmap ("prove jarvis_core actually runs," see
[`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md)) is
resuming intended work or resurrecting an intentionally shelved
experiment — the recommended next step is simply to ask.

---

## Chapter 5 — The vision documents arrive on top of the fragmentation

At some point in this timeline — not independently datable from git
metadata beyond "present at HEAD" — `CLAUDE.md` was authored at the repo
root, describing a unified "Chimera Ecosystem": a Brain/Jarvis/MQTT+
Tailscale layered architecture spanning the PC, the Nothing Phone 3a Lite,
spare OPPO and Vivo 1901 handsets, an Arduino UNO R4 WiFi, and salvaged
10.1" tablet components (Innolux P101DCA-AB0 LCD, MEMS mic, stereo
speakers) as a future dedicated Jarvis display/voice terminal.

Notably, **none of the three real Python implementations described above
implement this vision.** The legacy stack predates it and has no MQTT
awareness at all. jarvis_prod's topic scheme (`jarvis/node/<id>/...`) is
generically compatible with addressing multiple hardware nodes but no
code in the repo instantiates or targets any device beyond the Nothing
Phone. jarvis_core's Reflexion loop is the closest conceptual match to
"Brain owns memory," but has never run. The vision document was written
either aspirationally ahead of the code, or as a retrospective attempt to
give the fragmented effort a unifying frame — the evidence cannot
distinguish between these. `TBD — confirm with user.`

A broader "JARVIS X — AI Operating System" ambition (thin clients across
desktop/phone/web/CLI/voice/AR-VR, 3D generation, computer-use,
robotics) appears to have been layered on top of the Chimera framing at
some later point still. This is the largest and least-implemented layer
of ambition in the project; see
[`../vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md) for how this
document set treats it as a target rather than a current capability, and
[`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) for why it
is explicitly sequenced *after* the consolidation work below, not
alongside it.

Ten Claude Code skill personas (`.claude/skills/`: `ai-product-bible`,
`brain-auditor`, `fastapi-production`, `hardware-integration`,
`jarvis-architect`, `memory-system-reviewer`, `mqtt-debugger`,
`multi-agent-reviewer`, `startup-cto`, `voice-ai-engineer`) were all
created within minutes of each other on June 14 — evidently a single
tooling/scaffolding session, and the most recent dated activity this
audit can pin before today's planning phase. `jarvis-architect.md` in
particular already encodes "prevent tight coupling between Jarvis and
Brain" as an explicit persona rule, suggesting the CLAUDE.md principles
were already settled by that date even if the code did not yet reflect
them.

---

## Chapter 6 — Today: reconciliation before further implementation

As of **2026-07-01**, the repository contains four disconnected pieces of
working or semi-working code (legacy stack, jarvis_prod, jarvis_core, root
`core/`), two vendored academic reference repos with no integration, an
unmodified template web frontend, a vision document describing an
architecture none of the code implements, and a larger unwritten AI-OS
ambition on top of that. This planning phase — the document set this file
is part of — exists specifically to stop before adding a fifth island of
code, and instead produce a written decision about which existing
implementation becomes canonical, in what order the others get merged in
or archived, and what must be fixed (starting with the confirmed
executor.py safety bug) before any of it is trusted with real
device-control authority.

See [`CURRENT_STATUS.md`](CURRENT_STATUS.md) for the precise state of each
component as of today, [`DECISION_LOG.md`](DECISION_LOG.md) for the
decisions this reconciliation requires, and
[`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) for the
phased plan that follows from it.

---

## Summary of unresolved questions for the user

1. Why did `jarvis_core/` receive no commits after its single introduction
   (`5dfa65f`), while root `core/` continued to be actively developed the
   same night and the next morning? (See
   [The jarvis_core question](#the-jarvis_core-question-unexplained).)
2. Did filenames literally named `bridge_server.py` / `phone_bridge.py`
   ever exist, or is `awesome_chat.py` + the `jarvis/*_skills.py` /
   ADB bridge always what filled that role?
3. What is the exact origin point/date of the initial fork from
   Microsoft's JARVIS repo — was it a `git clone` starting fresh, or a
   long-lived fork that tracked upstream for a period before diverging?
4. Was `CLAUDE.md`'s "Chimera Ecosystem" vision written before, during, or
   after the jarvis_prod/jarvis_core/root-core burst of work on Apr 25–26?
5. Has credential rotation (Gmail app password, ElevenLabs key, GitHub
   PAT — see `aff3df3` and the README advisory) actually been completed?
   This audit could not confirm rotation status from repo evidence alone.

Answers to any of these should be appended to this document (or corrected
inline) rather than left to be re-guessed by a future session.

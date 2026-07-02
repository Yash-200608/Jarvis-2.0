# Decision Log

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

## Purpose and Scope

This log is the append-only, chronological record of architecturally significant decisions made across the JARVIS repository — both decisions that were deliberately made and decisions that appear, from the evidence in the repo, to have been made implicitly by omission or abandonment. It exists so that future Claude Code sessions and future contributors do not have to re-derive "why is it like this" from git archaeology every time.

Every entry below is reconstructed from repository evidence (commit history, code, config files, and test suites), not from firsthand testimony. Where the audit could not determine intent or rationale with confidence, the entry says so explicitly rather than inventing a justification. Entries are numbered in roughly the order the underlying work landed, not strict chronological commit order, since several of these decisions were bundled into the same commits.

### ADR Schema

As of this revision, every entry follows a formal Architecture Decision Record (ADR) structure rather than free-form prose. This was introduced because the previous version mixed fact and interpretation freely, had no status/ownership/risk metadata, and had no way to express dependencies between decisions. The new schema:

- **Semantic ID** — a stable, human-readable identifier (e.g. `ARCH-001`, `SEC-002`) independent of the sequential `ADR-0NN` number, grouped by domain prefix (`ARCH`, `SEC`, `NET`, `INFRA`, `AGENT`, `PLAN`). Use these IDs, not just the number, when cross-referencing from other docs, since prefixes carry meaning the bare number does not.
- **Status** — one of `Accepted (Retroactive)` (reconstructed from evidence after the fact, no contemporaneous record of deliberation), `Accepted` (made and recorded going forward), `Superseded`, `Deprecated`, `Experimental`, `Rejected`, or `Under Review` (open, not yet finalized). Note this document-level ADR `Status` field is distinct from the whole-document lifecycle `Status: Planning phase - pre-implementation` line at the top of this file.
- **Category** — one or more tags ([Architecture], [Security], [Infrastructure], [Memory], [Voice], [3D], [Agents], [Networking], [Planning], [UI], [Governance]) for filtering.
- **Owner** — currently always Yash (Architect), with an Approved/Pending marker reflecting whether a human has actually signed off, versus the decision being a system-generated placeholder awaiting sign-off.
- **Risk Level** — Low / Medium / High / Critical, an editorial judgment about what happens if this decision is wrong or reversed carelessly.
- **Implementation State** — Not Started / Planning / In Progress (X%) / Implemented — Not Verified / Implemented — Verified / Deprecated. This is deliberately distinct from `Status`: a decision can be `Status: Accepted` while its `Implementation State` is `Not Verified` (decided and built, but never proven to run correctly) — this distinction matters enormously in this repo, where most components were built once and never run end-to-end.
- **Depends On / Supersedes / Conflicts With** — explicit ADR-to-ADR relationships, so a change to one decision's premises can be traced to everything downstream of it.
- **Relates To Principles** — cross-references to the numbered, ID-stable principle registry in [`architecture/ARCHITECTURE_PRINCIPLES.md`](../architecture/ARCHITECTURE_PRINCIPLES.md) (a companion document in this same batch; distinct from the narrative version in `vision/PRODUCT_PRINCIPLES.md`).
- **Review** — the trigger condition under which this ADR should be re-examined (a roadmap phase, a specific event, or "Immediately" for urgent open items).
- **Assumptions** — what must remain true for the decision to still make sense; if the assumption breaks, the ADR is stale even if nothing else changed.
- **Evidence vs. Observed Behavior vs. Inference** — three deliberately separated sections. Evidence is raw fact (file paths, code behavior, commit hashes, test names, config values) with zero interpretation. Observed Behavior states what the evidence shows is actually happening, still objectively. Inference is clearly labeled interpretation, carrying an informal confidence marker ("high-confidence inference" vs "speculative"). This three-way split is the direct fix for the previous version's main defect: fact and interpretation being blended in the same paragraphs.

### Historical vs. Current Entries

Entries split into two groups with different evidentiary weight:

- **Reconstructed-from-archaeology entries** (ADR-001 through ADR-007, and ADR-009) describe decisions embedded in code and commits with no contemporaneous documentation. Their Evidence sections are kept intentionally short and point to [`context/DESIGN_HISTORY.md`](DESIGN_HISTORY.md) for the full narrative and commit-level detail, rather than duplicating that narrative here. This log is not the place to re-tell the story — it is the place to record the decision's structure, status, and consequences.
- **Live / current / forward-looking entries** (ADR-008, ADR-010, ADR-011) are not just historical record — they govern what happens next. These carry full detail directly in this document because there is no separate narrative doc that covers them, and because they are the entries most likely to be acted on.

This document should be read alongside:
- [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) for how these decisions compose into the current system shape
- [`context/CURRENT_STATUS.md`](CURRENT_STATUS.md) for the present-day state of each component named here
- [`context/DESIGN_HISTORY.md`](DESIGN_HISTORY.md) for the narrative version of how the repo got here
- [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) for what happens to each decision going forward (fix, keep, revisit, retire)
- [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md) for the security consequences of ADR-002, ADR-003, ADR-008, and ADR-010 in particular
- [`architecture/ARCHITECTURE_PRINCIPLES.md`](../architecture/ARCHITECTURE_PRINCIPLES.md) for the numbered, ID-stable principle registry (P-01, P-03, etc.) referenced throughout this log
- [`brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md) for terminology and invariants governing whatever is decided as the canonical Brain (see ADR-011)

Terminology used throughout follows the canonical labels established by the repo audit: **legacy stack** (`hugginggpt/server/`, ADB-driven, daily use), **jarvis_prod** (`hugginggpt/server/jarvis_prod/`, MQTT stack, smoke-tested but never live), **jarvis_core** (`jarvis_core/`, Ollama Reflexion prototype, never run), **root `core/`** (execution-safety kernel, dormant, has a confirmed live bug). These labels are not to be renamed or reinterpreted in this or any other doc in this set.

---

## ADR-001: Build jarvis_prod as an MQTT/Tailscale successor while keeping the legacy HTTP/ADB stack alive for daily use

Semantic ID: ARCH-001
Status: Accepted (Retroactive)
Category: [Architecture] [Networking]
Owner: Yash (Architect) — Approved
Risk Level: Medium
Implementation State: Implemented — Not Verified (never run live)
Depends On: None
Supersedes: None
Conflicts With: None
Relates To Principles: P-01, P-03, P-06
Review: After Phase 3
Date: Unconfirmed — landed as part of the commit range culminating in `4b32760` ("V2") / `fe41bf5` ("V2.1"). Exact commit introducing `hugginggpt/server/jarvis_prod/` not isolated by this audit; TBD - confirm with user via `git log --follow` on `brain_pc.py`.

### Assumptions
- Assumes the legacy stack continues to be viable as a daily-use fallback while jarvis_prod remains unproven.
- Assumes a future merge (not a rewrite) is the intended resolution path — see ADR-011.

### Evidence
See `context/DESIGN_HISTORY.md` Chapter 3 for full narrative and commit-level evidence. In short: `hugginggpt/server/jarvis_prod/` contains `brain_pc.py`, `agent_phone.py`, `protocol.py` (MQTT v5 via `aiomqtt` 2.x, QoS 1, `jarvis/node/<id>/...` topic scheme, Mosquitto broker over Tailscale); `hugginggpt/server/awesome_chat.py` is the legacy Flask/HuggingFace/Claude stack, untouched by this build.

### Observed Behavior
The new MQTT stack was built as a parallel, from-scratch codebase alongside the legacy stack rather than as an incremental modification of it. `jarvis_prod`'s `_brain_prompt` optionally imports the legacy stack's `parse_device_command` from `awesome_chat.py` for NL parsing — a soft coupling between the two otherwise-independent stacks.

### Inference
High-confidence inference: `jarvis_prod` was built as the intended architectural successor to the legacy stack, targeting the multi-node Chimera model (OPPO, Vivo, Arduino UNO R4, future ESP32s) that the legacy stack's single-phone ADB design cannot support. The audit found no branch, commit, or comment indicating an incremental-migration path (e.g., adding MQTT as an optional transport inside `awesome_chat.py`) was considered and rejected — the parallel-build approach appears to be the only path attempted.

### Decision
Build a new, parallel MQTT-based command-and-control stack as the intended successor to the legacy stack, rather than incrementally migrating the legacy stack itself. The legacy stack was left completely untouched and continues to run daily over ADB.

### Consequences
This is the direct origin of the repo's core fragmentation problem — two non-communicating "brains" now exist, each capable of driving phone actions, with no shared memory or state. jarvis_prod inherited none of the legacy stack's operational hardening (it has never had a live Tailscale run, while the legacy stack runs daily). The `_brain_prompt` → `parse_device_command` soft coupling means the two stacks are not as independent as they appear — legacy-stack changes can silently degrade jarvis_prod without erroring. See `architecture/SYSTEM_ARCHITECTURE.md` for the intended resolution (merge jarvis_prod's transport with jarvis_core's reasoning, not resurrect or migrate the legacy stack directly) and ADR-011 for the open decision this points toward.

---

## ADR-002: Rewrite git history with `filter-repo` to purge three leaked secrets

Semantic ID: SEC-001
Status: Accepted (Retroactive)
Category: [Security] [Governance]
Owner: Yash (Architect) — Approved
Risk Level: Critical
Implementation State: Implemented — Not Verified (rotation status unconfirmed)
Depends On: None
Supersedes: None
Conflicts With: None
Relates To Principles: P-10
Review: Immediately — confirm rotation status with user
Date: Unconfirmed exact date — evidenced by a live "rotate anyway" advisory in `README.md`. TBD - confirm with user for the exact date the rewrite was performed.

### Assumptions
- Assumes the three secrets (Gmail app password, ElevenLabs API key, GitHub PAT) have not been rotated unless explicitly confirmed otherwise. Treat this assumption as false-until-proven-true, not the reverse.
- Assumes no other copies of these secrets exist outside the canonical remote (unconfirmed — see Consequences).

### Evidence
See `context/DESIGN_HISTORY.md` Chapter 3 for full narrative and commit-level evidence. In short: `README.md` currently carries a live "rotate anyway" advisory referencing three secret types (Gmail app password, ElevenLabs API key, GitHub PAT); a `jarvis-old` remote is preserved alongside the rewritten canonical history.

### Observed Behavior
`git filter-repo` was used to strip the three secrets from all reachable commits in the canonical remote's history. The `jarvis-old` remote, which predates the rewrite, still contains the original (pre-purge) history.

### Inference
High-confidence inference: this was a deliberate, informed choice to prioritize history-cleanliness over history-preservation for the affected commits — `filter-repo` is the correct tool for that goal and its use is not accidental. No evidence of alternatives (e.g., relying solely on rotation without history rewrite, or GitHub push-protection alone) being considered.

### Decision
Use `git filter-repo` to strip three leaked secrets from the entire git history: a Gmail app password, an ElevenLabs API key, and a GitHub Personal Access Token.

### Consequences
History rewriting does **not** invalidate the credentials themselves — only rotation does that. **This audit could not confirm whether rotation actually happened.** Until confirmed, treat the Gmail app password, ElevenLabs key, and GitHub PAT as potentially still valid and compromised. This is flagged as a P0 action in `roadmap/MASTER_ROADMAP.md`. Anyone with a pre-rewrite clone or fork of the repo (including the preserved `jarvis-old` remote) still has the secrets in their local history — the rewrite only protects the canonical remote going forward, not any existing clones. See `architecture/SECURITY_MODEL.md`.

---

## ADR-003: Asymmetric MQTT session semantics — brain uses `clean_start=True`, phone uses `clean_start=False` + 24h session expiry

Semantic ID: NET-001
Status: Accepted (Retroactive)
Category: [Networking]
Owner: Yash (Architect) — Approved
Risk Level: Low
Implementation State: Implemented — Partially Verified (smoke-tested only, never live)
Depends On: ARCH-001 (ADR-001)
Supersedes: None
Conflicts With: None
Relates To Principles: P-03, P-06
Review: After Phase 4 (first live deployment)
Date: Unconfirmed — part of jarvis_prod's initial build. TBD - confirm with user.

### Assumptions
- Assumes the Brain's continuous-uptime, fail-fast-on-reconnect model and the phone's sleep/wake, durability-first model remain the correct fit for each role's hardware behavior.

### Evidence
`jarvis_prod/protocol.py` and its callers: `brain_pc.py` connects with `clean_start=True`; `agent_phone.py` connects with `clean_start=False` and a `SessionExpiryInterval` of 24 hours. Validated only by `_smoke.py` (real Brain + fake phone through a real broker) — never exercised on a live Tailscale link with real phone sleep/wake cycles.

### Observed Behavior
On reconnect, the Brain discards any queued/stale session state (`clean_start=True`). The phone's session persists queued QoS-1 messages across reconnects for up to 24 hours (`clean_start=False` + `SessionExpiryInterval`).

### Inference
High-confidence inference: this is a deliberate design choice, not an accident — the asymmetry is consistent across both connection sites and is correct for each role's reliability needs. The Brain runs continuously on the PC and should not replay a backlog of stale commands accumulated while offline; the phone sleeps frequently (screen-off, Doze mode, Termux backgrounding) and needs Mosquitto to hold commands queued for it, bounded to 24 hours so nothing queues forever. No evidence of a third alternative (e.g., message-level TTL instead of session-level) being considered.

### Decision
Configure `brain_pc.py` to connect with `clean_start=True` and `agent_phone.py` to connect with `clean_start=False` plus a 24-hour `SessionExpiryInterval`.

### Consequences
This is one of the soundest pieces of engineering in the repo per the audit and should be preserved as-is in any merge (see ADR-001 and `architecture/SYSTEM_ARCHITECTURE.md`). Known tradeoff: because the Brain's `clean_start=True` discards state on reconnect, a phone response arriving after a Brain restart is lost even if the phone successfully executed the action — this can produce confusing "it failed" entries in the JobLog for actions that actually succeeded (see ADR-006/INFRA-003 and `architecture/SECURITY_MODEL.md` risk notes).

---

## ADR-004: Force `WindowsSelectorEventLoopPolicy` at import time for `aiomqtt`/`paho` compatibility

Semantic ID: INFRA-001
Status: Accepted (Retroactive)
Category: [Infrastructure]
Owner: Yash (Architect) — Approved
Risk Level: Low
Implementation State: Implemented — Verified
Depends On: ADR-001 (ARCH-001)
Supersedes: None
Conflicts With: None
Relates To Principles: P-04
Review: On any refactor of brain_pc.py's entrypoint
Date: Unconfirmed — part of jarvis_prod's initial build. TBD - confirm with user.

### Assumptions
- Assumes the Brain continues to run on Windows (the main desktop PC per CLAUDE.md's hardware inventory), not WSL or a Linux host.

### Evidence
`brain_pc.py` and `_smoke.py` both call `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` at import time, before any event loop is created.

### Observed Behavior
The selector-policy override is applied unconditionally at module import, ahead of any asyncio loop creation, in both the production entrypoint (`brain_pc.py`) and the smoke-test entrypoint (`_smoke.py`).

### Inference
High-confidence inference: this is a targeted compatibility fix, not incidental code. The default Windows asyncio event loop (`ProactorEventLoop`) is a known incompatibility source for `paho-mqtt`'s socket handling as used by `aiomqtt` on Windows. Given the Brain is explicitly meant to run on the existing Windows desktop (not a new machine), forcing the selector policy is the minimal, correct fix rather than a workaround requiring new infrastructure (e.g. running under WSL). No evidence of alternatives being considered.

### Decision
Set `WindowsSelectorEventLoopPolicy` at import time in both `brain_pc.py` and `_smoke.py`.

### Consequences
This must be preserved in any future refactor that touches `brain_pc.py`'s startup sequence — removing or reordering it (e.g., moving it after another module's import that creates a loop) would silently reintroduce the incompatibility. It is a narrow but load-bearing platform-specific fix and should be called out explicitly in code comments and in `architecture/SYSTEM_ARCHITECTURE.md` if/when the Brain's entrypoint is restructured.

---

## ADR-005: Reflector fail-streak threshold set to 3 consecutive failures, hydrated from SQLite so it survives restarts

Semantic ID: INFRA-002
Status: Accepted (Retroactive)
Category: [Infrastructure] [Agents]
Owner: Yash (Architect) — Approved
Risk Level: Medium
Implementation State: Implemented — Not Verified
Depends On: ADR-001 (ARCH-001)
Supersedes: None
Conflicts With: None
Relates To Principles: P-06, P-11
Review: Before/during Phase 4 — add time-decay or explicit reset before live deployment
Date: Unconfirmed — part of jarvis_prod's `jobs.py`. TBD - confirm with user.

### Assumptions
- Assumes recurring/heartbeat-driven jobs are the primary consumer of this breaker and that transient failures (brief network blips, phone reboots) are common enough to matter operationally.

### Evidence
`jarvis_prod/jobs.py` implements a Reflector circuit breaker that trips after 3 consecutive job failures; the consecutive-failure count is persisted in SQLite and re-read on process restart. No time-decay mechanism exists anywhere in `jobs.py`.

### Observed Behavior
The breaker's failure count is not reset on process restart — it is rehydrated from SQLite, so a failure streak accumulated before a restart continues to count after the restart. The threshold (3) is flat and non-decaying: failures do not age out over time.

### Inference
High-confidence inference: rehydrating from SQLite is the correct choice for a guard like this — an in-memory-only counter would silently reset every time the Brain process restarts, defeating the breaker's purpose if the Brain happens to restart during a failure streak. An in-memory-only counter and a time-decayed threshold both appear to have been considered and not implemented (rejected by omission, not explicit rejection) in favor of a flat, persistent, non-decaying counter.

### Decision
Trip the Reflector circuit breaker after 3 consecutive job failures, with the failure count persisted in SQLite and rehydrated on restart rather than reset in memory.

### Consequences
The 3-strike threshold has no time decay, which the audit's risk assessment flags as an operational hazard: a heartbeat-driven skill (e.g., a 15-minute battery watchdog) that fails 3 times in a row for a transient reason will be **permanently** disabled with silence as the only symptom, and no automatic recovery path exists. This is a known risk to watch for specifically during the first live Tailscale deployment (`roadmap/MASTER_ROADMAP.md` Phase 4) — it will look like a mysterious outage if not monitored or fixed (e.g., by adding time-decay or an explicit reset command) before then.

---

## ADR-006: JobLog implemented as SQLite with an in-memory hot-path counter

Semantic ID: INFRA-003
Status: Accepted (Retroactive)
Category: [Infrastructure] [Memory]
Owner: Yash (Architect) — Approved
Risk Level: Low
Implementation State: Implemented — Not Verified
Depends On: ADR-005 (INFRA-002)
Supersedes: None
Conflicts With: None
Relates To Principles: P-05, P-14
Review: TBD - confirm with user
Date: Unconfirmed — part of jarvis_prod's `jobs.py`. TBD - confirm with user.

### Assumptions
- Assumes single-host, local-first scale continues to be sufficient (no need for a networked datastore like PostgreSQL or Redis at this stage).

### Evidence
`jarvis_prod/jobs.py` persists job/command execution history to a SQLite database (JobLog) and separately maintains an in-memory counter for the hot path (the Reflector's consecutive-failure check, see ADR-005/INFRA-002). `jobs.db` (20KB, dated Apr 25) is currently committed to the repo under `workspace/` with real smoke-test operational history.

### Observed Behavior
Two representations of job state coexist: a durable SQLite store and an in-memory counter used for the hot path rather than querying SQLite live on every check.

### Inference
High-confidence inference: SQLite gives durable, queryable history without the operational overhead of a separate database server, consistent with the project's "local-first where practical" principle; the in-memory counter avoids a disk read on every job-completion event, which matters for a system responding to frequent heartbeats. No evidence of an alternative datastore being evaluated — consistent with the project's small-scale, single-host design at this stage. This dual-representation pattern could drift out of sync if not carefully kept consistent (e.g., a crash between an in-memory increment and the corresponding SQLite write) — no evidence in the audit of this actually happening, this is a speculative risk, not an observed one.

### Decision
Persist execution history to SQLite (JobLog) while maintaining a separate in-memory counter for the hot-path Reflector check rather than querying SQLite on every check.

### Consequences
This dual-representation pattern is a normal and reasonable engineering choice but is a place where the two representations could drift out of sync if not carefully kept consistent. Worth testing explicitly if `jobs.py` survives into the merged Brain (`roadmap/MASTER_ROADMAP.md` Phase 3). Also note: `jobs.db` is currently committed to the repo under `workspace/` — this should be gitignored, not treated as source-controlled data (see `roadmap/MASTER_ROADMAP.md` P1/P2 hygiene items).

---

## ADR-007: Telegram gateway uses long-polling, not webhooks

Semantic ID: NET-002
Status: Accepted (Retroactive)
Category: [Networking]
Owner: Yash (Architect) — Approved
Risk Level: Low
Implementation State: Implemented — Not Verified
Depends On: ADR-001 (ARCH-001)
Supersedes: None
Conflicts With: None
Relates To Principles: P-04, P-05
Review: TBD - confirm with user
Date: Unconfirmed — part of jarvis_prod's Telegram integration. TBD - confirm with user.

### Assumptions
- Assumes the project continues to avoid public-facing HTTP infrastructure (no TLS termination, no reverse proxy, no exposed port) as a standing networking posture.

### Evidence
`telegram_gateway.py` uses Telegram's long-polling API (`getUpdates`), repeatedly called. No webhook registration code, public endpoint, or TLS certificate handling exists anywhere in the repo for Telegram integration.

### Observed Behavior
The gateway process maintains an open, repeatedly-polled connection to Telegram's servers rather than exposing an inbound HTTP endpoint for Telegram to push to.

### Inference
High-confidence inference: long-polling was chosen because it requires no public-facing HTTP endpoint, no TLS certificate, and no port-forwarding/reverse-proxy setup — it works from behind Tailscale/NAT with zero additional infrastructure, consistent with the project's local-first, Tailscale-only networking posture (CLAUDE.md: "Tailscale is the network... minimize cloud dependency"). Webhooks appear rejected by omission — given the project has no public-facing infrastructure anywhere else either, long-polling is the only option that fits the existing network model without introducing new exposure.

### Decision
Use long-polling (`getUpdates`) for the Telegram gateway rather than registering a webhook.

### Consequences
The gateway process must run continuously and maintain an open connection to Telegram's servers at all times — if the Brain process is down, Telegram messages queue on Telegram's side (bounded by Telegram's own retention) rather than being lost outright, but nothing is delivered until the process resumes. This is a reasonable tradeoff for a single-operator personal-assistant system and requires no changes at this time. Flag as a design constraint (not a defect) in `specifications/VOICE_SYSTEM.md` or wherever Telegram-as-a-channel is specified going forward.

---

## ADR-008: Legacy stack's `bridge_server.py` / `device_integration.py` remove all confirmation gates for "full access" device control

Semantic ID: SEC-002
Status: Under Review
Category: [Security] [Governance]
Owner: Yash (Architect) — Pending
Risk Level: Critical
Implementation State: Implemented — Verified (ironically, it is verified working-as-built, which is the problem)
Depends On: None
Supersedes: None
Conflicts With: the intended safety-gating architecture in `architecture/SECURITY_MODEL.md`. No formal ADR exists yet for that intended architecture — this is noted explicitly here rather than inventing a fake ADR number for it.
Relates To Principles: P-10
Review: Before Phase 3 (must be resolved before the merged Brain could inherit this pattern)
Date: Unconfirmed — part of the legacy stack. TBD - confirm with user for when and why this was introduced.

### Assumptions
Assumes the legacy stack's trust boundary is limited to the owner's own ADB-paired phone — this assumption is itself unverified and should be confirmed with the user, not treated as settled justification. If this assumption is false (e.g., the stack is or becomes reachable beyond that single paired device), the risk classification here understates the actual exposure.

### Evidence
`hugginggpt/server/bridge_server.py` and `hugginggpt/server/device_integration.py` implement a "full access" mode for device control over ADB in which confirmation gating is absent — no comment, commit message, or doc explaining the tradeoff was found anywhere in the repo.

### Observed Behavior
Actions in the legacy stack's "full access" mode execute over ADB without any human-in-the-loop confirmation check beforehand. This is the only system in the repo in daily production use, and it has the weakest confirmation gating of any of the four islands.

### Inference
Speculative inference: as the only system in daily use, the legacy stack appears to have prioritized frictionless operation — every confirmation prompt is a point of friction for a single trusted daily user. This is a plausible, but explicitly unconfirmed, rationale reconstructed from the code's behavior alone. No evidence of a considered-and-rejected middle ground (e.g., risk-tiered confirmation matching root `core/`'s `risk.py`/`safety.py`) was found — the removal of confirmation gates appears to be an unqualified, blanket choice rather than a risk-scoped one.

### Decision
Not yet resolved — this entry exists to flag the pattern for revisit, not to endorse it. The legacy stack currently runs in daily use with confirmation gating fully removed for "full access" device control.

### Consequences
**This decision should be revisited, not merely logged.** It directly conflicts with the project vision's safety-boundary requirements as embodied by root `core/`'s own design (risk tiers, confirmation-required high-risk actions, idempotency, undo) and with the Chimera principle of hardware-aware, fault-tolerant, safe automation. It is architecturally inconsistent for the one system in daily production use to have **weaker** safety gating than the dormant, never-wired-in `core/` module built specifically to provide that gating — and `core/` itself currently has a live safety-ordering bug of its own (see ADR-010/PLAN-001). This inconsistency should be resolved explicitly in `architecture/SECURITY_MODEL.md`: either the legacy stack's full-access mode needs documented, scoped justification (e.g., "only reachable by the owner's own ADB-paired phone, therefore the trust boundary is different from a network-exposed skill") or it needs the same confirmation/risk gating the rest of the vision assumes. This is an open risk item carried forward into `roadmap/MASTER_ROADMAP.md` as a decision requiring explicit re-approval or remediation before the legacy stack's capabilities are extended or replicated in the merged Brain.

---

## ADR-009: jarvis_core built from scratch as a local-first Ollama-based Reflexion Brain, committed once, never run or touched again

Semantic ID: AGENT-001
Status: Experimental
Category: [Agents] [Architecture] [Memory]
Owner: Yash (Architect) — Approved
Risk Level: Medium
Implementation State: Implemented — Not Verified (0% runtime-verified — never executed end-to-end even once)
Depends On: None
Supersedes: None
Conflicts With: None
Relates To Principles: P-01, P-05, P-10
Review: After Phase 2 (validation run)
Date: Committed in `5dfa65f` ("V1"). Left untouched through `4b32760` ("V2") and `fe41bf5` ("V2.1") — i.e., two subsequent version-labeled commits touched other parts of the repo without ever returning to `jarvis_core/`.

### Assumptions
- Assumes jarvis_core's structural design (Planner/Critic separation, sandbox chokepoint, 3-strike escalation) remains sound even though none of it has been exercised. This assumption is untested by definition and should not be relied upon until Phase 2 validation occurs.

### Evidence
See `context/DESIGN_HISTORY.md`'s "The jarvis_core question (unexplained)" section for full narrative and commit-level evidence. In short: `jarvis_core/` contains a Planner (Llama3) / Critic (Mistral) / Executor Reflexion loop against a local Ollama instance, ChromaDB-based RAG memory (`nomic-embed-text` embeddings), a `sandbox.safe_path()` chokepoint shared between the Executor and a post-Critic-approval re-check, and a 3-strike `NEED_ATTENTION.md` escalation gate. `NEED_ATTENTION.md` has never been written, no `.env`, no `history.log`, no `logs/` directory, no `brain/chroma/` directory exists, and `knowledge_graph.md` remains its template header, never appended to.

### Observed Behavior
The entire `jarvis_core/` module was committed once in `5dfa65f` and has had zero further commits, zero runtime artifacts (no logs, no persisted embeddings, no escalation file), across two subsequent version-labeled commits that touched other parts of the repo.

### Inference
This entry is explicitly not logging a reasoned choice between alternatives — it is flagging an absence. **There is no evidence in the repo of why development stopped.** This is the audit's most important distinction to preserve here: this is not a documented architectural decision to abandon jarvis_core, it is an unexplained stop. The "why did it stop" question is unresolved; do not speculate further in this entry — see `context/DESIGN_HISTORY.md`'s "The jarvis_core question (unexplained)" section, and do not retroactively construct a rationale for it in this or any other doc.

### Decision
No decision to log — jarvis_core was built once and left untouched. The absence of any subsequent action is the notable fact, not a choice.

### Consequences
Because jarvis_core has never been run end-to-end, it cannot yet be trusted as a component of the merged Brain regardless of how structurally sound it looks on paper — the repo audit explicitly calls for a validation phase (`roadmap/MASTER_ROADMAP.md` Phase 2: "Prove jarvis_core Actually Runs") before any of its code is merged into the canonical Brain. Until that validation happens, treat every claim about jarvis_core's behavior (the Reflexion loop, the Critic's rejection behavior, the 3-strike escalation, ChromaDB persistence) as **unverified design**, not confirmed capability. This is the clearest instance in the repo of the general failure pattern flagged in the audit's risk assessment: stopping at "designed and built" without ever reaching "observed working." See also ADR-011, which depends on this ADR being resolved via Phase 2 validation before a canonical-Brain decision can be finalized.

---

## ADR-010: Establish the `/docs` planning system, and treat the `core/executor.py` safety-bug fix as the top-priority next architectural action

Semantic ID: PLAN-001
Status: Accepted
Category: [Planning] [Governance]
Owner: Yash (Architect) — Approved
Risk Level: Medium
Implementation State: In Progress — the docs/ system itself is now substantially built (this describes the current deliverable); the core/executor.py fix described within it has NOT yet been started.
Depends On: SEC-002 (ADR-008), AGENT-001 (ADR-009)
Supersedes: None
Conflicts With: None
Relates To Principles: P-01, P-10, P-11
Review: After Phase 0 completes (see `roadmap/MASTER_ROADMAP.md`)
Date: 2026-07-01

### Assumptions
- Assumes that documenting the fragmented state of the repo (three non-communicating Brain candidates plus a dormant safety kernel) is a necessary precondition for any sound merge or consolidation decision, rather than the docs system itself becoming a substitute for that decision.
- Assumes the `core/executor.py` bug fix remains scoped as a small, well-tested reorder and does not expand into a larger refactor before landing.

### Evidence
A full repo audit (the basis for this entire `/docs` set) found: three non-communicating Python "Brain" candidates (legacy stack, jarvis_prod, jarvis_core) plus a dormant safety kernel (root `core/`); no written record anywhere of which is canonical; no CI; repo-hygiene debt (committed `node_modules`, an orphaned lockfile); unconfirmed secret-rotation status (see ADR-002/SEC-001); and a reproducible security bug in `core/executor.py`, where `safety.apply_safety()` rewrites `delete_file` to `move_to_trash` *before* `risk.get_risk_level()` is checked, letting a high-risk action through with zero confirmation. `core/`'s 565-line test suite has 3 currently-failing tests against exactly this bug: `test_high_risk_action_requires_confirmation_before_safety_rewrite`, `test_confirmation_bypass_attack_is_rejected`, `test_external_confirmation_does_not_bypass_safeguards`.

### Observed Behavior
As of 2026-07-01, the `/docs` directory is being actively populated as the canonical planning record. The `core/executor.py` bug remains unfixed as of this writing; the three failing tests named above still fail.

### Inference
High-confidence inference: continuing to add features on top of an undocumented, three-way-fragmented, safety-buggy foundation would compound technical and security debt rather than make progress. Continuing to build directly on jarvis_core or root `core/` without first fixing the bug or documenting the canonical-Brain decision would repeat the exact pattern that produced three unreconciled islands and one unexplained abandonment (see ADR-009/AGENT-001). Fixing `core/executor.py` first without establishing `/docs` would leave a point fix without a written decision record, which does not prevent the same fragmentation from recurring. Deferring both to prioritize shipping the missing `pc.*` skill executor or the web dashboard rebuild instead would build on an unsettled foundation and is explicitly sequenced later in `roadmap/MASTER_ROADMAP.md` (Phases 3 and 6 respectively).

### Decision
Major architectural work should not begin until the documented planning phase (Phase 0-1 of the roadmap) is complete. Critical bug fixes, security patches, and routine maintenance may continue during this period — this decision pauses new *architecture*, not all engineering activity. Concretely, this means: (1) stand up this `/docs` directory as the canonical, permanent planning and architecture record for the project, and (2) treat fixing the confirmed live safety-gate bypass in `core/executor.py` as the single highest-priority next action once architectural implementation resumes.

### Consequences
This phase is deliberately low-visibility from a new-major-architecture standpoint during the planning period, but critical fixes (including the `core/executor.py` bug) are not blocked by it and may proceed — this is assessed as the highest-leverage sequencing given the audit's findings. The `core/executor.py` fix itself is scoped as low-effort (a few-line reorder, validated by tests that already exist and already specify correct behavior — see `architecture/SECURITY_MODEL.md` and `roadmap/MASTER_ROADMAP.md` Phase 0) and should not be allowed to expand into a larger refactor before landing. This decision also formally establishes that future architecturally significant choices are expected to be recorded here going forward, using the template below, rather than left implicit in commit history the way ADR-001 and ADR-003 through ADR-007 had to be reconstructed after the fact.

---

## ADR-011: Designate the Canonical Brain Implementation

Semantic ID: ARCH-002
Status: Under Review (Not Yet Decided)
Category: [Architecture] [Agents] [Memory]
Owner: Yash (Architect) — Pending
Risk Level: High (blocks every downstream consolidation decision per roadmap Phase 1)
Implementation State: Not Started
Depends On: ARCH-001 (ADR-001), AGENT-001 (ADR-009)
Supersedes: None
Conflicts With: None
Relates To Principles: P-01, P-07
Review: This IS the Phase 1 review itself — see `roadmap/MASTER_ROADMAP.md` Phase 1.
Date: Unconfirmed — see `context/DESIGN_HISTORY.md`

### Assumptions
Assumes jarvis_core can be proven to run end-to-end in Phase 2 before this decision is finalized. If jarvis_core cannot be made to run, this ADR's premises (and the recommendation summarized below) would need to be revisited rather than assumed still valid.

### Evidence
This ADR does not introduce new evidence — it points to the already-written recommendation in `specifications/BRAIN_SPEC.md` Section 3, which proposes merging jarvis_prod's transport layer, jarvis_core's reasoning layer, and root `core/`'s safety kernel (post-fix) into a single canonical Brain. See that document for full detail rather than repeating it here.

### Observed Behavior
No merge work has begun. All four islands (legacy stack, jarvis_prod, jarvis_core, root `core/`) remain structurally separate as of this writing, per `context/CURRENT_STATUS.md`.

### Inference
High-confidence inference (carried from `specifications/BRAIN_SPEC.md`): jarvis_prod's transport (MQTT/Tailscale, ADR-001/ARCH-001) is the most architecturally forward-compatible piece for multi-node addressing; jarvis_core's Planner/Critic/Executor Reflexion design (ADR-009/AGENT-001) is the most structurally sound reasoning approach on paper but is entirely unverified; root `core/`'s safety kernel is the only component with adversarial test rigor but has a confirmed live bug (see ADR-010/PLAN-001) that must be fixed before it is trustworthy as a dependency. None of these three can be merged as-is today without their respective open items (live-run verification for jarvis_prod, end-to-end proof for jarvis_core, and the executor.py fix for root `core/`) being resolved first.

### Decision
This ADR is a **placeholder** recording that the canonical-Brain decision is pending, not yet made. It does not select jarvis_prod, jarvis_core, root `core/`, or any merge of them as canonical. Status remains `Under Review` until a human signs off per `specifications/BRAIN_SPEC.md` Section 3.1's preconditions.

### Consequences
Every downstream consolidation decision in `roadmap/MASTER_ROADMAP.md` Phase 1 and beyond is blocked on this ADR being resolved — this is why its Risk Level is High despite Implementation State being Not Started. Until this is decided, no new code should be written on the assumption that any one of the four islands is "the" Brain going forward. See `brain/BRAIN_CONSTITUTION.md` for the terminology and invariants that will apply once a canonical implementation is designated.

---

## Rejected Decisions

This section exists for decisions that were considered and explicitly not chosen, with `Reason` and `Alternative Chosen` fields for each. No decision in this log currently carries `Status: Rejected` — the repo audit found no evidence of an alternative being explicitly considered and rejected (e.g., no trace of an evaluated-and-discarded framework, library, or architecture). This section exists so that future rejected alternatives are recorded here rather than silently disappearing, preventing the same idea from being re-evaluated repeatedly. When a genuine rejected-alternative decision is identified with real evidence, add it here using the ADR template below with `Status: Rejected`.

---

## Template for Future Entries

Copy this block for every new architecturally significant decision. Append new entries below the last entry, in order, and give each one the next sequential ADR number and an appropriately-prefixed Semantic ID.

```
## ADR-0NN: <short imperative title>

Semantic ID: <PREFIX-0NN>
Status: Accepted (Retroactive) | Accepted | Superseded | Deprecated | Experimental | Rejected | Under Review
Category: [Architecture] [Security] [Infrastructure] [Memory] [Voice] [3D] [Agents] [Networking] [Planning] [UI] [Governance]  (one or more, pick the fitting ones)
Owner: Yash (Architect) — Approved | Pending
Risk Level: Low | Medium | High | Critical
Implementation State: Not Started | Planning | In Progress (X%) | Implemented — Not Verified | Implemented — Verified | Deprecated
Depends On: <ADR-0NN, ... or "None">
Supersedes: <ADR-0NN or "None">
Conflicts With: <ADR-0NN or "None">
Relates To Principles: <e.g. P-10, P-04 — from architecture/ARCHITECTURE_PRINCIPLES.md>
Review: <trigger, e.g. "After Phase 3" or "Immediately — confirm with user">
Date: <YYYY-MM-DD or "Unconfirmed — see context/DESIGN_HISTORY.md">

### Assumptions
<bullet list of what this decision assumes remains true; if it stops being true, this ADR should be revisited>

### Evidence
<PURE OBSERVED FACTS ONLY — file paths, code behavior, commit hashes, test names, config values. No interpretation. For entries that are primarily historical archaeology already narrated in full in context/DESIGN_HISTORY.md, keep this section SHORT and point there rather than re-narrating everything.>

### Observed Behavior
<what the evidence shows is actually happening/built, still objective>

### Inference
<clearly-labeled interpretation, with an informal confidence marker such as "high-confidence inference" or "speculative">

### Decision
<what was/is decided, one or two sentences>

### Consequences
<what this commits the project to, forecloses, or creates risk/debt around>
```

Guidance for future entries:
- If a decision's rationale cannot be confirmed from evidence (code, commits, config, or direct user statement), write "TBD — confirm with user" rather than inventing a plausible-sounding justification. This log's credibility depends on not blending reconstructed-from-evidence entries with invented ones.
- If a decision conflicts with the project vision or another standing decision in this log, use the `Conflicts With` field and flag it explicitly in Consequences (as in ADR-008) rather than logging it silently.
- If something looks like a decision but is actually an unexplained stop or abandonment (as in ADR-009), say so — do not retroactively construct intent that isn't evidenced. Use `Status: Experimental` rather than `Accepted` for such cases.
- Set `Status` honestly: use `Under Review` for anything not yet finalized by a human (see ADR-008, ADR-011), and do not mark something `Accepted` merely because code for it exists — `Accepted` is about the decision, `Implementation State` is about whether it was built and verified. These two axes are independent; a decision can be firmly `Accepted` while `Implementation State: Not Started`, or dubiously `Under Review` while `Implementation State: Implemented — Verified` (as in ADR-008).
- Use `Depends On` / `Supersedes` / `Conflicts With` even when the answer is "None" — leaving the fields out entirely makes it impossible to tell whether the relationship was considered and found absent, versus simply never checked.
- Keep `Risk Level` an honest editorial judgment, not a formality — critical security and safety items (see ADR-002, ADR-008) should read as urgent at a glance.

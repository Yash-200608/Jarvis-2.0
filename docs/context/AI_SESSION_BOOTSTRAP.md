Status: Planning phase - pre-implementation
Last updated: 2026-07-01

# AI Session Bootstrap

## Purpose

Every new Claude Code (or other AI) session starts with zero memory of prior
sessions. This document exists so that context comes from durable, written
files rather than being re-derived from chat history or re-discovered by
re-auditing the repo from scratch. It is the literal entry point for any
session touching this repository. [`README.md`](../README.md)'s "Read This
First" section should point here instead of listing individual docs inline —
this file is now the single source of truth for session-start reading order.

## Required Reading Order

Read these in order, before making any changes. Do not skip ahead.

1. [`docs/VISION.md`](../VISION.md) — the one-sentence North Star everything
   else serves. If a proposed change doesn't trace back to this, question it.
2. [`docs/brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md) — the
   terminology and invariants that must not be silently violated. Establishes
   the canonical labels (legacy stack / jarvis_prod / jarvis_core / root
   `core/`) used everywhere else in this doc set.
3. [`docs/context/CURRENT_STATUS.md`](CURRENT_STATUS.md) — what is actually
   real, working, broken, or untested right now, across all four islands.
4. [`docs/context/DECISION_LOG.md`](DECISION_LOG.md) — what has already been
   decided (and what is still open or pending). Pay special attention to any
   ADR flagged for revisit or left unresolved (see ADR-008, ADR-009) — these
   are decisions a session must not silently re-litigate or silently ignore.
5. [`docs/roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) — what
   phase the project is in and what's next, including the current phase's
   exit criteria (Section 2, per-phase "Exit criteria" line).
6. Open risks — read the Risk Register in
   [`docs/roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) (Section
   4) and the Known Issues table in
   [`docs/context/CURRENT_STATUS.md`](CURRENT_STATUS.md). Both must be
   checked; they are not duplicates of each other.
7. Next milestone — identify the current roadmap phase's exit criteria
   (Section 2 of `MASTER_ROADMAP.md`). A session should be able to answer
   "what does 'done' look like for the thing I'm about to touch" before
   writing anything.

## Before Writing Any Code

Confirm all of the following before making a change:

1. Does this touch a file inside one of the three fragmented islands (legacy
   stack / jarvis_prod / jarvis_core) or root `core/`? If so, is this
   consistent with the current roadmap phase in `MASTER_ROADMAP.md`, or does
   it risk adding a fourth island — the audit's core risk finding (see
   ADR-009, ADR-010)?
2. Does this action bypass any invariant listed in
   [`docs/brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md)
   Section 3?
3. Does this change qualify as a "critical bug fix / security patch /
   maintenance improvement" (allowed to proceed under ADR-010's scope even
   while new feature work is paused), or is it "major architectural work"
   that should wait for the relevant roadmap phase?
4. If touching root `core/`: is the confirmed `executor.py` safety-ordering
   bug (safety rewrite applied before risk level is checked — see ADR-010,
   `docs/context/CURRENT_STATUS.md` Known Issues) already fixed? If not,
   treat that fix as a blocking prerequisite per Roadmap Phase 0 before any
   other work in `core/`.
5. Is there an existing ADR in `DECISION_LOG.md` that already covers this
   decision? If yes, follow it or explicitly supersede it in writing — do not
   silently diverge.
6. Does this change require a new architectural decision? If yes, plan to
   append a new ADR before ending the session (see below).

## Before Ending a Session

Confirm all of the following before finishing:

1. If an architectural decision was made or changed, was it appended to
   [`DECISION_LOG.md`](DECISION_LOG.md) as a new entry — never silently
   overwritten or edited in place?
2. If the working/broken/untested status of any component changed, was
   [`CURRENT_STATUS.md`](CURRENT_STATUS.md) updated to reflect it?
3. If a roadmap phase's exit criteria were met, was
   [`MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) updated to reflect it?

## See Also

- [`README.md`](../README.md) — top-level doc set index.
- [`docs/context/PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) — longer-form
  background if the reading order above raises open questions.
- [`docs/context/DESIGN_HISTORY.md`](DESIGN_HISTORY.md) — historical record
  of how the codebase arrived at its current fragmented state.

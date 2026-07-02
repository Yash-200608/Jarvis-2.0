# Architecture Principles Registry

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

## Purpose

This document is the canonical, ID-stable registry of the project's engineering principles. It exists so that ADRs in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) and other docs have a permanent, short identifier (`P-01` ... `P-15`) to cite instead of restating or paraphrasing a principle each time.

This is a **companion to, not a replacement for**, [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md):

- **This document (`ARCHITECTURE_PRINCIPLES.md`)** — the short, stable, citable-by-ID list. One line per principle. No rationale, no evidence, no current-reality analysis. Optimized for fast scanning and for being linked to from anywhere in the repo without going stale.
- **[`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md)** — the full narrative for each of these same 15 principles: rationale, current-reality evidence per principle (file/path level), the summary table, and the de-duplication notes explaining how the 15 were derived from CLAUDE.md + the JARVIS X vision.

The ID mapping, order, and wording below are fixed and correspond 1:1 to `PRODUCT_PRINCIPLES.md`'s numbered list (P-01 = principle 1, P-15 = principle 15). Do not renumber, reorder, or reword the statements independently in either document — if a statement needs to change, see "Amending this registry" below.

Going forward, ADRs in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) should cite principles from this document by ID, e.g. `Relates to: P-10`.

---

## Registry

| ID | Statement | Status | Full rationale + evidence |
|---|---|---|---|
| **P-01** | Brain Owns Memory (Single Source of Truth). No component other than the Brain persists authoritative state. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #1 for full rationale and current-reality evidence. |
| **P-02** | Clients Stay Lightweight. Jarvis-layer clients (desktop, phone, web, CLI, voice, future AR/VR) contain no orchestration or business logic. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #2 for full rationale and current-reality evidence. |
| **P-03** | MQTT Is the Event-Driven Nervous System. All inter-node communication is asynchronous, pub/sub, and tolerant of absence. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #3 for full rationale and current-reality evidence. |
| **P-04** | Tailscale Is the Network. Node-to-node transport security is handled once, at the network layer, not reimplemented per-service. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #4 for full rationale and current-reality evidence. |
| **P-05** | Local-First, Cloud-Optional. Core functionality must not depend on internet reachability or a specific cloud provider's uptime. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #5 for full rationale and current-reality evidence. |
| **P-06** | Modular and Fault-Tolerant (Runtime). No single node's absence may crash or permanently wedge another node. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #6 for full rationale and current-reality evidence. |
| **P-07** | Modularity and Separation of Concerns (Code-Level). Single-responsibility components with clear, non-colliding boundaries. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #7 for full rationale and current-reality evidence. |
| **P-08** | Hardware-Aware. The system maintains an explicit model of what devices exist and what they can do. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #8 for full rationale and current-reality evidence. |
| **P-09** | Provider-Agnostic and Model-Agnostic. No orchestration logic is coupled to a specific LLM vendor or model. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #9 for full rationale and current-reality evidence. |
| **P-10** | Security and Permission-Gating. Every consequential or irreversible action is risk-classified and confirmation-gated by default. | Accepted (implementation non-compliant) | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #10 for full rationale and current-reality evidence; see [`SECURITY_MODEL.md`](SECURITY_MODEL.md) §2 for the confirmed live confirmation-bypass bug in `core/executor.py`. |
| **P-11** | Observability. Every consequential action is logged, traceable, and inspectable after the fact. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #11 for full rationale and current-reality evidence. |
| **P-12** | Extensibility. New nodes, skills, and capabilities can be added without modifying core orchestration. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #12 for full rationale and current-reality evidence. |
| **P-13** | Scalability. The design does not assume a fixed, small number of nodes or a single Brain instance. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #13 for full rationale and current-reality evidence. |
| **P-14** | Stateless-Where-Appropriate. Components do not accumulate server-side session state that belongs in the Brain's memory layer instead. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #14 for full rationale and current-reality evidence. |
| **P-15** | Never Hardcode Providers, Paths, or Environment Assumptions. Config over code, always. | Accepted | See [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) #15 for full rationale and current-reality evidence. |

---

## Note on P-10's status

The "Status" column above records whether the *principle itself* is accepted as a target, not whether the codebase currently complies with it. For all 15 principles, current implementation is partial-to-nonexistent (see [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md)) — that is expected of a pre-implementation planning phase and is not flagged per-row here. P-10 is called out specifically because it has a **confirmed, reproduced, live violation** (not merely an absence of implementation): `core/executor.py` evaluates risk on a post-rewrite action name, silently downgrading a high-risk `delete_file` call to a medium-risk `move_to_trash` call before the confirmation gate ever sees it, defeating the gate entirely. This is documented in detail in [`SECURITY_MODEL.md`](SECURITY_MODEL.md) §2 and is a P0 item in [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md). The principle P-10 remains Accepted — the fix is to correct the implementation, not to weaken the principle.

---

## Related documents

- [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md) — full narrative, rationale, and current-reality evidence per principle (this document's source of truth for content; this document only assigns IDs and one-line statements).
- [`../vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md) — product vision these principles serve.
- [`../VISION.md`](../VISION.md) — one-page North Star.
- [`../brain/BRAIN_CONSTITUTION.md`](../brain/BRAIN_CONSTITUTION.md) — Brain-specific terminology and invariants (relevant to P-01, P-09, P-14 in particular).
- [`SECURITY_MODEL.md`](SECURITY_MODEL.md) — full threat model and remediation plan referenced by P-04 and P-10.
- [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) — ADRs that should cite these IDs going forward.
- [`../context/AI_SESSION_BOOTSTRAP.md`](../context/AI_SESSION_BOOTSTRAP.md) — ordered session-start reading list that includes this registry.

---

## Amending this registry

These 15 principles are meant to be **near-immutable**. They are the fixed reference points every ADR, architecture doc, and specification in this repo is written against, and their IDs are expected to be cited by external documents indefinitely. Changing a principle's statement, status, or numbering here — even lightly — is itself an architectural decision and must go through the ADR process documented in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md), not be edited silently in this file. An ADR that changes a principle should record the old statement, the new statement, and the reason, and this document should only be updated once that ADR is accepted.

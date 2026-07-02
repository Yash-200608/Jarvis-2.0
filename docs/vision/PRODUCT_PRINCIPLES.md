# JARVIS / Chimera — Product & Engineering Principles

Status: Planning phase — pre-implementation
Last updated: 2026-07-01

## Purpose

This document merges the design principles scattered across two sources that must not drift apart:

1. The "Chimera Ecosystem" principles in the repo's root [`CLAUDE.md`](../../CLAUDE.md) (Brain owns memory, Jarvis stays lightweight, MQTT is the nervous system, Tailscale is the network, local-first, modular and fault-tolerant, hardware-aware).
2. The broader "JARVIS X" AI-Operating-System vision principles (modularity, scalability, maintainability, observability, security, extensibility, offline-first, cloud-optional, provider-agnostic, model-agnostic, stateless-where-appropriate, event-driven, never hardcode providers/APIs).

These two lists overlap heavily once de-duplicated (e.g. "local-first" and "offline-first" are the same principle stated twice; "MQTT is the nervous system" is a concrete instance of "event-driven"). This document collapses them into one canonical, non-redundant list. Every future architecture or specification doc in this set should cite principles from here by name rather than restating them.

This is a **planning document**. As established in [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) and the [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), almost none of these principles are actually enforced in the current codebase — three fragmented, non-communicating Python islands (the legacy stack, jarvis_prod, jarvis_core) plus a dormant root `core/` module exist today. Each principle below states the target, and is honest about where current code already honors it and where it is actively violated.

> **Citable ID registry:** each of the 15 principles below now has a permanent, short ID (`P-01`...`P-15`) in the companion document [`../architecture/ARCHITECTURE_PRINCIPLES.md`](../architecture/ARCHITECTURE_PRINCIPLES.md). ADRs in [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) cite principles by that ID rather than restating them. This document remains the source of truth for the full rationale and current-reality evidence per principle — the ID registry only adds a stable pointer.

See also: [`vision/JARVIS_VISION.md`](JARVIS_VISION.md) for the product vision this serves, [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) for how these principles map to components, and [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md) for the security principle's full threat model.

---

## How to read this document

Each principle has:
- **Rationale** — why it matters for this specific project, one line.
- **Current reality** — a concrete file/path where the codebase today either honors or violates it, per the repo audit synthesis. Where nothing exists yet, this is stated plainly rather than invented.

Labels used below match the canonical maturity labels established in [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md): **legacy stack** (`hugginggpt/server/`, daily-use, ADB-driven), **jarvis_prod** (`hugginggpt/server/jarvis_prod/`, smoke-tested MQTT stack, never run live), **jarvis_core** (`jarvis_core/`, never-run Planner/Critic/Executor Reflexion prototype), **root `core/`** (dormant execution-safety kernel with a live confirmed bug).

---

## 1. Brain owns memory (single source of truth)

**Rationale:** If more than one component can independently persist state, the system has no ground truth, and every client has to guess which copy is authoritative. Agents that touch a database directly instead of asking the Brain create silent forks of reality.

**Current reality — violated by omission, not by conflicting code:** there is no single Brain today; there are three. The legacy stack has no persistent memory beyond conversation-in-flight. jarvis_prod's `jobs.py` keeps a SQLite `JobLog` of execution history, not semantic memory. jarvis_core's `core/memory.py` implements ChromaDB RAG with `nomic-embed-text`, but has never persisted a single embedding — no `brain/chroma/` directory exists on disk, confirmed by the audit. Root `core/`'s own `memory.py` is dead code, unused even within its own module. Net effect: "Brain owns memory" is currently true only in the trivial sense that nothing owns it yet. This is the single principle most responsible for the fragmentation described in the roadmap's Phase 1 ("Decide and Document the Brain").

---

## 2. Jarvis stays lightweight (thin interaction layer, no orchestration logic)

**Rationale:** Voice/text/UI surfaces change often and run on constrained or user-facing devices; if they accumulate orchestration or business logic, every new client (phone, web, AR/VR) has to reimplement it, and the "thin client" AI-OS vision collapses into N independent apps.

**Current reality — mixed:** `agent_phone.py` (jarvis_prod) is a reasonably thin executor of `phone.*` skills dispatched over MQTT — it does not make its own routing decisions. In contrast, the legacy stack's `hugginggpt/server/awesome_chat.py` (Flask + HuggingFace models + Anthropic Claude API) both interacts with the user and does orchestration/model-selection in the same process — exactly the coupling this principle forbids, though it is tolerated for now because it is the only system in daily use. The web frontend (`hugginggpt/web/`) is thin by default only because it does almost nothing (single chat-bubble column, no store, no dashboard) — it hasn't earned "thin" so much as never grown.

---

## 3. MQTT is the event-driven nervous system (all inter-node communication is asynchronous and event-driven)

**Rationale:** A distributed, multi-device system (PC, phones, Arduino, future ESP32/display nodes) cannot use synchronous request/response as its primary integration style — nodes sleep, disconnect, and reconnect on their own schedules, so the transport must be pub/sub and tolerant of absence. This subsumes the generic "event-driven architecture" principle from the JARVIS X vision; MQTT is simply the concrete choice.

**Current reality — honored, but only in one never-deployed island:** jarvis_prod's `protocol.py` implements this correctly and deliberately: `brain_pc.py` uses `clean_start=True` (fail fast, don't trust stale backlogs) while `agent_phone.py` uses `clean_start=False` with a 24-hour `SessionExpiryInterval` so Mosquitto queues QoS-1 commands while the phone sleeps — a genuinely sound asymmetric design, validated by a real integration test (`_smoke.py`, real Brain + fake phone through a real broker). It has, however, never had a live Tailscale run; `mosquitto.conf.example` still points at the placeholder IP `100.64.0.1`. jarvis_core has its own, separate MQTT namespace that has never run at all. The legacy stack does not use MQTT and communicates over ADB instead — it is explicitly outside this principle's scope during the transition period (see `roadmap/MASTER_ROADMAP.md` Phase 0-4).

---

## 4. Tailscale is the network (encrypted mesh substrate, not an application concern)

**Rationale:** Node-to-node security should be handled once, at the network layer, rather than reimplemented per-service — this keeps every MQTT client simple and lets new hardware nodes join by joining the tailnet, not by implementing custom crypto.

**Current reality — currently the *only* transport security layer, which is a gap, not a strength:** there is no TLS on any MQTT path today (`ca_cert` plumbing exists in config but is unset everywhere) — the entire production security model rests solely on Tailscale's encryption, with no defense in depth if a device is compromised while still on the tailnet. `mosquitto.conf.example`'s ACL model is also flat (one `jarvis` user, `readwrite` on all of `jarvis/#`, per-node ACLs commented out as future work) — meaning a single leaked MQTT credential can impersonate any node on the mesh even though the network itself is encrypted. See `architecture/SECURITY_MODEL.md` for the full threat model this gap creates.

---

## 5. Local-first / offline-first, cloud-optional

**Rationale:** A personal AI-OS distributed across a home network should keep functioning when the internet is down or a cloud API is rate-limited; cloud inference should be an enhancement, not a load-bearing dependency for basic operation. (This merges CLAUDE.md's "local-first where practical" with the JARVIS X vision's "offline-first" and "cloud-optional" — they are one principle, not three.)

**Current reality — actively violated by the system in daily use:** the legacy stack (the only thing run daily) depends directly on the Anthropic Claude API and HuggingFace models over the network for its core reasoning — there is no offline fallback path. jarvis_core is the one island actually built local-first (Planner = local Llama3, Critic = local Mistral, both via Ollama, no cloud call in the reasoning loop) but it has never been run end-to-end, so this principle is currently better designed-for than delivered. jarvis_prod's transport layer (MQTT/Tailscale) has no inherent cloud dependency, which is correct, but its `_brain_prompt` soft-depends on the legacy stack's cloud-backed NL parsing via a try/except import.

---

## 6. Modular and fault-tolerant (each node/component degrades independently)

**Rationale:** In a mesh of independently-powered devices (phone sleeps, Arduino loses WiFi, PC reboots), no single node's absence should crash or permanently wedge another node — components must fail in isolation and recover without manual intervention.

**Current reality — good design in the transport layer, no cross-component test proves it holds under merge:** jarvis_prod's Reflector circuit breaker (`jobs.py`) correctly rehydrates its consecutive-failure state from SQLite on restart rather than resetting in memory. However, the same breaker is a blunt 3-strike counter with no time-decay — a single bad half-hour can permanently disable a heartbeat-driven skill (e.g. a battery watchdog) with silence as the only symptom, which is a fault-tolerance principle violated by an over-eager fault-tolerance mechanism. More broadly, "modular" is violated at the macro level: the legacy stack, jarvis_prod, and jarvis_core do not compose into a fault-tolerant whole because they don't communicate at all — there is no cross-island integration test, so nothing today proves the three "modules" can even coexist, let alone degrade gracefully together.

---

## 7. Modularity and separation of concerns (single-responsibility components, clear boundaries)

**Rationale:** Distinct from fault-tolerance above — this is about code-level boundaries (one agent, one job) so components can be reasoned about, replaced, and tested independently, per CLAUDE.md's "all agents are single-responsibility."

**Current reality — honored inside islands, violated across them:** within jarvis_prod, `protocol.py`, `brain_pc.py`, `agent_phone.py`, `jobs.py`, and `scheduler.py` are each cleanly scoped. Within root `core/`, the safety kernel is decomposed into single-purpose modules (`idempotency.py`, `sandbox.py`, `undo.py`, `risk.py`, `safety.py`, `validator.py`, `retry.py`, `state.py`) — a genuinely good separation of concerns. The violation is architectural, not file-level: root `core/` and `jarvis_core/core/` are both importable as the bare module name `core` depending on `sys.path` order (only avoided today because `jarvis_core/tests/conftest.py` explicitly inserts `jarvis_core/` onto `sys.path` first) — a naming collision that breaks the "clear boundaries" half of this principle and must be fixed (rename root `core/`) before any consolidation, per the roadmap's Phase 0.

---

## 8. Hardware-aware (the system knows what devices exist and their capabilities)

**Rationale:** A distributed system spanning a PC, multiple phones, an Arduino, and salvaged tablet components needs an explicit model of what hardware exists and what it can do, rather than hardcoding assumptions about a single device.

**Current reality — inventory exists on paper in CLAUDE.md, essentially nothing exists in code:** there is no Device Registry anywhere in the codebase. jarvis_prod's topic scheme (`jarvis/node/<id>/...`) is generic enough to address the OPPO, Vivo, Arduino UNO R4, or future ESP32 nodes from CLAUDE.md's hardware inventory, but no code instantiates or targets any of them — only `agent_phone.py` (Nothing Phone 3a Lite) exists as a live node implementation. The salvaged tablet components (Innolux P101DCA-AB0 LCD, MEMS mic, stereo speakers) and the Arduino UNO R4 have zero corresponding software nodes in any of the three islands.

---

## 9. Provider-agnostic and model-agnostic (never hardcode a specific LLM/API vendor)

**Rationale:** AI providers and models change price, availability, and capability faster than the system's core logic should — coupling business logic to a specific vendor's SDK/API means every provider swap becomes a rewrite instead of a config change. (This merges the JARVIS X vision's "provider-agnostic," "model-agnostic," and "never hardcode providers/APIs" into one principle — they describe the same discipline at three altitudes.)

**Current reality — violated in the two systems that call an LLM at all:** the legacy stack's `awesome_chat.py` calls the Anthropic Claude API directly and also loads specific HuggingFace models by name, with no provider abstraction layer. jarvis_core hardcodes Llama3 as Planner and Mistral as Critic via direct Ollama calls with no swappable model interface (though at least both are local, satisfying principle 5). Neither island has an adapter/interface boundary that would let a model or provider be swapped without touching orchestration code. jarvis_prod does not call any LLM directly today (its `_brain_prompt` soft-depends on the legacy stack's NL parsing), so it neither honors nor violates this principle yet — it simply hasn't reached the point of needing to.

---

## 10. Security and permission-gating (risk-aware confirmation before consequential actions)

**Rationale:** A system that can lock devices, delete files, run shell commands, and shut down machines on the user's behalf must classify actions by risk and require confirmation for anything destructive or irreversible — "the AI can act" and "the AI can act unsupervised on high-risk operations" are different features, and conflating them is how automation causes real damage.

**Current reality — this is the sharpest, most concrete violation in the entire repo:**

- `hugginggpt/server/bridge_server.py`'s `/execute` endpoint dispatches 33 distinct actions — including `shutdown_device`, `restart_device`, `kill_process`, `delete_file`, and an arbitrary `run_command` (raw shell execution) — behind nothing but a static bearer-token check (`auth != f"Bearer {token}"`). There is no risk classification, no confirmation step, and no dry-run gate once `dry_run=False`; a single authenticated HTTP POST executes `shutdown /s /t 0` or deletes a file immediately. This is a direct violation of the permission-gating principle in the one server that is Internet/LAN-reachable today.
- Root `core/` is the one module actually built to this principle's standard — `risk.py` defaults unknown tools to `"high"` (fail-closed), and `executor.py` is designed to require confirmation before high-risk actions execute. However, the audit found a **confirmed live bug**: `executor.py` calls `safety.apply_safety()` (which rewrites `delete_file` → `move_to_trash`, downgrading it to medium risk) **before** `risk.get_risk_level()` is evaluated — so a high-risk `delete_file` action is silently rewritten to a medium-risk one before the risk check ever sees it, bypassing confirmation entirely. This is reproduced directly and covered by 3 currently-failing tests in the module's own adversarial suite (`test_high_risk_action_requires_confirmation_before_safety_rewrite`, `test_confirmation_bypass_attack_is_rejected`, `test_external_confirmation_does_not_bypass_safeguards`). In other words: the one place in the codebase that tried hardest to implement this principle correctly still fails it today, due to an ordering bug — and it isn't even wired into `bridge_server.py` or any other live path yet. Fixing this ordering bug is Phase 0 / P0 work per `roadmap/MASTER_ROADMAP.md`, before `core/` is trusted as the safety kernel for anything.
- jarvis_core's design is the closest match to this principle's intent at the architecture level: its Planner/Critic split (different Ollama models, so one can't rubber-stamp the other) plus a 3-strike `NEED_ATTENTION.md` human-in-the-loop escalation gate is real risk-aware design — but it has never been run, so this remains unproven, not validated.

Full threat model and remediation plan: [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md).

---

## 11. Observability (every consequential action is logged, traceable, and inspectable after the fact)

**Rationale:** A system that acts autonomously across multiple devices must leave an audit trail — otherwise debugging a bad outcome ("why did the PC shut down?") is guesswork, and there is no way to distinguish "action failed" from "action succeeded but the confirmation was lost."

**Current reality — partial, and one confirmed correctness gap:** jarvis_prod has a SQLite `JobLog` (`jobs.py`) for execution history and a working circuit-breaker state machine. However, `brain_pc.py`'s use of `clean_start=True` means any command queued while the Brain was briefly offline is dropped by design, and a response arriving after a Brain restart is lost even if the phone successfully executed the action — producing confusing "it failed" log entries for actions that actually succeeded, which directly undermines this principle's purpose. There is also a masking-layer inconsistency: the Telegram gateway sends full SMS/WhatsApp bodies and phone numbers unmasked to chat, even though the same data is carefully PII-masked before reaching Langfuse — the right instinct (mask PII) implemented in the wrong layer. jarvis_core's intended observability artifacts (`brain/history.log`, `knowledge_graph.md`, `NEED_ATTENTION.md`) are currently all either missing or template-only, because the module has never run.

---

## 12. Extensibility (new nodes, skills, and capabilities can be added without touching core orchestration)

**Rationale:** The hardware inventory (OPPO, Vivo, Arduino, salvaged tablet parts) and the AI-OS ambition (new client types, new skills) both assume the system can grow by addition, not by modification of a monolith.

**Current reality — the schema supports it, the implementation doesn't yet deliver it:** jarvis_prod's `protocol.py` topic namespacing (`jarvis/<node>/<action>`, matching CLAUDE.md's convention) is generic enough to onboard new node types without redesign — a genuine extensibility win. But the gap between "schema supports it" and "it works" is concrete: `brain_pc.py`'s `_known_skills` registry, `telegram_gateway.py`'s help text, and `schedule.example.json` all already reference `pc.*` commands (`pc.system.lock`, `pc.shell.run`, etc.) that have **no receiving agent implemented anywhere** — only `agent_phone.py`'s `phone.*` skills actually exist. Sending `pc.system.lock` today times out silently. This is the extensibility principle failing in the specific way that matters most: the interface was extended before the implementation was, leaving a documented-but-nonfunctional capability.

---

## 13. Scalability (the design does not assume a fixed, small number of nodes or a single Brain instance)

**Rationale:** The hardware roadmap explicitly plans for more nodes over time (ESP32s, display nodes, camera nodes) — the transport and addressing scheme must not need to be redesigned each time a device is added.

**Current reality — reasonably future-proofed at the protocol level, untested in practice:** MQTT pub/sub with per-node topic namespaces scales naturally to additional nodes without broker-side redesign, and this is the one scalability property jarvis_prod's design gets right. In practice, though, only one live node (`agent_phone.py`) has ever been exercised, and even that only via smoke test — there is no evidence yet that the broker, ACL model, or Reflector logic behave correctly with more than one or two concurrent nodes.

---

## 14. Stateless-where-appropriate (don't accumulate server-side session state that doesn't need to persist)

**Rationale:** Components that hold unnecessary state are harder to restart, harder to scale, and harder to reason about after a crash; state should live in the Brain's memory layer (principle 1), not scattered across transient process memory in every service.

**Current reality — reasonably honored in the transport layer, violated by the legacy stack's process model:** jarvis_prod's `brain_pc.py` and `agent_phone.py` are largely stateless per-message handlers, with durable state (job history, circuit-breaker counters) explicitly pushed to SQLite rather than kept in memory — a correct application of this principle. The legacy stack's Flask process, by contrast, holds conversational and model state in-process with no externalized store, which is acceptable for its current single-daily-user ADB use case but would not scale to the multi-node model this document targets.

---

## 15. Never hardcode providers, paths, or environment assumptions (config over code)

**Rationale:** Distinct from principle 9's LLM-provider scope — this is the general discipline of not baking in machine-specific paths, IPs, or credentials, which is exactly what makes a distributed multi-device system portable across the actual hardware inventory.

**Current reality — repeatedly violated in exactly the ways that block a live deployment:** `mosquitto.conf.example` hardcodes the placeholder Tailscale IP `100.64.0.1` rather than being templated for the real tailnet address at deploy time. The legacy stack, jarvis_prod, and jarvis_core each ship their own `requirements.txt` (8 total across the repo) with no shared lockfile or `pyproject.toml`, so environment assumptions are duplicated and drift silently (e.g. `tiktoken 0.3.3` vs `0.7.0`, `psutil >=5.9.0` vs `==6.0.0`, `paho-mqtt` unpinned vs `==2.1.0` "pinned for QoS-1 fixes"). The web frontend's `chatgpt.ts` calls OpenAI directly from the browser with the API key stored in `localStorage` — a hardcoded-credential-in-client-code pattern that violates both this principle and principle 10.

---

## Summary table

| # | Principle | Source | Status today |
|---|---|---|---|
| 1 | Brain owns memory | CLAUDE.md | Not implemented — 3 disconnected partial memories, none authoritative |
| 2 | Jarvis stays lightweight | CLAUDE.md | Mixed — agent_phone.py thin; legacy awesome_chat.py is not |
| 3 | MQTT is the event-driven nervous system | CLAUDE.md + JARVIS X ("event-driven") | Honored in jarvis_prod design; never run live |
| 4 | Tailscale is the network | CLAUDE.md | Sole security layer today — no TLS, flat MQTT ACL |
| 5 | Local-first / offline-first / cloud-optional | CLAUDE.md + JARVIS X (merged) | Violated by the daily-use legacy stack's cloud dependency |
| 6 | Modular and fault-tolerant | CLAUDE.md | Good within jarvis_prod; unproven across islands |
| 7 | Modularity / separation of concerns | JARVIS X | Good within islands; naming collision across root core/ and jarvis_core/core/ |
| 8 | Hardware-aware | CLAUDE.md | Inventory documented on paper only; no Device Registry in code |
| 9 | Provider-agnostic / model-agnostic | JARVIS X (merged, 3→1) | Violated — Claude/HuggingFace and Llama3/Mistral both hardcoded |
| 10 | Security / permission-gating | JARVIS X ("security") | Actively violated — bridge_server.py has no gate; core/ has a live confirmed bypass bug |
| 11 | Observability | JARVIS X | Partial — JobLog exists; response-loss and PII-masking-layer gaps |
| 12 | Extensibility | JARVIS X | Schema supports it; pc.* skills referenced but never implemented |
| 13 | Scalability | JARVIS X | Protocol scales in theory; only ever tested with one node |
| 14 | Stateless-where-appropriate | JARVIS X | Honored in jarvis_prod; violated by legacy stack's in-process state |
| 15 | Never hardcode providers/paths | JARVIS X | Violated — placeholder IPs, 8 unreconciled requirements.txt files, browser-side API key |

---

## Notes on de-duplication

The following pairs/groups from the two source lists were identified as the same principle and merged above rather than kept separate:

- "Local-first" (CLAUDE.md) + "offline-first" (JARVIS X) + "cloud-optional" (JARVIS X) → Principle 5.
- "MQTT is the nervous system" (CLAUDE.md, a concrete transport choice) + "event-driven" (JARVIS X, the general architectural style) → Principle 3, stated as the general principle with MQTT as its current concrete implementation.
- "Provider-agnostic" + "model-agnostic" + "never hardcode providers/APIs" (all JARVIS X) → Principle 9 (LLM/provider-specific scope), with the general non-LLM version of "never hardcode" kept separate as Principle 15 since it covers paths, IPs, and credentials, not just AI providers.
- "Modular and fault-tolerant" (CLAUDE.md, one bullet) was split into two principles here — Principle 6 (runtime fault isolation between independently-powered nodes) and Principle 7 (code-level single-responsibility boundaries) — because the audit surfaced distinct violations of each (a circuit-breaker tuning problem for the former, a sys.path naming collision for the latter) that would otherwise be conflated.
- "Security" (JARVIS X, general) was scoped specifically to risk-aware permission-gating (Principle 10) because that is the dimension with concrete, reproduced evidence in this repo; other security concerns (TLS, secret rotation, ACLs) are captured under Principle 4 and in `architecture/SECURITY_MODEL.md` rather than duplicated here.

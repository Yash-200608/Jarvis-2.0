# JARVIS — Architecture Index

JARVIS is the **embodiment platform** for a Chimera deployment: device and OS
integration, voice/UI/chat surfaces, and safety-gated execution. It does **not**
own memory, multi-step planning, or authorization policy. Those belong in the
separate [Brain](https://github.com/Yash-200608/Brain) repository.

```
User → Jarvis (this repo) → Brain (cognition) → MQTT → Hardware node → Action
```

This file is a map. Do not treat it as a second ecosystem spec.

## Role

- Sense, act, render, and enforce safety at the point of action.
- Advertise capabilities and state; execute Brain-authorized action shapes.
- Never originate new goals or persist cross-session planning on JARVIS's
  own initiative.
- Independently deployable. No imports from Brain internals.

The binding contract is Brain's
[CHIMERA_ECOSYSTEM_ARCHITECTURE.md](https://github.com/Yash-200608/Brain/blob/main/docs/CHIMERA_ECOSYSTEM_ARCHITECTURE.md)
(ADR-011 / ADR-012). Topic grammar is `chimera/<node>/<verb>` (ADR-013).

## Islands in this repo

These packages share a filesystem. They do not share a runtime.

| Island | Path | Maturity |
|---|---|---|
| **jarvis_node_sdk** | `jarvis_node_sdk/` | **Active.** Signed MQTT spine, live-tested against Brain. |
| Legacy HTTP stack | `hugginggpt/server/` | Daily use (ADB / Flask bridges). |
| jarvis_prod | `hugginggpt/server/jarvis_prod/` | Smoke-tested MQTT; not the Chimera contract path. |
| jarvis_core | `jarvis_core/` | Prototype. Cognition half is retired as authority (ADR-014). |
| Root safety kernel | `core/` | Tested, mostly unwired; known confirmation-order bug. |

New embodiment work should land in `jarvis_node_sdk` (or a thin skill on
top of it), not a fifth planner.

## Canonical documents (this repo)

| Topic | Document |
|---|---|
| Session-start reading order | [docs/context/AI_SESSION_BOOTSTRAP.md](docs/context/AI_SESSION_BOOTSTRAP.md) |
| What actually runs today | [docs/context/CURRENT_STATUS.md](docs/context/CURRENT_STATUS.md) |
| File-level island audit (superseded at ownership level) | [docs/architecture/SYSTEM_ARCHITECTURE.md](docs/architecture/SYSTEM_ARCHITECTURE.md) |
| Security model and known gaps | [docs/architecture/SECURITY_MODEL.md](docs/architecture/SECURITY_MODEL.md) |
| JARVIS-internal ADRs (ADR-001–010) | [docs/context/DECISION_LOG.md](docs/context/DECISION_LOG.md) |
| Operator guide | [README.md](README.md) |

Ecosystem ADRs from ADR-011 onward live in Brain's
[docs/DECISION_LOG.md](https://github.com/Yash-200608/Brain/blob/main/docs/DECISION_LOG.md).

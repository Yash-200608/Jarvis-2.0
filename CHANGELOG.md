# Changelog

All notable changes to JARVIS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Labels below are historical project tags and milestone names, not a
published PyPI semver sequence.

## [Unreleased]

### Added

- GitHub community files: `ARCHITECTURE.md`, `CONTRIBUTING.md`,
  `SECURITY.md`, `LICENSE`, root `.env.example`, issue and pull-request
  templates, and CI for `jarvis_node_sdk/tests`.

## Historical seed

These entries summarize work already in the tree. Dates are approximate.

### Chimera node SDK (2026-07)

- `jarvis_node_sdk/`: signed `chimera/<node>/<verb>` MQTT contract extracted
  from jarvis_core's non-cognitive parts (ADR-011 / ADR-013 / ADR-014).
- Live-tested presence, command dispatch, and signed responses against Brain.

### V2.1 / V2 / V1 (`jarvis_core`)

- Prototype Planner / Critic / Executor loop over Ollama. Cognition half is
  **not** the ecosystem authority (see Brain ADR-014).

### jarvis_prod MQTT draft

- `brain_pc.py` + `agent_phone.py`, aiomqtt QoS-1, Telegram gateway, cron,
  Langfuse SDK v4. Smoke-tested; not the daily driver.

### Legacy HuggingGPT stack

- Daily-use Flask path: `awesome_chat.py`, PC/phone bridges, voice, email,
  WhatsApp. Still the only island in routine production use for ADB
  automation.

Upstream: this tree incorporates [Microsoft JARVIS / HuggingGPT](https://github.com/microsoft/JARVIS).
See [README.md](README.md#acknowledgements).

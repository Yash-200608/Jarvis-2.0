# Contributing to JARVIS

JARVIS is the Chimera embodiment platform: sensors, actuators, voice, UI,
bridges, and the signed MQTT node runtime. Planning, long-term memory, and
identity policy belong in [Brain](https://github.com/Yash-200608/Brain).
Do not add a new Planner/Critic/memory loop in this repository.

## Prerequisites

- Python 3.11+ (legacy stack historically ran on 3.10; new work targets 3.11)
- Windows for the PC bridge / PyAutoGUI path; Termux for the phone node
- Optional: Mosquitto + Tailscale for MQTT; ADB for the legacy phone bridge

## Which island to touch

| If you are changing… | Work in | Notes |
|---|---|---|
| Chimera node contract, signing, skills, presence | `jarvis_node_sdk/` | Preferred path. Covered by CI. |
| Daily ADB / Flask PC or phone actions | `hugginggpt/server/bridge_server.py`, `phone_bridge.py` | Test with `BRIDGE_DRY_RUN=true` first |
| Draft MQTT prod stack | `hugginggpt/server/jarvis_prod/` | Do not treat as the live Chimera grammar |
| Retired cognition prototype | `jarvis_core/` | Non-cognitive leftovers only; no new planner |
| Safety kernel | `core/` | Fix bugs; do not wire it as a second Brain |

Read [ARCHITECTURE.md](ARCHITECTURE.md) and
[docs/context/CURRENT_STATUS.md](docs/context/CURRENT_STATUS.md) before
picking an island.

## Setup

```bash
git clone https://github.com/Yash-200608/Jarvis-2.0.git
cd Jarvis-2.0
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r hugginggpt/server/requirements_jarvis.txt
cp .env.example .env
```

Chimera node runtime only:

```bash
pip install -r requirements-chimera-node.txt
```

Legacy HuggingGPT model tasks: `hugginggpt/server/requirements.txt`.
Draft MQTT PC/phone extras: `hugginggpt/server/jarvis_prod/requirements_pc.txt`
or `requirements_phone.txt`.

Operator runbooks stay in [README.md](README.md).

## Tests

```bash
# CI suite — signed node contract, no broker required
pytest jarvis_node_sdk/tests -q

# Legacy Flask suite (optional deps skip)
cd hugginggpt/server && pytest test_jarvis.py -v

# Draft prod protocol rename checks
pytest hugginggpt/server/jarvis_prod/tests -q
```

Root `tests/` and `core/tests/` cover the dormant safety kernel. Three
confirmation-bypass tests are documented as failing today
([docs/architecture/SECURITY_MODEL.md](docs/architecture/SECURITY_MODEL.md)).
Do not gate new work on making those green unless you are fixing that bug.
CI does **not** run them.

## Adding a device action

**Legacy HTTP stack**

1. Open `hugginggpt/server/bridge_server.py`.
2. Add an `if action == "your_action":` branch in `execute()`.
3. Validate params → execute → return `jsonify(base)`.
4. Run the bridge with `BRIDGE_DRY_RUN=true` before a live try.

**Chimera node (`jarvis_node_sdk`)**

1. Add or extend a skill in the SDK (PC or phone).
2. Advertise it through the capability / state publishers.
3. Keep execution local and safety-gated; Brain decides *whether* to dispatch.
4. `pytest jarvis_node_sdk/tests -q`.

**jarvis_prod (draft only)**

1. Add a handler in `agent_phone.py` or `brain_pc.py`.
2. Update `protocol.py` if the command shape changes.
3. Prefer `pytest hugginggpt/server/jarvis_prod/tests` over a live `_smoke.py`
   unless you have a broker.

## Boundaries

- No new cross-session memory store or multi-step planner here.
- No `import` of Brain packages. Talk over HTTP or signed MQTT
  (`chimera/<node>/<verb>`).
- Do not commit `.env`, tokens, or HMAC keys. Stage explicit paths — never
  `git add .`.
- Device bridges are privileged. Default new destructive actions to dry-run
  or confirmation, not immediate execution.

## Pull requests

Open an issue first for large changes. Use the pull-request template.
Name the island you touched.

## Reporting issues

Use GitHub Issues (bug or feature template). Include OS, Python version,
traceback, and which island (legacy / jarvis_prod / jarvis_core / `core/` /
`jarvis_node_sdk`). Security reports go through [SECURITY.md](SECURITY.md).

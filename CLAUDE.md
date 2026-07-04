# CHIMERA ECOSYSTEM — PROJECT CONTEXT

> Drop this file as `CLAUDE.md` at the root of any repo or workspace.
> Claude Code and Cowork will automatically load it as persistent project context.

---

## What This Project Is

> JARVIS is a local-first AI Operating System that runs on your personal
> computer, orchestrates AI reasoning through Brain, manages devices
> through an execution spine, and provides a unified interface for voice,
> memory, automation, vision, and hardware control.

The **Chimera Ecosystem** is a distributed AI system built around three layers:

| Layer | Component | Role |
|---|---|---|
| Cognition | **Brain** | Memory, agents, orchestration, knowledge graph |
| Interaction | **Jarvis 2.0** | Voice, conversation, command routing |
| Connectivity | **MQTT + Tailscale** | Hardware mesh, secure remote access |

The goal is a unified intelligence distributed across multiple physical devices — not a single chatbot.

---

## Architecture

```
USER
  │
  ▼
JARVIS LAYER        ← lightweight, conversational shell
  │
  ▼
BRAIN LAYER         ← memory, reasoning, agent orchestration
  │
  ├── Memory Manager     (SQLite / PostgreSQL / VectorDB)
  ├── Context Manager
  ├── Agent Manager
  ├── Knowledge Layer
  └── Device Registry
  │
  ▼
MQTT (over Tailscale)
  │
  ├── PC Node (Brain host / MQTT broker)
  ├── Phone Node (Nothing Phone 3a Lite)
  ├── Secondary Phone Nodes (OPPO, Vivo 1901)
  ├── Arduino Node (UNO R4 WiFi)
  └── Future: ESP32 nodes, display nodes, camera nodes
```

Data flow:
`User → Jarvis → Brain → Agent → MQTT → Hardware Node → Action`

---

## Active Sub-Projects

### Brain
Central intelligence platform on the main desktop PC.
- Long-term memory and context management
- Agent orchestration
- Knowledge graph
- All other nodes report to Brain

### Jarvis 2.0
Distributed AI assistant.
- Voice + text interfaces
- Mobile node (Nothing Phone via Termux / Tailscale / SSH)
- MQTT-based messaging
- FastAPI components

### WALKER2CANE
AI-assisted rehabilitation system. (Active research project.)

---

## Hardware Inventory

### Computing Nodes
| Device | Status | Role |
|---|---|---|
| Main Desktop PC | Active | Brain host, dev machine, MQTT broker |
| Nothing Phone (3a Lite) | Active (daily) | Mobile Jarvis node, Termux/SSH |
| OPPO Smartphone | Spare | Future camera / dashboard / voice node |
| Vivo 1901 | Spare | Future sensor / monitor node |
| Arduino UNO R4 WiFi | Active | Microcontroller, sensors, actuators |

### Salvaged Tablet Components (Android ~10.1")
Motherboard: `A6001_MB_PCB_V4` — store intact, potential repair candidate.

| Component | ID / Spec | Status | Priority Use |
|---|---|---|---|
| LCD Panel | Innolux P101DCA-AB0 (10.1") | Likely functional | Jarvis smart display |
| Battery | 3.85V Li-ion, 7000mAh | Unknown, no swell | Portable node power |
| Speakers | 09C26QA / 09C26QB (stereo) | Intact | Jarvis voice output |
| Camera | HQK5A03 | Intact | Vision node |
| RF Antenna | A6001_DRX_ANT_PCB_V1.0 | Intact | RF/ESP32 experiments |
| Microphone | MEMS (likely) | Recoverable | Wake-word / voice input |
| Digitizer | Heavily cracked | Unusable | Keep with LCD for protection |

> ⚠️ Do not harvest BGA chips (CPU, RAM, eMMC, PMIC) without rework equipment.
> Store motherboard intact.
> Store LCD flat. Do not bend flex cables.
> Inspect battery for swelling/leakage before any use.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Primary language | Python |
| API framework | FastAPI |
| Device messaging | MQTT |
| Secure networking | Tailscale |
| AI inference | Local AI (experimental) |
| Storage (planned) | SQLite → PostgreSQL + Vector DB |

---

## Design Principles

- **Brain owns memory.** Agents never access databases directly.
- **Jarvis stays lightweight.** Routing and interaction only — no orchestration logic.
- **MQTT is the nervous system.** All nodes communicate through it.
- **Tailscale is the network.** MQTT runs on top of Tailscale.
- **Local-first where practical.** Minimize cloud dependency for core functions.
- **Modular and fault-tolerant.** Each node operates independently if Brain is unreachable.
- **Hardware-aware.** The system knows what devices exist and their capabilities.

---

## Hardware Acquisition Priorities

Before buying new devices, acquire these tools first:

1. **Digital Multimeter** — battery testing, power rail diagnostics, continuity checks (highest ROI)
2. **Soldering Iron** — connector repair, hardware recovery, component work
3. **USB Logic Analyzer** — UART/I2C/SPI debugging for hardware integration
4. **ESP32 Modules** — low-cost distributed sensor nodes, MQTT-native

---

## Roadmap

| Phase | Goal |
|---|---|
| 1 | Organize salvaged components, build inventory |
| 2 | Acquire multimeter + soldering iron. Diagnose tablet motherboard. |
| 3 | Repurpose OPPO and Vivo as dedicated nodes (camera / dashboard / voice) |
| 4 | Integrate all devices through Brain + MQTT + Tailscale as unified ecosystem |

---

## Coding Conventions

- Keep Brain and Jarvis as **separate services** — do not merge them.
- Use MQTT topics with clear namespaces: `chimera/<node>/<action>`
- All agents are single-responsibility.
- Document every hardware interface (pin maps, I2C addresses, UART baud rates).
- Prefer async Python (asyncio / FastAPI) for all network-facing services.

---

## Context for Claude

When helping with this project:
- Assume MQTT + Tailscale are already running infrastructure.
- Assume Python + FastAPI as the default stack unless specified otherwise.
- Suggest solutions that fit the distributed node model.
- Flag anything that would tightly couple Jarvis and Brain — that coupling should be avoided.
- Hardware suggestions should consider the existing inventory before recommending new purchases.
- The tablet LCD (`Innolux P101DCA-AB0`) and speakers are priority recovery targets for a Jarvis display/voice terminal.
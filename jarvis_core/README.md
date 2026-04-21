# Jarvis-Core

A 24/7, distributed autonomous-agent runtime that spans a Windows/Linux PC
and an Android (Termux) phone, glued together over a Tailscale mesh and a
persistent Mosquitto MQTT broker. All reasoning is local — Llama 3 plans,
Mistral audits, Ollama serves them — and every action is gated by a Critic
plus a hard filesystem sandbox.

This is the next-generation companion to the existing HuggingGPT-based
JARVIS in the project root. The two can coexist; Jarvis-Core does not
replace `awesome_chat.py` or the HTTP bridges yet — it runs alongside them
on its own broker topics.

## Architecture at a glance

```
                      ┌───────────────────────────────────────────┐
                      │                Tailscale (100.x.x.x)       │
                      └───────────────────────────────────────────┘
                                  │                       │
   ┌──────────────────────────────┤                       ├────────────────────┐
   │ Windows / Linux PC           │                       │ Android (Termux)   │
   │  ┌────────┐  ┌─────────┐     │                       │  ┌──────────────┐  │
   │  │Sensors │->│  Brain  │     │     ┌─────────┐       │  │phone_executor│  │
   │  └────────┘  │ Planner │ ◄───┼────►│Mosquitto│ ◄────►│  │  (aiomqtt)   │  │
   │              │  Critic │     │     │  QoS 1  │       │  └──────┬───────┘  │
   │              └────┬────┘     │     └─────────┘       │         │ termux-api│
   │                   ▼          │                       │         ▼          │
   │             Executor + Sandbox                       │   Tasker watchdog  │
   │              (PyAutoGUI, shell)                      │   (every 10 min)   │
   └──────────────────────────────┘                       └────────────────────┘
```

## Repo layout

```
jarvis_core/
├── configs/jarvis_core.yaml    # All knobs live here
├── core/
│   ├── transport.py            # aiomqtt wrapper (auto-reconnect + RPC)
│   ├── brain.py                # Reflexion loop + Circuit Breaker
│   ├── ollama_client.py        # Llama 3 / Mistral over local Ollama
│   ├── memory.py               # ChromaDB long-term RAG
│   ├── sensors.py              # Inbox + system + MQTT sensor pump
│   ├── executor.py             # PC tools (sandboxed) + phone dispatch
│   ├── scheduler.py            # Hourly compress / health / VRAM
│   ├── sandbox.py              # Path resolver — single chokepoint
│   ├── signing.py              # HMAC envelopes (Zero-Trust)
│   ├── logging_setup.py        # Loguru + per-component colours
│   └── config.py               # Typed config (pydantic)
├── agent/
│   ├── phone_executor.py       # Termux-side MQTT agent
│   └── phone.example.yaml
├── android/
│   ├── Tasker_Watchdog.xml     # Import in Tasker
│   ├── start_phone_executor.sh # Restart hook
│   ├── probe_phone_executor.sh # Liveness probe
│   └── README.md               # Termux + Tasker setup guide
├── brain/
│   ├── inbox/                  # Drop *.txt to issue commands
│   ├── history.log             # Append-only event ledger
│   └── knowledge_graph.md      # Compressed long-term memory
├── sandbox/                    # ALL file IO lives here
├── tests/                      # pytest suite
├── run_jarvis_core.py          # PC entrypoint
└── requirements*.txt
```

## PC quick-start

```bash
cd jarvis_core
python -m venv .venv && source .venv/bin/activate   # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env
# Generate the shared HMAC key:
python -c "import secrets; print(secrets.token_hex(32))"
# Paste into .env as JARVIS_HMAC_KEY=…

# Pull the local models (one-off):
ollama pull llama3:8b
ollama pull mistral:7b
ollama pull nomic-embed-text

# Launch:
python run_jarvis_core.py
```

Issue commands by dropping a text file into `brain/inbox/`:

```bash
echo "Take a screenshot and save it as snapshot.png" > brain/inbox/01.txt
```

## Phone quick-start

See `android/README.md` for the full Termux + Tasker walkthrough.

## Safety model — at a glance

| Layer | What it stops |
|------|----------------|
| Critic (Mistral) | Plans with `safety_score < 8` or any flagged violation |
| `core/sandbox.py` | File IO outside `./sandbox/` (defeats `..`, abs paths, symlink traversal) |
| HMAC envelopes | Replays + tampering on the MQTT bus (PC ↔ phone) |
| Circuit Breaker | Three Critic rejections in a row halt the brain and write `NEED_ATTENTION.md` |
| Allowlisted Termux shell | `phone_executor.py` only executes a small set of binaries |

## Recursive maintenance (hourly)

The scheduler in `core/scheduler.py` runs every `maintenance_interval_seconds`:

1. **Context compression** — when `history.log` exceeds the token budget,
   summarise via the Planner and append to `knowledge_graph.md`.
2. **Phone health check** — verify recent retained heartbeat; alert + write
   `NEED_ATTENTION.md` if the phone has been silent past the threshold.
3. **VRAM management** — call Ollama with `keep_alive: 0` for any model
   idle for > `idle_unload_seconds` (default 10 min).

## Tests

```bash
pytest tests/
```

The suite focuses on the safety primitives (sandbox + signing). The full
integration loop is exercised manually with a local Mosquitto + Ollama.

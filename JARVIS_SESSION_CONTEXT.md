# JARVIS Project — Session Handoff Context

Paste this entire file into a new Claude session to continue development exactly where we left off.

---

## Two Coexisting Stacks

This repo now has **two** working JARVIS implementations side-by-side:

1. **Legacy — `hugginggpt/server/`** (HTTP-bridge, LAN-only, Groq/Llama LLM)
   Original Microsoft HuggingGPT fork. `awesome_chat.py` + `phone_bridge.py:8091` + `bridge_server.py:8092`. Still runs for daily use over ADB.

2. **Production — `hugginggpt/server/jarvis_prod/`** (MQTT-backed, internet-capable, Telegram + cron)
   Built 2026-04-19/20. Phone + PC are peers on a Tailscale mesh, talking over QoS-1 MQTT (Mosquitto). Runs without the PC being at home. Full feature inventory below.

Most new work goes in `jarvis_prod/`. The legacy stack is documented below the `jarvis_prod` section and still works.

---

## What This Project Is

A fully functional AI assistant (JARVIS) running on a Windows PC that controls both the PC and an Android phone via natural language commands. Built on Microsoft's HuggingGPT/JARVIS research codebase, powered by **Groq API (llama-3.3-70b-versatile)** — free and fast.

**Owner:** Ian  
**Project path:** `C:\Users\Hp\OneDrive\Desktop\JARVIS\hugginggpt\server\`  
**Virtual env:** `jarvis_env` (activate before running anything)  
**Python:** 3.10 for the legacy stack (`jarvis_env`), 3.12 for `jarvis_prod/` (system interpreter, `py -3.12`).

---

## `jarvis_prod/` — Production Stack (2026-04-20 state)

### Architecture

```
   Android (Termux)                           Windows PC
   ┌──────────────────┐    Tailscale VPN     ┌────────────────────┐
   │ agent_phone.py   │ ─── QoS-1 MQTT ───►  │  Mosquitto 2.1.2   │
   │  18 phone skills │ ◄────────────────────│  :1883 (user jarvis)│
   │  termux-* CLIs   │                      └────────────────────┘
   │  Tasker watchdog │                                ▲
   └──────────────────┘                                │
         ▲                                             │
         │ commands/responses/presence/events/state    │
         │                                             ▼
   ┌──────────────────────────────────────────────────────┐
   │ brain_pc.py (Python 3.12)                            │
   │  • MQTT correlator + REPL                            │
   │  • Scheduler (croniter)          ─┐                  │
   │  • Telegram gateway              │ all in one        │
   │  • JobLog (SQLite, workspace/jobs.db) │ asyncio.TaskGroup │
   │  • Reflector (repeat-fail skip)  │                   │
   │  • Langfuse tracing (optional)  ─┘                   │
   └──────────────────────────────────────────────────────┘
```

### File Inventory

| File | Role |
|---|---|
| `protocol.py` | Topic helpers, Command/Response/Event models, presence payload |
| `brain_pc.py` | PC brain (MQTT correlator + REPL + scheduler host + TG host + Reflector + Langfuse) |
| `agent_phone.py` | Termux agent — 18 phone skills wired to `termux-*` CLIs |
| `telegram_gateway.py` | OpenClaw-style DM gateway (allowlist, `/status /skills /cron /jobs /unblock`) |
| `scheduler.py` | **croniter-powered** cron + one-shot reminders, aligned to minute boundary |
| `jobs.py` | **SQLite-persisted** JobLog + Reflector (3-fail-skip, survives restarts) |
| `start_agent.sh` | Termux launcher w/ wake-lock + PID + log rotation |
| `tasker_watchdog.txt` | Tasker profile spec, OEM killer bypass, Phantom Process ADB, smoke checklist |
| `mosquitto.conf.example` | Template broker config (Tailscale listener, persistence, MQTT v5 tuning) |
| `run_jarvis.ps1` | Windows launcher — starts Mosquitto if needed, exports env, runs brain |
| `requirements_pc.txt` | `aiomqtt`, `psutil`, `pyautogui`, `Pillow`, `PyYAML`, **`croniter`**, **`langfuse`**, pycaw/comtypes (win32) |
| `requirements_phone.txt` | `aiomqtt` only |
| `workspace/IDENTITY.yaml` · `workspace/HEARTBEAT.md` | Persona + idle-behavior contract |
| `workspace/jobs.db` | SQLite ring-store of all outbound commands — indexed by ts + (skill,target) |
| `schedule.example.json` | 4 sample cron jobs |
| `_smoke.py` | End-to-end round-trip test (currently green) |

### Installed on the PC (verified)

- Python 3.12.10, Mosquitto 2.1.2 running with user `jarvis` + ACL `readwrite jarvis/#`, Tailscale 1.96.3 (**not yet logged in**).
- All pip deps installed for Python 3.12: `aiomqtt 2.x`, `paho-mqtt`, `psutil`, `pyautogui`, `Pillow`, `PyYAML`, `pycaw`, `comtypes`, **`croniter 6.2`, `langfuse 4.3`**.

### Manual steps Ian still has to do

1. `tailscale up` (browser OAuth) on PC, then same on Android (Tailscale Play Store/F-Droid app).
2. Swap Mosquitto listener from `0.0.0.0:1883` to the Tailscale IP in `C:\ProgramData\mosquitto\mosquitto.conf`, restart service.
3. Install F-Droid apps on phone: Termux, Termux:API, Termux:Boot, Termux:Tasker, Tasker (Play Store, paid).
4. Phantom Process Killer bypass via ADB (`tasker_watchdog.txt` §4).
5. Copy `jarvis_prod/` to Termux `~/jarvis/`, edit `start_agent.sh` broker IP/pw, `chmod +x`, symlink to `~/.termux/boot/`.
6. Tasker profile for 2-min pgrep watchdog (`tasker_watchdog.txt` §1).
7. (Optional) `@BotFather` → Telegram bot → export `JARVIS_TG_TOKEN` + `JARVIS_TG_ADMIN`.
8. (Optional) Langfuse Cloud account → export `LANGFUSE_SECRET_KEY` + `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_HOST` (+ optional `LANGFUSE_RELEASE`) for command tracing. See the **Langfuse tracing** subsection below for what appears in the UI.
9. Launch: `.\run_jarvis.ps1 -BrokerIp 100.x.x.x -Pass "<the 32-char pw>"`.

### Design decisions (non-obvious)

- **Brain uses `clean_start=True`, phone uses `clean_start=False`.** Phone must catch queued commands after dropouts; brain fails fast on stale responses.
- **MQTT v5 `Properties` not Client kwargs** for `SessionExpiryInterval` — aiomqtt 2.x changed this, caused a bug on first run.
- **Windows Selector loop policy is forced** at import time in `brain_pc.py` + `_smoke.py` — ProactorEventLoop can't `add_reader` on TCP, which paho needs.
- **Reflector threshold = 3.** Lower = annoying, higher = burns battery on dead targets.
- **JobLog is SQLite with in-memory hot-path counter.** Restart-safe; `should_skip()` stays O(1).
- **Langfuse uses SDK v4** (`start_as_current_observation` + `propagate_attributes`). No keys ⇒ every trace call is a `nullcontext(None)` no-op. The `awesome_chat.parse_device_command` LLM call is tagged `as_type="generation"` with `model="llama-3.3-70b-versatile"`, so the UI shows model/token/cost analytics. PII fields (`message`, `text`, `body`, `phone`, `pin`, `password`, …) are redacted both inline at call sites and via SDK-level `mask=` callback. Flush runs on `request_stop()` and on broker reconnect so nothing drops.
- **Cron is croniter — full crontab syntax.** Supports ranges (`1-5`), names (`MON-FRI`, `MAR-OCT`), `L`, `#`, lists, steps.
- **Telegram uses long-polling, not webhooks.** No public ports, no cert juggling.
- **Scheduler ticks align to minute boundaries.** `*/5 * * * *` fires at `:00 :05 :10`, not at random phase.

### Langfuse tracing (what you'll see in the UI)

Activates when `LANGFUSE_SECRET_KEY` is set. Follows the `langfuse/skills` v4 instrumentation playbook (skill installed at `~/.claude/skills/langfuse/`).

- **Trace hierarchy**
  - `brain.prompt` (span, root) — one per Telegram NL message, `session_id=chat_id`, `user_id=ian`, tags `[telegram, brain.prompt]`. Output records which route fired (`builtin.remind` / `builtin.ring` / `builtin.battery` / `awesome_chat` / `fallback`).
  - `awesome_chat.parse_device_command` (**generation**) — nested under `brain.prompt` whenever the NL parser runs. `model=llama-3.3-70b-versatile`. Enables token/cost columns in Langfuse.
  - `jarvis.send_command` (span) — one per outbound MQTT command. `session_id=target` groups all commands to a given node, tags `[source, target]`, metadata `{skill, source, target}`. Input carries masked params; output carries `{ok, result, error}` or the reflector-skip / timeout details.

- **Filter recipes**
  - Sessions view → pick a phone/pc target to see every command to that node.
  - Sessions view → pick a Telegram chat_id to see the full conversation flow.
  - Tags → filter by `source` (repl / telegram / scheduler) to compare traffic sources.
  - Generations view → model/cost rollups for the Groq LLM calls only.

- **Masking** — values for keys `message, text, body, phone, to_phone, recipient, pin, password` are replaced with `***masked-*` markers. Dict shape is preserved so you can still see what was called.

- **No-keys behaviour** — verified via `_smoke.py`: the brain runs clean with zero network overhead when `LANGFUSE_SECRET_KEY` is unset.

### Smoke test invocation (green)

```
cd C:\Users\Hp\OneDrive\Desktop\JARVIS\hugginggpt\server\jarvis_prod
py -3.12 _smoke.py > smoke.log 2>&1
```

Expected (and confirmed as of 2026-04-20):
```
DRIVER: got response: ok=True  result={'ok': True, 'battery': {'level': 77, 'charging': False}}
DRIVER: got response: ok=False error=fake phone has no skill 'phone.nosuch'
DRIVER: presence={'ian-phone': True, 'ian-pc': True}
```

The trailing `ExceptionGroup: aiomqtt.MqttError: stop requested` is the **intentional** clean-shutdown path from `_watch_stop`, not a bug.

---

## Legacy stack — `hugginggpt/server/` (HuggingGPT + HTTP bridges)

## How to Run

Open **3 separate PowerShell windows**:

**Window 1 — Phone Bridge:**
```powershell
cd C:\Users\Hp\OneDrive\Desktop\JARVIS\hugginggpt\server
.\jarvis_env\Scripts\Activate.ps1
$env:BRIDGE_TOKEN = "jarvis-phone-token-123"
$env:BRIDGE_DRY_RUN = "false"
python phone_bridge.py --port 8091
```

**Window 2 — Computer Bridge (optional, for PC control):**
```powershell
cd C:\Users\Hp\OneDrive\Desktop\JARVIS\hugginggpt\server
.\jarvis_env\Scripts\Activate.ps1
$env:BRIDGE_TOKEN = "jarvis-pc-token-123"
$env:BRIDGE_DRY_RUN = "false"
python bridge_server.py --port 8092
```

**Window 3 — JARVIS (main chat):**
```powershell
cd C:\Users\Hp\OneDrive\Desktop\JARVIS\hugginggpt\server
.\jarvis_env\Scripts\Activate.ps1
python awesome_chat.py --config configs/config.default.yaml --mode cli
```

---

## Architecture

```
User text → awesome_chat.py
              │
              ├─ parse_device_command(text)   ← LLM-powered, returns list of commands
              │     Sends text to Groq/Llama with full action catalog prompt
              │     Returns: [{"target": "phone"|"computer", "action": "...", "params": {...}}]
              │
              ├─ DeviceOrchestrator.execute_multi(commands)
              │     Same-device commands → SEQUENTIAL (with delay between them)
              │     Different-device commands → PARALLEL threads
              │
              ├─ EndpointDeviceAdapter → HTTP POST → phone_bridge.py :8091
              └─ EndpointDeviceAdapter → HTTP POST → bridge_server.py :8092
```

---

## Key Files and What Was Changed

### `awesome_chat.py`
The main brain. All device routing goes through here.

**Changes made:**
- `LLM_encoding` fallback for non-OpenAI models (Groq/Llama)
- Removed `startswith("sk-")` API key check
- Removed `logit_bias` from all request dicts (Groq rejects them)
- Fixed `fix_dep()` and `unfold()` to use `.get("args", {})` safely
- **`parse_device_command(text)`** — completely replaced with LLM-powered version:
  - Sends user text to Llama with a structured system prompt listing ALL phone+computer actions
  - Returns `[{"target", "action", "params"}, ...]` — supports multiple commands
  - Pre-filter: checks for device keywords before calling LLM (saves tokens)
  - Strips noise phrases from app names automatically
- **Call site** in `chat_huggingface()`:
  - Removed ALL "I confirm" / high-risk gates — full access, no confirmations needed
  - Uses `execute_multi()` for parallel/sequential execution
  - Debug print truncates large fields (screenshots, contact lists) to `<N chars>`
  - Result summary shows ✅/❌ per command with human-readable detail

### `phone_bridge.py`
Android ADB bridge on port 8091.

**Changes made:**
- `COMMON_APPS` dict maps friendly names → package names (camera fixed to `com.android.camera`)
- **`_clean_number(num)`** — normalizes phone numbers, removes spaces/dashes, keeps `+`
- **`_is_phone_number(s)`** — detects if input is a number or a contact name
- **`_lookup_contact(name)`** — searches ALL contact stores:
  - `content://contacts/phones/` (device contacts)
  - `content://com.android.contacts/data` (Google/Gmail synced contacts)
  - `content://icc/adn` (SIM contacts)
  - No WHERE clause (avoids ADB shell quoting bugs) — fetches all, filters in Python
  - Flexible matching: "Parth", "Parth Bajaj", "Bajaj" all work
- **`_resolve_recipient(recipient)`** — if input is a name, looks up contact number
- **`_tap_send_button()`** — finds and taps send button in any messaging app:
  - Dumps UI hierarchy: `uiautomator dump /sdcard/jarvis_ui.xml` → `cat` → parse XML
  - Finds any clickable node with "send" in content-desc/resource-id/text
  - Fallback: proportional tap using `wm size` (width×height, preserving order)
- **`_send_message_via_app(platform, recipient, message)`** — universal messenger:
  - Resolves contact name → number automatically
  - WhatsApp: `whatsapp://send?phone=...&text=...`
  - Telegram: `tg://resolve?domain=...&text=...`
  - SMS: `sms:` intent
  - Instagram/Snapchat: opens app + UI automation
  - Calls `_tap_send_button()` after loading
- **`open_app`** — fixed: now actually runs ADB command (`monkey -p <pkg> LAUNCHER 1`)
- **`send_whatsapp`** / **`send_sms`** — route through `_send_message_via_app()`
- **`send_message`** — universal action: `{"platform": "whatsapp|telegram|sms|...", "recipient": "name or number", "message": "..."}`
- **`send_telegram`**, **`send_instagram_dm`** — dedicated actions
- **`lookup_contact`** — action to search contacts directly
- **`make_call`** — resolves contact names, uses `_clean_number()` for `tel:` URI
- **`swipe`** — fixed: reads actual screen size via `wm size`, supports `{"direction": "up|down|left|right"}` shortcut, clamps all coordinates to screen bounds
- **`set_volume`** — uses `cmd media_session volume --stream 3 --set {level}` with keyevent fallback

### `device_integration.py`
Orchestration layer between awesome_chat.py and bridges.

**Changes made:**
- `EndpointDeviceAdapter.execute()`: per-action timeout (60s for messaging/screenshots, 15s otherwise)
- **`DeviceOrchestrator.execute_multi()`** — smart execution:
  - Groups commands by target device
  - Same-device: runs **sequentially** in order
  - After `open_app`/`open_url`: waits **4 seconds** (app load time)
  - After other actions: waits **0.8 seconds**
  - Different devices: parallel threads
  - 120s total timeout per group

### `bridge_server.py`
Windows PC bridge on port 8092. 30+ PC actions: screenshot, type_text, press_hotkey, open_url, open_app, mouse control, clipboard, file ops, email (SMTP/IMAP), media control, system info, process management.

### `configs/config.default.yaml`
```yaml
openai:
  api_key: ${GROQ_API_KEY}   # or paste a gsk_… key locally (don't commit it)
  base_url: https://api.groq.com/openai/v1
model: llama-3.3-70b-versatile
integrations:
  phone:
    enabled: true
    base_url: http://localhost:8091
    token: jarvis-phone-token-123
  computer:
    enabled: true
    base_url: http://localhost:8092
    token: jarvis-pc-token-123
```

---

## LLM Phone Actions (full list in parse_device_command prompt)

```
open_app, close_app, take_screenshot, send_sms, send_whatsapp,
send_telegram, send_instagram_dm, send_message (universal),
make_call, end_call, tap, swipe (direction: up/down/left/right),
type_text, press_key, set_volume, get_battery, get_device_info,
list_apps, lock_device, unlock_device, push_file, pull_file,
record_screen, lookup_contact
```

## LLM Computer Actions

```
take_screenshot, type_text, press_hotkey, open_url, open_app,
run_command, move_mouse, left_click, right_click, double_click,
scroll, get_clipboard, set_clipboard, get_system_info, list_processes,
kill_process, control_media, set_volume, mute_audio, lock_device,
shutdown_device, restart_device, minimize_window, maximize_window,
close_window, send_email, read_emails, read_file, write_file,
list_directory, search_files
```

---

## Things Currently Working

- ✅ Open any app by name ("open WhatsApp", "open camera")
- ✅ Get battery level
- ✅ Get device info
- ✅ Take screenshot (phone)
- ✅ Swipe in any direction ("swipe up", "swipe down")
- ✅ Call by name or number ("Call Tanishq", "Call +91...")
- ✅ Send SMS by name or number
- ✅ Send WhatsApp by name or number
- ✅ Multi-step commands ("open WhatsApp and take a screenshot" → sequential, 4s delay between)
- ✅ Multi-device commands ("take screenshot on both phone and computer")
- ✅ Contact lookup across device/Google/SIM stores
- ✅ No confirmation prompts — all commands execute immediately
- ✅ Debug output clean (large fields truncated)

## Things Still Needing Work / Known Issues

- ⚠️ **WhatsApp/SMS send button** — `_tap_send_button()` uses uiautomator dump to find send button. Works most of the time but can miss it on some screens. If it fails, falls back to screen-proportional tap (95% width, 88% height). May need coordinate tuning per device.
- ⚠️ **SMS send** — ENTER key adds newline instead of sending; relies on `_tap_send_button()` working
- ⚠️ **Instagram DM automation** — uses coordinate-based UI automation (not reliable on all screen sizes/Instagram versions)
- ⚠️ **Computer bridge** — functional but not battle-tested. Requires `pip install pyautogui pyperclip psutil pywhatkit pillow` in the venv on Windows.

---

## User's Device Info

- **Phone:** Android device connected via ADB (USB debugging enabled)
- **Screen resolution:** 1544×720 (confirmed from `wm size` output in earlier session)
- **Contacts storage:** Mix of device contacts (6 shown via basic query) + Google/Gmail synced contacts (Parth Bajaj is a Gmail contact)
- **PC:** Windows 11, Python 3.10, path: `C:\Users\Hp\...`

---

## Note on Model

The user asked for "claude-opus-4.7" — this model does not exist yet. Current latest models are:
- `claude-opus-4-6` (most capable)
- `claude-sonnet-4-6` (fast + capable, used in this session)
- `claude-haiku-4-5-20251001` (fastest)

To switch JARVIS itself to use Claude instead of Groq, edit `config.default.yaml`:
```yaml
# Comment out the openai block, uncomment anthropic:
openai:
  api_key: sk-ant-YOUR_KEY_HERE
model: llama-3.3-70b-versatile
```

---

## What to Ask the AI in the New Session

Good opening prompt for the new session:
> "I'm continuing development of my JARVIS AI assistant project. I've pasted the full context above. The main things still needing work are: (1) the WhatsApp/SMS send button tap is unreliable — sometimes it finds it via uiautomator, sometimes falls back to screen calc and misses. My screen is 1544×720. Can you make the send button detection more reliable? (2) [any other issue you're hitting]"

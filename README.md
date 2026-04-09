# JARVIS — Personal AI Assistant

> *"Just A Rather Very Intelligent System"*

JARVIS is a fully local, voice-activated personal AI assistant that can control your **Windows PC**, **Android phone**, **email**, **WhatsApp**, and **browser** — all through natural language commands, spoken or typed. It is built on top of the [HuggingGPT](https://arxiv.org/abs/2303.17580) framework and powered by **Anthropic Claude** as its reasoning brain.

---

## Table of Contents

1. [What JARVIS Can Do](#what-jarvis-can-do)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running JARVIS](#running-jarvis)
7. [Available Actions Reference](#available-actions-reference)
8. [Voice Commands](#voice-commands)
9. [Android Phone Control](#android-phone-control)
10. [Email & WhatsApp Setup](#email--whatsapp-setup)
11. [Switching LLM Backends](#switching-llm-backends)
12. [Project Structure](#project-structure)
13. [Troubleshooting](#troubleshooting)
14. [Contributing](#contributing)

---

## What JARVIS Can Do

| Category | Examples |
|---|---|
| **PC Automation** | Open apps, run commands, take screenshots, type text, click, scroll |
| **System Control** | Volume, mute, lock, shutdown, restart, process management |
| **File Management** | Read/write/delete files, search directories, copy/move |
| **Email** | Send emails, read your inbox (Gmail, Outlook, any IMAP/SMTP) |
| **WhatsApp** | Send WhatsApp messages to any contact by phone number |
| **Android Phone** | Send SMS, make calls, open apps, tap/swipe screen, take phone screenshots |
| **Voice Control** | Wake word "Hey Jarvis" → speak → hear response (no keyboard needed) |
| **AI Tasks** | Text generation, image captioning, translation, summarization via HuggingFace |
| **Web** | Open URLs, search the web |
| **Media** | Play/pause, next/previous track, volume via media keys |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOU (User)                               │
│              Voice ("Hey Jarvis") or Text (chat)               │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   voice_module.py                               │
│  Mic → Wake Word → STT (Google/Whisper) → HTTP → TTS Response  │
└────────────────────────┬────────────────────────────────────────┘
                         │  POST /hugginggpt
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   awesome_chat.py  (port 8004)                  │
│                                                                 │
│  ① Parse Task   →  ② Select Model  →  ③ Execute  →  ④ Respond  │
│                                                                 │
│  LLM Brain: Claude (claude_adapter.py) | OpenAI | Azure        │
└──────────┬────────────────────────────────────────┬────────────┘
           │ HuggingFace Models                     │ Device Actions
           ▼                                        ▼
┌──────────────────────┐             ┌──────────────────────────────┐
│   models_server.py   │             │     device_integration.py    │
│   (port 8005)        │             │                              │
│  Image / Audio / NLP │             │  ┌────────────────────────┐  │
└──────────────────────┘             │  │   bridge_server.py     │  │
                                     │  │   (port 8092 — PC)     │  │
                                     │  │   30+ PC/email actions │  │
                                     │  └────────────────────────┘  │
                                     │  ┌────────────────────────┐  │
                                     │  │   phone_bridge.py      │  │
                                     │  │   (port 8091 — Android)│  │
                                     │  │   ADB commands         │  │
                                     │  └────────────────────────┘  │
                                     └──────────────────────────────┘
```

**Data flow:** Your command enters through voice or text → Claude parses it into a task plan → each task is either routed to a HuggingFace AI model (for image/audio/NLP tasks) or to a bridge server that executes it directly on your devices → the final response is spoken back to you.

---

## Prerequisites

### All platforms
- Python 3.9 or higher
- Git

### PC Bridge (`bridge_server.py`)
- Windows 10/11 (most actions), macOS/Linux (limited support)
- Gmail or Outlook account with App Password enabled (for email features)
- Chrome logged into WhatsApp Web (for WhatsApp features)

### Phone Bridge (`phone_bridge.py`)
- Android device with **USB Debugging enabled**
  - Settings → About Phone → tap Build Number 7× → Developer Options → Enable USB Debugging
- [Android Platform Tools (ADB)](https://developer.android.com/tools/releases/platform-tools) installed and on your system PATH
- USB cable connecting phone to PC

### Voice Module (`voice_module.py`)
- Microphone
- PortAudio (required by PyAudio):
  - Windows: installed automatically with `pip install pyaudio`
  - macOS: `brew install portaudio`
  - Linux: `sudo apt install portaudio19-dev`

### AI Brain
- An **Anthropic API key** — get one at [console.anthropic.com](https://console.anthropic.com)
- (Optional) A **HuggingFace token** for AI model tasks (image, audio, NLP)

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/your-username/JARVIS.git
cd JARVIS
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
# Core JARVIS extensions (Claude, PC control, voice, phone)
pip install -r hugginggpt/server/requirements_jarvis.txt

# Original HuggingGPT dependencies (AI model tasks)
pip install -r hugginggpt/server/requirements.txt
```

> **Note for PyAudio on Windows:** If `pip install pyaudio` fails, try:
> ```bash
> pip install pipwin
> pipwin install pyaudio
> ```

### 4. (Optional) Install Whisper for offline voice recognition

```bash
pip install openai-whisper
```

### 5. (Optional) Install ElevenLabs for natural-sounding voice responses

```bash
pip install elevenlabs
```

---

## Configuration

All settings live in `hugginggpt/server/configs/config.default.yaml`. Open it and fill in your keys:

### Step 1 — Set your Claude API key

```yaml
anthropic:
  api_key: sk-ant-YOUR_KEY_HERE
```

### Step 2 — Set your bridge tokens

These are secret strings you choose — they authenticate the bridge servers. Pick any random string (e.g. `my-secret-token-123`).

```yaml
integrations:
  computer:
    enabled: true
    base_url: http://localhost:8092
    token: my-computer-secret-token

  phone:
    enabled: true                    # set false if you don't have an Android device
    base_url: http://localhost:8091
    token: my-phone-secret-token
```

Use the **same tokens** in the corresponding launch scripts below.

### Step 3 — Set email credentials (optional)

```yaml
email:
  smtp_host: smtp.gmail.com
  smtp_port: 587
  imap_host: imap.gmail.com
  imap_port: 993
  username: you@gmail.com
  password: your-gmail-app-password   # NOT your login password — see below
```

> **Gmail App Password:** Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create an App Password for "Mail", and paste the 16-character code here.

### Step 4 — (Optional) Set HuggingFace token

```yaml
huggingface:
  token: hf_YOUR_TOKEN_HERE
```

---

## Running JARVIS

JARVIS requires **up to 3 terminal windows** running simultaneously. Start them in this order:

### Terminal 1 — PC Bridge

```powershell
# Open PowerShell in hugginggpt/server/
$env:BRIDGE_TOKEN   = "my-computer-secret-token"   # must match config.yaml
$env:BRIDGE_DRY_RUN = "false"                       # "true" to test without real OS changes
python bridge_server.py --device computer --port 8092
```

Or use the included script:
```powershell
.\run_computer_bridge.ps1
```

You should see:
```
[bridge] 🟢  LIVE mode — OS commands WILL be executed.
[bridge] Listening on http://0.0.0.0:8092  (device=computer)
```

### Terminal 2 — Phone Bridge (skip if no Android device)

First, verify your phone is connected:
```bash
adb devices
# Should show:  XXXXXXXX    device
```

Then:
```powershell
$env:BRIDGE_TOKEN   = "my-phone-secret-token"
$env:BRIDGE_DRY_RUN = "false"
python phone_bridge.py --port 8091
```

Or:
```powershell
.\run_phone_bridge.ps1
```

### Terminal 3 — Main JARVIS Server

```bash
cd hugginggpt/server
python awesome_chat.py --config configs/config.default.yaml --mode cli
```

Wait for the server to start on port `8004`.

### Terminal 4 — Voice Module (speak to JARVIS)

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-YOUR_KEY"
python voice_module.py --server http://localhost:8004 --api-type claude
```

Or:
```powershell
.\run_voice.ps1
```

You'll hear: *"JARVIS online. Say 'Hey Jarvis' followed by your command."*

---

## Available Actions Reference

### PC Control

| Action | Key Parameters | Example |
|---|---|---|
| `open_app` | `app` (name) | Open Chrome |
| `run_command` | `command`, `shell` (auto/powershell) | Run `ipconfig` |
| `take_screenshot` | `region` (optional bbox) | Take a screenshot |
| `type_text` | `text`, `interval` | Type "Hello World" |
| `press_hotkey` | `keys` (list or string) | Press `["ctrl","c"]` |
| `move_mouse` | `x`, `y`, `duration` | Move mouse to 500,300 |
| `left_click` | `x`, `y` (optional) | Click at current position |
| `right_click` | `x`, `y` (optional) | Right-click |
| `double_click` | `x`, `y` (optional) | Double-click |
| `scroll` | `clicks` (+up/-down), `x`, `y` | Scroll down 3 clicks |
| `open_url` | `url` | Open `https://google.com` |
| `get_clipboard` | — | Read clipboard contents |
| `set_clipboard` | `text` | Copy text to clipboard |
| `minimize_window` | — | Minimize current window |
| `maximize_window` | — | Maximize current window |
| `close_window` | — | Close current window (Alt+F4) |

### System Control

| Action | Key Parameters | Example |
|---|---|---|
| `lock_device` | — | Lock the PC |
| `shutdown_device` | — | Shut down immediately |
| `restart_device` | — | Restart the PC |
| `set_volume` | `level` (0–100) | Set volume to 50 |
| `mute_audio` | `muted` (true/false) | Mute/unmute |
| `get_system_info` | — | CPU, RAM, disk, battery |
| `list_processes` | `name` (optional filter) | List running processes |
| `kill_process` | `name` or `pid` | Kill a process by name |

### File Management

| Action | Key Parameters | Example |
|---|---|---|
| `read_file` | `path` | Read contents of a file |
| `write_file` | `path`, `content`, `mode` (w/a) | Create or overwrite a file |
| `delete_file` | `path` | Delete a file or folder |
| `list_directory` | `path` | List folder contents |
| `search_files` | `root`, `pattern`, `content_query` | Find files by name or content |
| `copy_file` | `src`, `dst` | Copy a file |
| `move_file` | `src`, `dst` | Move a file |
| `create_directory` | `path` | Create a new folder |

### Email

| Action | Key Parameters | Example |
|---|---|---|
| `send_email` | `to`, `subject`, `body`, `html` | Send an email |
| `read_emails` | `folder`, `max_count`, `unread_only` | Read unread inbox |

### WhatsApp

| Action | Key Parameters | Example |
|---|---|---|
| `send_whatsapp` | `phone` (E.164), `message` | Send WhatsApp to `+1234567890` |

> Requires Chrome to be installed and already logged into [web.whatsapp.com](https://web.whatsapp.com).

### Media Control

| Action | `command` values | Example |
|---|---|---|
| `control_media` | `play`, `pause`, `next`, `previous`, `stop`, `volume_up`, `volume_down`, `mute` | Pause music |

---

## Voice Commands

After saying **"Hey Jarvis"** (or just **"Jarvis"**), speak your command naturally. JARVIS will figure out what to do.

### Example voice commands

```
"Hey Jarvis, open Chrome"
"Jarvis, take a screenshot"
"Hey Jarvis, send an email to mom saying I'll be home at 8"
"Jarvis, what's my CPU usage?"
"Hey Jarvis, send a WhatsApp to +1 555 123 4567 saying I'm on my way"
"Jarvis, turn the volume down to 30 percent"
"Hey Jarvis, find all Python files on my desktop"
"Jarvis, lock my computer"
"Hey Jarvis, stop listening"   ← puts voice module to sleep
```

### Voice settings

| Flag | Default | Options |
|---|---|---|
| `--stt` | `google` | `google`, `whisper` |
| `--tts` | `auto` | `auto`, `pyttsx3`, `elevenlabs`, `print` |
| `--whisper-model` | `base` | `tiny`, `base`, `small`, `medium`, `large` |

**Google STT** is faster but requires internet. **Whisper** runs fully offline (downloads ~150MB model on first use).

**pyttsx3** is the default TTS — it uses your system's built-in voices. **ElevenLabs** gives much more natural speech (requires an API key set as `ELEVENLABS_API_KEY`).

---

## Android Phone Control

Make sure ADB is working first:

```bash
adb devices
# List of devices attached
# R9JT701234A    device     ← good, device connected
```

### Example phone commands (via JARVIS chat or voice)

```
"Send a WhatsApp to +1 555 999 0000 saying I'll be late"
"Call +44 20 7946 0958"
"Open Spotify on my phone"
"Take a screenshot of my phone"
"Send an SMS to 07700900000 saying running 10 minutes late"
"Go to the home screen on my phone"
"Turn the phone volume to 8"
```

### Supported phone actions

| Action | Description |
|---|---|
| `send_sms` | Send SMS via Android Messages intent |
| `send_whatsapp` | Open WhatsApp and pre-fill a message |
| `make_call` | Dial a phone number |
| `open_app` | Launch by package name or common name (e.g. "spotify") |
| `close_app` | Force-stop an app |
| `take_screenshot` | Capture phone screen, returns base64 PNG |
| `tap` | Tap at pixel coordinates (x, y) |
| `swipe` | Swipe between two points |
| `type_text` | Type on the focused field |
| `press_key` | Press hardware keys: home, back, volume_up, enter, etc. |
| `get_battery` | Battery level and charging status |
| `get_device_info` | Model, Android version, screen size |
| `set_volume` | Set media volume (0–15) |
| `list_apps` | List all installed packages |
| `push_file` | Copy a file from PC to phone |
| `pull_file` | Copy a file from phone to PC |

---

## Email & WhatsApp Setup

### Gmail (Recommended)

1. Enable 2-Step Verification on your Google Account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create an App Password for **Mail / Windows Computer**
4. Copy the 16-character password into `config.default.yaml → email.password`

### Outlook / Hotmail

```yaml
email:
  smtp_host: smtp-mail.outlook.com
  smtp_port: 587
  imap_host: outlook.office365.com
  imap_port: 993
  username: you@outlook.com
  password: your-outlook-password
```

### WhatsApp

JARVIS uses [pywhatkit](https://github.com/Ankit404butfound/PyWhatKit) to send WhatsApp messages via WhatsApp Web.

1. Open Chrome and go to [web.whatsapp.com](https://web.whatsapp.com)
2. Scan the QR code with your phone once
3. Leave Chrome open (it remembers the session)
4. JARVIS will now be able to send messages

Phone numbers must be in international format, e.g. `+14155552671`.

---

## Switching LLM Backends

### Claude (default, recommended)

```yaml
anthropic:
  api_key: sk-ant-YOUR_KEY
model: claude-sonnet-4-6    # or claude-opus-4-6, claude-haiku-4-5-20251001
```

### OpenAI

Comment out `anthropic:` and uncomment `openai:`:

```yaml
# anthropic:
#   api_key: ...

openai:
  api_key: sk-YOUR_OPENAI_KEY
model: gpt-4o
use_completion: false
```

### Azure OpenAI

```yaml
azure:
  api_key: YOUR_AZURE_KEY
  base_url: https://YOUR_RESOURCE.openai.azure.com
  deployment_name: YOUR_DEPLOYMENT
  api_version: "2024-02-01"
model: gpt-4
```

---

## Project Structure

```
JARVIS/
├── hugginggpt/
│   └── server/
│       ├── awesome_chat.py            ← Main JARVIS server (HuggingGPT pipeline)
│       ├── bridge_server.py           ← PC/email/WhatsApp bridge (port 8092)
│       ├── phone_bridge.py            ← Android ADB bridge (port 8091)
│       ├── voice_module.py            ← Voice input/output pipeline
│       ├── claude_adapter.py          ← Anthropic Claude API adapter
│       ├── device_integration.py      ← Bridge HTTP client (used by awesome_chat)
│       ├── models_server.py           ← Local HuggingFace model server
│       ├── get_token_ids.py           ← Tokenizer utilities
│       ├── configs/
│       │   ├── config.default.yaml    ← ⭐ Main config file (edit this)
│       │   ├── config.azure.yaml      ← Azure OpenAI config template
│       │   ├── config.gradio.yaml     ← Gradio web UI config
│       │   └── config.lite.yaml       ← Lightweight / HuggingFace-only config
│       ├── demos/                     ← Few-shot examples for Claude/GPT
│       ├── data/
│       │   └── p0_models.jsonl        ← HuggingFace model registry
│       ├── run_computer_bridge.ps1    ← Launch PC bridge (PowerShell)
│       ├── run_phone_bridge.ps1       ← Launch phone bridge (PowerShell)
│       ├── run_voice.ps1              ← Launch voice module (PowerShell)
│       └── requirements_jarvis.txt    ← Python dependencies
│
├── easytool/                          ← EasyTool benchmark module
├── taskbench/                         ← TaskBench evaluation module
└── README.md                          ← You are here
```

---

## Troubleshooting

### "No module named 'pyautogui'"
```bash
pip install pyautogui
```

### "No module named 'speech_recognition'"
```bash
pip install SpeechRecognition pyaudio
```

### PyAudio fails to install on Windows
```bash
pip install pipwin
pipwin install pyaudio
```

### ADB device not detected
1. Enable **USB Debugging** in Developer Options on your phone
2. Plug in the USB cable and accept the "Allow USB Debugging?" prompt on your phone
3. Run `adb kill-server && adb start-server && adb devices`
4. Try a different USB port or cable if still not detected

### WhatsApp message not sending
- Make sure Chrome is open and logged into [web.whatsapp.com](https://web.whatsapp.com)
- The phone number must include the country code: `+1XXXXXXXXXX`
- JARVIS schedules the message 1 minute in the future — wait for it

### Voice not recognizing speech
- Run with `--stt google` first to rule out Whisper issues
- Increase microphone sensitivity in Windows Sound Settings
- Speak clearly within 1 metre of the microphone
- If using `--stt whisper`, the first run downloads ~150MB — wait for it

### Claude API key errors
- Ensure the key starts with `sk-ant-`
- Check it's set in `config.default.yaml` or exported as `ANTHROPIC_API_KEY`
- Make sure `anthropic:` section is **not commented out** in the config file

### Bridge server returns `{"ok": false, "error": "Unauthorized"}`
- The `BRIDGE_TOKEN` in your `.ps1` script must **exactly** match the `token:` in `config.default.yaml`

### PyAutoGUI FailSafeException
PyAutoGUI is configured to abort if the mouse reaches a corner of the screen (a safety feature). Move the mouse away from the corner and retry.

---

## Contributing

Pull requests are welcome. For large changes, please open an issue first.

### Development setup

```bash
git clone https://github.com/your-username/JARVIS.git
cd JARVIS
python -m venv venv && source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r hugginggpt/server/requirements_jarvis.txt
```

### Adding a new device action

1. Open `hugginggpt/server/bridge_server.py`
2. Add a new `if action == "your_action":` block in the `execute()` function
3. Follow the existing pattern: validate params → execute → return `jsonify(base)`
4. Test with `BRIDGE_DRY_RUN=true` first

### Adding a new phone action

Same pattern in `hugginggpt/server/phone_bridge.py`.

### Reporting issues

Please include:
- OS version
- Python version (`python --version`)
- Full error traceback
- Which bridge server was running

---

## Acknowledgements

This project builds on top of:
- [JARVIS / HuggingGPT](https://github.com/microsoft/JARVIS) by Microsoft Research
- [Anthropic Claude](https://www.anthropic.com) as the AI reasoning engine
- [EasyTool](https://arxiv.org/abs/2401.06201) — tool learning benchmark
- [TaskBench](https://arxiv.org/abs/2311.18760) — task automation benchmark

---

*Built with Fun*

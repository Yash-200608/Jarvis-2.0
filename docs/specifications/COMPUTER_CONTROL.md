# Computer Control Specification

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

Related documents: [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) | [TOOL_ROUTING.md](../architecture/TOOL_ROUTING.md) | [SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) | [AGENT_ARCHITECTURE.md](../architecture/AGENT_ARCHITECTURE.md) | [BRAIN_SPEC.md](BRAIN_SPEC.md) | [MASTER_ROADMAP.md](../roadmap/MASTER_ROADMAP.md) | [DECISION_LOG.md](../context/DECISION_LOG.md) | [CURRENT_STATUS.md](../context/CURRENT_STATUS.md)

---

## 1. Purpose and Scope

This document specifies JARVIS's computer-use / device-control surface: how the system takes real actions on the Windows PC, on Android phones, and (in the future) on Linux, browsers, and other endpoints. It covers three things that already exist in the repo in varying states of completeness, plus the one thing that is specified everywhere but implemented nowhere:

1. `bridge_server.py` — Windows PC control (legacy stack)
2. `phone_bridge.py` — Android control via ADB (legacy stack)
3. `agent_phone.py` — Android control via Termux:API (jarvis_prod)
4. The missing **pc.\* skill executor** for jarvis_prod (does not exist)

This is a **planning document**. The vision described here — safe, permission-bounded, OpenClaw-style computer-use across Windows/Linux/Android/browser/desktop-apps/terminal/files/clipboard/mouse/keyboard/OCR/vision — is a target, not current reality. Section 3 makes the gap explicit action by action.

**The single most important fact in this document**: the most feature-complete computer-control code in the repo today (`bridge_server.py`) has **zero confirmation gates**. It executes `shutdown_device`, `delete_file`, and `kill_process` immediately, by design, per its own inline reasoning. That is flagged as the top-priority safety gap for this entire subsystem — see Section 4.

---

## 2. Current State: Three Implementations, Zero Shared Safety Layer

Per the repo-wide labels established in [DECISION_LOG.md](../context/DECISION_LOG.md) and [CURRENT_STATUS.md](../context/CURRENT_STATUS.md), computer-control code is split across the same three fragmented islands as everything else, plus the dormant root `core/` kernel:

| Implementation | Path | Island | Transport | Status |
|---|---|---|---|---|
| PC bridge (Windows) | `hugginggpt/server/bridge_server.py` | Legacy stack | Local Flask HTTP (`/execute`), POSTed to by `awesome_chat.py` | Runs daily, in real use |
| Phone bridge (Android, ADB) | `hugginggpt/server/phone_bridge.py` | Legacy stack | Local Flask HTTP (`/execute`), driven over USB/WiFi ADB | Runs daily, in real use |
| Phone agent (Android, Termux:API) | `hugginggpt/server/jarvis_prod/agent_phone.py` | jarvis_prod | MQTT v5 command/response over Tailscale | Smoke-tested, never had a live Tailscale run |
| PC skill executor (Windows) | **does not exist** | jarvis_prod | Would be MQTT v5 command/response, symmetric to `agent_phone.py` | Referenced everywhere, implemented nowhere |
| Execution-safety kernel | `core/` (root) | Fourth, disconnected module | N/A — a library, not a server | Dormant; imported by nothing outside its own 3 test files; has a confirmed live safety bug (Section 4) |

None of these four pieces of code import or call each other. The legacy stack's two bridges are structurally similar (Flask `/execute` endpoint, Bearer-token auth, `dry_run` flag, action dispatch by string match) but were clearly written independently from jarvis_prod's MQTT skill model, and neither talks to `core/`.

### 2.1 `bridge_server.py` — Windows PC control (legacy stack)

Flask app on port 8092 (`--port`, default from `BRIDGE_PORT` env, default `8092`). Auth is a single shared bearer token (`BRIDGE_TOKEN` env var, required at startup). Two modes: `BRIDGE_DRY_RUN=true` (default) short-circuits every action and echoes back `{"ok": true, "dry_run": true, ...}` without touching the OS; `BRIDGE_DRY_RUN=false` executes for real.

Implemented actions (all string-dispatched inside one `/execute` handler in `create_app()`):

- **PC control**: `open_app`, `run_command` (raw shell/PowerShell execution via `subprocess.run(..., shell=True)`), `take_screenshot` (Pillow `ImageGrab`), `type_text`, `press_hotkey`, `move_mouse`, `left_click`/`right_click`/`double_click`, `scroll`, `open_url`, `get_clipboard`/`set_clipboard`, `minimize_window`/`maximize_window`/`close_window` (via hotkeys, Windows-only)
- **System**: `lock_device`, `shutdown_device`, `restart_device`, `set_volume`/`mute_audio` (raw `ctypes.windll.winmm` calls), `get_system_info`, `list_processes`, `kill_process`
- **File ops**: `read_file`, `write_file`, `delete_file` (`shutil.rmtree` for dirs, `os.remove` for files — no trash, no confirmation), `list_directory`, `search_files` (optional in-file content grep), `copy_file`, `move_file`, `create_directory`
- **Email**: `send_email` (SMTP, credentials from `EMAIL_USERNAME`/`EMAIL_PASSWORD` env or request params), `read_emails` (IMAP)
- **WhatsApp**: `send_whatsapp` (via `pywhatkit`, opens a browser tab and schedules a send)
- **Media**: `control_media` (media keys via `pyautogui`)

Mouse/keyboard automation depends on `pyautogui` (with `pyautogui.FAILSAFE = True` — moving the mouse to a screen corner aborts in-flight automation, the one safety mechanism present in the file). Optional dependencies (`pyautogui`, `pyperclip`, `psutil`, `pywhatkit`, `Pillow`) are soft-imported; missing ones degrade individual actions to an error response rather than crashing the server, and are reported in `/health`.

**No confirmation, no risk tiering, no allowlisting exist anywhere in this file.** Every action that passes the dry-run check and the bearer-token check executes immediately. `run_command` in particular accepts an arbitrary shell string from the request body with no allowlist, no sandboxing, and a flat 30-second timeout — it is the PC-bridge equivalent of jarvis_prod's still-unimplemented `pc.shell.run`.

### 2.2 `phone_bridge.py` — Android control via ADB (legacy stack)

Flask app on port 8091, same auth/dry-run pattern as `bridge_server.py`. Controls a physically or WiFi-ADB-connected Android device (used with the Nothing Phone 3a Lite per [CLAUDE.md](../../CLAUDE.md)'s hardware inventory, though the bridge itself is device-agnostic).

Implemented actions: `send_sms`, `send_whatsapp`, `send_message` (universal, dispatches to `whatsapp`/`telegram`/`instagram`/`sms`/`facebook`/`snapchat` via Android intents or UI automation), `send_telegram`, `send_instagram_dm`, `lookup_contact` (queries three separate Android contact content-provider URIs and merges results), `make_call`, `end_call`, `open_app`, `close_app`, `list_apps`, `take_screenshot`, `tap`, `swipe` (with named-direction shortcuts), `type_text`, `press_key`, `get_battery`, `get_device_info`, `set_volume`, `push_file`, `pull_file`.

Notable engineering detail worth preserving: `_tap_send_button()` dumps the live UI hierarchy (`uiautomator dump`) and scores candidate nodes by resource-id, content-description, clickability, and screen position to find the actual "send" button in WhatsApp/Telegram/SMS/Instagram composers — a real (if fragile) form of on-device visual/structural inference, not a hardcoded tap coordinate. This is the closest thing in the current codebase to the vision's "OCR/vision"-driven automation, and it exists only for Android messaging, not for any Windows workflow.

Like `bridge_server.py`, this file has no confirmation gates. `make_call`, `send_sms`, and `send_whatsapp` all dial or send immediately once dry-run is off, with no per-action risk check.

### 2.3 `agent_phone.py` — Android control via Termux:API (jarvis_prod)

Runs inside Termux on the phone, connected over MQTT v5 (aiomqtt 2.x, QoS 1) to `brain_pc.py` via Mosquitto/Tailscale — see [SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) for the transport details. Ten `phone.*` skills are implemented in a flat `SKILLS: dict[str, SkillFn]` registry:

| Skill | Function |
|---|---|
| `phone.battery` | Battery level/charging/health via `termux-battery-status`, falls back to `dumpsys battery` |
| `phone.tts` | Text-to-speech via `termux-tts-speak` |
| `phone.sms.send` | SMS via `termux-sms-send` |
| `phone.torch` | Flashlight on/off |
| `phone.vibrate` | Vibrate for N ms |
| `phone.notify` | Post a notification |
| `phone.ring` | "Find my phone" — concurrent flash + vibrate + TTS loop for N seconds |
| `phone.location` | GPS fix via `termux-location` |
| `phone.app.open` | Launch app by package/friendly name via `monkey` |
| `phone.whatsapp.send` | Opens WhatsApp composer via deep link — does **not** auto-send, requires the user to tap send |

All skill I/O is non-blocking (`asyncio.create_subprocess_exec`), matching the file's stated architectural commitment to a single event-loop process under ~30 MB RSS (relevant on Android's Phantom Process Killer). Commands arrive as MQTT `Command` messages on `T.cmd(node_id)`, are dispatched by `cmd.skill` string against the `SKILLS` dict, and unknown skills return a clean `Response(ok=False, error="unknown skill: ...")` rather than crashing — this is a real, if minimal, allowlist: only the ten registered skill names are reachable at all.

**Notably absent even from this most disciplined implementation**: no per-skill risk classification, no confirmation flow, no audit log beyond MQTT response messages. `phone.whatsapp.send`'s "opens composer, doesn't auto-send" behavior is the closest thing to a safety boundary anywhere in the phone-control code, and it appears to be an artifact of using a public deep link rather than a deliberate safety design.

### 2.4 The missing pc.\* skill executor (jarvis_prod)

This is the largest structural gap in the computer-control subsystem. `brain_pc.py`'s `_known_skills` registry lists PC skills it expects to be able to dispatch:

```
pc.volume.set, pc.volume.mute, pc.media.control,
pc.type, pc.hotkey, pc.click, pc.screenshot,
pc.app.open, pc.url.open, pc.process.list, pc.process.kill,
pc.system.info, pc.system.lock, pc.system.shutdown,
pc.shell.run
```

`telegram_gateway.py`'s help text documents these to end users (e.g. `` !pc.volume.set level=20 ``), and `schedule.example.json` schedules cron jobs that invoke them. `requirements_pc.txt` pulls in `pyautogui>=0.9.54`, `psutil>=5.9.0`, `Pillow>=10.0.0`, and Windows-only `pycaw`/`comtypes` for audio control — dependencies that are meaningless unless something on the PC side consumes them to implement exactly this skill set.

**No such executor exists.** There is no `pc_executor.py`, no `SKILLS` dict analogous to `agent_phone.py`'s, nothing subscribed to a PC-side command topic. Sending `pc.system.lock` today over jarvis_prod's MQTT transport reaches `brain_pc.py`, matches a name in `_known_skills`, and then has nowhere to go — it will time out silently with no receiving agent, per the synthesis audit. jarvis_prod is, as shipped, non-functional for the PC-control capability it advertises to users via the Telegram gateway.

Building this executor is the concrete deliverable this document specifies (Section 5). It is **not** a matter of writing new automation primitives from scratch — `bridge_server.py` already has working, if unguarded, implementations of nearly every one of these actions (`type_text`→`pc.type`, `press_hotkey`→`pc.hotkey`, `left_click`→`pc.click`, `take_screenshot`→`pc.screenshot`, `open_app`→`pc.app.open`, `open_url`→`pc.url.open`, `list_processes`→`pc.process.list`, `kill_process`→`pc.process.kill`, `get_system_info`→`pc.system.info`, `lock_device`→`pc.system.lock`, `shutdown_device`→`pc.system.shutdown`, `run_command`→`pc.shell.run`, `set_volume`→`pc.volume.set`, `mute_audio`→`pc.volume.mute`, `control_media`→`pc.media.control`). The work is porting these into an MQTT-native, safety-gated skill registry — not reinventing pyautogui calls.

---

## 3. Target Vision vs. Current Reality

Per [CLAUDE.md](../../CLAUDE.md) and the broader AI-OS ambition referenced in [JARVIS_VISION.md](../vision/JARVIS_VISION.md), the target is safe, permission-bounded computer-use across every surface JARVIS can reach — architected in the spirit of OpenClaw-style computer-use agents, but with the confirmation/allowlisting/audit discipline those agents are frequently criticized for lacking. The table below is the honest gap assessment.

| Capability | Vision | Current reality |
|---|---|---|
| Windows PC control | Full desktop automation, safety-gated | `bridge_server.py` implements the actions but with **no gating at all** |
| Linux control | Cross-platform parity | Not implemented; `bridge_server.py`'s Windows-only branches (`lock_device`, `set_volume`, `mute_audio`, window control) would need Linux equivalents |
| Android control | Safety-gated, MQTT-native, offline-tolerant | Two disconnected implementations: `phone_bridge.py` (ADB, legacy, unguarded) and `agent_phone.py` (Termux:API, jarvis_prod, MQTT-native, minimally allowlisted by virtue of a fixed `SKILLS` dict, but no risk tiering) |
| Browser automation | Autonomous browsing, form-filling, navigation | Not implemented in any island. `open_url`/`send_whatsapp` open URLs/tabs but do not drive page content |
| Desktop app control | Structured app-level interaction (not just coordinate clicks) | Not implemented; all current "app control" is either process-launch (`open_app`) or blind coordinate/hotkey injection (`pyautogui`) |
| Terminal / shell access | Sandboxed, allowlisted command execution | `run_command` in `bridge_server.py` runs **arbitrary shell strings with no allowlist**; `pc.shell.run` doesn't exist yet and is correctly flagged in Section 4 as needing its own allowlist before it ships |
| File operations | Safety-tiered (read=low risk, delete=high risk, gated) | Implemented, unguarded. `delete_file` calls `shutil.rmtree`/`os.remove` directly — same severity class as `core/`'s already-known bug, just with no safety layer wrapping it at all rather than a broken one |
| Clipboard | Read/write, low risk | Implemented (`get_clipboard`/`set_clipboard` via `pyperclip`), unguarded but low-severity |
| Mouse / keyboard | Precise, low-risk automation | Implemented via `pyautogui`; `pyautogui.FAILSAFE=True` (corner-abort) is the only safety mechanism present anywhere in this subsystem |
| OCR / vision | Screen understanding to drive automation intelligently | Not implemented. `take_screenshot` returns raw PNG bytes; nothing parses them. The Android `_tap_send_button()` UI-hierarchy scoring in `phone_bridge.py` is the closest analog but is XML-hierarchy-based, not OCR/vision-based, and Android-only |
| Cross-device automation ("do X on my phone from my PC" or vice versa) | Unified skill routing through the Brain | Not implemented — the legacy stack's two bridges and jarvis_prod's phone agent are three separate command surfaces with no shared routing layer; see [TOOL_ROUTING.md](../architecture/TOOL_ROUTING.md) |
| Confirmation / permission boundaries | Every destructive action is risk-tiered and gated per [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) | **Absent everywhere except `core/`, which has its own confirmed bug that defeats the gate for the highest-risk action it protects** (Section 4) |

The gap is not primarily "missing automation primitives" — most primitives exist in `bridge_server.py`/`phone_bridge.py`/`agent_phone.py` already. The gap is entirely in the **safety and routing layer** sitting on top of them, which does not exist for any implementation, and in the missing pc.\* executor that would let jarvis_prod's MQTT transport reach the PC at all.

---

## 4. Safety Gap Analysis — the Priority Finding

### 4.1 `bridge_server.py` has no confirmation gates by design

The module's own docstring lists `shutdown_device`, `delete_file`, and `kill_process` alongside every other action with no distinction in severity. Reading the `/execute` handler confirms this is not an oversight the code is silent about — there is no risk classification data structure anywhere in the file, no confirmation-required response path, no allowlist for `run_command`. Once `BRIDGE_DRY_RUN=false` and the bearer token matches, every action listed in Section 2.1 executes unconditionally:

- `shutdown_device` → `shutdown /s /t 0` — runs immediately, no confirmation
- `delete_file` → `shutil.rmtree`/`os.remove` — runs immediately, no confirmation, no trash/recovery path
- `kill_process` → iterates all processes matching name or PID and kills them — runs immediately, no confirmation, no protection against killing a critical system or user process
- `run_command` → arbitrary shell execution, no allowlist, no sandboxing, 30-second timeout is the only bound

This is the **single largest gap between current implementation and the vision's safety requirement** for the entire computer-control subsystem. It is materially worse than the confirmed bug in `core/` (Section 4.2), because `core/` at least has a safety layer that is merely mis-ordered — `bridge_server.py` has no safety layer to mis-order. Anyone (or anything) able to reach the bridge's `/execute` endpoint with a valid bearer token has unrestricted, immediate, unconfirmed control over shutdown, file deletion, and process termination on the PC that also hosts the Brain.

### 4.2 `core/`'s safety kernel exists, but has a confirmed live bug

Root `core/` (distinct from `jarvis_core/core/` — see the naming-collision note in [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md)) is a 512-line execution-safety kernel — `idempotency.py`, `sandbox.py`, `undo.py`, `risk.py`, `safety.py`, `validator.py`, `executor.py`, `state.py`, `retry.py`, and an 8-tool registry in `tools.py`. It is the only safety-oriented code in the entire repo with a real adversarial test suite (565 lines across `core/tests/test_execution_safety.py`, `tests/test_adversarial_execution.py`, `tests/test_executor_safety.py`), but it is currently **imported by nothing outside its own test files** — not by `bridge_server.py`, not by `phone_bridge.py`, not by `agent_phone.py`, not by `brain_pc.py`.

Its `tools.py` registry defines exactly the risk tiers this document needs:

```python
TOOLS = {
    "open_app":        {"risk": "low"},
    "delete_file":      {"risk": "high"},
    "move_to_trash":    {"risk": "medium"},
    "send_sms":         {"risk": "medium"},
    "send_email":       {"risk": "medium"},
    "send_whatsapp":    {"risk": "medium"},
    "shutdown_device":  {"risk": "high"},
    "read_file":        {"risk": "low"},
}
```

But `core/executor.py`'s `execute_task()` calls `safety.apply_safety(action)` **before** `risk.get_risk_level(action["name"])`. `safety.apply_safety()` unconditionally rewrites `delete_file` to `move_to_trash`:

```python
# core/safety.py
if name == "delete_file":
    action["name"] = "move_to_trash"
```

By the time `execute_task()` checks risk, the action's name is already `move_to_trash` (risk: `medium`), not `delete_file` (risk: `high`) — so the high-risk confirmation path is never triggered. A `delete_file` action executes with zero confirmation today, on unmodified code, reproduced by direct execution. Three tests in the module's own suite already assert the correct behavior and currently fail:

- `test_high_risk_action_requires_confirmation_before_safety_rewrite`
- `test_confirmation_bypass_attack_is_rejected`
- `test_external_confirmation_does_not_bypass_safeguards`

Full technical detail, the fix, and the fix's priority ranking (P0, before `core/` is wired into anything) live in [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) — this document defers to that one as the source of truth for the bug itself and only addresses its consequences for computer-control design.

### 4.3 Why this matters for pc.\* specifically

`pc.shell.run` is flagged as **the single highest-severity command in the whole system**. Unlike `delete_file` (recoverable via `move_to_trash`, once the ordering bug is fixed) or `shutdown_device` (disruptive but not data-destructive), an arbitrary shell command can do anything the two other high-risk actions can do and more — including deleting files outside any sandbox, exfiltrating data, installing persistence, or disabling the safety kernel itself. It must not ship as a passthrough of `bridge_server.py`'s current `run_command` (`subprocess.run(command, shell=True, ...)`, no allowlist). It needs its own allowlist of permitted command patterns, separate from and stricter than the generic `high` risk tier applied to `delete_file`/`shutdown_device`.

---

## 5. Recommended Architecture

### 5.1 Principle: wire the fixed `core/` kernel in front of every bridge action, tiered by destructiveness

Once the ordering bug in Section 4.2 is fixed (per [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md)'s Phase 0 priority) and `core/` is renamed to resolve its `sys.path` collision with `jarvis_core/core/`, the fixed kernel becomes the foundation for:

1. **A safety-gating layer in front of `bridge_server.py`'s existing actions** — every action currently dispatched unconditionally in `/execute` should first pass through `risk.get_risk_level()` (evaluated against the *original*, pre-rewrite action name) and, for `medium`/`high` risk, a confirmation step before `safety.apply_safety()`/execution. This does not require rewriting `bridge_server.py`'s automation code — `pyautogui`, `psutil`, `ctypes.windll.winmm`, `shutil`, `smtplib`/`imaplib` calls are all reusable as-is. It requires inserting the kernel's `executor.execute_task()` flow (fixed) as a chokepoint before those calls run, and undo/idempotency/sandbox wrapping (from `core/undo.py`, `core/sandbox.py`, `core/idempotency.py`) around file and process operations specifically.
2. **The foundation for the missing pc.\* executor** — the new jarvis_prod PC-side agent (symmetric to `agent_phone.py`) should be built with the fixed `core/` kernel as its execution core from day one, not retrofitted later. Concretely: a `pc_executor.py` (naming TBD — confirm with user; see suggested layout in [SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md)) that:
   - Subscribes to a PC command topic symmetric to `agent_phone.py`'s `T.cmd(node_id)` pattern
   - Maps each `pc.*` skill name to the risk tier already defined in `core/tools.py`'s `TOOLS` registry, extended to cover the full `_known_skills` list in `brain_pc.py`
   - Routes every command through `core/executor.py`'s (fixed) `execute_task()` before invoking the underlying `pyautogui`/`psutil`/`ctypes` call — reusing `bridge_server.py`'s already-working implementations of the individual actions, not rewriting them
   - Uses `core/sandbox.py`'s path resolution and validation for any `pc.*` skill touching the filesystem
   - Uses `core/undo.py` to record an inverse action wherever one exists (e.g., `move_to_trash` is itself already an "undo-able" rewrite of `delete_file` — once the ordering bug is fixed, this becomes a real safety benefit instead of a bypass vector)
   - Uses `core/idempotency.py` to deduplicate retried commands arriving over MQTT's at-least-once QoS-1 delivery

3. **A dedicated, stricter allowlist for `pc.shell.run`** — given its flagged severity (Section 4.3), it should not simply inherit the generic `high` risk tier. It needs its own allowlist of permitted command patterns (TBD — confirm with user what commands are actually needed day-to-day; candidates based on existing usage patterns in `schedule.example.json` and `telegram_gateway.py` help text would need to be enumerated explicitly rather than left open-ended) and should default to **rejecting** anything not on that list, rather than defaulting to `high`-risk-but-confirmable per `core/risk.py`'s current fail-closed-to-`high` default for unknown tools.

### 5.2 Suggested risk-tier mapping for the pc.\* command set

This extends `core/tools.py`'s existing four-tool pattern to the fourteen `pc.*` names referenced in `brain_pc.py`/`telegram_gateway.py`/`schedule.example.json`. This is a proposed starting point, not a final specification — confirm with user before implementation:

| pc.\* skill | Backing `bridge_server.py` action | Proposed risk tier | Notes |
|---|---|---|---|
| `pc.system.info` | `get_system_info` | low | Read-only |
| `pc.process.list` | `list_processes` | low | Read-only |
| `pc.screenshot` | `take_screenshot` | low | Read-only, but consider privacy implications of remote screen capture |
| `pc.volume.set` / `pc.volume.mute` | `set_volume` / `mute_audio` | low | Reversible, non-destructive |
| `pc.media.control` | `control_media` | low | Reversible, non-destructive |
| `pc.app.open` / `pc.url.open` | `open_app` / `open_url` | low-medium | Could launch unexpected/malicious content if `app`/`url` params aren't validated |
| `pc.type` / `pc.hotkey` / `pc.click` | `type_text` / `press_hotkey` / `left_click`+`right_click`+`double_click` | medium | Blind input injection — could trigger destructive actions indirectly inside whatever app has focus |
| `pc.process.kill` | `kill_process` | high | Same class of harm as `delete_file` — needs move-to-graceful-stop equivalent, not raw `psutil.Process.kill()`, and must not allow killing the Brain's own process or other safety-critical processes |
| `pc.system.lock` | `lock_device` | low | Reversible (user unlocks), non-destructive |
| `pc.system.shutdown` | `shutdown_device` | high | Already in `core/tools.py`'s `TOOLS` registry as `shutdown_device`, risk `high` |
| `pc.shell.run` | `run_command` | **highest — dedicated allowlist, not the generic `high` tier** | See Section 4.3 |

File operations (`delete_file`, `write_file`, `move_file`, `copy_file`, `create_directory`) are not in `brain_pc.py`'s current `_known_skills` list but exist in `bridge_server.py` and should be added to the pc.\* skill set with risk tiers mirroring `core/tools.py`'s existing `delete_file: high` precedent when they are eventually exposed over MQTT.

### 5.3 Android bridges

`phone_bridge.py` (ADB) and `agent_phone.py` (Termux:API) should receive the same fixed-kernel gating treatment, scoped appropriately:

- `agent_phone.py`'s ten `phone.*` skills should each get an explicit risk tier (currently: none). `phone.sms.send` and `phone.whatsapp.send` (message-sending, medium risk per `core/tools.py`'s existing `send_sms`/`send_whatsapp` precedent) and `phone.ring`/`phone.torch`/`phone.vibrate`/`phone.notify` (low risk, reversible/non-destructive) are natural first candidates.
- `phone_bridge.py`'s `make_call`, `send_sms`, `send_whatsapp`, `send_message`, `send_telegram`, `send_instagram_dm` (all message/call actions with real-world, hard-to-undo consequences) and `delete`-adjacent file operations (`pull_file`/`push_file` are lower risk; there is no `delete_file` equivalent on the phone bridge today) should be tiered similarly once this bridge is brought under the same safety model — whether that happens by porting `phone_bridge.py`'s logic into jarvis_prod's `phone.*` skill set (consolidating on one Android control path) or by wrapping the legacy bridge in place is an open architecture decision; see [DECISION_LOG.md](../context/DECISION_LOG.md).

### 5.4 What this document does not resolve

- **Exact allowlist contents for `pc.shell.run`.** TBD - confirm with user.
- **Whether `phone_bridge.py` (ADB) and `agent_phone.py` (Termux:API) are consolidated into one Android control path, or kept as two purpose-built bridges** (ADB for USB/local, Termux:API for remote/MQTT). TBD - confirm with user; this is a superset of the general "which island is canonical" decision covered in [DECISION_LOG.md](../context/DECISION_LOG.md).
- **Browser automation, OCR/vision-driven automation, and Linux parity** are not designed anywhere in the current codebase and are out of scope for this document beyond the gap identification in Section 3. They belong in a future revision once the Windows/Android safety-gating work in Section 5.1-5.3 is complete.
- **How confirmation is actually delivered to a human** (Telegram prompt via `telegram_gateway.py`? A separate approval channel? A timeout-based auto-deny?) is not specified anywhere in the current codebase and needs its own design pass — see [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) and [AGENT_ARCHITECTURE.md](../architecture/AGENT_ARCHITECTURE.md).

---

## 6. Cross-References

- [SECURITY_MODEL.md](../architecture/SECURITY_MODEL.md) — full detail on the `core/` safety-bug (root cause, fix, priority), the `core/` vs `jarvis_core/core/` naming collision, and the broader security posture (MQTT ACLs, TLS status, secret rotation) that this document's recommendations depend on.
- [TOOL_ROUTING.md](../architecture/TOOL_ROUTING.md) — how skill/action names (phone.\*, pc.\*, and any future browser.\*/linux.\* namespaces) are expected to be routed from the Brain to the correct node; this document assumes but does not itself define that routing layer.
- [SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md) — where the proposed `pc_executor.py` fits in the target `brain/`, `nodes/` folder structure and how it relates to jarvis_prod's MQTT transport.
- [AGENT_ARCHITECTURE.md](../architecture/AGENT_ARCHITECTURE.md) — how computer-control skills are invoked by the Planner/Critic/Executor reasoning loop once jarvis_core's Reflexion architecture is validated and merged.
- [DECISION_LOG.md](../context/DECISION_LOG.md) — the canonical-Brain decision and any recorded decisions about which computer-control implementation(s) are retained vs. archived.
- [CURRENT_STATUS.md](../context/CURRENT_STATUS.md) — up-to-date status of whether the pc.\* executor has been built and whether the `core/` safety fix has landed.

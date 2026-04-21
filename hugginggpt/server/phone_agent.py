"""
JARVIS phone_agent
==================
Runs on the Android phone itself (inside Termux) and executes device
actions locally — no ADB, no USB cable, no same-LAN requirement.

It opens a persistent outbound WebSocket to relay_server.py and handles
every command the PC sends it. Response shapes match phone_bridge.py so
awesome_chat.py's summary builder works unchanged.

INSTALL ON PHONE (one-time)
---------------------------
    pkg update && pkg upgrade
    pkg install python termux-api
    pip install websockets
    # (optional for screenshots from outside Termux — needs media permission)
    termux-setup-storage
    # grab this file + save to ~/phone_agent.py

RUN
---
    python phone_agent.py \
        --relay-url wss://your-relay-host:8093 \
        --relay-token <same-secret-as-relay> \
        --agent-id ian-pixel-001

AUTOSTART (optional)
--------------------
    mkdir -p ~/.termux/boot
    cat > ~/.termux/boot/start-jarvis-agent <<'EOF'
    #!/data/data/com.termux/files/usr/bin/sh
    termux-wake-lock
    cd ~
    python phone_agent.py --relay-url ... --relay-token ... --agent-id ... \
        >> ~/jarvis_agent.log 2>&1
    EOF
    chmod +x ~/.termux/boot/start-jarvis-agent
    # Install the "Termux:Boot" app from F-Droid so this runs on reboot.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.parse
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
)
log = logging.getLogger("agent")


# ──────────────────────────────────────────────────────────────────────────
#  Shell helpers
# ──────────────────────────────────────────────────────────────────────────
def _run(cmd: list[str] | str, timeout: int = 15, shell: bool = False,
         input_bytes: Optional[bytes] = None) -> dict:
    """Run a command. Returns {exit_code, stdout, stderr}."""
    try:
        if shell and isinstance(cmd, list):
            cmd = " ".join(shlex.quote(c) for c in cmd)
        p = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            shell=shell,
            input=input_bytes,
        )
        return {
            "exit_code": p.returncode,
            "stdout": p.stdout.decode("utf-8", errors="replace") if isinstance(p.stdout, (bytes, bytearray)) else str(p.stdout),
            "stderr": p.stderr.decode("utf-8", errors="replace") if isinstance(p.stderr, (bytes, bytearray)) else str(p.stderr),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except FileNotFoundError as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"not found: {e}"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


def _run_raw(cmd: list[str], timeout: int = 15) -> tuple[int, bytes, bytes]:
    """Run + return raw bytes (needed for screenshots)."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return -1, b"", str(e).encode()


def _have(binary: str) -> bool:
    return _run(["which", binary], timeout=3)["exit_code"] == 0


# ──────────────────────────────────────────────────────────────────────────
#  Detect capabilities on startup
# ──────────────────────────────────────────────────────────────────────────
HAS_TERMUX_API = _have("termux-sms-send")
HAS_AM         = _have("am")
HAS_INPUT      = _have("input")
HAS_SCREENCAP  = _have("screencap")
HAS_DUMPSYS    = _have("dumpsys")


# Android keycode map — mirrors phone_bridge so the LLM's "press_key" calls
# work identically from either transport.
KEYCODE_MAP = {
    "home":          "KEYCODE_HOME",
    "back":          "KEYCODE_BACK",
    "menu":          "KEYCODE_MENU",
    "power":         "KEYCODE_POWER",
    "volume_up":     "KEYCODE_VOLUME_UP",
    "volume_down":   "KEYCODE_VOLUME_DOWN",
    "mute":          "KEYCODE_VOLUME_MUTE",
    "enter":         "KEYCODE_ENTER",
    "recent_apps":   "KEYCODE_APP_SWITCH",
    "camera":        "KEYCODE_CAMERA",
    "search":        "KEYCODE_SEARCH",
    "play_pause":    "KEYCODE_MEDIA_PLAY_PAUSE",
    "next":          "KEYCODE_MEDIA_NEXT",
    "previous":      "KEYCODE_MEDIA_PREVIOUS",
}

APP_PACKAGES = {
    "whatsapp":     "com.whatsapp",
    "youtube":      "com.google.android.youtube",
    "chrome":       "com.android.chrome",
    "maps":         "com.google.android.apps.maps",
    "camera":       "com.android.camera",
    "settings":     "com.android.settings",
    "contacts":     "com.google.android.contacts",
    "phone":        "com.google.android.dialer",
    "messages":     "com.google.android.apps.messaging",
    "gmail":        "com.google.android.gm",
    "spotify":      "com.spotify.music",
    "instagram":    "com.instagram.android",
    "facebook":     "com.facebook.katana",
    "twitter":      "com.twitter.android",
    "telegram":     "org.telegram.messenger",
    "netflix":      "com.netflix.mediaclient",
    "snapchat":     "com.snapchat.android",
    "tiktok":       "com.zhiliaoapp.musically",
    "calculator":   "com.android.calculator2",
    "clock":        "com.google.android.deskclock",
    "calendar":     "com.google.android.calendar",
    "files":        "com.google.android.documentsui",
    "play_store":   "com.android.vending",
}


# ──────────────────────────────────────────────────────────────────────────
#  Action implementations
# ──────────────────────────────────────────────────────────────────────────
def _get_screen_size() -> tuple[int, int]:
    r = _run(["wm", "size"], timeout=5)
    if r["exit_code"] == 0:
        # "Physical size: 1080x2400\nOverride size: ..." — prefer override
        matches = re.findall(r"(\d+)x(\d+)", r["stdout"])
        if matches:
            w, h = matches[-1]
            return int(w), int(h)
    return 1080, 1920


def _lookup_contact_local(name: str) -> list[dict]:
    if not HAS_TERMUX_API:
        return []
    r = _run(["termux-contact-list"], timeout=20)
    if r["exit_code"] != 0:
        return []
    try:
        rows = json.loads(r["stdout"])
    except json.JSONDecodeError:
        return []
    needle = name.lower().strip()
    matches = []
    for row in rows:
        nm = str(row.get("name", "")).lower()
        num = str(row.get("number", "")).strip()
        if needle in nm and num:
            matches.append({"name": row.get("name"), "number": num})
    return matches


def _resolve_recipient(recipient: str) -> Optional[dict]:
    """If recipient looks like a name not a number, look it up via Termux:API."""
    r = str(recipient).strip()
    if not r:
        return None
    # Already a phone number?
    if re.match(r"^\+?\d[\d\s\-]{5,}$", r.replace(" ", "")):
        return {"name": None, "number": r}
    matches = _lookup_contact_local(r)
    return matches[0] if matches else None


def act_send_sms(params: dict) -> dict:
    recipient = str(params.get("phone") or params.get("name") or "").strip()
    message   = str(params.get("message", "")).strip()
    if not recipient or not message:
        return {"ok": False, "error": "missing phone/name or message"}
    resolved = _resolve_recipient(recipient)
    number = resolved["number"] if resolved else recipient
    if HAS_TERMUX_API:
        r = _run(["termux-sms-send", "-n", number, message], timeout=30)
        ok = r["exit_code"] == 0
        return {"ok": ok, "resolved_contact": resolved,
                "sms_result": r,
                **({"error": r["stderr"] or "sms failed"} if not ok else {})}
    # Fallback: fire an SMS intent. Requires user to press send.
    if HAS_AM:
        uri = f"smsto:{urllib.parse.quote(number)}"
        body = urllib.parse.quote(message)
        r = _run(["am", "start", "-a", "android.intent.action.SENDTO",
                  "-d", uri, "--es", "sms_body", message], timeout=20)
        return {"ok": r["exit_code"] == 0, "resolved_contact": resolved,
                "via": "intent", "sms_result": r,
                "note": "SMS composer opened — no termux-api; user must tap send"}
    return {"ok": False, "error": "no SMS backend (termux-api or am)"}


def _send_via_intent(platform: str, recipient: str, message: str) -> dict:
    """Open <platform> with the message pre-filled. Can't auto-send without
    termux:api + specific per-app accessibility tricks, so we fire the right
    intent and return a note."""
    if not HAS_AM:
        return {"ok": False, "error": "am not available"}
    p = platform.lower()
    resolved = _resolve_recipient(recipient)
    number = resolved["number"] if resolved else recipient
    msg = urllib.parse.quote(message)

    if p == "whatsapp":
        clean = re.sub(r"[^\d+]", "", number)
        url = f"https://api.whatsapp.com/send?phone={clean}&text={msg}"
        r = _run(["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                 timeout=20)
        return {"ok": r["exit_code"] == 0, "platform": "whatsapp",
                "resolved_contact": resolved, "via": "intent",
                "whatsapp_result": r}
    if p == "telegram":
        url = f"tg://resolve?domain={urllib.parse.quote(recipient.lstrip('@'))}"
        r = _run(["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                 timeout=20)
        return {"ok": r["exit_code"] == 0, "platform": "telegram",
                "via": "intent", "telegram_result": r}
    if p == "instagram":
        url = f"https://www.instagram.com/_u/{urllib.parse.quote(recipient.lstrip('@'))}/"
        r = _run(["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                 timeout=20)
        return {"ok": r["exit_code"] == 0, "platform": "instagram",
                "via": "intent", "instagram_result": r}
    if p == "sms":
        return act_send_sms({"phone": number, "message": message})
    return {"ok": False, "error": f"unsupported platform: {platform}"}


def act_send_whatsapp(params: dict) -> dict:
    return _send_via_intent("whatsapp",
                            str(params.get("phone") or params.get("name", "")),
                            str(params.get("message", "")))


def act_send_telegram(params: dict) -> dict:
    return _send_via_intent("telegram",
                            str(params.get("username") or params.get("name", "")),
                            str(params.get("message", "")))


def act_send_instagram_dm(params: dict) -> dict:
    return _send_via_intent("instagram",
                            str(params.get("username") or params.get("name", "")),
                            str(params.get("message", "")))


def act_send_message(params: dict) -> dict:
    platform = str(params.get("platform", "whatsapp")).lower()
    recipient = str(params.get("recipient") or params.get("phone")
                    or params.get("username") or params.get("name", ""))
    message = str(params.get("message", ""))
    return _send_via_intent(platform, recipient, message)


def act_make_call(params: dict) -> dict:
    recipient = str(params.get("phone") or params.get("name", "")).strip()
    if not recipient:
        return {"ok": False, "error": "missing phone/name"}
    resolved = _resolve_recipient(recipient)
    number = resolved["number"] if resolved else recipient
    if HAS_TERMUX_API:
        r = _run(["termux-telephony-call", number], timeout=15)
        return {"ok": r["exit_code"] == 0, "resolved_contact": resolved,
                "call_result": r}
    if HAS_AM:
        r = _run(["am", "start", "-a", "android.intent.action.DIAL",
                  "-d", f"tel:{number}"], timeout=15)
        return {"ok": r["exit_code"] == 0, "resolved_contact": resolved,
                "via": "intent", "call_result": r,
                "note": "dialer opened — tap call"}
    return {"ok": False, "error": "no call backend"}


def act_end_call(params: dict) -> dict:
    r = _run(["input", "keyevent", "KEYCODE_ENDCALL"], timeout=5)
    return {"ok": r["exit_code"] == 0, "result": r}


def act_lookup_contact(params: dict) -> dict:
    name = str(params.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "missing name"}
    matches = _lookup_contact_local(name)
    return {"ok": bool(matches),
            "contacts": matches, "count": len(matches),
            **({"error": f"no contact matching '{name}'"} if not matches else {})}


def act_open_app(params: dict) -> dict:
    app = str(params.get("app", "")).lower().strip()
    if not app:
        return {"ok": False, "error": "missing app"}
    pkg = APP_PACKAGES.get(app, app)  # allow raw package names too
    # Use monkey — most reliable "launch by package" incantation.
    r = _run(["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER",
              "1"], timeout=15)
    return {"ok": r["exit_code"] == 0, "package": pkg, "result": r}


def act_close_app(params: dict) -> dict:
    app = str(params.get("app", "")).lower().strip()
    pkg = APP_PACKAGES.get(app, app)
    r = _run(["am", "force-stop", pkg], timeout=10)
    return {"ok": r["exit_code"] == 0, "package": pkg, "result": r}


def act_list_apps(params: dict) -> dict:
    system = bool(params.get("system", False))
    flag = "-f" if system else "-3"
    r = _run(["pm", "list", "packages", flag], timeout=20)
    packages = [line.replace("package:", "").strip()
                for line in r["stdout"].splitlines() if line.startswith("package:")]
    return {"ok": r["exit_code"] == 0, "count": len(packages),
            "packages": packages}


def act_take_screenshot(params: dict) -> dict:
    if not HAS_SCREENCAP:
        return {"ok": False, "error": "screencap not available"}
    # screencap -p writes PNG to stdout
    rc, out, err = _run_raw(["screencap", "-p"], timeout=15)
    if rc != 0 or not out:
        return {"ok": False, "error": err.decode("utf-8", errors="replace")
                or "screencap failed"}
    return {"ok": True, "screenshot_b64": base64.b64encode(out).decode()}


def act_tap(params: dict) -> dict:
    x = params.get("x"); y = params.get("y")
    if x is None or y is None:
        return {"ok": False, "error": "missing x/y"}
    r = _run(["input", "tap", str(int(x)), str(int(y))], timeout=5)
    return {"ok": r["exit_code"] == 0, "result": r}


def act_swipe(params: dict) -> dict:
    sw, sh = _get_screen_size()
    cx = sw // 2
    DIR_MAP = {
        "up":    (cx, int(sh * 0.80), cx, int(sh * 0.20)),
        "down":  (cx, int(sh * 0.20), cx, int(sh * 0.80)),
        "left":  (int(sw * 0.85), sh // 2, int(sw * 0.15), sh // 2),
        "right": (int(sw * 0.15), sh // 2, int(sw * 0.85), sh // 2),
    }
    direction = str(params.get("direction", "")).lower()
    if direction in DIR_MAP:
        x1, y1, x2, y2 = DIR_MAP[direction]
    else:
        x1 = int(params.get("x1", cx))
        y1 = int(params.get("y1", int(sh * 0.80)))
        x2 = int(params.get("x2", cx))
        y2 = int(params.get("y2", int(sh * 0.20)))
    dur = int(params.get("duration_ms", 300))
    r = _run(["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(dur)],
             timeout=8)
    return {"ok": r["exit_code"] == 0, "screen_size": f"{sw}x{sh}",
            "swipe_coords": f"({x1},{y1})→({x2},{y2})", "result": r}


def act_type_text(params: dict) -> dict:
    text = str(params.get("text", ""))
    if not text:
        return {"ok": False, "error": "missing text"}
    escaped = text.replace(" ", "%s")
    r = _run(["input", "text", escaped], timeout=10)
    return {"ok": r["exit_code"] == 0, "result": r}


def act_press_key(params: dict) -> dict:
    key = str(params.get("key", "home")).lower()
    keycode = KEYCODE_MAP.get(key,
                              key.upper() if key.startswith("KEYCODE_")
                              else f"KEYCODE_{key.upper()}")
    r = _run(["input", "keyevent", keycode], timeout=5)
    return {"ok": r["exit_code"] == 0, "keycode": keycode, "result": r}


def act_get_battery(params: dict) -> dict:
    if HAS_TERMUX_API:
        r = _run(["termux-battery-status"], timeout=10)
        if r["exit_code"] == 0:
            try:
                info = json.loads(r["stdout"])
                battery = {
                    "level":    info.get("percentage"),
                    "charging": info.get("status", "").upper() == "CHARGING",
                    "health":   info.get("health"),
                    "temp":     info.get("temperature"),
                    "plugged":  info.get("plugged"),
                }
                return {"ok": True, "battery": battery, "raw": info}
            except json.JSONDecodeError:
                pass
    # Fallback: dumpsys battery
    r = _run(["dumpsys", "battery"], timeout=10)
    info = {}
    for line in r["stdout"].splitlines():
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower()] = v.strip()
    level = info.get("level")
    plugged = info.get("ac powered", "false").lower() == "true" or \
              info.get("usb powered", "false").lower() == "true"
    return {"ok": r["exit_code"] == 0,
            "battery": {"level": int(level) if level and level.isdigit() else None,
                        "charging": plugged},
            "raw": info}


def act_get_device_info(params: dict) -> dict:
    def _prop(name):
        return _run(["getprop", name], timeout=5)["stdout"].strip()
    return {
        "ok": True,
        "device_info": {
            "manufacturer":   _prop("ro.product.manufacturer"),
            "model":          _prop("ro.product.model"),
            "device":         _prop("ro.product.device"),
            "android_ver":    _prop("ro.build.version.release"),
            "sdk":            _prop("ro.build.version.sdk"),
            "serial":         _prop("ro.serialno"),
        },
    }


def act_set_volume(params: dict) -> dict:
    level = int(params.get("level", 5))
    level = max(0, min(15, level))
    if HAS_TERMUX_API:
        r = _run(["termux-volume", "music", str(level)], timeout=8)
        return {"ok": r["exit_code"] == 0, "level": level, "result": r}
    # Fallback: simulate volume key presses (rough)
    r = _run(["media", "volume", "--stream", "3", "--set", str(level)],
             timeout=8)
    return {"ok": r["exit_code"] == 0, "level": level, "result": r}


def act_lock_device(params: dict) -> dict:
    r = _run(["input", "keyevent", "KEYCODE_POWER"], timeout=5)
    return {"ok": r["exit_code"] == 0, "result": r}


def act_unlock_device(params: dict) -> dict:
    # Best-effort: wake + swipe up
    a = _run(["input", "keyevent", "KEYCODE_WAKEUP"], timeout=5)
    sw, sh = _get_screen_size()
    b = _run(["input", "swipe", str(sw // 2), str(int(sh * 0.8)),
              str(sw // 2), str(int(sh * 0.2)), "200"], timeout=5)
    return {"ok": a["exit_code"] == 0 and b["exit_code"] == 0,
            "wake_result": a, "swipe_result": b,
            "note": "if a PIN is set, agent cannot bypass it"}


def act_push_file(params: dict) -> dict:
    # "push" from phone perspective = receive upload. Relay doesn't carry
    # files yet — caller should base64 the content in params["b64"].
    dst = str(params.get("dst", ""))
    b64 = params.get("b64")
    if not dst or not b64:
        return {"ok": False, "error": "push_file requires params.dst + params.b64"}
    try:
        data = base64.b64decode(b64)
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
        return {"ok": True, "dst": dst, "bytes": len(data)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def act_pull_file(params: dict) -> dict:
    src = str(params.get("src", ""))
    if not src or not os.path.exists(src):
        return {"ok": False, "error": f"src not found: {src}"}
    try:
        with open(src, "rb") as f:
            data = f.read()
        return {"ok": True, "src": src, "bytes": len(data),
                "b64": base64.b64encode(data).decode()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def act_record_screen(params: dict) -> dict:
    return {"ok": False, "error": "record_screen over relay not implemented — "
            "use take_screenshot for stills"}


# Dispatch table
ACTIONS = {
    "send_sms":           act_send_sms,
    "send_whatsapp":      act_send_whatsapp,
    "send_telegram":      act_send_telegram,
    "send_instagram_dm":  act_send_instagram_dm,
    "send_message":       act_send_message,
    "make_call":          act_make_call,
    "end_call":           act_end_call,
    "lookup_contact":     act_lookup_contact,
    "open_app":           act_open_app,
    "close_app":          act_close_app,
    "list_apps":          act_list_apps,
    "take_screenshot":    act_take_screenshot,
    "tap":                act_tap,
    "swipe":              act_swipe,
    "type_text":          act_type_text,
    "press_key":          act_press_key,
    "get_battery":        act_get_battery,
    "get_device_info":    act_get_device_info,
    "set_volume":         act_set_volume,
    "lock_device":        act_lock_device,
    "unlock_device":      act_unlock_device,
    "push_file":          act_push_file,
    "pull_file":          act_pull_file,
    "record_screen":      act_record_screen,
}


def dispatch(action: str, params: dict) -> dict:
    fn = ACTIONS.get(action)
    if fn is None:
        return {"ok": False, "error": f"unknown action: {action}"}
    try:
        out = fn(params or {})
        if not isinstance(out, dict):
            out = {"ok": True, "raw": out}
        out.setdefault("action", action)
        return out
    except Exception as e:
        log.exception("action %s crashed", action)
        return {"ok": False, "action": action, "error": f"{type(e).__name__}: {e}"}


# ──────────────────────────────────────────────────────────────────────────
#  WebSocket main loop
# ──────────────────────────────────────────────────────────────────────────
async def run(relay_url: str, relay_token: str, agent_id: str):
    backoff = 1.0
    while True:
        try:
            log.info("connecting to relay %s (agent_id=%s)", relay_url, agent_id)
            async with websockets.connect(
                relay_url,
                max_size=8 * 1024 * 1024,
                ping_interval=30,
                ping_timeout=30,
            ) as ws:
                await ws.send(json.dumps({
                    "type": "hello",
                    "role": "agent",
                    "agent_id": agent_id,
                    "token": relay_token,
                }))
                hello = await asyncio.wait_for(ws.recv(), timeout=10)
                try:
                    hello_obj = json.loads(hello)
                except json.JSONDecodeError:
                    hello_obj = {}
                if hello_obj.get("type") != "hello_ok":
                    log.error("relay rejected: %s", hello_obj)
                    await asyncio.sleep(min(60, backoff))
                    backoff = min(60, backoff * 2)
                    continue

                log.info("✔ agent connected. waiting for commands.")
                backoff = 1.0

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    if mtype == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        continue
                    if mtype != "cmd":
                        continue

                    req_id = msg.get("req_id")
                    action = msg.get("action", "")
                    params = msg.get("params") or {}
                    log.info("→ %s %s", action, list(params.keys()))

                    # Offload dispatch to a thread — some actions shell out
                    # for 10+ seconds and we don't want to block the ws read.
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, dispatch, action, params)

                    log.info("← %s ok=%s", action, result.get("ok"))
                    await ws.send(json.dumps({
                        "type": "res",
                        "req_id": req_id,
                        "result": result,
                    }))
        except ConnectionClosed as e:
            log.warning("connection closed: %s", e)
        except Exception as e:
            log.warning("loop error: %s", e)

        await asyncio.sleep(min(30, backoff))
        backoff = min(60, backoff * 2)


def main():
    ap = argparse.ArgumentParser(description="JARVIS phone agent (Termux-side)")
    ap.add_argument("--relay-url",
                    default=os.environ.get("RELAY_URL", "ws://localhost:8093"))
    ap.add_argument("--relay-token",
                    default=os.environ.get("RELAY_TOKEN", ""))
    ap.add_argument("--agent-id",
                    default=os.environ.get("AGENT_ID", "ian-phone"))
    args = ap.parse_args()

    if not args.relay_token:
        print("error: --relay-token (or RELAY_TOKEN env) required", file=sys.stderr)
        sys.exit(1)
    if not args.agent_id:
        print("error: --agent-id required", file=sys.stderr)
        sys.exit(1)

    log.info("capabilities: termux-api=%s am=%s input=%s screencap=%s dumpsys=%s",
             HAS_TERMUX_API, HAS_AM, HAS_INPUT, HAS_SCREENCAP, HAS_DUMPSYS)
    if not HAS_AM and not HAS_TERMUX_API:
        log.warning("NEITHER `am` NOR termux-api found — most actions will fail. "
                    "Install termux-api (`pkg install termux-api`) and/or make sure "
                    "Android `am`/`input` commands are in PATH.")

    try:
        asyncio.run(run(args.relay_url, args.relay_token, args.agent_id))
    except KeyboardInterrupt:
        log.info("agent stopped")


if __name__ == "__main__":
    main()

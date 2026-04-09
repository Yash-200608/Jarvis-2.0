"""
JARVIS Phone Bridge Server  (Android via ADB)
=============================================
Listens for action requests from awesome_chat.py and forwards them to a
connected Android device via ADB (Android Debug Bridge).

Prerequisites:
  - ADB installed and on PATH  (https://developer.android.com/tools/adb)
  - Android device connected via USB (or WiFi ADB) with USB Debugging ON
  - BRIDGE_TOKEN env var set to a secret token

Run:
  $env:BRIDGE_TOKEN = "your-secret-token"
  $env:BRIDGE_DRY_RUN = "false"      # set to true for testing
  python phone_bridge.py --port 8091

Supported actions:
  send_sms          – Send an SMS message
  send_whatsapp     – Send WhatsApp message (opens app via intent)
  make_call         – Dial a phone number
  open_app          – Launch an installed app by package name or common name
  take_screenshot   – Capture phone screen → base64 PNG
  tap               – Tap at screen coordinates
  swipe             – Swipe from one point to another
  type_text         – Type text on the focused input field
  press_key         – Press a hardware/virtual key (home, back, menu, ...)
  set_volume        – Set media volume (0-15 on Android)
  list_apps         – List installed packages
  get_battery       – Get battery level + charging status
  get_device_info   – Model, Android version, screen size
  record_screen     – Start/stop a screenrecord (saved on device)
  push_file         – Push a file from PC to device
  pull_file         – Pull a file from device to PC
"""

import argparse
import base64
import os
import subprocess
import tempfile
import time

from flask import Flask, jsonify, request

# ─────────────────────────────────────────────────────────────────────────────

COMMON_APPS = {
    "whatsapp":    "com.whatsapp",
    "youtube":     "com.google.android.youtube",
    "chrome":      "com.android.chrome",
    "maps":        "com.google.android.apps.maps",
    "camera":      "com.android.camera2",
    "settings":    "com.android.settings",
    "contacts":    "com.android.contacts",
    "phone":       "com.android.dialer",
    "messages":    "com.google.android.apps.messaging",
    "gmail":       "com.google.android.gm",
    "spotify":     "com.spotify.music",
    "instagram":   "com.instagram.android",
    "facebook":    "com.facebook.katana",
    "twitter":     "com.twitter.android",
    "telegram":    "org.telegram.messenger",
    "netflix":     "com.netflix.mediaclient",
    "snapchat":    "com.snapchat.android",
    "tiktok":      "com.zhiliaoapp.musically",
    "calculator":  "com.android.calculator2",
    "clock":       "com.android.deskclock",
    "calendar":    "com.google.android.calendar",
    "files":       "com.google.android.documentsui",
    "play_store":  "com.android.vending",
}

KEYCODE_MAP = {
    "home":         "KEYCODE_HOME",
    "back":         "KEYCODE_BACK",
    "menu":         "KEYCODE_MENU",
    "power":        "KEYCODE_POWER",
    "volume_up":    "KEYCODE_VOLUME_UP",
    "volume_down":  "KEYCODE_VOLUME_DOWN",
    "enter":        "KEYCODE_ENTER",
    "delete":       "KEYCODE_DEL",
    "tab":          "KEYCODE_TAB",
    "escape":       "KEYCODE_ESCAPE",
    "search":       "KEYCODE_SEARCH",
    "camera":       "KEYCODE_CAMERA",
    "call":         "KEYCODE_CALL",
    "end_call":     "KEYCODE_ENDCALL",
    "dpad_up":      "KEYCODE_DPAD_UP",
    "dpad_down":    "KEYCODE_DPAD_DOWN",
    "dpad_left":    "KEYCODE_DPAD_LEFT",
    "dpad_right":   "KEYCODE_DPAD_RIGHT",
    "dpad_center":  "KEYCODE_DPAD_CENTER",
    "recent_apps":  "KEYCODE_APP_SWITCH",
    "screenshot":   "KEYCODE_SYSRQ",
    "brightness_up":"KEYCODE_BRIGHTNESS_UP",
    "brightness_down":"KEYCODE_BRIGHTNESS_DOWN",
}


def _bool_env(name, default="true"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _adb(args, timeout=15):
    """Run an adb command and return dict with exit_code, stdout, stderr."""
    cmd = ["adb"] + (args if isinstance(args, list) else args.split())
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "exit_code": r.returncode,
            "stdout":    r.stdout.strip(),
            "stderr":    r.stderr.strip(),
        }
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "",
                "stderr": "adb not found. Install Android Platform Tools and add to PATH."}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"adb command timed out after {timeout}s"}


def _adb_shell(command, timeout=15):
    return _adb(["shell", command], timeout=timeout)


def _device_connected():
    r = _adb(["devices"])
    lines = r["stdout"].splitlines()
    devices = [l for l in lines[1:] if l.strip() and "\tdevice" in l]
    return len(devices) > 0, r


def create_app(token, dry_run):
    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        connected, _ = _device_connected()
        return jsonify({"ok": True, "device": "phone", "dry_run": dry_run,
                        "adb_device_connected": connected})

    @app.route("/execute", methods=["POST"])
    def execute():
        # Auth
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {token}":
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        action  = payload.get("action")
        params  = payload.get("params", {})

        if not action:
            return jsonify({"ok": False, "error": "Missing action"}), 400

        base = {"ok": True, "device": "phone", "action": action,
                "params": params, "dry_run": dry_run}

        if dry_run:
            return jsonify(base)

        # Check device connectivity (skip for list_apps to still attempt)
        connected, adb_out = _device_connected()
        if not connected and action != "get_device_info":
            return jsonify({"ok": False,
                            "error": "No Android device detected via ADB. "
                                     "Check USB connection and that USB Debugging is enabled.",
                            "adb_output": adb_out}), 503

        # ══════════════════════════════════════════════════════════════════════
        #  MESSAGING
        # ══════════════════════════════════════════════════════════════════════

        if action == "send_sms":
            phone   = str(params.get("phone", "")).strip()
            message = str(params.get("message", "")).strip()
            if not phone or not message:
                return jsonify({"ok": False, "error": "Missing params.phone or params.message"}), 400
            # Use Android intent to compose SMS (auto-send with ADB)
            escaped  = message.replace("'", "\\'").replace('"', '\\"')
            adb_cmd  = (f'am start -a android.intent.action.SENDTO '
                        f'-d "sms:{phone}" '
                        f'--es sms_body "{escaped}" '
                        f'--ez exit_on_sent true')
            r = _adb_shell(adb_cmd)
            # Give UI a moment, then send with ENTER
            time.sleep(1)
            _adb_shell("input keyevent KEYCODE_ENTER")
            base["sms_result"] = r
            return jsonify(base)

        if action == "send_whatsapp":
            phone   = str(params.get("phone", "")).strip().lstrip("+")
            message = str(params.get("message", "")).strip()
            if not phone or not message:
                return jsonify({"ok": False, "error": "Missing params.phone or params.message"}), 400
            from urllib.parse import quote
            encoded  = quote(message)
            adb_cmd  = (f'am start -a android.intent.action.VIEW '
                        f'-d "https://api.whatsapp.com/send?phone={phone}&text={encoded}" '
                        f'--package com.whatsapp')
            r = _adb_shell(adb_cmd)
            time.sleep(3)
            # Tap the send button (approximate position — may vary by device)
            _adb_shell("input keyevent KEYCODE_ENTER")
            base["whatsapp_result"] = r
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  CALLS
        # ══════════════════════════════════════════════════════════════════════

        if action == "make_call":
            phone = str(params.get("phone", "")).strip()
            if not phone:
                return jsonify({"ok": False, "error": "Missing params.phone"}), 400
            r = _adb_shell(f'am start -a android.intent.action.CALL -d "tel:{phone}"')
            base["call_result"] = r
            return jsonify(base)

        if action == "end_call":
            r = _adb_shell("input keyevent KEYCODE_ENDCALL")
            base["result"] = r
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  APP CONTROL
        # ══════════════════════════════════════════════════════════════════════

        if action == "open_app":
            app_name = (params.get("app") or params.get("package") or "").strip()
            if not app_name:
                return jsonify({"ok": False, "error": "Missing params.app"}), 400
            # Resolve common name → package
            package = COMMON_APPS.get(app_name.lower(), app_name)
            r = _adb_shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            base["package"]    = package
            base["app_result"] = r
            return jsonify(base)

        if action == "close_app":
            package = (params.get("package") or "").strip()
            common  = (params.get("app") or "").strip().lower()
            if common:
                package = COMMON_APPS.get(common, common)
            if not package:
                return jsonify({"ok": False, "error": "Missing params.package or params.app"}), 400
            r = _adb_shell(f"am force-stop {package}")
            base["result"] = r
            return jsonify(base)

        if action == "list_apps":
            system  = bool(params.get("system", False))
            flag    = "" if system else "-3"   # -3 = third-party only
            r       = _adb_shell(f"pm list packages {flag}")
            packages = [line.replace("package:", "") for line in r["stdout"].splitlines()]
            base["packages"] = sorted(packages)
            base["count"]    = len(packages)
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  SCREEN INTERACTION
        # ══════════════════════════════════════════════════════════════════════

        if action == "take_screenshot":
            tmp = f"/sdcard/jarvis_shot_{int(time.time())}.png"
            _adb_shell(f"screencap -p {tmp}")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                local_path = tf.name
            r = _adb(["pull", tmp, local_path])
            _adb_shell(f"rm {tmp}")
            if r["exit_code"] != 0:
                return jsonify({"ok": False, "error": r["stderr"]}), 500
            with open(local_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            os.unlink(local_path)
            base["screenshot_b64"] = b64
            return jsonify(base)

        if action == "tap":
            x = params.get("x")
            y = params.get("y")
            if x is None or y is None:
                return jsonify({"ok": False, "error": "Missing params.x / params.y"}), 400
            r = _adb_shell(f"input tap {int(x)} {int(y)}")
            base["result"] = r
            return jsonify(base)

        if action == "swipe":
            x1       = params.get("x1", 500)
            y1       = params.get("y1", 1500)
            x2       = params.get("x2", 500)
            y2       = params.get("y2", 500)
            duration = int(params.get("duration_ms", 300))
            r = _adb_shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {duration}")
            base["result"] = r
            return jsonify(base)

        if action == "type_text":
            text = str(params.get("text", ""))
            # Escape spaces for ADB
            escaped = text.replace(" ", "%s").replace("'", "\\'")
            r = _adb_shell(f"input text '{escaped}'")
            base["result"] = r
            return jsonify(base)

        if action == "press_key":
            key = str(params.get("key", "home")).lower()
            keycode = KEYCODE_MAP.get(key, key.upper() if key.startswith("KEYCODE_") else f"KEYCODE_{key.upper()}")
            r = _adb_shell(f"input keyevent {keycode}")
            base["keycode"] = keycode
            base["result"]  = r
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  DEVICE INFO & SETTINGS
        # ══════════════════════════════════════════════════════════════════════

        if action == "get_battery":
            r = _adb_shell("dumpsys battery")
            info = {}
            for line in r["stdout"].splitlines():
                if "level:" in line:
                    info["level"] = int(line.split(":")[-1].strip())
                if "status:" in line:
                    status_code = int(line.split(":")[-1].strip())
                    info["charging"] = status_code == 2
                if "temperature:" in line:
                    info["temp_tenths_C"] = int(line.split(":")[-1].strip())
            base["battery"] = info
            return jsonify(base)

        if action == "get_device_info":
            props = {
                "model":       _adb_shell("getprop ro.product.model")["stdout"],
                "manufacturer":_adb_shell("getprop ro.product.manufacturer")["stdout"],
                "android_ver": _adb_shell("getprop ro.build.version.release")["stdout"],
                "sdk_version": _adb_shell("getprop ro.build.version.sdk")["stdout"],
                "serial":      _adb(["get-serialno"])["stdout"],
            }
            # Screen size
            wm = _adb_shell("wm size")["stdout"]
            props["screen_size"] = wm
            base["device_info"] = props
            base["adb_device_connected"] = connected
            return jsonify(base)

        if action == "set_volume":
            level   = int(params.get("level", 8))   # 0–15 for media stream
            stream  = int(params.get("stream", 3))  # 3 = STREAM_MUSIC
            r = _adb_shell(f"media volume --stream {stream} --set {level}")
            base["result"] = r
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  FILE TRANSFER
        # ══════════════════════════════════════════════════════════════════════

        if action == "push_file":
            src = params.get("src", "")
            dst = params.get("dst", "/sdcard/Download/")
            if not src:
                return jsonify({"ok": False, "error": "Missing params.src"}), 400
            r = _adb(["push", src, dst])
            base["result"] = r
            return jsonify(base)

        if action == "pull_file":
            src = params.get("src", "")
            dst = params.get("dst", ".")
            if not src:
                return jsonify({"ok": False, "error": "Missing params.src"}), 400
            r = _adb(["pull", src, dst])
            base["result"] = r
            return jsonify(base)

        # ── Fallthrough ───────────────────────────────────────────────────────
        return jsonify({"ok": False, "error": f"Unsupported action: '{action}'"}), 400

    return app


def main():
    parser = argparse.ArgumentParser(description="JARVIS phone bridge (Android ADB)")
    parser.add_argument("--host",  default=os.getenv("BRIDGE_HOST", "0.0.0.0"))
    parser.add_argument("--port",  type=int, default=int(os.getenv("BRIDGE_PORT", "8091")))
    args = parser.parse_args()

    token = os.getenv("BRIDGE_TOKEN", "").strip()
    if not token:
        raise ValueError("BRIDGE_TOKEN env var is required.")

    dry_run = _bool_env("BRIDGE_DRY_RUN", "true")
    if dry_run:
        print("[phone-bridge] ⚠️  DRY-RUN mode — ADB commands will NOT execute.")
    else:
        connected, _ = _device_connected()
        status = "✅ device detected" if connected else "⚠️  no device detected"
        print(f"[phone-bridge] 🟢  LIVE mode — ADB {status}")

    app = create_app(token, dry_run)
    print(f"[phone-bridge] Listening on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

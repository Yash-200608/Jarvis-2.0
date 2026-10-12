"""
JARVIS Computer Bridge Server
Exposes a local HTTP endpoint that awesome_chat.py can POST actions to.
Run with: python bridge_server.py --device computer --port 8092
Required env: BRIDGE_TOKEN=<secret>
Optional env: BRIDGE_DRY_RUN=true (default), BRIDGE_DRY_RUN=false to enable live execution

Actions supported:
  PC Control  : open_app, run_command, take_screenshot, type_text, press_hotkey,
                move_mouse, left_click, right_click, double_click, scroll,
                open_url, get_clipboard, set_clipboard, minimize_window,
                maximize_window, close_window
  System      : lock_device, shutdown_device, restart_device, set_volume,
                mute_audio, get_system_info, list_processes, kill_process
  File Ops    : read_file, write_file, delete_file, list_directory, search_files,
                copy_file, move_file, create_directory
  Email       : send_email, read_emails
  WhatsApp    : send_whatsapp
  Media       : control_media
"""

import argparse
import base64
import ctypes
import hashlib
import hmac
import imaplib
import json
import os
import shutil
import smtplib
import subprocess
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from glob import glob
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, request

from bridge_ratelimit import RateLimiter

# ── Optional heavy deps ───────────────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True   # Move mouse to corner to abort
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pywhatkit
    HAS_PYWHATKIT = True
except ImportError:
    HAS_PYWHATKIT = False

try:
    from PIL import ImageGrab
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ─────────────────────────────────────────────────────────────────────────────

LAST_NONZERO_VOLUME = 50

# Actions that are destructive/irreversible-adjacent enough to require an
# explicit, separate confirmation round-trip before they execute. Neither the
# LLM parser upstream (awesome_chat.py) nor a single in-band flag can satisfy
# this gate in one request -- the caller must see confirmation_required, then
# resubmit the identical action+params with the returned confirmation_token.
DESTRUCTIVE_ACTIONS = {
    "run_command", "delete_file", "shutdown_device", "restart_device", "kill_process",
}


def _action_fingerprint(action, params):
    """Deterministic fingerprint of an action+params pair. Not a secret --
    it's a challenge the caller must echo back on a second, separate request
    to prove the destructive action was actually seen and re-submitted
    deliberately, not auto-completed in one shot by an LLM or script."""
    payload = json.dumps({"action": action, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bool_env(name, default="true"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _is_windows():
    return os.name == "nt"


def _run_command(command, shell_type="auto"):
    """Run a shell command and return exit code + stdout + stderr."""
    if shell_type == "powershell" and _is_windows():
        cmd = ["powershell", "-Command", command]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    else:
        completed = subprocess.run(command, shell=True, capture_output=True,
                                   text=True, timeout=30)
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _require(lib_name, available):
    if not available:
        return {"ok": False, "error": f"'{lib_name}' is not installed. Run: pip install {lib_name}"}
    return None


# ── Volume helpers (Windows WinMM) ────────────────────────────────────────────

def _get_windows_volume_percent():
    if not _is_windows():
        return None
    current = ctypes.c_uint()
    result = ctypes.windll.winmm.waveOutGetVolume(0, ctypes.byref(current))
    if result != 0:
        return None
    left = current.value & 0xFFFF
    return int(round((left / 65535.0) * 100))


def _set_windows_volume_percent(level):
    if not _is_windows():
        return {"ok": False, "error": "set_volume is only implemented for Windows."}
    level = max(0, min(100, int(level)))
    scalar = int((level / 100.0) * 65535)
    packed = scalar | (scalar << 16)
    result = ctypes.windll.winmm.waveOutSetVolume(0, packed)
    if result != 0:
        return {"ok": False, "error": f"waveOutSetVolume failed with code {result}."}
    return {"ok": True, "level": level}


def _toggle_or_set_windows_mute(force_mute=True):
    global LAST_NONZERO_VOLUME
    if not _is_windows():
        return {"ok": False, "error": "mute_audio is only implemented for Windows."}
    current = _get_windows_volume_percent()
    if current is None:
        return {"ok": False, "error": "Unable to read current system volume."}
    if force_mute:
        if current > 0:
            LAST_NONZERO_VOLUME = current
        r = _set_windows_volume_percent(0)
        return r if not r.get("ok") else {"ok": True, "muted": True,
                                           "previous_volume": LAST_NONZERO_VOLUME}
    target = LAST_NONZERO_VOLUME if LAST_NONZERO_VOLUME > 0 else 50
    r = _set_windows_volume_percent(target)
    return r if not r.get("ok") else {"ok": True, "muted": False, "restored_volume": target}


# ── Email helpers ─────────────────────────────────────────────────────────────

def _send_email(cfg, to, subject, body, html=False):
    """Send email using SMTP config from env or cfg dict."""
    smtp_host = cfg.get("smtp_host") or os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(cfg.get("smtp_port") or os.getenv("EMAIL_SMTP_PORT", "587"))
    username  = cfg.get("username") or os.getenv("EMAIL_USERNAME", "")
    password  = cfg.get("password") or os.getenv("EMAIL_PASSWORD", "")
    sender    = cfg.get("sender")   or username

    if not username or not password:
        return {"ok": False, "error": "EMAIL_USERNAME and EMAIL_PASSWORD env vars are required."}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = to if isinstance(to, str) else ", ".join(to)

    part = MIMEText(body, "html" if html else "plain", "utf-8")
    msg.attach(part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(username, password)
            server.sendmail(sender, to if isinstance(to, list) else [to], msg.as_string())
        return {"ok": True, "message": f"Email sent to {to}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _read_emails(cfg, folder="INBOX", max_count=10, unread_only=True):
    """Read emails using IMAP."""
    imap_host = cfg.get("imap_host") or os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
    imap_port = int(cfg.get("imap_port") or os.getenv("EMAIL_IMAP_PORT", "993"))
    username  = cfg.get("username") or os.getenv("EMAIL_USERNAME", "")
    password  = cfg.get("password") or os.getenv("EMAIL_PASSWORD", "")

    if not username or not password:
        return {"ok": False, "error": "EMAIL_USERNAME and EMAIL_PASSWORD env vars are required."}

    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(username, password)
        mail.select(folder)

        search_criteria = "(UNSEEN)" if unread_only else "ALL"
        _, data = mail.search(None, search_criteria)
        ids = data[0].split()
        ids = ids[-max_count:]  # most recent

        emails = []
        for num in reversed(ids):
            _, msg_data = mail.fetch(num, "(RFC822)")
            import email as emaillib
            msg = emaillib.message_from_bytes(msg_data[0][1])

            def _decode(val):
                if val is None:
                    return ""
                parts = decode_header(val)
                decoded = []
                for part, enc in parts:
                    if isinstance(part, bytes):
                        decoded.append(part.decode(enc or "utf-8", errors="replace"))
                    else:
                        decoded.append(part)
                return " ".join(decoded)

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

            emails.append({
                "from":    _decode(msg["From"]),
                "to":      _decode(msg["To"]),
                "subject": _decode(msg["Subject"]),
                "date":    msg["Date"],
                "body":    body[:2000],  # truncate long bodies
            })

        mail.logout()
        return {"ok": True, "emails": emails, "count": len(emails)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Flask app factory ─────────────────────────────────────────────────────────

def _limiter_from_env():
    """Per-IP limiter for /execute. BRIDGE_RATE_LIMIT=0 disables it."""
    max_requests = int(os.getenv("BRIDGE_RATE_LIMIT", "60"))
    if max_requests <= 0:
        return None
    return RateLimiter(max_requests=max_requests,
                       window_seconds=float(os.getenv("BRIDGE_RATE_WINDOW", "60")))


def create_app(device_name, token, dry_run):
    app = Flask(__name__)
    limiter = _limiter_from_env()

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"ok": True, "device": device_name, "dry_run": dry_run,
                        "capabilities": {
                            "pyautogui":  HAS_PYAUTOGUI,
                            "pyperclip":  HAS_PYPERCLIP,
                            "psutil":     HAS_PSUTIL,
                            "pywhatkit":  HAS_PYWHATKIT,
                            "pil":        HAS_PIL,
                        }})

    @app.route("/execute", methods=["POST"])
    def execute():
        # ── Rate limit (before auth, so failed-auth attempts are throttled) ───
        if limiter and not limiter.is_allowed(request.remote_addr or "unknown"):
            return jsonify({"ok": False, "error": "Rate limit exceeded"}), 429

        # ── Auth ──────────────────────────────────────────────────────────────
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(auth, expected):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        action  = payload.get("action")
        params  = payload.get("params", {})

        if not action:
            return jsonify({"ok": False, "error": "Missing action"}), 400

        base = {"ok": True, "device": device_name, "action": action,
                "params": params, "dry_run": dry_run}

        # ── Dry-run short-circuit ─────────────────────────────────────────────
        if dry_run:
            return jsonify(base)

        # ── Confirmation gate for destructive actions ──────────────────────────
        # Fail closed: a destructive action must be re-submitted, identically,
        # with the confirmation_token this endpoint returns on the first
        # attempt. No caller -- human, LLM, or script -- can complete one of
        # these actions in a single request.
        if action in DESTRUCTIVE_ACTIONS:
            fp = _action_fingerprint(action, params)
            supplied_token = str(payload.get("confirmation_token", ""))
            if not hmac.compare_digest(supplied_token, fp):
                return jsonify({
                    "ok": False,
                    "confirmation_required": True,
                    "device": device_name,
                    "action": action,
                    "params": params,
                    "confirmation_token": fp,
                    "error": (
                        f"'{action}' is destructive and requires confirmation. "
                        f"Re-submit the identical action and params with "
                        f'"confirmation_token": "{fp}" to execute it.'
                    ),
                }), 409

        # ══════════════════════════════════════════════════════════════════════
        #  PC CONTROL
        # ══════════════════════════════════════════════════════════════════════

        # ── open_app ──────────────────────────────────────────────────────────
        if action == "open_app":
            app_name = (params.get("app") or "").strip()
            if not app_name:
                return jsonify({"ok": False, "error": "Missing params.app"}), 400
            if _is_windows():
                cmd = f'start "" "{app_name}"'
            else:
                cmd = f'open -a "{app_name}"' if sys.platform == "darwin" else f"xdg-open '{app_name}'"
            base["command_result"] = _run_command(cmd)
            return jsonify(base)

        # ── run_command ───────────────────────────────────────────────────────
        if action == "run_command":
            cmd         = params.get("command", "")
            shell_type  = params.get("shell", "auto")   # auto | powershell | cmd
            if not cmd:
                return jsonify({"ok": False, "error": "Missing params.command"}), 400
            base["command_result"] = _run_command(cmd, shell_type)
            return jsonify(base)

        # ── take_screenshot ───────────────────────────────────────────────────
        if action == "take_screenshot":
            err = _require("Pillow", HAS_PIL)
            if err:
                return jsonify(err)
            region = params.get("region")  # (left, top, right, bottom) optional
            try:
                img = ImageGrab.grab(bbox=region)
                buf = BytesIO()
                img.save(buf, format="PNG")
                base["screenshot_b64"] = base64.b64encode(buf.getvalue()).decode()
                base["width"]  = img.width
                base["height"] = img.height
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── type_text ─────────────────────────────────────────────────────────
        if action == "type_text":
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            text     = params.get("text", "")
            interval = float(params.get("interval", 0.02))
            try:
                pyautogui.write(text, interval=interval)
                base["typed"] = text
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── press_hotkey ──────────────────────────────────────────────────────
        if action == "press_hotkey":
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            keys = params.get("keys")   # e.g. ["ctrl", "c"] or "enter"
            if not keys:
                return jsonify({"ok": False, "error": "Missing params.keys"}), 400
            try:
                if isinstance(keys, list):
                    pyautogui.hotkey(*keys)
                else:
                    pyautogui.press(keys)
                base["keys_pressed"] = keys
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── move_mouse ────────────────────────────────────────────────────────
        if action == "move_mouse":
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            x        = params.get("x")
            y        = params.get("y")
            duration = float(params.get("duration", 0.2))
            if x is None or y is None:
                return jsonify({"ok": False, "error": "Missing params.x / params.y"}), 400
            try:
                pyautogui.moveTo(int(x), int(y), duration=duration)
                base["moved_to"] = {"x": x, "y": y}
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── left_click ────────────────────────────────────────────────────────
        if action in ("left_click", "right_click", "double_click"):
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            x = params.get("x")
            y = params.get("y")
            try:
                kwargs = {}
                if x is not None and y is not None:
                    kwargs = {"x": int(x), "y": int(y)}
                if action == "left_click":
                    pyautogui.click(**kwargs)
                elif action == "right_click":
                    pyautogui.rightClick(**kwargs)
                elif action == "double_click":
                    pyautogui.doubleClick(**kwargs)
                base["clicked"] = {"x": x, "y": y}
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── scroll ────────────────────────────────────────────────────────────
        if action == "scroll":
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            clicks    = int(params.get("clicks", 3))   # positive = up, negative = down
            x         = params.get("x")
            y         = params.get("y")
            try:
                kwargs = {}
                if x is not None and y is not None:
                    kwargs = {"x": int(x), "y": int(y)}
                pyautogui.scroll(clicks, **kwargs)
                base["scrolled"] = clicks
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── open_url ──────────────────────────────────────────────────────────
        if action == "open_url":
            url = (params.get("url") or "").strip()
            if not url:
                return jsonify({"ok": False, "error": "Missing params.url"}), 400
            import webbrowser
            webbrowser.open(url)
            base["url_opened"] = url
            return jsonify(base)

        # ── get_clipboard ─────────────────────────────────────────────────────
        if action == "get_clipboard":
            err = _require("pyperclip", HAS_PYPERCLIP)
            if err:
                return jsonify(err)
            try:
                base["clipboard"] = pyperclip.paste()
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── set_clipboard ─────────────────────────────────────────────────────
        if action == "set_clipboard":
            err = _require("pyperclip", HAS_PYPERCLIP)
            if err:
                return jsonify(err)
            text = params.get("text", "")
            try:
                pyperclip.copy(text)
                base["clipboard_set"] = text
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── minimize_window / maximize_window / close_window ──────────────────
        if action in ("minimize_window", "maximize_window", "close_window"):
            if not _is_windows():
                return jsonify({"ok": False, "error": "Window control is only implemented for Windows."})
            mapping = {
                "minimize_window": "win+down",
                "maximize_window": "win+up",
                "close_window":    "alt+F4",
            }
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            keys = mapping[action].split("+")
            pyautogui.hotkey(*keys)
            base["window_action"] = action
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  SYSTEM
        # ══════════════════════════════════════════════════════════════════════

        # ── lock_device ───────────────────────────────────────────────────────
        if action == "lock_device":
            base["command_result"] = _run_command("rundll32.exe user32.dll,LockWorkStation")
            return jsonify(base)

        # ── shutdown_device ───────────────────────────────────────────────────
        if action == "shutdown_device":
            base["command_result"] = _run_command("shutdown /s /t 0")
            return jsonify(base)

        # ── restart_device ────────────────────────────────────────────────────
        if action == "restart_device":
            base["command_result"] = _run_command("shutdown /r /t 0")
            return jsonify(base)

        # ── set_volume ────────────────────────────────────────────────────────
        if action == "set_volume":
            level = params.get("level")
            if level is None:
                return jsonify({"ok": False, "error": "Missing params.level"}), 400
            r = _set_windows_volume_percent(level)
            if not r.get("ok"):
                return jsonify(r), 501
            base["volume"] = r
            return jsonify(base)

        # ── mute_audio ────────────────────────────────────────────────────────
        if action == "mute_audio":
            muted  = params.get("muted", True)
            r      = _toggle_or_set_windows_mute(force_mute=bool(muted))
            if not r.get("ok"):
                return jsonify(r), 501
            base["mute"] = r
            return jsonify(base)

        # ── get_system_info ───────────────────────────────────────────────────
        if action == "get_system_info":
            err = _require("psutil", HAS_PSUTIL)
            if err:
                return jsonify(err)
            try:
                import platform
                cpu_pct  = psutil.cpu_percent(interval=1)
                mem      = psutil.virtual_memory()
                disk     = psutil.disk_usage("/")
                net      = psutil.net_io_counters()
                batt     = psutil.sensors_battery()
                base["system_info"] = {
                    "platform":      platform.system(),
                    "platform_ver":  platform.version(),
                    "machine":       platform.machine(),
                    "processor":     platform.processor(),
                    "cpu_percent":   cpu_pct,
                    "cpu_cores":     psutil.cpu_count(logical=True),
                    "ram_total_gb":  round(mem.total / 1e9, 2),
                    "ram_used_gb":   round(mem.used  / 1e9, 2),
                    "ram_percent":   mem.percent,
                    "disk_total_gb": round(disk.total / 1e9, 2),
                    "disk_used_gb":  round(disk.used  / 1e9, 2),
                    "disk_percent":  disk.percent,
                    "net_bytes_sent": net.bytes_sent,
                    "net_bytes_recv": net.bytes_recv,
                    "battery_percent": batt.percent if batt else None,
                    "battery_charging": batt.power_plugged if batt else None,
                }
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── list_processes ────────────────────────────────────────────────────
        if action == "list_processes":
            err = _require("psutil", HAS_PSUTIL)
            if err:
                return jsonify(err)
            name_filter = (params.get("name") or "").lower()
            try:
                procs = []
                for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
                    info = p.info
                    if name_filter and name_filter not in info["name"].lower():
                        continue
                    procs.append(info)
                base["processes"] = sorted(procs, key=lambda p: p.get("cpu_percent") or 0, reverse=True)[:50]
                base["count"] = len(base["processes"])
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── kill_process ──────────────────────────────────────────────────────
        if action == "kill_process":
            err = _require("psutil", HAS_PSUTIL)
            if err:
                return jsonify(err)
            name = params.get("name")
            pid  = params.get("pid")
            if not name and not pid:
                return jsonify({"ok": False, "error": "Provide params.name or params.pid"}), 400
            killed = []
            try:
                for p in psutil.process_iter(["pid", "name"]):
                    if (pid and p.pid == int(pid)) or (name and name.lower() in p.info["name"].lower()):
                        p.kill()
                        killed.append({"pid": p.pid, "name": p.info["name"]})
                base["killed"] = killed
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  FILE MANAGEMENT
        # ══════════════════════════════════════════════════════════════════════

        if action == "read_file":
            path = params.get("path", "")
            if not path:
                return jsonify({"ok": False, "error": "Missing params.path"}), 400
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                base["content"] = content
                base["size_bytes"] = os.path.getsize(path)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "write_file":
            path    = params.get("path", "")
            content = params.get("content", "")
            mode    = params.get("mode", "w")   # "w" overwrite or "a" append
            if not path:
                return jsonify({"ok": False, "error": "Missing params.path"}), 400
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, mode, encoding="utf-8") as f:
                    f.write(content)
                base["written"] = path
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "delete_file":
            path = params.get("path", "")
            if not path:
                return jsonify({"ok": False, "error": "Missing params.path"}), 400
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                base["deleted"] = path
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "list_directory":
            path = params.get("path", os.path.expanduser("~"))
            try:
                entries = []
                for entry in os.scandir(path):
                    entries.append({
                        "name":       entry.name,
                        "type":       "dir" if entry.is_dir() else "file",
                        "size_bytes": entry.stat().st_size if entry.is_file() else None,
                    })
                base["entries"] = sorted(entries, key=lambda e: (e["type"] == "file", e["name"]))
                base["path"]    = path
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "search_files":
            root    = params.get("root",    os.path.expanduser("~"))
            pattern = params.get("pattern", "*")
            content_query = params.get("content_query", "")   # optional text search inside files
            max_results   = int(params.get("max_results", 50))
            try:
                matches = []
                for p in Path(root).rglob(pattern):
                    if content_query:
                        try:
                            text = p.read_text(encoding="utf-8", errors="replace")
                            if content_query.lower() not in text.lower():
                                continue
                        except Exception:
                            continue
                    matches.append(str(p))
                    if len(matches) >= max_results:
                        break
                base["matches"] = matches
                base["count"]   = len(matches)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "copy_file":
            src = params.get("src", "")
            dst = params.get("dst", "")
            if not src or not dst:
                return jsonify({"ok": False, "error": "Missing params.src / params.dst"}), 400
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                base["copied"] = {"src": src, "dst": dst}
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "move_file":
            src = params.get("src", "")
            dst = params.get("dst", "")
            if not src or not dst:
                return jsonify({"ok": False, "error": "Missing params.src / params.dst"}), 400
            try:
                shutil.move(src, dst)
                base["moved"] = {"src": src, "dst": dst}
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        if action == "create_directory":
            path = params.get("path", "")
            if not path:
                return jsonify({"ok": False, "error": "Missing params.path"}), 400
            try:
                os.makedirs(path, exist_ok=True)
                base["created"] = path
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  COMMUNICATION — EMAIL
        # ══════════════════════════════════════════════════════════════════════

        if action == "send_email":
            to      = params.get("to")
            subject = params.get("subject", "(no subject)")
            body    = params.get("body", "")
            html    = bool(params.get("html", False))
            if not to:
                return jsonify({"ok": False, "error": "Missing params.to"}), 400
            cfg = params.get("email_config", {})
            r   = _send_email(cfg, to, subject, body, html)
            if not r.get("ok"):
                return jsonify(r), 500
            base["email"] = r
            return jsonify(base)

        if action == "read_emails":
            folder      = params.get("folder", "INBOX")
            max_count   = int(params.get("max_count", 10))
            unread_only = bool(params.get("unread_only", True))
            cfg = params.get("email_config", {})
            r   = _read_emails(cfg, folder, max_count, unread_only)
            if not r.get("ok"):
                return jsonify(r), 500
            base["emails"] = r
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  COMMUNICATION — WHATSAPP
        # ══════════════════════════════════════════════════════════════════════

        if action == "send_whatsapp":
            err = _require("pywhatkit", HAS_PYWHATKIT)
            if err:
                return jsonify(err)
            phone   = str(params.get("phone", "")).strip()
            message = str(params.get("message", "")).strip()
            wait    = int(params.get("wait_seconds", 15))  # seconds before sending
            if not phone or not message:
                return jsonify({"ok": False, "error": "Missing params.phone or params.message"}), 400
            # Ensure E.164 format
            if not phone.startswith("+"):
                phone = "+" + phone
            now   = time.localtime()
            hour  = now.tm_hour
            mins  = now.tm_min + 1     # send 1 minute from now
            if mins >= 60:
                hour = (hour + 1) % 24
                mins = mins - 60
            try:
                pywhatkit.sendwhatmsg(phone, message, hour, mins,
                                      wait_time=wait, tab_close=True, close_time=3)
                base["whatsapp"] = {"phone": phone, "message": message, "status": "queued"}
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  MEDIA CONTROL
        # ══════════════════════════════════════════════════════════════════════

        if action == "control_media":
            err = _require("pyautogui", HAS_PYAUTOGUI)
            if err:
                return jsonify(err)
            command = (params.get("command") or "").lower()
            media_keys = {
                "play":       "playpause",
                "pause":      "playpause",
                "play_pause": "playpause",
                "next":       "nexttrack",
                "previous":   "prevtrack",
                "prev":       "prevtrack",
                "stop":       "stop",
                "volume_up":  "volumeup",
                "volume_down":"volumedown",
                "mute":       "volumemute",
            }
            key = media_keys.get(command)
            if not key:
                return jsonify({"ok": False,
                                "error": f"Unknown media command '{command}'. Valid: {list(media_keys.keys())}"}), 400
            try:
                pyautogui.press(key)
                base["media_command"] = command
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            return jsonify(base)

        # ── Fallthrough ───────────────────────────────────────────────────────
        return jsonify({"ok": False, "error": f"Unsupported action: '{action}'"}), 400

    return app


def main():
    parser = argparse.ArgumentParser(description="JARVIS computer bridge server")
    parser.add_argument("--device", default=os.getenv("BRIDGE_DEVICE", "computer"))
    parser.add_argument("--host",   default=os.getenv("BRIDGE_HOST",   "0.0.0.0"))
    parser.add_argument("--port",   type=int, default=int(os.getenv("BRIDGE_PORT", "8092")))
    args = parser.parse_args()

    token = os.getenv("BRIDGE_TOKEN", "").strip()
    if not token:
        raise ValueError("BRIDGE_TOKEN env var is required.")

    dry_run = _bool_env("BRIDGE_DRY_RUN", "true")
    if dry_run:
        print("[bridge] ⚠️  DRY-RUN mode — no OS changes will be executed.")
    else:
        print("[bridge] 🟢  LIVE mode — OS commands WILL be executed.")

    app = create_app(args.device, token, dry_run)
    print(f"[bridge] Listening on http://{args.host}:{args.port}  (device={args.device})")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

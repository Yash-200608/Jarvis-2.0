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
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

from flask import Flask, jsonify, request

from bridge_ratelimit import RateLimiter

# ─────────────────────────────────────────────────────────────────────────────

COMMON_APPS = {
    "whatsapp":    "com.whatsapp",
    "youtube":     "com.google.android.youtube",
    "chrome":      "com.android.chrome",
    "maps":        "com.google.android.apps.maps",
    "camera":      "com.android.camera",
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


def _is_phone_number(s):
    """Return True if s looks like a phone number rather than a contact name."""
    return bool(re.match(r'^[+\d\s\-().]+$', s.strip())) and any(c.isdigit() for c in s)


def _clean_number(num):
    """
    Normalize a phone number from the contacts DB.
    Removes spaces, dashes, parentheses — keeps digits and a leading +.
    e.g. '+91 72910 54990' → '+917291054990'
         '91-72910-54990'  → '+917291054990'
         '7291054990'      → '7291054990'
    """
    num = (num or "").strip()
    has_plus = num.startswith('+')
    digits = re.sub(r'\D', '', num)          # keep only digits
    return ('+' if has_plus else '') + digits


def _lookup_contact(name):
    """
    Search ALL Android contact stores for a name and return matches.
    Each match: {"name": "...", "number": "...", "source": "..."}

    Queries three sources in parallel and merges results:
      1. content://contacts/phones/          — device / phone contacts
      2. content://com.android.contacts/data — Google/Gmail synced contacts
                                               (fetches all rows, filters mimetype in Python)
      3. content://icc/adn                   — SIM card contacts

    No WHERE clause anywhere — avoids ADB shell quoting issues.
    Filtering is done entirely in Python.
    """
    name_query = name.strip().lower()
    words = [w for w in name_query.split() if len(w) > 1]

    def _matches_name(contact_name):
        cl = contact_name.lower()
        return name_query in cl or (words and all(w in cl for w in words))

    def _extract_field(line, field):
        """Extract a field value from a content query row, handling spaces in values."""
        # Match field=VALUE where VALUE ends at ', nextfield=' or end of line
        m = re.search(rf'{field}=(.*?)(?:,\s*\w+=|$)', line)
        return m.group(1).strip() if m else None

    def _parse_phones_uri(output):
        """Parse output of content://contacts/phones/ (display_name, number columns)."""
        results, seen = [], set()
        for line in output.splitlines():
            contact = _extract_field(line, 'display_name')
            raw_num = _extract_field(line, 'number')
            if not contact or not raw_num:
                continue
            num = _clean_number(raw_num)
            if _matches_name(contact) and num not in seen and re.search(r'\d{5,}', num):
                seen.add(num)
                results.append({"name": contact, "number": num, "source": "device"})
        return results

    def _parse_data_uri(output):
        """
        Parse output of content://com.android.contacts/data
        (display_name, data1, mimetype columns).
        Only keep rows where mimetype contains 'phone'.
        """
        results, seen = [], set()
        for line in output.splitlines():
            mime = _extract_field(line, 'mimetype') or ''
            if 'phone' not in mime.lower():
                continue
            contact = _extract_field(line, 'display_name')
            raw_num = _extract_field(line, 'data1')
            if not contact or not raw_num:
                continue
            num = _clean_number(raw_num)
            if _matches_name(contact) and num not in seen and re.search(r'\d{5,}', num):
                seen.add(num)
                results.append({"name": contact, "number": num, "source": "google"})
        return results

    def _parse_sim_uri(output):
        """Parse output of content://icc/adn (tag, number columns)."""
        results, seen = [], set()
        for line in output.splitlines():
            contact = _extract_field(line, 'tag')
            raw_num = _extract_field(line, 'number')
            if not contact or not raw_num:
                continue
            num = _clean_number(raw_num)
            if _matches_name(contact) and num not in seen and re.search(r'\d{5,}', num):
                seen.add(num)
                results.append({"name": contact, "number": num, "source": "sim"})
        return results

    all_matches = []
    seen_numbers = set()

    # 1. Device contacts
    r1 = _adb_shell("content query --uri content://contacts/phones/ --projection display_name:number")
    for m in _parse_phones_uri(r1.get("stdout", "")):
        if m["number"] not in seen_numbers:
            seen_numbers.add(m["number"])
            all_matches.append(m)

    # 2. Google/Gmail synced contacts (fetch all data rows, filter mimetype in Python)
    r2 = _adb_shell(
        "content query --uri content://com.android.contacts/data "
        "--projection display_name:data1:mimetype"
    )
    for m in _parse_data_uri(r2.get("stdout", "")):
        if m["number"] not in seen_numbers:
            seen_numbers.add(m["number"])
            all_matches.append(m)

    # 3. SIM contacts
    r3 = _adb_shell("content query --uri content://icc/adn --projection tag:number")
    for m in _parse_sim_uri(r3.get("stdout", "")):
        if m["number"] not in seen_numbers:
            seen_numbers.add(m["number"])
            all_matches.append(m)

    # Sort: exact full-name match first, then partial
    exact = [m for m in all_matches if m["name"].lower() == name_query]
    partial = [m for m in all_matches if m["name"].lower() != name_query]
    return exact + partial


def _resolve_recipient(recipient):
    """
    If recipient looks like a name (not a phone number), look it up in contacts.
    Returns (resolved_number_or_original, contact_name_or_None, error_or_None).
    """
    if _is_phone_number(recipient):
        return recipient, None, None   # already a number — pass through

    matches = _lookup_contact(recipient)
    if not matches:
        return None, None, f"No contact found matching '{recipient}'"
    # Return the first (best) match — prefer exact name match if available
    exact = [m for m in matches if m["name"].lower() == recipient.lower()]
    best  = exact[0] if exact else matches[0]
    return best["number"], best["name"], None


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


# ── Platform deep-link templates ─────────────────────────────────────────────
# Each entry: (intent_command_template, needs_type_text)
# {phone}  → phone number without +
# {text}   → URL-encoded message
# {raw}    → raw message (for input text)
# {user}   → username/handle
PLATFORM_INTENTS = {
    "whatsapp":  'am start -a android.intent.action.VIEW -d "whatsapp://send?phone={phone}&text={text}"',
    "telegram":  'am start -a android.intent.action.VIEW -d "tg://resolve?domain={user}&text={text}"',
    "instagram": None,   # no clean DM deep link — uses full UI automation
    "snapchat":  None,   # no clean DM deep link — uses full UI automation
    "sms":       'am start -a android.intent.action.SENDTO -d "sms:{phone}" --es sms_body "{raw}" --ez exit_on_sent true',
    "facebook":  'am start -a android.intent.action.VIEW -d "fb://messaging"',
    "twitter":   None,
    "signal":    'am start -a android.intent.action.VIEW -d "sgnl://note_to_self"',
}

# No platform reliably uses KEYCODE_ENTER to send — modern messaging apps
# all require tapping the dedicated send button. Use _tap_send_button() for all.
ENTER_SENDS = set()   # kept for future use (e.g. terminal apps)


def _get_screen_size():
    """
    Return (width, height) of the CURRENT screen — prefers Override size
    (the live resolution when the device is rotated or scaled) over the
    hardware Physical size.
    """
    wm = _adb_shell("wm size")["stdout"]
    override = re.search(r'Override size:\s*(\d+)\s*x\s*(\d+)', wm, re.IGNORECASE)
    if override:
        return int(override.group(1)), int(override.group(2))
    physical = re.search(r'Physical size:\s*(\d+)\s*x\s*(\d+)', wm, re.IGNORECASE)
    if physical:
        return int(physical.group(1)), int(physical.group(2))
    m = re.search(r'(\d+)\s*x\s*(\d+)', wm)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# Phrases that match 'send' but are NOT the keyboard/compose send button
SEND_NEGATIVE_MATCHES = (
    "feedback", "report", "reset", "sender", "resend",
    "location", "photo", "file", "sticker", "voice note", "audio",
    "gif", "attachment", "emoji", "camera",
)

# Prefer resource-ids that exactly mean the compose send button
SEND_POSITIVE_IDS = (
    "send_btn", "send_button", "sendbtn", "btn_send",
    "entry_send", "conversation_send", "composer_send",
    "action_send", "iv_send", "ib_send",
)


def _tap_send_button(retries=2, delay=1.0):
    """
    Find the compose/send button in any messaging app.

    Ranking (higher score wins):
      +10  resource-id in SEND_POSITIVE_IDS
      +6   content-desc == 'send' or 'send message'
      +4   node is clickable=true
      +3   node is in the bottom 40% of the screen
      +2   node is on the right half of the screen
      -10  text/desc/id contains a negative-match phrase
    If nothing scores > 0, fall back to proportional tap (95%w, 95%h)
    — tuned for portrait WhatsApp/SMS/Telegram composer position.
    """
    width, height = _get_screen_size()

    for attempt in range(retries):
        _adb_shell("uiautomator dump /sdcard/jarvis_ui.xml")
        r = _adb_shell("cat /sdcard/jarvis_ui.xml; rm -f /sdcard/jarvis_ui.xml")
        xml_content = r.get("stdout", "").strip()

        if xml_content and "<hierarchy" in xml_content:
            try:
                root = ET.fromstring(xml_content)
                best = None          # (score, cx, cy, meta)
                for node in root.iter():
                    desc   = (node.get("content-desc") or "").lower()
                    text   = (node.get("text") or "").lower()
                    res_id = (node.get("resource-id") or "").lower()
                    clickable = (node.get("clickable") or "").lower() == "true"

                    haystack = f"{desc} {text} {res_id}"
                    if "send" not in haystack:
                        continue

                    if any(neg in haystack for neg in SEND_NEGATIVE_MATCHES):
                        continue

                    bounds = node.get("bounds", "")
                    nums = re.findall(r'\d+', bounds)
                    if len(nums) < 4:
                        continue
                    x1, y1, x2, y2 = (int(n) for n in nums[:4])
                    if x2 <= x1 or y2 <= y1:
                        continue
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2

                    score = 0
                    if any(p in res_id for p in SEND_POSITIVE_IDS):
                        score += 10
                    if desc.strip() in ("send", "send message", "send button"):
                        score += 6
                    if text.strip() == "send":
                        score += 4
                    if clickable:
                        score += 4
                    if height and cy > int(height * 0.60):
                        score += 3
                    if width and cx > int(width * 0.50):
                        score += 2

                    if score <= 0:
                        continue

                    cand = (score, cx, cy, f"score={score} id='{res_id or desc or text}'")
                    if best is None or cand[0] > best[0]:
                        best = cand

                if best:
                    _, cx, cy, meta = best
                    _adb_shell(f"input tap {cx} {cy}")
                    return True, f"ui_tap ({cx},{cy}) [{meta}]"
            except ET.ParseError:
                pass

        if attempt < retries - 1:
            time.sleep(delay)

    # Fallback: proportional tap. On portrait composers the send button sits
    # at the right edge of the input row, roughly 95% w / 95% h.
    if width and height:
        tap_x = int(width  * 0.95)
        tap_y = int(height * 0.95)
        _adb_shell(f"input tap {tap_x} {tap_y}")
        return True, f"screen_calc ({tap_x},{tap_y}) on {width}x{height}"

    return False, "could_not_determine_screen_size"


def _send_message_via_app(platform, recipient, message, base):
    """
    Universal message sender for any platform.
    platform  : 'whatsapp' | 'telegram' | 'instagram' | 'sms' | 'facebook' | 'snapchat' | ...
    recipient : phone number (with or without +) OR @username/handle
    message   : plain text to send
    base      : response dict to update in-place
    Returns updated base dict.
    """
    from urllib.parse import quote as _quote

    platform  = platform.lower().strip()
    recipient = str(recipient).strip()

    # Resolve contact name → phone number (skip for platforms that use @username)
    if platform not in ("instagram", "snapchat", "twitter", "telegram"):
        resolved, contact_name, err = _resolve_recipient(recipient)
        if err:
            base["ok"]    = False
            base["error"] = err
            return base
        if contact_name:
            base["resolved_contact"] = {"name": contact_name, "number": resolved}
        recipient = resolved

    phone = _clean_number(str(recipient)).lstrip("+")  # clean + strip + for URL use
    user  = str(recipient).lstrip("@").strip()
    encoded  = _quote(message)
    raw      = message.replace('"', '\\"')

    intent_tmpl = PLATFORM_INTENTS.get(platform)

    if intent_tmpl:
        adb_cmd = intent_tmpl.format(
            phone=phone, text=encoded, raw=raw, user=user
        )
        r = _adb_shell(adb_cmd)
        if r["exit_code"] != 0:
            base["ok"]    = False
            base["error"] = r.get("stderr") or "App launch failed"
            base["adb"]   = r
            return base
        # Give the messaging app time to load the compose/chat screen
        load_wait = 2.0 if platform == "sms" else 3.0
        time.sleep(load_wait)

    elif platform in ("instagram", "snapchat", "twitter"):
        # No deep-link DM → open app then automate via UI
        pkg = COMMON_APPS.get(platform, "")
        if not pkg:
            base["ok"]    = False
            base["error"] = f"No package mapping for {platform}"
            return base
        # Open app
        _adb_shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        time.sleep(3)
        # Instagram: tap DM icon (top-right area), tap search, type username, tap user, type message
        if platform == "instagram":
            w, h = _get_screen_size()
            if not w:
                w, h = 1080, 2400
            _adb_shell(f"input tap {int(w*0.93)} {int(h*0.05)}")   # DM icon (top-right)
            time.sleep(1.5)
            _adb_shell(f"input tap {int(w*0.85)} {int(h*0.07)}")   # New DM / compose
            time.sleep(1.5)
            _adb_shell(f"input text '{user}'")                       # search for user
            time.sleep(2)
            _adb_shell(f"input tap {int(w*0.5)} {int(h*0.25)}")    # first result
            time.sleep(1.5)
            # find Chat/Message button
            _adb_shell(f"input tap {int(w*0.5)} {int(h*0.65)}")
            time.sleep(1.5)
        # Now type the message into the focused input field
        safe_msg = message.replace(" ", "%s").replace("'", "\\'")
        _adb_shell(f"input text '{safe_msg}'")
        time.sleep(1)
    else:
        base["ok"]    = False
        base["error"] = f"Platform '{platform}' not supported for messaging"
        return base

    # For SMS/Signal: ENTER is faster and reliable — skip UI dump
    if platform in ENTER_SENDS:
        _adb_shell("input keyevent KEYCODE_ENTER")
        base["send_method"] = "keyevent_enter"
    else:
        # Tap the send button via UI dump (WhatsApp, Telegram, Instagram, etc.)
        tapped, method = _tap_send_button()
        base["send_method"] = method
        if not tapped:
            base["warning"] = "Could not confirm send button was tapped"
    return base


def _limiter_from_env():
    """Per-IP limiter for /execute. BRIDGE_RATE_LIMIT=0 disables it."""
    max_requests = int(os.getenv("BRIDGE_RATE_LIMIT", "60"))
    if max_requests <= 0:
        return None
    return RateLimiter(max_requests=max_requests,
                       window_seconds=float(os.getenv("BRIDGE_RATE_WINDOW", "60")))


def create_app(token, dry_run):
    app = Flask(__name__)
    limiter = _limiter_from_env()

    @app.route("/health", methods=["GET"])
    def health():
        connected, _ = _device_connected()
        return jsonify({"ok": True, "device": "phone", "dry_run": dry_run,
                        "adb_device_connected": connected})

    @app.route("/execute", methods=["POST"])
    def execute():
        # Rate limit first, so failed-auth attempts are throttled too
        if limiter and not limiter.is_allowed(request.remote_addr or "unknown"):
            return jsonify({"ok": False, "error": "Rate limit exceeded"}), 429

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
            recipient = str(params.get("phone") or params.get("name") or "").strip()
            message   = str(params.get("message", "")).strip()
            if not recipient or not message:
                return jsonify({"ok": False, "error": "Missing params.phone/name and params.message"}), 400
            base = _send_message_via_app("sms", recipient, message, base)
            return jsonify(base)

        if action == "send_whatsapp":
            recipient = str(params.get("phone") or params.get("name") or "").strip()
            message   = str(params.get("message", "")).strip()
            if not recipient or not message:
                return jsonify({"ok": False, "error": "Missing params.phone/name and params.message"}), 400
            base = _send_message_via_app("whatsapp", recipient, message, base)
            return jsonify(base)

        # ── Universal send_message (works for any platform) ───────────────────
        if action == "send_message":
            platform  = str(params.get("platform", "whatsapp")).lower().strip()
            recipient = str(params.get("recipient") or params.get("phone") or
                           params.get("name") or params.get("username") or "").strip()
            message   = str(params.get("message", "")).strip()
            if not recipient or not message:
                return jsonify({"ok": False, "error": "Missing params.recipient and params.message"}), 400
            base = _send_message_via_app(platform, recipient, message, base)
            return jsonify(base)

        if action == "send_telegram":
            recipient = str(params.get("username") or params.get("phone") or
                           params.get("name") or "").strip()
            message   = str(params.get("message", "")).strip()
            if not recipient or not message:
                return jsonify({"ok": False, "error": "Missing params.username/name and params.message"}), 400
            base = _send_message_via_app("telegram", recipient, message, base)
            return jsonify(base)

        if action == "send_instagram_dm":
            recipient = str(params.get("username") or params.get("name") or "").strip()
            message   = str(params.get("message", "")).strip()
            if not recipient or not message:
                return jsonify({"ok": False, "error": "Missing params.username/name and params.message"}), 400
            base = _send_message_via_app("instagram", recipient, message, base)
            return jsonify(base)

        # ── lookup_contact ────────────────────────────────────────────────────
        if action == "lookup_contact":
            name = str(params.get("name", "")).strip()
            if not name:
                return jsonify({"ok": False, "error": "Missing params.name"}), 400
            matches = _lookup_contact(name)
            base["contacts"] = matches
            base["count"]    = len(matches)
            if not matches:
                base["ok"]    = False
                base["error"] = f"No contact found matching '{name}'"
            return jsonify(base)

        # ══════════════════════════════════════════════════════════════════════
        #  CALLS
        # ══════════════════════════════════════════════════════════════════════

        if action == "make_call":
            recipient = str(params.get("phone") or params.get("name") or "").strip()
            if not recipient:
                return jsonify({"ok": False, "error": "Missing params.phone or params.name"}), 400
            # Resolve contact name if needed
            if not _is_phone_number(recipient):
                resolved, contact_name, err = _resolve_recipient(recipient)
                if err:
                    return jsonify({"ok": False, "error": err}), 404
                if contact_name:
                    base["resolved_contact"] = {"name": contact_name, "number": resolved}
                recipient = resolved
            phone = _clean_number(recipient)   # removes spaces/dashes, keeps +
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
            # Strip noise phrases the LLM may include
            for phrase in ["on my phone", "on the phone", "on phone", "on my device",
                           "on device", "the ", " app", " application"]:
                app_name = app_name.lower().replace(phrase, "").strip()
            package = COMMON_APPS.get(app_name.lower(), app_name)
            # Launch via monkey (works for any installed package, no activity name needed)
            r = _adb_shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            if r["exit_code"] != 0 or "Error" in r.get("stderr", ""):
                # Fallback: try am start with MAIN intent
                r2 = _adb_shell(
                    f'am start -a android.intent.action.MAIN '
                    f'-c android.intent.category.LAUNCHER '
                    f'-n $(pm resolve-activity --brief -a android.intent.action.MAIN {package} | tail -1)'
                )
                if r2["exit_code"] == 0:
                    r = r2
            base["package"] = package
            base["result"]  = r
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
            # Get actual screen dimensions so coordinates are always valid
            sw, sh = _get_screen_size()
            if not sw:
                sw, sh = 1080, 1920
            cx  = sw // 2                           # horizontal centre

            direction = str(params.get("direction", "")).lower()

            # Named direction shortcuts — generate sensible coords automatically
            DIR_MAP = {
                "up":    (cx, int(sh * 0.80), cx, int(sh * 0.20)),
                "down":  (cx, int(sh * 0.20), cx, int(sh * 0.80)),
                "left":  (int(sw * 0.85), sh // 2, int(sw * 0.15), sh // 2),
                "right": (int(sw * 0.15), sh // 2, int(sw * 0.85), sh // 2),
            }

            if direction in DIR_MAP:
                x1, y1, x2, y2 = DIR_MAP[direction]
            else:
                # Use provided coordinates; clamp to screen bounds
                x1 = max(0, min(int(params.get("x1", cx)),       sw))
                y1 = max(0, min(int(params.get("y1", int(sh*0.80))), sh))
                x2 = max(0, min(int(params.get("x2", cx)),       sw))
                y2 = max(0, min(int(params.get("y2", int(sh*0.20))), sh))

            duration = int(params.get("duration_ms", 300))
            r = _adb_shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
            base["screen_size"] = f"{sw}x{sh}"
            base["swipe_coords"] = f"({x1},{y1})→({x2},{y2})"
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
            level  = int(params.get("level", 8))
            # Try cmd media_session first, fall back to key events
            r = _adb_shell(f"cmd media_session volume --stream 3 --set {level}")
            if r["exit_code"] != 0:
                # Fallback: use volume key events
                presses = max(1, level)
                _adb_shell("input keyevent KEYCODE_VOLUME_DOWN " * 15)  # reset to 0 first
                for _ in range(presses):
                    _adb_shell("input keyevent KEYCODE_VOLUME_UP")
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

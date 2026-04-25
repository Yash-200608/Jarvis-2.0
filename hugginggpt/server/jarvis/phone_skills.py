"""Skills that run on the phone (Termux).

Everything shells out to `termux-api` binaries or the raw Android
`am`/`input`/`dumpsys` shell, exactly like phone_agent.py did — but
packaged as Skill objects so they autopublish into the catalog.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import subprocess
import time
import urllib.parse
from typing import Any, Optional

from .skills import SkillRegistry

log = logging.getLogger("jarvis.phone_skills")


# ──────────────────────────────────────────────────────────────────────────
#  Shell helpers (async-friendly)
# ──────────────────────────────────────────────────────────────────────────
async def _sh(cmd: list[str] | str, timeout: int = 15,
              shell: bool = False, stdin_bytes: Optional[bytes] = None) -> dict:
    def _do():
        try:
            if shell and isinstance(cmd, list):
                c = " ".join(shlex.quote(x) for x in cmd)
            else:
                c = cmd
            p = subprocess.run(c, shell=shell, capture_output=True,
                               timeout=timeout, input=stdin_bytes)
            return {
                "exit_code": p.returncode,
                "stdout": p.stdout.decode("utf-8", errors="replace"),
                "stderr": p.stderr.decode("utf-8", errors="replace"),
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s"}
        except FileNotFoundError as e:
            return {"exit_code": -1, "stdout": "", "stderr": f"not found: {e}"}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
    return await asyncio.get_event_loop().run_in_executor(None, _do)


async def _sh_raw(argv: list[str], timeout: int = 15) -> tuple[int, bytes, bytes]:
    """For binary stdout (screenshots)."""
    def _do():
        try:
            p = subprocess.run(argv, capture_output=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except Exception as e:
            return -1, b"", str(e).encode()
    return await asyncio.get_event_loop().run_in_executor(None, _do)


def _have(binary: str) -> bool:
    from shutil import which
    return which(binary) is not None


HAS_TERMUX_API = _have("termux-sms-send")
HAS_AM         = _have("am")
HAS_INPUT      = _have("input")
HAS_SCREENCAP  = _have("screencap")


APP_PACKAGES = {
    "whatsapp": "com.whatsapp", "youtube": "com.google.android.youtube",
    "chrome": "com.android.chrome", "maps": "com.google.android.apps.maps",
    "camera": "com.android.camera", "settings": "com.android.settings",
    "contacts": "com.google.android.contacts",
    "phone": "com.google.android.dialer",
    "messages": "com.google.android.apps.messaging",
    "gmail": "com.google.android.gm", "spotify": "com.spotify.music",
    "instagram": "com.instagram.android",
    "telegram": "org.telegram.messenger",
}


async def _battery_dict() -> dict:
    if HAS_TERMUX_API:
        r = await _sh(["termux-battery-status"], timeout=10)
        if r["exit_code"] == 0:
            try:
                info = json.loads(r["stdout"])
                return {
                    "level":    info.get("percentage"),
                    "charging": (info.get("status", "").upper() == "CHARGING"),
                    "health":   info.get("health"),
                    "temp":     info.get("temperature"),
                }
            except json.JSONDecodeError:
                pass
    r = await _sh(["dumpsys", "battery"], timeout=10)
    info = {}
    for line in r["stdout"].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower()] = v.strip()
    level = info.get("level")
    plugged = any(info.get(k, "false").lower() == "true"
                  for k in ("ac powered", "usb powered"))
    return {"level": int(level) if level and level.isdigit() else None,
            "charging": plugged}


# ──────────────────────────────────────────────────────────────────────────
#  Skill registration
# ──────────────────────────────────────────────────────────────────────────
def register_phone_skills(reg: SkillRegistry):

    # ── battery / device info ────────────────────────────────────────
    @reg.skill("phone.battery", "Get battery level and charging status.",
               owner="phone")
    async def battery(router, params):
        return {"ok": True, "battery": await _battery_dict()}

    @reg.skill("phone.device_info",
               "Get manufacturer/model/Android version.",
               owner="phone")
    async def device_info(router, params):
        async def p(k):
            r = await _sh(["getprop", k], timeout=5)
            return r["stdout"].strip()
        return {"ok": True, "device_info": {
            "manufacturer":  await p("ro.product.manufacturer"),
            "model":         await p("ro.product.model"),
            "android_ver":   await p("ro.build.version.release"),
            "sdk":           await p("ro.build.version.sdk"),
        }}

    # ── find-my-phone / ring ─────────────────────────────────────────
    @reg.skill("phone.ring",
               "Play a loud tone on the phone at max volume for N seconds.",
               params_schema={"seconds": "int=10"},
               owner="phone")
    async def ring(router, params):
        seconds = int(params.get("seconds", 10))
        # Set media volume to max, then play the default alarm.
        if HAS_TERMUX_API:
            await _sh(["termux-volume", "music", "15"], timeout=5)
            # tone-generator via termux-tts-speak (loud beep alternative)
            await _sh(["termux-torch", "on"], timeout=3)
            await _sh(["termux-vibrate", "-d", str(seconds * 1000),
                       "-f"], timeout=seconds + 2)
            # if media-player binary exists, play alarm loop
            await _sh(
                "for i in $(seq 1 %d); do "
                "termux-tts-speak -r 0.9 -p 1.3 'Jarvis locator ping'; "
                "sleep 1; done" % seconds,
                shell=True, timeout=seconds + 5,
            )
            await _sh(["termux-torch", "off"], timeout=3)
            return {"ok": True, "seconds": seconds, "via": "termux-api"}
        return {"ok": False, "error": "termux-api not installed"}

    # ── messaging ────────────────────────────────────────────────────
    @reg.skill("phone.sms.send",
               "Send an SMS. params: phone, message.",
               params_schema={"phone": "str", "message": "str"},
               owner="phone")
    async def sms_send(router, params):
        number = str(params.get("phone", ""))
        msg = str(params.get("message", ""))
        if not number or not msg:
            return {"ok": False, "error": "phone + message required"}
        if HAS_TERMUX_API:
            r = await _sh(["termux-sms-send", "-n", number, msg], timeout=20)
            return {"ok": r["exit_code"] == 0, **r}
        return {"ok": False, "error": "termux-api not installed"}

    @reg.skill("phone.whatsapp.send",
               "Open WhatsApp with a prefilled message.",
               params_schema={"phone": "str", "message": "str"},
               owner="phone")
    async def wa_send(router, params):
        number = re.sub(r"[^\d+]", "", str(params.get("phone", "")))
        msg    = str(params.get("message", ""))
        if not number or not msg:
            return {"ok": False, "error": "phone + message required"}
        url = (f"https://api.whatsapp.com/send?phone={number}"
               f"&text={urllib.parse.quote(msg)}")
        r = await _sh(["am", "start", "-a", "android.intent.action.VIEW",
                       "-d", url], timeout=15)
        return {"ok": r["exit_code"] == 0, **r,
                "note": "WhatsApp composer opened — tap send"}

    @reg.skill("phone.call",
               "Dial a number (opens dialer, optionally auto-calls).",
               params_schema={"phone": "str"}, owner="phone")
    async def call(router, params):
        number = str(params.get("phone", ""))
        if HAS_TERMUX_API:
            r = await _sh(["termux-telephony-call", number], timeout=15)
            return {"ok": r["exit_code"] == 0, **r}
        r = await _sh(["am", "start", "-a", "android.intent.action.DIAL",
                       "-d", f"tel:{number}"], timeout=15)
        return {"ok": r["exit_code"] == 0, **r, "note": "dialer opened"}

    # ── apps ─────────────────────────────────────────────────────────
    @reg.skill("phone.app.open", "Open an app by name or package.",
               params_schema={"app": "str"}, owner="phone")
    async def open_app(router, params):
        app = str(params.get("app", "")).lower().strip()
        pkg = APP_PACKAGES.get(app, app)
        r = await _sh(["monkey", "-p", pkg, "-c",
                       "android.intent.category.LAUNCHER", "1"], timeout=15)
        return {"ok": r["exit_code"] == 0, "package": pkg, **r}

    @reg.skill("phone.app.close", "Force-stop an app.",
               params_schema={"app": "str"}, owner="phone")
    async def close_app(router, params):
        app = str(params.get("app", "")).lower().strip()
        pkg = APP_PACKAGES.get(app, app)
        r = await _sh(["am", "force-stop", pkg], timeout=10)
        return {"ok": r["exit_code"] == 0, "package": pkg, **r}

    # ── input: tap/swipe/type ────────────────────────────────────────
    @reg.skill("phone.tap", "Tap at x,y.",
               params_schema={"x": "int", "y": "int"}, owner="phone")
    async def tap(router, params):
        r = await _sh(["input", "tap",
                       str(int(params.get("x", 0))),
                       str(int(params.get("y", 0)))], timeout=5)
        return {"ok": r["exit_code"] == 0, **r}

    @reg.skill("phone.swipe", "Swipe up/down/left/right.",
               params_schema={"direction": "up|down|left|right"}, owner="phone")
    async def swipe(router, params):
        wm = await _sh(["wm", "size"], timeout=5)
        m = re.findall(r"(\d+)x(\d+)", wm["stdout"])
        w, h = (int(m[-1][0]), int(m[-1][1])) if m else (1080, 1920)
        cx = w // 2
        coords = {
            "up":    (cx, int(h * .8), cx, int(h * .2)),
            "down":  (cx, int(h * .2), cx, int(h * .8)),
            "left":  (int(w * .85), h // 2, int(w * .15), h // 2),
            "right": (int(w * .15), h // 2, int(w * .85), h // 2),
        }
        d = str(params.get("direction", "up")).lower()
        x1, y1, x2, y2 = coords.get(d, coords["up"])
        r = await _sh(["input", "swipe", str(x1), str(y1),
                       str(x2), str(y2), "300"], timeout=8)
        return {"ok": r["exit_code"] == 0, **r}

    @reg.skill("phone.type_text", "Type text into focused field.",
               params_schema={"text": "str"}, owner="phone")
    async def type_text(router, params):
        text = str(params.get("text", "")).replace(" ", "%s")
        r = await _sh(["input", "text", text], timeout=10)
        return {"ok": r["exit_code"] == 0, **r}

    # ── screen ───────────────────────────────────────────────────────
    @reg.skill("phone.screenshot",
               "Capture a full-screen PNG, return base64.",
               owner="phone")
    async def screenshot(router, params):
        rc, png, err = await _sh_raw(["screencap", "-p"], timeout=15)
        if rc != 0 or not png:
            return {"ok": False,
                    "error": err.decode(errors="replace") or "screencap failed"}
        return {"ok": True, "screenshot_b64": base64.b64encode(png).decode()}

    # ── hardware: torch, vibrate, tts, notify ────────────────────────
    @reg.skill("phone.torch", "Turn flashlight on/off.",
               params_schema={"on": "bool"}, owner="phone")
    async def torch(router, params):
        if not HAS_TERMUX_API:
            return {"ok": False, "error": "termux-api not installed"}
        on = bool(params.get("on", True))
        r = await _sh(["termux-torch", "on" if on else "off"], timeout=5)
        return {"ok": r["exit_code"] == 0, "on": on, **r}

    @reg.skill("phone.vibrate", "Vibrate for N ms.",
               params_schema={"ms": "int=500"}, owner="phone")
    async def vibrate(router, params):
        if not HAS_TERMUX_API:
            return {"ok": False, "error": "termux-api not installed"}
        r = await _sh(["termux-vibrate", "-d",
                       str(int(params.get("ms", 500)))], timeout=10)
        return {"ok": r["exit_code"] == 0, **r}

    @reg.skill("phone.tts",
               "Speak text aloud (Android TTS engine).",
               params_schema={"text": "str"}, owner="phone")
    async def tts(router, params):
        text = str(params.get("text", ""))
        if not text:
            return {"ok": False, "error": "missing text"}
        if HAS_TERMUX_API:
            r = await _sh(["termux-tts-speak", text], timeout=60)
            return {"ok": r["exit_code"] == 0, **r}
        return {"ok": False, "error": "termux-api not installed"}

    @reg.skill("phone.notify",
               "Post an Android notification.",
               params_schema={"title": "str", "content": "str",
                              "id": "int=9000"},
               owner="phone")
    async def notify(router, params):
        if not HAS_TERMUX_API:
            return {"ok": False, "error": "termux-api not installed"}
        args = ["termux-notification",
                "--id", str(int(params.get("id", 9000))),
                "--title", str(params.get("title", "JARVIS")),
                "--content", str(params.get("content", ""))]
        r = await _sh(args, timeout=10)
        return {"ok": r["exit_code"] == 0, **r}

    # ── volume ───────────────────────────────────────────────────────
    @reg.skill("phone.volume.set", "Set media volume (0-15).",
               params_schema={"level": "int 0-15"}, owner="phone")
    async def set_vol(router, params):
        level = max(0, min(15, int(params.get("level", 5))))
        if HAS_TERMUX_API:
            r = await _sh(["termux-volume", "music", str(level)], timeout=8)
            return {"ok": r["exit_code"] == 0, "level": level, **r}
        return {"ok": False, "error": "termux-api not installed"}

    # ── location ─────────────────────────────────────────────────────
    @reg.skill("phone.location", "Get current GPS location.",
               owner="phone")
    async def location(router, params):
        if not HAS_TERMUX_API:
            return {"ok": False, "error": "termux-api not installed"}
        r = await _sh(["termux-location", "-p", "gps"], timeout=30)
        if r["exit_code"] != 0:
            return {"ok": False, **r}
        try:
            return {"ok": True, "location": json.loads(r["stdout"])}
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad location json", **r}

    # ── lock / unlock ────────────────────────────────────────────────
    @reg.skill("phone.lock", "Lock the phone (press power).",
               owner="phone")
    async def lock_phone(router, params):
        r = await _sh(["input", "keyevent", "KEYCODE_POWER"], timeout=5)
        return {"ok": r["exit_code"] == 0, **r}

    log.info("registered %d phone skills", len(reg.all()))

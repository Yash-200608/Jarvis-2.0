"""Phone skills -- the 10 working Termux skill implementations, ported from
hugginggpt/server/jarvis_prod/agent_phone.py onto the node SDK (Priority #4
Milestone 5).

Ported faithfully -- same skill names, same params, same result shapes --
with one deliberate structural change: agent_phone.py hardcodes its `sh()`
subprocess helper and detects Termux at import time; here both are
injected via build_phone_skills(runner=..., have=...) so tests can fake
the Termux boundary and capability detection happens at build time, not
import time. Production callers use the defaults (real subprocesses, real
shutil.which).

Skills never raise: the runner returns {"exit_code": -1, ...} on every
failure mode (timeout, missing binary, anything), mirroring
agent_phone.py's sh() contract, and each skill maps that to
{"ok": False, ...} in its result.

NOT a general skill framework: exactly the 10 proven phone.* skills, a
dict from name to coroutine. Registration into a SkillRegistry (M3) and
dispatch (command_handler.dispatch_command) are the caller's wiring.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import urllib.parse
from typing import Any, Awaitable, Callable

SkillFn = Callable[[dict], Awaitable[dict]]
Runner = Callable[..., Awaitable[dict]]


async def sh(*argv: str, timeout: float = 15.0) -> dict[str, Any]:
    """Run `argv` non-blocking. Never raises -- always returns a dict with
    exit_code/stdout/stderr (exit_code -1 on timeout/missing binary/any
    error). Same contract as agent_phone.py's sh()."""
    try:
        async with asyncio.timeout(timeout):
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            return {
                "exit_code": proc.returncode,
                "stdout": out.decode("utf-8", errors="replace"),
                "stderr": err.decode("utf-8", errors="replace"),
            }
    except TimeoutError:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s"}
    except FileNotFoundError as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"not found: {e}"}
    except Exception as e:  # noqa: BLE001 -- the never-raise contract is the point
        return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


def _default_have(binary: str) -> bool:
    return shutil.which(binary) is not None


def build_phone_skills(
    runner: Runner = sh, have: Callable[[str], bool] = _default_have
) -> dict[str, SkillFn]:
    """Builds the phone.* skill table, closing over `runner` (the shell
    boundary) and a Termux capability check evaluated once at build time
    (mirrors agent_phone.py's import-time HAS_TERMUX detection)."""
    has_termux = have("termux-sms-send")

    async def skill_battery(params: dict) -> dict:
        if has_termux:
            r = await runner("termux-battery-status", timeout=10)
            if r["exit_code"] == 0:
                try:
                    info = json.loads(r["stdout"])
                    return {
                        "ok": True,
                        "battery": {
                            "level": info.get("percentage"),
                            "charging": info.get("status", "").upper() == "CHARGING",
                            "health": info.get("health"),
                            "temp": info.get("temperature"),
                        },
                    }
                except json.JSONDecodeError:
                    pass
        # Fallback: dumpsys (works on plain adb/Android shells without termux-api)
        r = await runner("dumpsys", "battery", timeout=10)
        info = {}
        for line in r["stdout"].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip().lower()] = v.strip()
        level = info.get("level")
        return {
            "ok": r["exit_code"] == 0,
            "battery": {
                "level": int(level) if level and level.isdigit() else None,
                "charging": any(
                    info.get(k, "false").lower() == "true"
                    for k in ("ac powered", "usb powered")
                ),
            },
        }

    async def skill_tts(params: dict) -> dict:
        text = str(params.get("text", "")).strip()
        if not text:
            return {"ok": False, "error": "missing text"}
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-tts-speak", text, timeout=60)
        return {"ok": r["exit_code"] == 0, **r}

    async def skill_sms_send(params: dict) -> dict:
        phone = str(params.get("phone", "")).strip()
        message = str(params.get("message", "")).strip()
        if not phone or not message:
            return {"ok": False, "error": "phone + message required"}
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-sms-send", "-n", phone, message, timeout=20)
        return {"ok": r["exit_code"] == 0, "phone": phone, **r}

    async def skill_torch(params: dict) -> dict:
        on = bool(params.get("on", True))
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-torch", "on" if on else "off", timeout=5)
        return {"ok": r["exit_code"] == 0, "on": on, **r}

    async def skill_vibrate(params: dict) -> dict:
        ms = int(params.get("ms", 500))
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-vibrate", "-d", str(ms), timeout=max(10, ms // 1000 + 3))
        return {"ok": r["exit_code"] == 0, "ms": ms, **r}

    async def skill_notify(params: dict) -> dict:
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner(
            "termux-notification",
            "--id", str(int(params.get("id", 9000))),
            "--title", str(params.get("title", "JARVIS")),
            "--content", str(params.get("content", "")),
            timeout=10,
        )
        return {"ok": r["exit_code"] == 0, **r}

    async def skill_ring(params: dict) -> dict:
        """Find-my-phone: flash, vibrate, and loud TTS for N seconds."""
        seconds = max(1, min(60, int(params.get("seconds", 10))))
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        await runner("termux-volume", "music", "15", timeout=5)
        await runner("termux-torch", "on", timeout=3)

        async def _flasher():
            for _ in range(seconds):
                await runner("termux-vibrate", "-d", "400", timeout=3)
                await asyncio.sleep(0.5)

        async def _talker():
            for _ in range(seconds):
                await runner(
                    "termux-tts-speak", "-r", "0.9", "-p", "1.3",
                    "Jarvis locator ping", timeout=4,
                )
                await asyncio.sleep(0.2)

        try:
            async with asyncio.timeout(seconds + 5):
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(_flasher())
                    tg.create_task(_talker())
        finally:
            await runner("termux-torch", "off", timeout=3)
        return {"ok": True, "seconds": seconds}

    async def skill_location(params: dict) -> dict:
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-location", "-p", "gps", timeout=30)
        if r["exit_code"] != 0:
            return {"ok": False, **r}
        try:
            return {"ok": True, "location": json.loads(r["stdout"])}
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad json from termux-location", **r}

    async def skill_open_app(params: dict) -> dict:
        app = str(params.get("app", "")).strip()
        if not app:
            return {"ok": False, "error": "missing app"}
        PKGS = {
            "whatsapp": "com.whatsapp", "chrome": "com.android.chrome",
            "youtube": "com.google.android.youtube",
            "gmail": "com.google.android.gm", "spotify": "com.spotify.music",
            "instagram": "com.instagram.android", "settings": "com.android.settings",
            "maps": "com.google.android.apps.maps",
        }
        pkg = PKGS.get(app.lower(), app)

        def _launch_ok(result: dict) -> bool:
            if result.get("exit_code") == 0:
                return True
            combined = (result.get("stdout") or "") + (result.get("stderr") or "")
            return "Starting: Intent" in combined or "Status: ok" in combined

        # am start works from proot Ubuntu; monkey is often absent there.
        r = await runner(
            "am", "start",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-p", pkg,
            timeout=15,
        )
        if not _launch_ok(r):
            resolved = await runner(
                "cmd", "package", "resolve-activity", "--brief",
                "-c", "android.intent.category.LAUNCHER", pkg,
                timeout=10,
            )
            lines = [
                ln.strip()
                for ln in (resolved.get("stdout") or "").splitlines()
                if ln.strip() and "/" in ln
            ]
            if lines:
                r = await runner("am", "start", "-n", lines[-1], timeout=15)
        if not _launch_ok(r) and have("monkey"):
            r = await runner(
                "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1",
                timeout=15,
            )
        return {"ok": _launch_ok(r), "package": pkg, **r}

    async def skill_whatsapp_send(params: dict) -> dict:
        phone = re.sub(r"[^\d+]", "", str(params.get("phone", "")))
        msg = str(params.get("message", ""))
        if not phone or not msg:
            return {"ok": False, "error": "phone + message required"}
        url = f"https://api.whatsapp.com/send?phone={phone}&text={urllib.parse.quote(msg)}"
        r = await runner(
            "am", "start", "-a", "android.intent.action.VIEW", "-d", url, timeout=15
        )
        return {"ok": r["exit_code"] == 0, **r,
                "note": "composer opened — user must tap send"}

    return {
        "phone.battery": skill_battery,
        "phone.tts": skill_tts,
        "phone.sms.send": skill_sms_send,
        "phone.torch": skill_torch,
        "phone.vibrate": skill_vibrate,
        "phone.notify": skill_notify,
        "phone.ring": skill_ring,
        "phone.location": skill_location,
        "phone.app.open": skill_open_app,
        "phone.whatsapp.send": skill_whatsapp_send,
    }

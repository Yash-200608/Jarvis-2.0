"""Skills that run on the PC brain.

Hard deps are imported lazily so a skill is skipped (with a warning) if
its backend is missing on this machine — that way one broken import
can't kill the whole brain process.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Optional

from .skills import SkillRegistry

log = logging.getLogger("jarvis.pc_skills")

# ── lazy backend imports ─────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    HAS_PYAUTOGUI = True
except Exception as e:
    HAS_PYAUTOGUI = False
    log.warning("pyautogui unavailable: %s", e)

try:
    import psutil
    HAS_PSUTIL = True
except Exception as e:
    HAS_PSUTIL = False
    log.warning("psutil unavailable: %s", e)

# Pycaw: Windows-only audio control
try:
    if platform.system() == "Windows":
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        HAS_PYCAW = True
    else:
        HAS_PYCAW = False
except Exception as e:
    HAS_PYCAW = False
    log.warning("pycaw unavailable: %s", e)


async def _run_shell(cmd: str, timeout: int = 15) -> dict:
    """Run a shell command without blocking the loop."""
    def _do():
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True,
                               timeout=timeout)
            return {
                "exit_code": p.returncode,
                "stdout": p.stdout.decode("utf-8", errors="replace"),
                "stderr": p.stderr.decode("utf-8", errors="replace"),
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s"}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
    return await asyncio.get_event_loop().run_in_executor(None, _do)


def _get_master_volume() -> Optional[IAudioEndpointVolume]:
    if not HAS_PYCAW:
        return None
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_,
                                 CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def register_pc_skills(reg: SkillRegistry):
    """Populate the given registry with everything the PC can do."""

    # ── media / audio ────────────────────────────────────────────────
    @reg.skill("pc.volume.set",
               "Set master PC volume (0-100).",
               params_schema={"level": "int 0-100"},
               owner="pc")
    async def set_volume(router, params):
        level = max(0, min(100, int(params.get("level", 50))))
        vol = _get_master_volume()
        if vol is None:
            return {"ok": False, "error": "pycaw not available"}
        vol.SetMasterVolumeLevelScalar(level / 100.0, None)
        return {"ok": True, "level": level}

    @reg.skill("pc.volume.mute",
               "Mute or unmute PC master audio.",
               params_schema={"muted": "bool"},
               owner="pc")
    async def mute(router, params):
        muted = bool(params.get("muted", True))
        vol = _get_master_volume()
        if vol is None:
            return {"ok": False, "error": "pycaw not available"}
        vol.SetMute(1 if muted else 0, None)
        return {"ok": True, "muted": muted}

    @reg.skill("pc.media.control",
               "Media playback control: play_pause|next|previous|stop.",
               params_schema={"command": "str"},
               owner="pc")
    async def media_control(router, params):
        KEY_MAP = {
            "play_pause": "playpause", "play": "playpause", "pause": "playpause",
            "next": "nexttrack", "previous": "prevtrack",
            "stop": "stop", "volume_up": "volumeup", "volume_down": "volumedown",
            "mute": "volumemute",
        }
        cmd = str(params.get("command", "play_pause")).lower()
        key = KEY_MAP.get(cmd)
        if key is None or not HAS_PYAUTOGUI:
            return {"ok": False, "error": f"unknown media cmd or pyautogui missing: {cmd}"}
        pyautogui.press(key)
        return {"ok": True, "command": cmd}

    # ── input: keyboard + mouse ──────────────────────────────────────
    @reg.skill("pc.type",
               "Type text at current cursor focus.",
               params_schema={"text": "str"}, owner="pc")
    async def type_text(router, params):
        text = str(params.get("text", ""))
        if not HAS_PYAUTOGUI:
            return {"ok": False, "error": "pyautogui missing"}
        pyautogui.typewrite(text, interval=0.01)
        return {"ok": True, "chars": len(text)}

    @reg.skill("pc.hotkey",
               "Press a key combination. params.keys = ['ctrl','shift','t'].",
               params_schema={"keys": "list[str]"}, owner="pc")
    async def hotkey(router, params):
        keys = params.get("keys") or []
        if not keys or not HAS_PYAUTOGUI:
            return {"ok": False, "error": "no keys or pyautogui missing"}
        pyautogui.hotkey(*keys)
        return {"ok": True, "keys": keys}

    @reg.skill("pc.click",
               "Click at coordinates or at current mouse position.",
               params_schema={"x": "int?", "y": "int?",
                              "button": "left|right|middle"}, owner="pc")
    async def click(router, params):
        x, y = params.get("x"), params.get("y")
        btn = str(params.get("button", "left"))
        if not HAS_PYAUTOGUI:
            return {"ok": False, "error": "pyautogui missing"}
        if x is not None and y is not None:
            pyautogui.click(int(x), int(y), button=btn)
        else:
            pyautogui.click(button=btn)
        return {"ok": True}

    # ── screen ───────────────────────────────────────────────────────
    @reg.skill("pc.screenshot",
               "Take a full-screen screenshot, return PNG base64.",
               owner="pc")
    async def screenshot(router, params):
        if not HAS_PYAUTOGUI:
            return {"ok": False, "error": "pyautogui missing"}
        def _shot():
            img = pyautogui.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()
        b64 = await asyncio.get_event_loop().run_in_executor(None, _shot)
        return {"ok": True, "screenshot_b64": b64}

    # ── apps ─────────────────────────────────────────────────────────
    @reg.skill("pc.app.open",
               "Launch an application by name or path.",
               params_schema={"app": "str"}, owner="pc")
    async def open_app(router, params):
        app = str(params.get("app", ""))
        if not app:
            return {"ok": False, "error": "missing app"}
        if platform.system() == "Windows":
            os.startfile(app) if os.path.exists(app) else await _run_shell(f'start "" "{app}"')
        elif platform.system() == "Darwin":
            await _run_shell(f"open -a {app!r}")
        else:
            await _run_shell(f"{app!r} &")
        return {"ok": True, "app": app}

    @reg.skill("pc.url.open",
               "Open a URL in the default browser.",
               params_schema={"url": "str"}, owner="pc")
    async def open_url(router, params):
        url = str(params.get("url", ""))
        if not url:
            return {"ok": False, "error": "missing url"}
        webbrowser.open(url)
        return {"ok": True, "url": url}

    @reg.skill("pc.browser.list_tabs",
               "List open Chrome tabs (titles + URLs).",
               owner="pc")
    async def list_tabs(router, params):
        # Walks Chrome's remote debugging port if enabled, else reports
        # the user needs to start Chrome with --remote-debugging-port=9222.
        try:
            import urllib.request
            data = urllib.request.urlopen(
                "http://127.0.0.1:9222/json", timeout=2).read()
            import json as _j
            tabs = _j.loads(data)
            out = [{"title": t.get("title"), "url": t.get("url")}
                   for t in tabs if t.get("type") == "page"]
            return {"ok": True, "count": len(out), "tabs": out}
        except Exception as e:
            return {"ok": False,
                    "error": ("Chrome not running with --remote-debugging-port=9222; "
                              f"detail: {e}")}

    # ── processes ────────────────────────────────────────────────────
    @reg.skill("pc.process.list",
               "List top-level processes sorted by CPU.",
               owner="pc")
    async def list_procs(router, params):
        if not HAS_PSUTIL:
            return {"ok": False, "error": "psutil missing"}
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent",
                                      "memory_percent"]):
            try:
                procs.append(p.info)
            except Exception:
                continue
        procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
        return {"ok": True, "count": len(procs), "top": procs[:30]}

    @reg.skill("pc.process.kill",
               "Kill a process by name (e.g. chrome.exe).",
               params_schema={"name": "str"}, owner="pc")
    async def kill_proc(router, params):
        if not HAS_PSUTIL:
            return {"ok": False, "error": "psutil missing"}
        target = str(params.get("name", "")).lower()
        if not target:
            return {"ok": False, "error": "missing name"}
        killed = 0
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() == target:
                try:
                    p.kill(); killed += 1
                except Exception:
                    pass
        return {"ok": killed > 0, "killed": killed, "name": target}

    # ── system ───────────────────────────────────────────────────────
    @reg.skill("pc.system.info",
               "Basic system info (OS, CPU, memory).",
               owner="pc")
    async def sys_info(router, params):
        info = {
            "os": platform.system(), "release": platform.release(),
            "cpu_count": os.cpu_count(),
        }
        if HAS_PSUTIL:
            vm = psutil.virtual_memory()
            info.update({
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "mem_percent": vm.percent,
                "mem_total_gb": round(vm.total / 1e9, 1),
            })
        return {"ok": True, **info}

    @reg.skill("pc.system.lock",
               "Lock the PC (Windows+L equivalent).",
               owner="pc")
    async def lock_pc(router, params):
        sys_ = platform.system()
        if sys_ == "Windows":
            await _run_shell("rundll32.exe user32.dll,LockWorkStation")
        elif sys_ == "Darwin":
            await _run_shell("pmset displaysleepnow")
        else:
            await _run_shell("xdg-screensaver lock")
        return {"ok": True}

    @reg.skill("pc.system.shutdown",
               "Shut down the PC immediately.",
               owner="pc")
    async def shutdown_pc(router, params):
        sys_ = platform.system()
        if sys_ == "Windows":
            await _run_shell("shutdown /s /t 0")
        elif sys_ == "Darwin":
            await _run_shell("sudo shutdown -h now")
        else:
            await _run_shell("systemctl poweroff")
        return {"ok": True}

    @reg.skill("pc.shell.run",
               "Run any shell command. params.command is executed verbatim.",
               params_schema={"command": "str", "timeout": "int=30"},
               owner="pc")
    async def shell_run(router, params):
        cmd = str(params.get("command", ""))
        if not cmd:
            return {"ok": False, "error": "missing command"}
        out = await _run_shell(cmd, timeout=int(params.get("timeout", 30)))
        return {"ok": out["exit_code"] == 0, **out}

    # ── cross-node convenience ───────────────────────────────────────
    @reg.skill("pc.wake_phone",
               "Send a ring command to the phone node.",
               params_schema={"phone_node": "str", "seconds": "int=10"},
               owner="pc")
    async def wake_phone(router, params):
        resp = await router.issue_command(
            to=str(params.get("phone_node", "ian-phone")),
            skill="phone.ring",
            params={"seconds": int(params.get("seconds", 10))},
            timeout=15.0,
        )
        return {"ok": resp.ok, "phone_response": resp.result, "error": resp.error}

    log.info("registered %d PC skills", len(reg.all()))

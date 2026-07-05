"""PC skills -- the minimal pc.* subset executing through root core/'s
safety kernel (Priority #4 Milestone 6). The kernel's formerly-stub
_dispatch() gets its first real handlers here.

Two independent safety layers, deliberately ordered (NP-8, ecosystem
architecture Section 6 invariant 5):

1. **Operator ceiling (Brain-immune).** A local JSON file, owned by
   whoever operates this node, sets `max_auto_risk` (default "medium")
   and `disabled_skills`. The ceiling is checked against the KERNEL'S OWN
   risk classification of the mapped action before the kernel is ever
   invoked. Nothing in any envelope -- signed or not, approved or not --
   can modify or bypass it: raising the ceiling requires editing a file
   on this machine. This is the "confirmation channel Brain cannot itself
   satisfy" for the highest tier: with the default ceiling, a high-risk
   action (pc.shell.run) is refused even when Brain authorized it.

2. **Kernel risk gate (approval-satisfiable).** Actions that clear the
   ceiling still pass through core.executor.execute_task() -- validation,
   risk-before-rewrite classification, fingerprint-bound two-phase
   confirmation for high risk, sandboxing, idempotency, undo. Brain's
   approval flow (Milestone 7) can satisfy THIS gate by setting
   params.confirmed -- but only for actions the operator's ceiling
   already allows.

Import note: `core` here is the JARVIS repo-root safety kernel, NOT
jarvis_core/core/ -- the two collide if jarvis_core/ is placed on
sys.path ahead of the repo root (a known, documented footgun in
docs/context/CURRENT_STATUS.md). The node SDK's callers put the repo
root on sys.path, where `core` resolves correctly; imports are kept
inside build_pc_skills() so merely importing the SDK never touches the
kernel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

SkillFn = Callable[[dict], Awaitable[dict]]
SyncRunner = Callable[..., dict]

logger = logging.getLogger("pc_skills")

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# Windows virtual-key codes sent via WScript.Shell SendKeys.
_MEDIA_KEYS = {
    "play_pause": 179,
    "next": 176,
    "previous": 177,
    "mute": 173,
    "volume_up": 175,
    "volume_down": 174,
}


@dataclass
class Ceilings:
    """Operator-configured node policy (NP-8). Loaded from a local JSON
    file; absent file or absent keys mean the fail-closed defaults below.
    An unrecognized max_auto_risk value degrades to "low" (most
    restrictive), never to permissive."""

    max_auto_risk: str = "medium"
    disabled_skills: list[str] = field(default_factory=list)

    def allows(self, risk: str) -> bool:
        ceiling = _RISK_ORDER.get(self.max_auto_risk, 0)
        return _RISK_ORDER.get(risk, 2) <= ceiling


def load_ceilings(path: str | None) -> Ceilings:
    """Reads the operator ceiling file. No file (or no path) = defaults.
    A malformed file degrades to the DEFAULTS (max_auto_risk="medium"),
    logged loudly -- an operator's broken edit must not silently disable
    the node, but must also never grant more than the default."""
    if not path:
        return Ceilings()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        max_auto_risk = raw.get("max_auto_risk", "medium")
        if max_auto_risk not in _RISK_ORDER:
            logger.warning("ceiling file %r has invalid max_auto_risk %r -- using 'low'", path, max_auto_risk)
            max_auto_risk = "low"
        disabled = raw.get("disabled_skills", [])
        if not isinstance(disabled, list) or not all(isinstance(s, str) for s in disabled):
            logger.warning("ceiling file %r has malformed disabled_skills -- ignoring it", path)
            disabled = []
        return Ceilings(max_auto_risk=max_auto_risk, disabled_skills=disabled)
    except FileNotFoundError:
        return Ceilings()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("ceiling file %r unreadable (%s) -- using defaults", path, exc)
        return Ceilings()


def run_argv(argv: list[str] | str, *, shell: bool = False, timeout: float = 30.0) -> dict[str, Any]:
    """Synchronous shell boundary (the kernel is synchronous). Never
    raises -- same result contract as phone_skills.sh()."""
    try:
        proc = subprocess.run(
            argv,
            shell=shell,
            capture_output=True,
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s"}
    except FileNotFoundError as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"not found: {e}"}
    except Exception as e:  # noqa: BLE001 -- the never-raise contract is the point
        return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


def build_pc_skills(
    ceilings_path: str | None = None,
    runner: SyncRunner = run_argv,
    platform: str = sys.platform,
) -> dict[str, SkillFn]:
    """Builds the pc.* skill table, registers the real kernel dispatch
    handlers (core.executor.register_handler), and closes every skill over
    the operator ceilings loaded from `ceilings_path`.

    Ceilings are loaded once at build time -- changing the file requires a
    node restart to take effect, which is deliberate: policy changes
    should be an explicit operator act, not something a running process
    re-reads (and could be raced) mid-flight.
    """
    from core import executor as kernel  # repo-root safety kernel (see module docstring)
    from core import risk as kernel_risk

    ceilings = load_ceilings(ceilings_path)

    # -- real handlers behind the kernel's dispatch ------------------------
    def _handle_lock_device(action: dict) -> dict:
        if platform != "win32":
            return {"ok": False, "error": f"lock_device unsupported on {platform}"}
        r = runner(["rundll32.exe", "user32.dll,LockWorkStation"], timeout=10)
        return {"ok": r["exit_code"] == 0, **r}

    def _handle_media_control(action: dict) -> dict:
        command = action["params"]["command"]
        code = _MEDIA_KEYS.get(command)
        if code is None:
            return {"ok": False, "error": f"unknown media command {command!r}"}
        if platform != "win32":
            return {"ok": False, "error": f"media_control unsupported on {platform}"}
        ps = f"(New-Object -ComObject WScript.Shell).SendKeys([char]{code})"
        r = runner(["powershell", "-NoProfile", "-Command", ps], timeout=15)
        return {"ok": r["exit_code"] == 0, "command": command, **r}

    def _handle_run_command(action: dict) -> dict:
        r = runner(action["params"]["command"], shell=True, timeout=60)
        return {"ok": r["exit_code"] == 0, **r}

    kernel.register_handler("lock_device", _handle_lock_device)
    kernel.register_handler("media_control", _handle_media_control)
    kernel.register_handler("run_command", _handle_run_command)

    # -- skill layer: ceiling check, then kernel ---------------------------
    async def _through_kernel(skill: str, action_name: str, params: dict, confirmed: bool) -> dict:
        if skill in ceilings.disabled_skills:
            return {"ok": False, "refused_by": "local_ceiling", "error": f"{skill} disabled by operator"}

        action_risk = kernel_risk.get_risk_level(action_name)
        if not ceilings.allows(action_risk):
            return {
                "ok": False,
                "refused_by": "local_ceiling",
                "error": (
                    f"risk {action_risk!r} exceeds this node's operator-configured "
                    f"ceiling {ceilings.max_auto_risk!r}"
                ),
                "risk": action_risk,
            }

        command_id = uuid.uuid4().hex
        action = {"name": action_name, "params": params}

        if confirmed:
            approved = await asyncio.to_thread(
                kernel.execute_task, command_id, {**action, "confirmed": True}
            )
            if approved.get("status") != "approved":
                # Low/medium actions skip the approval phase entirely and
                # execute on the first call -- pass that result through.
                return _kernel_to_skill_result(approved)

        result = await asyncio.to_thread(kernel.execute_task, command_id, action)
        return _kernel_to_skill_result(result)

    def _kernel_to_skill_result(result: dict) -> dict:
        status = result.get("status")
        if status == "success":
            out = {"ok": True, "kernel": result.get("dispatch", {})}
            handler_result = result.get("dispatch", {}).get("result")
            if isinstance(handler_result, dict):
                out["ok"] = bool(handler_result.get("ok", True))
                out.update(handler_result)
            return out
        if status == "confirmation_required":
            return {"ok": False, "confirmation_required": True, "risk": result.get("risk")}
        return {"ok": False, "kernel_status": status, "error": result.get("reason", status)}

    async def skill_lock(params: dict) -> dict:
        return await _through_kernel("pc.system.lock", "lock_device", {}, False)

    async def skill_media_control(params: dict) -> dict:
        command = str(params.get("command", "")).strip()
        if not command:
            return {"ok": False, "error": "missing command"}
        return await _through_kernel(
            "pc.media.control", "media_control", {"command": command}, False
        )

    async def skill_shell_run(params: dict) -> dict:
        command = str(params.get("command", "")).strip()
        if not command:
            return {"ok": False, "error": "missing command"}
        confirmed = params.get("confirmed", False) is True
        return await _through_kernel(
            "pc.shell.run", "run_command", {"command": command}, confirmed
        )

    return {
        "pc.system.lock": skill_lock,
        "pc.media.control": skill_media_control,
        "pc.shell.run": skill_shell_run,
    }

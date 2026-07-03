"""Level-4 PC Executor.

Receives validated :class:`Plan` objects from the brain and dispatches each
tool call to either:

* a local subprocess in the chrooted sandbox (file IO, shell), or
* PyAutoGUI for desktop automation (best-effort — gracefully degrades when
  PyAutoGUI isn't installed, e.g. on a headless VM), or
* the MQTT bus for phone-targeted commands.

Every shell command is wrapped with the sandbox so even a malicious plan
that slipped past the Critic cannot escape ``./sandbox/``.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .config import JarvisConfig
from .logging_setup import get_logger
from jarvis_node_sdk.sandbox import SandboxViolation, safe_path
from jarvis_node_sdk.signing import sign_envelope
from jarvis_node_sdk.transport import MqttTransport

log = get_logger("EXECUTOR")


class ExecutionError(RuntimeError):
    pass


class Executor:
    """Resolves and runs a :class:`Plan`'s tool calls."""

    def __init__(self, cfg: JarvisConfig, bus: MqttTransport) -> None:
        self._cfg = cfg
        self._bus = bus

    # ------------------------------------------------------------------ #
    # Entrypoint                                                         #
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        target: str,
        tool: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a single tool call. Returns the executor's result dict."""
        log.info(f"target={target} tool={tool} payload={list(payload)}")
        if target.lower() == "phone":
            return await self._execute_phone(tool, payload)
        return await self._execute_pc(tool, payload)

    # ------------------------------------------------------------------ #
    # Phone leg — publish over MQTT and await the matching reply.        #
    # ------------------------------------------------------------------ #

    async def _execute_phone(
        self, tool: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        # request_id must be part of the payload *before* signing -- transport.py
        # injects request_id into the outgoing payload, and the receiver verifies
        # the HMAC over the full envelope it receives (request_id included). If
        # signing happens first, verification fails on every phone command.
        request_id = str(uuid.uuid4())
        envelope = sign_envelope(
            {"action": tool, "params": payload, "request_id": request_id},
            self._cfg.hmac_key,
        )
        try:
            return await self._bus.request(
                topic=self._cfg.mqtt.topics.phone_cmd,
                payload=envelope,
                reply_topic=self._cfg.mqtt.topics.phone_state,
                timeout=30.0,
            )
        except asyncio.TimeoutError as exc:
            raise ExecutionError(
                f"Phone did not respond to {tool!r} within 30s"
            ) from exc

    # ------------------------------------------------------------------ #
    # PC leg — local tool dispatch.                                      #
    # ------------------------------------------------------------------ #

    async def _execute_pc(
        self, tool: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        handler = _PC_TOOLS.get(tool)
        if handler is None:
            raise ExecutionError(f"Unknown PC tool: {tool!r}")
        return await handler(self, payload)

    # ------------------------------------------------------------------ #
    # Tool implementations                                               #
    # ------------------------------------------------------------------ #

    async def _tool_write_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = safe_path(self._cfg.sandbox.root, params["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(params.get("content", ""), encoding="utf-8")
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size}

    async def _tool_read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path = safe_path(self._cfg.sandbox.root, params["path"])
        if not path.is_file():
            raise ExecutionError(f"No such file: {path}")
        return {"ok": True, "path": str(path), "content": path.read_text("utf-8")}

    async def _tool_list_dir(self, params: dict[str, Any]) -> dict[str, Any]:
        path = safe_path(self._cfg.sandbox.root, params.get("path", "."))
        if not path.is_dir():
            raise ExecutionError(f"Not a directory: {path}")
        return {"ok": True, "entries": [p.name for p in path.iterdir()]}

    async def _tool_run_shell(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run a shell command with cwd locked to the sandbox.

        We deliberately disallow ``cd`` and ``;`` chains by using shlex.split
        and a frozen cwd. Anything trying to escape is caught by the sandbox
        path resolver.
        """
        cmd = params["cmd"]
        if isinstance(cmd, str):
            argv = shlex.split(cmd)
        else:
            argv = list(cmd)
        cwd = safe_path(self._cfg.sandbox.root, params.get("cwd", "."))
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "HOME": str(cwd)},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            raise ExecutionError("Shell command timed out after 30s")
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
        }

    async def _tool_pyautogui(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Best-effort GUI automation — only used after Critic approval."""
        try:
            import pyautogui  # noqa: WPS433
        except Exception as exc:  # noqa: BLE001
            raise ExecutionError(
                "pyautogui not installed on this host"
            ) from exc

        action = params["action"]
        if action == "type":
            pyautogui.typewrite(params.get("text", ""), interval=0.02)
        elif action == "hotkey":
            pyautogui.hotkey(*params.get("keys", []))
        elif action == "click":
            pyautogui.click(params.get("x", 0), params.get("y", 0))
        elif action == "screenshot":
            out = safe_path(
                self._cfg.sandbox.root, params.get("path", "screenshot.png")
            )
            pyautogui.screenshot(str(out))
            return {"ok": True, "path": str(out)}
        else:
            raise ExecutionError(f"Unsupported pyautogui action: {action!r}")
        return {"ok": True}


# Lookup table — keep at module scope so :func:`_execute_pc` is a one-liner.
_PC_TOOLS = {
    "write_file": Executor._tool_write_file,
    "read_file": Executor._tool_read_file,
    "list_dir": Executor._tool_list_dir,
    "run_shell": Executor._tool_run_shell,
    "pyautogui": Executor._tool_pyautogui,
}

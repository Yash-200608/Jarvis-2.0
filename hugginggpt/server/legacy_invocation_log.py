"""Legacy-stack invocation log for Priority #4 M11 trial adoption measurement.

Every device command executed through awesome_chat's DeviceOrchestrator is
append-only recorded here. Brain's trial report reads this file to prove
zero legacy use for covered command classes during the trial window
(PRIORITY-4_DEFINITION.md §3.6(2)(a)).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Maps legacy bridge target+action to keystone demo command classes.
_LEGACY_TO_CLASS: dict[tuple[str, str], str] = {
    ("phone", "get_battery"): "phone.battery",
    ("phone", "send_whatsapp"): "phone.whatsapp.send",
    ("phone", "send_sms"): "phone.sms.send",
    ("phone", "open_app"): "phone.app.open",
    ("computer", "lock_device"): "pc.system.lock",
    ("computer", "set_volume"): "pc.media.control",
    ("computer", "volume_up"): "pc.media.control",
    ("computer", "volume_down"): "pc.media.control",
    ("computer", "mute"): "pc.media.control",
    ("computer", "play_pause"): "pc.media.control",
    ("computer", "media_play_pause"): "pc.media.control",
}

_DEFAULT_LOG = Path.home() / ".chimera" / "legacy_invocations.jsonl"


def _log_path() -> Path:
    raw = os.environ.get("CHIMERA_LEGACY_INVOCATION_LOG")
    return Path(raw) if raw else _DEFAULT_LOG


def map_legacy_command_class(target: str | None, action: str | None) -> str | None:
    if not target or not action:
        return None
    return _LEGACY_TO_CLASS.get((target.lower(), action.lower()))


def record_legacy_invocation(
    command: dict,
    result: dict | None = None,
    *,
    source: str = "awesome_chat",
) -> None:
    target = command.get("target")
    action = command.get("action")
    row = {
        "ts": time.time(),
        "source": source,
        "target": target,
        "action": action,
        "params": command.get("params") or {},
        "command_class": map_legacy_command_class(target, action),
        "ok": bool(result and result.get("ok")),
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

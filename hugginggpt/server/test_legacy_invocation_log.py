"""Tests for legacy invocation log (M11 trial instrumentation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from legacy_invocation_log import map_legacy_command_class, record_legacy_invocation


def test_map_legacy_command_class() -> None:
    assert map_legacy_command_class("phone", "get_battery") == "phone.battery"
    assert map_legacy_command_class("computer", "lock_device") == "pc.system.lock"
    assert map_legacy_command_class("phone", "take_screenshot") is None


def test_record_legacy_invocation(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("CHIMERA_LEGACY_INVOCATION_LOG", str(log))
    record_legacy_invocation(
        {"target": "phone", "action": "get_battery", "params": {}},
        {"ok": True},
    )
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["command_class"] == "phone.battery"
    assert row["ok"] is True

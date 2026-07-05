"""PC skill tests (Priority #4 Milestone 6) -- the pc.* subset executing
through root core/'s REAL safety kernel (not a mock of it), with the shell
boundary faked. The kernel-refusal tests here are the milestone's
completion criterion (NP-8): a command Brain fully authorized is refused
by the node's operator-configured ceiling.

Note: `core` in this suite is the repo-root safety kernel (repo root is on
sys.path via conftest.py); jarvis_core/ is not on this suite's path, so
the documented core-name collision cannot bite here.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core import config as kernel_config
from core import executor as kernel_executor
from jarvis_node_sdk.pc_skills import Ceilings, build_pc_skills, load_ceilings


@pytest.fixture(autouse=True)
def _clean_kernel_state():
    saved_handlers = dict(kernel_executor.DISPATCH_HANDLERS)
    saved_mode = kernel_config.get_execution_mode()
    kernel_config.set_execution_mode("execute")
    yield
    kernel_executor.DISPATCH_HANDLERS.clear()
    kernel_executor.DISPATCH_HANDLERS.update(saved_handlers)
    kernel_config.set_execution_mode(saved_mode)


def _fake_runner(default: dict | None = None):
    calls: list = []
    result = default or {"exit_code": 0, "stdout": "", "stderr": ""}

    def runner(argv, *, shell=False, timeout=30.0):
        calls.append({"argv": argv, "shell": shell})
        return dict(result)

    return runner, calls


# ---------------------------------------------------------------------- #
# Ceilings                                                                 #
# ---------------------------------------------------------------------- #


def test_load_ceilings_no_path_gives_defaults() -> None:
    c = load_ceilings(None)
    assert c.max_auto_risk == "medium"
    assert c.disabled_skills == []


def test_load_ceilings_missing_file_gives_defaults(tmp_path) -> None:
    c = load_ceilings(str(tmp_path / "does-not-exist.json"))
    assert c.max_auto_risk == "medium"


def test_load_ceilings_reads_operator_file(tmp_path) -> None:
    p = tmp_path / "ceilings.json"
    p.write_text(json.dumps({"max_auto_risk": "high", "disabled_skills": ["pc.system.lock"]}))
    c = load_ceilings(str(p))
    assert c.max_auto_risk == "high"
    assert c.disabled_skills == ["pc.system.lock"]


def test_load_ceilings_invalid_risk_degrades_to_low_not_permissive(tmp_path) -> None:
    p = tmp_path / "ceilings.json"
    p.write_text(json.dumps({"max_auto_risk": "yolo"}))
    c = load_ceilings(str(p))
    assert c.max_auto_risk == "low"


def test_ceilings_allows_is_fail_closed_for_unknown_risk() -> None:
    assert Ceilings(max_auto_risk="medium").allows("weird-future-tier") is False


# ---------------------------------------------------------------------- #
# NP-8: the operator ceiling refuses what Brain authorized                 #
# ---------------------------------------------------------------------- #


def test_shell_run_refused_by_default_ceiling_before_kernel_runs() -> None:
    """THE milestone completion criterion: pc.shell.run maps to the
    kernel-classified high-risk run_command; the default operator ceiling
    is medium; the skill refuses -- and the shell boundary proves the
    kernel handler never even fired. No envelope content (params,
    confirmed, anything) can change this outcome."""
    runner, calls = _fake_runner()
    skills = build_pc_skills(ceilings_path=None, runner=runner)

    result = asyncio.run(skills["pc.shell.run"]({"command": "echo hi", "confirmed": True}))

    assert result["ok"] is False
    assert result["refused_by"] == "local_ceiling"
    assert result["risk"] == "high"
    assert calls == []  # nothing executed, kernel handler untouched


def test_disabled_skill_refused_even_at_low_risk(tmp_path) -> None:
    p = tmp_path / "ceilings.json"
    p.write_text(json.dumps({"disabled_skills": ["pc.system.lock"]}))
    runner, calls = _fake_runner()
    skills = build_pc_skills(ceilings_path=str(p), runner=runner)

    result = asyncio.run(skills["pc.system.lock"]({}))

    assert result["ok"] is False
    assert result["refused_by"] == "local_ceiling"
    assert calls == []


def test_raised_ceiling_allows_shell_run_via_kernel_two_phase(tmp_path) -> None:
    """With the operator having raised the ceiling to high, pc.shell.run
    with confirmed=True passes the kernel's two-phase approval and the
    real handler runs the command through the (faked) shell."""
    p = tmp_path / "ceilings.json"
    p.write_text(json.dumps({"max_auto_risk": "high"}))
    runner, calls = _fake_runner({"exit_code": 0, "stdout": "hi\n", "stderr": ""})
    skills = build_pc_skills(ceilings_path=str(p), runner=runner)

    result = asyncio.run(skills["pc.shell.run"]({"command": "echo hi", "confirmed": True}))

    assert result["ok"] is True
    assert result["stdout"] == "hi\n"
    assert calls[0] == {"argv": "echo hi", "shell": True}


def test_raised_ceiling_without_confirmation_still_gated_by_kernel(tmp_path) -> None:
    """Ceiling high + no confirmed: the kernel's own fingerprint gate is
    the second layer -- confirmation_required, handler never fires."""
    p = tmp_path / "ceilings.json"
    p.write_text(json.dumps({"max_auto_risk": "high"}))
    runner, calls = _fake_runner()
    skills = build_pc_skills(ceilings_path=str(p), runner=runner)

    result = asyncio.run(skills["pc.shell.run"]({"command": "echo hi"}))

    assert result["ok"] is False
    assert result["confirmation_required"] is True
    assert calls == []


# ---------------------------------------------------------------------- #
# Happy paths through the real kernel                                      #
# ---------------------------------------------------------------------- #


def test_lock_executes_through_kernel_on_windows() -> None:
    runner, calls = _fake_runner()
    skills = build_pc_skills(runner=runner, platform="win32")

    result = asyncio.run(skills["pc.system.lock"]({}))

    assert result["ok"] is True
    assert calls[0]["argv"] == ["rundll32.exe", "user32.dll,LockWorkStation"]


def test_lock_unsupported_platform_is_truthful_failure() -> None:
    runner, calls = _fake_runner()
    skills = build_pc_skills(runner=runner, platform="linux")

    result = asyncio.run(skills["pc.system.lock"]({}))

    assert result["ok"] is False
    assert "unsupported" in result["error"]


def test_media_control_sends_the_right_key() -> None:
    runner, calls = _fake_runner()
    skills = build_pc_skills(runner=runner, platform="win32")

    result = asyncio.run(skills["pc.media.control"]({"command": "volume_up"}))

    assert result["ok"] is True
    ps = calls[0]["argv"][-1]
    assert "[char]175" in ps


def test_media_control_unknown_command_rejected() -> None:
    runner, calls = _fake_runner()
    skills = build_pc_skills(runner=runner, platform="win32")

    result = asyncio.run(skills["pc.media.control"]({"command": "explode"}))

    assert result["ok"] is False
    assert calls == []  # rejected inside the handler before any shell call


def test_all_three_skills_present() -> None:
    runner, _ = _fake_runner()
    skills = build_pc_skills(runner=runner)
    assert sorted(skills) == ["pc.media.control", "pc.shell.run", "pc.system.lock"]

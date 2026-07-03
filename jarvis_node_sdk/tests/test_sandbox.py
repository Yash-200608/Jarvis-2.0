"""Sandbox resolver unit tests.

Focused on the one thing we absolutely cannot get wrong — path escape.
Run with ``pytest jarvis_node_sdk/tests/test_sandbox.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_node_sdk.sandbox import SandboxViolation, is_inside_sandbox, safe_path


def test_simple_relative(tmp_path: Path) -> None:
    out = safe_path(tmp_path, "foo/bar.txt")
    assert str(out).startswith(str(tmp_path.resolve()))


def test_parent_traversal_blocked(tmp_path: Path) -> None:
    with pytest.raises(SandboxViolation):
        safe_path(tmp_path, "../../etc/passwd")


def test_absolute_path_remapped(tmp_path: Path) -> None:
    """Absolute paths get re-rooted inside the sandbox, not honoured."""
    out = safe_path(tmp_path, "/etc/passwd")
    assert out.is_relative_to(tmp_path.resolve())


def test_symlink_escape_blocked(tmp_path: Path) -> None:
    # Create a sibling "outside" dir alongside the sandbox so the test
    # never touches the real filesystem (and pytest can clean up cleanly).
    sandbox = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    sandbox.mkdir()
    outside.mkdir()
    (outside / "passwd").write_text("root:x:0:0::/root:/bin/sh")

    link = sandbox / "escape"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    try:
        with pytest.raises(SandboxViolation):
            safe_path(sandbox, "escape/passwd")
    finally:
        # Unlink before pytest tears down so the resolver never recurses
        # through the symlink chain again during cleanup.
        link.unlink(missing_ok=True)


def test_is_inside_sandbox_matches(tmp_path: Path) -> None:
    assert is_inside_sandbox(tmp_path, "ok/file.txt") is True
    assert is_inside_sandbox(tmp_path, "../nope") is False

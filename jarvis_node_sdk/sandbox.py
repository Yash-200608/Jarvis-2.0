"""Filesystem sandbox enforcement.

Every executor file operation must be funnelled through :func:`safe_path`,
which resolves the requested path against the sandbox root and refuses
anything that would escape it (``..``, absolute paths, symlink traversal).

This is the *single chokepoint* the Critic relies on — the spec says
"No file operations permitted outside the defined ./sandbox/ directory."
"""

from __future__ import annotations

from pathlib import Path


class SandboxViolation(PermissionError):
    """Raised when a path resolves outside the sandbox root."""


def safe_path(sandbox_root: str | Path, requested: str | Path) -> Path:
    """Return an absolute :class:`Path` guaranteed to live inside the sandbox.

    Behaviour:
        * Treats absolute paths as relative to the sandbox root, never the
          system root — so ``/etc/passwd`` becomes ``<sandbox>/etc/passwd``.
        * Resolves ``..`` segments before the sandbox check, defeating
          ``../../etc/passwd`` attacks.
        * Follows existing symlinks via :py:meth:`Path.resolve`, so a
          pre-planted symlink inside the sandbox can't be used to escape.
    """
    root = Path(sandbox_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    rel = Path(requested)
    if rel.is_absolute() or rel.root or rel.drive:
        # Strip root/drive anchors so POSIX-style and Windows absolute paths
        # land inside the sandbox on every platform.
        rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path()

    candidate = (root / rel).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SandboxViolation(
            f"Path {requested!r} escapes sandbox root {root!r}"
        ) from exc
    return candidate


def is_inside_sandbox(sandbox_root: str | Path, requested: str | Path) -> bool:
    """Boolean version of :func:`safe_path` for the Critic's quick checks."""
    try:
        safe_path(sandbox_root, requested)
    except SandboxViolation:
        return False
    return True

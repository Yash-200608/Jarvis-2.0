"""pytest bootstrap — jarvis_prod's modules import each other with bare
names (``from protocol import T``), matching how brain_pc.py/agent_phone.py
actually run in production, so the package directory itself needs to be on
sys.path (not just the repo root)."""

from __future__ import annotations

import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

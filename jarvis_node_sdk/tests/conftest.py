"""pytest bootstrap — make ``jarvis_node_sdk`` (and ``jarvis_core``, for the
signing round-trip tests that verify against phone_executor.py) importable
when tests run from the repo root."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # JARVIS/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

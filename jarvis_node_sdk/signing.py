"""HMAC-SHA256 command signing for Zero-Trust cross-device traffic.

The PC brain and the Termux phone executor share a 32-byte secret loaded from
``$JARVIS_HMAC_KEY``. Every command envelope is signed so neither side will
honour a message that was replayed from an adversarial client on the same
broker. The signature covers a canonical JSON rendering plus a nonce and
timestamp to defeat replay attacks.

Callers must include every field that should be covered by the signature --
including ``request_id`` -- in the payload passed to :func:`sign_envelope`
*before* calling it. ``jarvis_core/core/executor.py``'s ``_execute_phone()``
generates ``request_id`` up front for exactly this reason: signing after a
downstream layer (``transport.py``) mutates the payload produces an envelope
that the receiver's independent ``verify()`` can never validate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

# Maximum clock skew we tolerate between PC and phone (seconds).
MAX_SKEW_SECONDS = 120


def _canonical(payload: dict[str, Any]) -> bytes:
    """Stable JSON encoding so both sides hash the exact same bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_envelope(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``payload`` wrapped with ``nonce``, ``ts``, and ``sig`` fields."""
    if not key:
        raise ValueError("HMAC key is empty — set JARVIS_HMAC_KEY in .env")
    envelope = {
        **payload,
        "nonce": secrets.token_hex(16),
        "ts": int(time.time()),
    }
    sig = hmac.new(key.encode(), _canonical(envelope), hashlib.sha256).hexdigest()
    envelope["sig"] = sig
    return envelope


def verify_envelope(envelope: dict[str, Any], key: str) -> bool:
    """Validate signature + timestamp. Returns True on success."""
    if not key or "sig" not in envelope or "ts" not in envelope:
        return False
    provided = envelope.pop("sig")
    ts = envelope.get("ts", 0)
    if abs(time.time() - ts) > MAX_SKEW_SECONDS:
        return False
    expected = hmac.new(
        key.encode(), _canonical(envelope), hashlib.sha256
    ).hexdigest()
    # Restore sig so callers still have the full envelope.
    envelope["sig"] = provided
    return hmac.compare_digest(provided, expected)

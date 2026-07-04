# AUTO-GENERATED -- DO NOT EDIT.
#
# Vendored copy of Brain's canonical chimera wire contract. Brain
# (protocols/chimera_contract.py) is the single source of truth (see
# Brain/docs/DECISION_LOG.md ADR-013); JARVIS must not hand-edit this
# file. To pick up upstream changes, re-run from the Brain repo:
#
#     python scripts/sync_chimera_contract.py --jarvis-root <path-to-JARVIS>
#
# Source repo:      https://github.com/Yash-200608/Brain.git
# Source commit:    bbd74e9ea996065e4021eeb0043fbbadcd0b5240+dirty
# Contract version: 1
# Generated (UTC):  2026-07-04T09:54:56+00:00
# Content hash:     985bbfb659b6390e  (sha256 of source, first 16 hex chars)

"""ChimeraEnvelope — HMAC-signed east-west (Brain <-> node) wire contract.

Implements ADR-013 (docs/DECISION_LOG.md): the ``chimera/<node>/<verb>`` MQTT
topic namespace's message envelope, HMAC-signed with a nonce + timestamp skew
window mirroring the design already proven in JARVIS's
``jarvis_node_sdk/signing.py`` (formerly ``jarvis_core/core/signing.py``), so
both sides of the wire compute byte-identical signatures.

Distinct from :mod:`protocols.envelope` -- that module is the unsigned
north-south (client <-> Brain) contract used for WebSocket/HTTP traffic. This
module is a new, separate east-west (Brain <-> physical node) contract; do
not conflate the two or repurpose one for the other.

Brain is the CANONICAL source of this module. JARVIS consumes a generated,
do-not-edit vendored copy produced by ``scripts/sync_chimera_contract.py`` --
never hand-edited on the JARVIS side. See that script's docstring.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = 1

# Matches jarvis_node_sdk/signing.py's MAX_SKEW_SECONDS exactly -- both sides
# of the wire must agree on the tolerated clock skew.
MAX_SKEW_SECONDS = 120


class RiskTier(str, enum.Enum):
    """v1 risk taxonomy baseline (ADR-013), ported from JARVIS root
    ``core/risk.py``'s string tiers. Fail-closed: any unrecognized, missing,
    or malformed input must resolve to HIGH, never LOW or MEDIUM. Extending
    this enum later must preserve fail-closed-on-unknown for old consumers.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def from_value(cls, value: Any) -> "RiskTier":
        """Fail-closed coercion: any non-member value maps to HIGH."""
        if isinstance(value, RiskTier):
            return value
        if isinstance(value, str):
            try:
                return cls(value.lower())
            except ValueError:
                return cls.HIGH
        return cls.HIGH

    @classmethod
    def from_legacy_int(cls, value: int) -> "RiskTier":
        """Boundary helper only -- Brain's existing ``Task.risk`` /
        ``ApprovalRequested.risk`` int fields (0=safe/1=ask/2=block, per
        ``agents/planner.py``) are NOT rewritten to this enum; that
        representation is pervasive across ``core/task.py``,
        ``core/pipeline.py``, ``agents/planner.py``, and ``core/planning.py``,
        and rewriting it is out of scope for this milestone. Callers that
        need a :class:`RiskTier` from a Brain-internal int (e.g. a future
        bridge deciding whether a node command needs approval) call this at
        the boundary instead.
        """
        if value <= 0:
            return cls.LOW
        if value == 1:
            return cls.MEDIUM
        return cls.HIGH  # value >= 2, or anything unexpected -- fail closed

    def to_legacy_int(self) -> int:
        return {RiskTier.LOW: 0, RiskTier.MEDIUM: 1, RiskTier.HIGH: 2}[self]


class ChimeraEnvelope(BaseModel):
    """Signed east-west envelope for Brain <-> node (``chimera/<node>/<verb>``)
    traffic. Field set mirrors ``jarvis_node_sdk.signing``'s dict-based
    envelope plus a formal risk tier and the chimera/* verb the message
    rides on.
    """

    # Deliberately stricter than protocols.envelope.Envelope's extra="ignore":
    # this is a *signed* payload, so silently accepting unknown extra fields
    # is a way to smuggle unsigned data past validation that later code might
    # trust. Consistent with this project's fail-closed security posture
    # established in Priority #1.
    model_config = ConfigDict(extra="forbid")

    version: int = CONTRACT_VERSION
    node: str = Field(..., min_length=1)
    verb: str = Field(..., min_length=1)  # cmd|response|event|presence|state|heartbeat
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskTier = RiskTier.HIGH  # fail-closed default
    nonce: str = Field(default_factory=lambda: secrets.token_hex(16))
    ts: int = Field(default_factory=lambda: int(time.time()))
    # Optional correlation id (ADR-013's adopted message shape names
    # "correlation-id-based request/response matching"; Priority #3 shipped
    # without it as a deliberate, recorded deferral -- see
    # docs/audits/PRIORITY-3-EXECUTION-SPINE-CLOSURE.md item 2). Completed in
    # Priority #4 Milestone 2: a "cmd" envelope's request_id, when a
    # responder echoes it back on the matching "response", lets
    # mqtt/signed.py's send_command_and_await_response() correlate
    # concurrent overlapping commands to the same node+action safely.
    # Optional and additive (Section 8's shape-versioning rule) -- a
    # response with no request_id still correlates via the (verb, action)
    # fallback that was the sole matching mechanism before this field
    # existed.
    request_id: str | None = None
    sig: str = ""  # populated by sign(); empty until signed

    def signable_dict(self) -> dict[str, Any]:
        """Exact field set the HMAC covers -- everything except ``sig``
        itself. Must match ``verify()``'s reconstruction field-for-field."""
        return self.model_dump(exclude={"sig"})


def _canonical(payload: dict[str, Any]) -> bytes:
    """Byte-identical canonicalization to jarvis_node_sdk.signing._canonical."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign(envelope: ChimeraEnvelope, key: str) -> ChimeraEnvelope:
    """Return a copy of ``envelope`` with ``sig`` populated."""
    if not key:
        raise ValueError("HMAC key is empty")
    unsigned = envelope.model_copy(update={"sig": ""})
    sig = hmac.new(
        key.encode(), _canonical(unsigned.signable_dict()), hashlib.sha256
    ).hexdigest()
    return envelope.model_copy(update={"sig": sig})


def verify(envelope: ChimeraEnvelope, key: str) -> bool:
    """Validate signature + timestamp skew. Returns True on success."""
    if not key or not envelope.sig:
        return False
    if abs(time.time() - envelope.ts) > MAX_SKEW_SECONDS:
        return False
    expected = hmac.new(
        key.encode(), _canonical(envelope.signable_dict()), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(envelope.sig, expected)

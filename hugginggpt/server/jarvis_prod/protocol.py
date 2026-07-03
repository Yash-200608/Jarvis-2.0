"""JARVIS wire protocol.

One message envelope across both nodes. Every Command carries a unique
`id`; the matching Response echoes that `id` back — that's the req/res
pairing used by brain_pc.py to correlate outstanding futures.

Shared by agent_phone.py and brain_pc.py. Copy this file to both
machines verbatim — same code, same schema, no drift.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = 1

# Session persistence on the broker. MQTT-5 session_expiry_interval: how
# long Mosquitto holds queued QoS-1 messages for us after we disconnect.
SESSION_EXPIRY_SECS = 24 * 3600       # 24 h
KEEPALIVE_SECS      = 60
HEARTBEAT_SECS      = 20

# MQTT QoS — 1 = at-least-once. Duplicates possible, delivery guaranteed
# so long as the broker has our session state.
QOS = 1


# ──────────────────────────────────────────────────────────────────────
#  Topic builders  (one source of truth)
# ──────────────────────────────────────────────────────────────────────
class T:
    """`presence`/`state` are retained; `cmd`/`response`/`event`/`heartbeat`
    are not. Heartbeat is QoS 0 (disposable); everything else is QoS 1."""
    @staticmethod
    def cmd(node: str)       -> str: return f"chimera/{node}/cmd"
    @staticmethod
    def response(node: str)  -> str: return f"chimera/{node}/response"
    @staticmethod
    def event(node: str)     -> str: return f"chimera/{node}/event"
    @staticmethod
    def presence(node: str)  -> str: return f"chimera/{node}/presence"
    @staticmethod
    def state(node: str)     -> str: return f"chimera/{node}/state"
    @staticmethod
    def heartbeat(node: str) -> str: return f"chimera/{node}/heartbeat"

    ALL_RESPONSE  = "chimera/+/response"
    ALL_EVENT     = "chimera/+/event"
    ALL_PRESENCE  = "chimera/+/presence"
    ALL_STATE     = "chimera/+/state"
    ALL_HEARTBEAT = "chimera/+/heartbeat"


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


# ──────────────────────────────────────────────────────────────────────
#  Messages
# ──────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Command:
    skill:   str
    params:  dict[str, Any] = field(default_factory=dict)
    to:      str            = ""
    from_:   str            = ""
    id:      str            = field(default_factory=_new_id)
    ttl:     float          = 30.0        # seconds
    ts:      float          = field(default_factory=_now)
    type:    str            = "cmd"
    version: int            = SCHEMA

    def to_bytes(self) -> bytes:
        d = asdict(self); d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, b: bytes | str) -> "Command":
        d = json.loads(b); d["from_"] = d.pop("from", "")
        d.setdefault("params", {})
        return cls(**d)


@dataclass(slots=True)
class Response:
    id:      str                                   # == Command.id
    ok:      bool
    result:  Any            = None
    error:   str | None     = None
    from_:   str            = ""
    ts:      float          = field(default_factory=_now)
    type:    str            = "response"
    version: int            = SCHEMA

    def to_bytes(self) -> bytes:
        d = asdict(self); d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, b: bytes | str) -> "Response":
        d = json.loads(b); d["from_"] = d.pop("from", "")
        return cls(**d)


@dataclass(slots=True)
class Event:
    kind:    str
    data:    dict[str, Any] = field(default_factory=dict)
    from_:   str            = ""
    id:      str            = field(default_factory=_new_id)
    ts:      float          = field(default_factory=_now)
    type:    str            = "event"
    version: int            = SCHEMA

    def to_bytes(self) -> bytes:
        d = asdict(self); d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, b: bytes | str) -> "Event":
        d = json.loads(b); d["from_"] = d.pop("from", "")
        return cls(**d)


def presence_payload(node_id: str, online: bool) -> bytes:
    return json.dumps({
        "node_id": node_id,
        "online":  online,
        "ts":      _now(),
    }, separators=(",", ":")).encode()

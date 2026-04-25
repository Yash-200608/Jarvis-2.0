"""Message schema for the JARVIS nervous system.

Every inter-node message is a JSON object with a `type` discriminator.
We use dataclasses rather than pydantic to keep the phone agent dependency
list tiny (Termux users want thin).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = 1


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


# ──────────────────────────────────────────────────────────────────────────
#  Command  — brain → node. One side-effect-producing instruction.
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Command:
    skill:     str                                      # e.g. "phone.ring"
    params:    dict[str, Any]      = field(default_factory=dict)
    to:        str                 = ""                 # target node_id
    from_:     str                 = "brain"            # issuing node_id
    id:        str                 = field(default_factory=_new_id)
    ttl:       float               = 30.0               # seconds
    ts:        float               = field(default_factory=_now)
    type:      str                 = "cmd"
    version:   int                 = SCHEMA_VERSION
    reply_to:  Optional[str]       = None               # explicit response topic

    def to_json(self) -> str:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Command":
        d = json.loads(raw)
        d["from_"] = d.pop("from", "")
        d.setdefault("params", {})
        return cls(**d)


# ──────────────────────────────────────────────────────────────────────────
#  Response  — node → brain. Result of a Command.
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Response:
    id:        str                                      # same as Command.id
    ok:        bool
    result:    Any                 = None
    error:     Optional[str]       = None
    from_:     str                 = ""
    ts:        float               = field(default_factory=_now)
    type:      str                 = "response"
    version:   int                 = SCHEMA_VERSION

    def to_json(self) -> str:
        d = asdict(self); d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw) -> "Response":
        d = json.loads(raw); d["from_"] = d.pop("from", "")
        return cls(**d)


# ──────────────────────────────────────────────────────────────────────────
#  Event  — node → anyone. Unsolicited fact ("battery dropped to 14%").
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Event:
    kind:      str                                      # "battery_low"
    data:      dict[str, Any]      = field(default_factory=dict)
    from_:     str                 = ""
    id:        str                 = field(default_factory=_new_id)
    ts:        float               = field(default_factory=_now)
    type:      str                 = "event"
    version:   int                 = SCHEMA_VERSION

    def to_json(self) -> str:
        d = asdict(self); d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw) -> "Event":
        d = json.loads(raw); d["from_"] = d.pop("from", "")
        return cls(**d)


# ──────────────────────────────────────────────────────────────────────────
#  State  — node → anyone (retained). The node's current snapshot.
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class State:
    node_id:   str
    online:    bool                = True
    meta:      dict[str, Any]      = field(default_factory=dict)
    ts:        float               = field(default_factory=_now)
    type:      str                 = "state"
    version:   int                 = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw) -> "State":
        return cls(**json.loads(raw))


# ──────────────────────────────────────────────────────────────────────────
#  Request  — user voice/text intent arriving at the brain for planning.
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Request:
    text:      str                                      # raw user utterance
    from_:     str                 = ""                 # originating node
    id:        str                 = field(default_factory=_new_id)
    ts:        float               = field(default_factory=_now)
    type:      str                 = "request"
    version:   int                 = SCHEMA_VERSION

    def to_json(self) -> str:
        d = asdict(self); d["from"] = d.pop("from_")
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw) -> "Request":
        d = json.loads(raw); d["from_"] = d.pop("from", "")
        return cls(**d)


# ──────────────────────────────────────────────────────────────────────────
#  Topic helpers — single source of truth so typos don't break routing.
# ──────────────────────────────────────────────────────────────────────────
class T:
    @staticmethod
    def presence(node: str) -> str:  return f"jarvis/node/{node}/presence"
    @staticmethod
    def state(node: str)    -> str:  return f"jarvis/node/{node}/state"
    @staticmethod
    def heartbeat(node: str)-> str:  return f"jarvis/node/{node}/heartbeat"
    @staticmethod
    def cmd(node: str)      -> str:  return f"jarvis/node/{node}/cmd"
    @staticmethod
    def response(node: str) -> str:  return f"jarvis/node/{node}/response"
    @staticmethod
    def event(node: str)    -> str:  return f"jarvis/node/{node}/event"
    @staticmethod
    def catalog(node: str)  -> str:  return f"jarvis/skills/catalog/{node}"

    BROADCAST_REQUEST = "jarvis/broadcast/request"
    ALL_PRESENCE      = "jarvis/node/+/presence"
    ALL_STATE         = "jarvis/node/+/state"
    ALL_HEARTBEAT     = "jarvis/node/+/heartbeat"
    ALL_EVENT         = "jarvis/node/+/event"
    ALL_RESPONSE      = "jarvis/node/+/response"
    ALL_CATALOG       = "jarvis/skills/catalog/+"

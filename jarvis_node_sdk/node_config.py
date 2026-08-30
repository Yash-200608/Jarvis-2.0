"""Production node configuration (Priority #4 Milestone 8).

Sources the node's identity and secrets from the environment (optionally
seeded from an env file), never from CLI flags -- the demo script's
--key-on-the-command-line pattern leaks secrets into process lists and
shell history, which is acceptable for a dev tool and not for a node that
runs unattended.

Variables (prefix CHIMERA_ to stay clear of Brain's JARVIS_* settings and
jarvis_core's legacy vars):

    CHIMERA_NODE_ID     required -- this node's identity (e.g. jarvis-core-pc)
    CHIMERA_HMAC_KEY    required -- the shared spine HMAC key
    CHIMERA_BROKER_HOST default 127.0.0.1
    CHIMERA_BROKER_PORT default 1883
    CHIMERA_CEILINGS    optional -- path to the operator ceiling JSON
                        (absent = pc_skills' fail-closed defaults)
    CHIMERA_SKILL_SET   optional -- pc | phone | all (default: infer from
                        CHIMERA_NODE_ID: "pc" in id → pc, "phone" → phone)

Fail-closed: a missing required variable raises NodeConfigError with a
message naming exactly what to set -- the node never starts half-configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class NodeConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeConfig:
    node_id: str
    hmac_key: str
    broker_host: str = "127.0.0.1"
    broker_port: int = 1883
    ceilings_path: str | None = None
    skill_set: str = "all"  # pc | phone | all


def load_node_config(
    env: dict | None = None, env_file: str | None = None
) -> NodeConfig:
    """Builds NodeConfig from `env` (default os.environ), optionally
    seeding missing keys from a KEY=VALUE env file first (real environment
    variables always win over file values)."""
    environment = dict(env if env is not None else os.environ)

    if env_file:
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    environment.setdefault(key.strip(), value.strip())
        except FileNotFoundError:
            raise NodeConfigError(f"env file not found: {env_file}")

    node_id = environment.get("CHIMERA_NODE_ID", "").strip()
    hmac_key = environment.get("CHIMERA_HMAC_KEY", "").strip()
    if not node_id:
        raise NodeConfigError("CHIMERA_NODE_ID is required (e.g. jarvis-core-pc)")
    if not hmac_key:
        raise NodeConfigError("CHIMERA_HMAC_KEY is required (the shared spine HMAC key)")

    port_raw = environment.get("CHIMERA_BROKER_PORT", "1883").strip()
    try:
        port = int(port_raw)
    except ValueError:
        raise NodeConfigError(f"CHIMERA_BROKER_PORT must be an integer, got {port_raw!r}")

    skill_set = environment.get("CHIMERA_SKILL_SET", "").strip().lower()
    if not skill_set:
        nid = node_id.lower()
        if "phone" in nid:
            skill_set = "phone"
        elif "pc" in nid:
            skill_set = "pc"
        else:
            skill_set = "all"
    elif skill_set not in {"pc", "phone", "all"}:
        raise NodeConfigError(
            f"CHIMERA_SKILL_SET must be pc, phone, or all — got {skill_set!r}"
        )

    return NodeConfig(
        node_id=node_id,
        hmac_key=hmac_key,
        broker_host=environment.get("CHIMERA_BROKER_HOST", "127.0.0.1").strip() or "127.0.0.1",
        broker_port=port,
        ceilings_path=environment.get("CHIMERA_CEILINGS", "").strip() or None,
        skill_set=skill_set,
    )

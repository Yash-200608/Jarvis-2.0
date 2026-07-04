"""Skill capability declaration -- Priority #4 Milestone 3.

Salvages the dead hugginggpt/server/jarvis/schema.py's per-node catalog
topic concept (`jarvis/skills/catalog/{node}`) under the chimera/* grammar
(`chimera/<node>/capabilities`) -- completing the one genuinely reusable
idea from that ADR-014 "revive as skill/HAL catalog" candidate. That
package itself remains dead/archived; it never actually defined a catalog
*payload* shape to port, only the one-topic-per-node naming convention,
which is what's salvaged here.

CHIMERA_ECOSYSTEM_ARCHITECTURE.md Section 5: "Capability declaration, not
hardcoded skill lists. ... Brain builds its plan of available actions from
live declarations, never a static table baked into Brain's code." A
node's SkillRegistry is the single source of truth for what it can do;
Brain (Milestone 4's subscriber) must plan only from what a node actually
declares here.

This module is node-side only: it declares skills, it does not execute
them (Milestones 5/6) or store anyone else's declarations (Milestone 4).
"""

from __future__ import annotations

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign
from jarvis_node_sdk.transport import MqttTransport


class SkillRegistry:
    """In-memory set of skill names this node currently offers.

    Milestones 5 and 6 populate a real registry by calling register() for
    each phone/pc skill they wire up. This milestone provides only the
    registry and the means to publish it -- no skills are registered here.
    """

    def __init__(self) -> None:
        self._skills: set[str] = set()

    def register(self, skill: str) -> None:
        if not skill:
            raise ValueError("skill name must be non-empty")
        self._skills.add(skill)

    def unregister(self, skill: str) -> None:
        self._skills.discard(skill)

    def list(self) -> list[str]:
        """Sorted for a stable, diffable wire representation."""
        return sorted(self._skills)


async def publish_capabilities(
    transport: MqttTransport, key: str, node: str, registry: SkillRegistry
) -> None:
    """Signs and publishes `registry`'s current skill list to
    chimera/{node}/capabilities.

    One-shot, like state_publisher.publish_state() -- callers publish once
    on connect and again whenever the registry changes. No automatic
    re-publish loop exists here; inventing one would be exactly the
    "advanced automation" the Priority #3 capstone (and this priority's own
    Non-negotiable Principles) excludes. Deciding *when* the registry
    changed is the caller's responsibility, not this function's.
    """
    envelope = ChimeraEnvelope(
        node=node, verb="capabilities", params={"skills": registry.list()}
    )
    signed = sign(envelope, key)
    await transport.publish(f"chimera/{node}/capabilities", signed.model_dump())

"""Skill framework — subclass `Skill`, register, done.

A Skill is a named, awaitable, side-effect-producing function with a
machine-readable description. The description is what the LLM planner
reads when deciding which skill to invoke.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("jarvis.skills")


# ──────────────────────────────────────────────────────────────────────────
#  Skill  — the unit of capability.
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Skill:
    name: str                               # e.g. "phone.ring"
    description: str                        # one line — for LLM consumption
    handler: Callable[..., Awaitable[Any]]  # async (router, params) -> dict | primitive
    params_schema: dict[str, str] = field(default_factory=dict)  # {"level": "int 0-15"}
    owner: str = "any"                      # "pc" | "phone" | "any"


# ──────────────────────────────────────────────────────────────────────────
#  SkillRegistry  — per-node lookup; publishes its catalog on MQTT.
# ──────────────────────────────────────────────────────────────────────────
class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        if skill.name in self._skills:
            log.warning("skill '%s' already registered — overwriting", skill.name)
        self._skills[skill.name] = skill
        log.info("registered skill: %s", skill.name)

    def skill(self,
              name: str,
              description: str = "",
              params_schema: Optional[dict[str, str]] = None,
              owner: str = "any"):
        """Decorator form:

            @registry.skill("phone.ring", "Play a loud tone on the phone")
            async def ring(router, params): ...
        """
        def _wrap(fn: Callable[..., Awaitable[Any]]):
            if not inspect.iscoroutinefunction(fn):
                raise TypeError(f"skill handler must be async: {name}")
            self.register(Skill(name=name,
                                description=description,
                                handler=fn,
                                params_schema=params_schema or {},
                                owner=owner))
            return fn
        return _wrap

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def catalog(self) -> list[dict[str, Any]]:
        """Serializable catalog — published as retained MQTT message so the
        brain can introspect every node's capabilities at any time."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "owner": s.owner,
                "params": s.params_schema,
            }
            for s in self._skills.values()
        ]

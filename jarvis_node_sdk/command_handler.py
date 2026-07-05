"""Command verification + dispatch for inbound chimera/<node>/cmd envelopes.

Priority #3 Milestone 6 built handle_command() -- a synchronous, ping-only
dummy proving the authenticated request/response path. Priority #4
Milestone 2 added the request_id echo. Priority #4 Milestone 5 adds
dispatch_command(): the async, table-driven dispatcher the real skills
(phone_skills.py) execute through. handle_command() is kept, unchanged in
behavior, as the ping-only special case -- existing callers and tests
stay valid; the command listener now routes through dispatch_command().

Still NOT a general task/skill framework: dispatch is a dict lookup, one
skill per command, no chaining/queueing/scheduling -- the plan step ->
skill mapping lives Brain-side (cognition), never here (NP-1/NP-2).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Mapping

from pydantic import ValidationError

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign, verify

logger = logging.getLogger("command_handler")

SkillFn = Callable[[dict], Awaitable[dict]]


def verify_command(payload: dict, key: str) -> ChimeraEnvelope | None:
    """Validates shape + signature + verb of an inbound cmd payload.
    Returns the envelope on success, None on any failure (mirrors Brain's
    verify_payload()'s non-raising contract)."""
    try:
        envelope = ChimeraEnvelope.model_validate(payload)
    except (ValidationError, TypeError):
        return None
    if not verify(envelope, key):
        return None
    if envelope.verb != "cmd":
        return None
    return envelope


def _build_response(
    envelope: ChimeraEnvelope, key: str, node: str, result: dict
) -> ChimeraEnvelope:
    response = ChimeraEnvelope(
        node=node,
        verb="response",
        action=envelope.action,
        params=result,
        request_id=envelope.request_id,
    )
    return sign(response, key)


def handle_command(payload: dict, key: str, node: str) -> ChimeraEnvelope | None:
    """The ping-only synchronous handler (Priority #3 Milestone 6 shape,
    kept verbatim in behavior). An unrecognized action still returns a
    valid signed response (params={"error": "unknown_action"}), proving
    the round trip for the failure case too."""
    envelope = verify_command(payload, key)
    if envelope is None:
        return None

    if envelope.action == "ping":
        result = {"pong": True}
    else:
        result = {"error": "unknown_action"}

    return _build_response(envelope, key, node, result)


async def dispatch_command(
    payload: dict, key: str, node: str, skills: Mapping[str, SkillFn]
) -> ChimeraEnvelope | None:
    """Verifies an inbound cmd and dispatches it to `skills` (Priority #4
    Milestone 5). Returns a signed response envelope, or None only when
    verification fails (an unverified message gets no reply at all).

    Dispatch rules:
    - "ping" is built in -- every node answers it regardless of its skill
      table, so liveness probing never depends on declared capabilities.
    - An action present in `skills` is awaited with the envelope's params;
      its dict result becomes the response params verbatim (skills own
      their result shapes; the proven phone.* skills all use {"ok": ...}).
    - A skill that raises (the ported skills never should -- their runner
      never raises -- but a future skill might) is caught and answered
      with {"ok": False, "error": ...}: a skill bug must produce a
      truthful failure response, never kill the listener or leave Brain
      waiting for a timeout.
    - An unknown action answers {"error": "unknown_action"} -- same
      contract as handle_command().
    """
    envelope = verify_command(payload, key)
    if envelope is None:
        return None

    if envelope.action == "ping":
        result: dict = {"pong": True}
    elif envelope.action in skills:
        try:
            result = await skills[envelope.action](envelope.params)
        except Exception as exc:  # noqa: BLE001 -- truthful failure beats a dead listener
            logger.exception("skill %r raised", envelope.action)
            result = {"ok": False, "error": f"skill raised {type(exc).__name__}: {exc}"}
    else:
        result = {"error": "unknown_action"}

    return _build_response(envelope, key, node, result)

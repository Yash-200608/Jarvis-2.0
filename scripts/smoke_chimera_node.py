"""Offline smoke test for the Chimera node command path -- the README's
recommended demo. No broker, no hardware, no device actions.

Drives jarvis_node_sdk's real dispatch_command() (exactly what
command_listener.py calls for every chimera/<node>/cmd message) with an
empty skill table and a throwaway HMAC key, so the only thing that can run
is the built-in ping. Proves the four properties a reviewer cares about:

  1. a correctly signed ping gets a signed pong, request_id echoed
  2. an unknown action still gets a truthful signed reply
  3. a forged signature (wrong key) gets no reply at all
  4. a tampered envelope (params edited after signing) gets no reply

Usage (from the repo root):
    pip install -r requirements-chimera-node.txt
    python scripts/smoke_chimera_node.py

Exit code 0 on PASS, 1 on any failed check.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_node_sdk.chimera_contract import ChimeraEnvelope, sign, verify  # noqa: E402
from jarvis_node_sdk.command_handler import dispatch_command  # noqa: E402

NODE = "smoke-node"


def _cmd(action: str, key: str, **fields) -> dict:
    envelope = ChimeraEnvelope(node=NODE, verb="cmd", action=action, **fields)
    return sign(envelope, key).model_dump()


async def _run() -> list[tuple[str, bool, str]]:
    key = secrets.token_hex(32)
    results: list[tuple[str, bool, str]] = []

    reply = await dispatch_command(_cmd("ping", key, request_id="smoke-1"), key, NODE, {})
    ok = (
        reply is not None
        and reply.params == {"pong": True}
        and reply.request_id == "smoke-1"
        and verify(reply, key)
    )
    results.append(("signed ping", ok, f"{reply.params if reply else None}  (signed reply, request_id echoed)"))

    reply = await dispatch_command(_cmd("pc.does.not.exist", key), key, NODE, {})
    ok = reply is not None and reply.params == {"error": "unknown_action"} and verify(reply, key)
    results.append(("unknown action", ok, f"{reply.params if reply else None}"))

    reply = await dispatch_command(_cmd("ping", secrets.token_hex(32)), key, NODE, {})
    results.append(("forged signature", reply is None, "dropped, no reply" if reply is None else "REPLIED"))

    tampered = _cmd("ping", key, params={"n": 1})
    tampered["params"] = {"n": 2}
    reply = await dispatch_command(tampered, key, NODE, {})
    results.append(("tampered envelope", reply is None, "dropped, no reply" if reply is None else "REPLIED"))

    return results


def main() -> int:
    print("Chimera node smoke (offline: no broker, no device actions)")
    results = asyncio.run(_run())
    for name, ok, detail in results:
        print(f"  [{'ok' if ok else 'FAIL':^4}] {name:<18} -> {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{'PASS' if passed == len(results) else 'FAIL'} {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

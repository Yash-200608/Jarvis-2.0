# JARVIS / Chimera — Security Model

**Status: Planning phase — pre-implementation**
**Last updated: 2026-07-01**

This document is the canonical security reference for the JARVIS / Chimera Ecosystem repository. It describes, with direct evidence from the codebase, what security controls exist today, where they are missing or bypassed, and the target permission/risk model the project must build toward before any device-control surface (PC bridge, phone bridge, MQTT command channel) is allowed to run unattended. It is not a description of a secure system — several of the findings below are live, exploitable gaps in code that exists in this repository right now.

Related documents: [`../vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md), [`../vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md), [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md), [`AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md), [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md), [`TOOL_ROUTING.md`](TOOL_ROUTING.md), [`../specifications/VOICE_SYSTEM.md`](../specifications/VOICE_SYSTEM.md), [`../specifications/UI_SPEC.md`](../specifications/UI_SPEC.md), [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md), [`../specifications/3D_ENGINE.md`](../specifications/3D_ENGINE.md), [`../specifications/COMPUTER_CONTROL.md`](../specifications/COMPUTER_CONTROL.md), [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), [`../context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md), [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md), [`../context/DESIGN_HISTORY.md`](../context/DESIGN_HISTORY.md), [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md).

---

## 1. Executive Summary

The repository contains two opposite security postures that have never been reconciled:

1. **The device-control surfaces that actually execute actions today** — `hugginggpt/server/bridge_server.py` (PC bridge) and `hugginggpt/server/device_integration.py` (the orchestrator the legacy stack uses to reach phone/PC bridges) — have **no permission tiers, no risk scoring, and no confirmation gate of any kind**. Any authenticated caller can issue `delete_file`, `shutdown_device`, `kill_process`, or `run_command` and it executes immediately, gated only by a single shared bearer token and an optional global dry-run flag.
2. **A genuinely well-engineered safety kernel already exists** at root `core/` — idempotency, undo/rollback, sandboxing, risk scoring, retry bounding, and tool-schema validation, backed by 565 lines of adversarial tests. But it is wired into nothing (`core/executor.py` is imported by no production code path — see [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) §2), and it currently contains a **confirmed live bug that defeats its own confirmation gate** for the single highest-risk action it defines (`delete_file`).

Until both of these are fixed — the bridge servers gaining a permission layer, and the safety kernel's own ordering bug being fixed before it is trusted as that layer — neither of the two "production-ish" device-control paths in this repository (the legacy ADB stack's bridge servers, or a future `jarvis_prod`/`jarvis_core` merge) can be considered safe to run against real user data or hardware. This is the top-priority security finding in the repository and is addressed first, in Section 2.

---

## 2. Top Finding: The Safety Kernel's Own Confirmation Gate Is Bypassable

### 2.1 Where the bug is

File: `core/executor.py`, function `execute_task()`.

```python
action = safety.apply_safety(action)          # line 80 — rewrites the action FIRST
fp = _fingerprint(action)

action_risk = risk.get_risk_level(action["name"])   # line 83 — checks risk on the REWRITTEN name
if action_risk == "high":
    ...
```

`core/safety.py` — `apply_safety()`:

```python
def apply_safety(action: dict) -> dict:
    action = deepcopy(action)
    name = action.get("name")
    action.setdefault("params", {})

    if name == "delete_file":
        action["name"] = "move_to_trash"     # rewrite happens here
    elif name in {"send_email", "send_sms", "send_whatsapp"}:
        action["preview"] = True

    return action
```

`core/tools.py` — the risk registry:

| Tool | Declared risk |
|---|---|
| `delete_file` | **high** |
| `move_to_trash` | medium |
| `shutdown_device` | high |
| `send_sms` / `send_email` / `send_whatsapp` | medium |
| `open_app` / `read_file` | low |

### 2.2 Why it is a bug, not a feature

`apply_safety()` is called at line 80, **before** `risk.get_risk_level()` is called at line 83. `apply_safety()` silently rewrites `delete_file` (declared `high` risk) into `move_to_trash` (declared `medium` risk) as a "softening" measure — presumably so a delete becomes a reversible trash operation. But because the risk check runs on `action["name"]` **after** this rewrite has already happened, the executor evaluates the risk of `move_to_trash` (medium — no confirmation required) instead of the risk of the action the caller actually requested (`delete_file`, high — confirmation required). The result: a caller can submit `{"name": "delete_file", "params": {"path": ...}}` and it executes immediately, with **zero confirmation prompt**, despite `delete_file` being explicitly registered as the highest risk tier the kernel defines.

This is a textbook confirmation-bypass-via-reinterpretation bug: the safety layer's own rewrite is what defeats the safety layer's own gate.

### 2.3 Confirmed by the kernel's own test suite

Three tests in the repository's adversarial test suite currently **fail** against unmodified `core/executor.py`, `core/safety.py`, and `core/risk.py`:

| Test | File | Asserts |
|---|---|---|
| `test_high_risk_action_requires_confirmation_before_safety_rewrite` | `core/tests/test_execution_safety.py` | Risk must be evaluated on the pre-rewrite action name |
| `test_confirmation_bypass_attack_is_rejected` | `tests/test_adversarial_execution.py` | A `delete_file` call with `"confirmed": True` sent on the *first* request (no prior `confirmation_required` round-trip) must still be rejected — must return `status == "confirmation_required"` and must not reach `_dispatch()` |
| `test_external_confirmation_does_not_bypass_safeguards` | `tests/test_executor_safety.py` | Same invariant, isolated in the kernel's own primary suite |

Reproduction (from `test_confirmation_bypass_attack_is_rejected`):

```python
result = executor.execute_task(
    new_command_id(),
    {"name": "delete_file", "params": {"path": "C:/Users/Hp/important.txt"}, "confirmed": True},
)
assert result["status"] == "confirmation_required"   # FAILS — actual status is "success"
assert calls == []                                     # FAILS — _dispatch() was called
```

Note that this failure mode is **worse** than a normal confirmation-required response silently being skipped: because `apply_safety()` has already renamed the action to `move_to_trash`, even the `result["action"]["name"]` returned to the caller no longer says `delete_file`, obscuring what actually happened in logs and undo history.

### 2.4 Required fix (Phase 0 — do first)

Evaluate risk on the **pre-rewrite** action name, not the post-rewrite one. Two equivalent approaches:

- Move `risk.get_risk_level(action["name"])` to run on the original `action` dict, before `safety.apply_safety()` executes, and gate the high-risk confirmation check on that pre-rewrite risk value; only apply the rewrite after confirmation has been established.
- Or: have `apply_safety()` preserve/attach the original tool name (e.g. `action["_original_name"] = name`) and have `risk.get_risk_level()` consult that field when present.

Either fix is a small, contained change validated entirely by the three existing failing tests — this is explicitly called out as Phase 0, low-effort, high-impact work in [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md). **No code wiring `core/` into `bridge_server.py`, `jarvis_prod`, or `jarvis_core` should happen before this fix lands and all three tests pass.**

A secondary, lower-priority issue in the same area: `_approved_commands` (the in-memory store the executor uses to remember that a given `command_id` + action-fingerprint pair was already confirmed) is a plain module-level dict with no eviction — unbounded under a long-running process, unlike `retry.fail_counts` (capped at `MAX_TRACKED_FAILURES = 1000`) and `undo.history` (capped at `MAX_HISTORY = 20`) elsewhere in the same module. This should be bounded before the kernel is deployed long-running.

---

## 3. The Device-Control Surfaces In Actual Use Today Have No Confirmation Model At All

While `core/` (Section 2) at least has the *concept* of risk tiers and confirmation gates — even if currently buggy — the code paths that are actually exercised daily have none.

### 3.1 `bridge_server.py` — the PC bridge

`hugginggpt/server/bridge_server.py` is a Flask app exposing a single `/execute` endpoint. Its entire security model is:

1. A shared-secret bearer token check (`Authorization: Bearer <BRIDGE_TOKEN>`), string-compared with `!=` (not constant-time — see Section 3.3).
2. An optional global `BRIDGE_DRY_RUN` flag (`true` by default) that short-circuits *all* actions identically, with no per-action distinction.

Past that point, every action — from `open_app` (low risk) to `delete_file` and `shutdown_device` (high risk in `core/`'s own taxonomy) — dispatches directly to its handler with no risk classification, no confirmation round-trip, and no sandboxing of file paths. Concretely, `delete_file`'s handler:

```python
if action == "delete_file":
    path = params.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "Missing params.path"}), 400
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        base["deleted"] = path
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify(base)
```

This calls `shutil.rmtree()` / `os.remove()` on any path the caller supplies, with **no sandbox check** (contrast with `core/sandbox.py`'s `resolve_path()`, which restricts to `ALLOWED_ROOTS`, or `jarvis_core/core/sandbox.py`'s `safe_path()`, which resolves everything relative to a sandbox root and rejects escapes). The same pattern holds for `shutdown_device`, `restart_device`, `kill_process`, and `run_command` (arbitrary shell execution via `_run_command()`). The file's own docstring lists all 29 supported actions — PC control, system power/process control, file operations, email, WhatsApp, media — under one flat, ungated action namespace. This is the concrete basis for the audit's characterization of this surface as offering full device access with no confirmation step for destructive operations once the caller is authenticated.

`hugginggpt/server/device_integration.py` (the `DeviceOrchestrator`/`EndpointDeviceAdapter` pair the legacy stack uses to reach `bridge_server.py` and the analogous phone bridge) adds no additional gating either — it is a thin HTTP relay that forwards `{"action": ..., "params": ...}` verbatim to whichever bridge is configured for the named `target`, with the same bearer-token model and no local risk awareness at all.

`hugginggpt/server/phone_bridge.py` (866 lines, the Android/Termux-side counterpart) follows the same pattern for phone actions (`send_sms`, `send_whatsapp`, `send_telegram`, `make_call`, etc.) — actions dispatch on receipt of a valid bearer token with no confirmation step specific to irreversible actions like `send_sms` or `make_call`.

### 3.2 Why this matters given what `core/` already proves is possible

The existence of `core/`'s risk tiers (`core/tools.py`), sandboxing (`core/sandbox.py`, `jarvis_core/core/sandbox.py`), and confirmation-required response contract (`core/executor.py`'s `confirmation_required` status) demonstrates the pattern needed here is already designed and half-built — it is simply not connected to the code path that actually runs. **Section 6 recommends wiring `core/` (post-fix) directly in front of `bridge_server.py`'s and `phone_bridge.py`'s action dispatch, rather than designing a second, parallel permission model.**

### 3.3 Auth model detail — `BRIDGE_TOKEN`

Both `bridge_server.py` and `phone_bridge.py` require a `BRIDGE_TOKEN` environment variable (`bridge_server.py` raises `ValueError("BRIDGE_TOKEN env var is required.")` at startup if unset — there is no anonymous-access fallback, which is correct). Auth is a single shared token per device, compared via Python's `!=` operator:

```python
auth = request.headers.get("Authorization", "")
if auth != f"Bearer {token}":
    return jsonify({"ok": False, "error": "Unauthorized"}), 401
```

This is a **timing side-channel** (non-constant-time string comparison) and, more importantly, is **all-or-nothing**: one token per bridge process grants every action that bridge exposes, with no scoping (e.g. no read-only vs. destructive token split). This mirrors the flat-ACL problem described for MQTT in Section 4 — the project has, in two independent places (HTTP bridge tokens and MQTT credentials), converged on "one shared secret = full access to everything on that node," with no tiering in either.

---

## 4. `jarvis_core/core/sandbox.py` and `signing.py` — Designed Well, Never Exercised

Unlike root `core/`, the `jarvis_core` island's safety primitives (`jarvis_core/core/sandbox.py`, `jarvis_core/core/signing.py`) are well-designed on paper but have **never run end-to-end** (see [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) §2, Island 3 — no `.env`, no `history.log`, no live Ollama/Mosquitto run recorded anywhere in the repo).

### 4.1 `jarvis_core/core/sandbox.py` — `safe_path()`

A single chokepoint function, documented as such in its own module docstring ("the *single chokepoint* the Critic relies on"):

- Treats absolute paths as relative to the sandbox root (`/etc/passwd` becomes `<sandbox>/etc/passwd`), not the real filesystem root.
- Resolves `..` segments before the containment check, defeating `../../etc/passwd`-style traversal.
- Uses `Path.resolve()`, which follows existing symlinks, so a pre-planted symlink inside the sandbox cannot be used to escape it.
- Raises a dedicated `SandboxViolation(PermissionError)` rather than returning a boolean, making it hard to silently ignore a violation in calling code (though `is_inside_sandbox()` does provide a boolean wrapper for the Critic's use).

This is architecturally the strongest of the three sandbox implementations in the repo (root `core/sandbox.py`'s `resolve_path()` is coarser — it only checks whether a resolved path falls under a small `ALLOWED_ROOTS` allowlist, with no explicit symlink-escape or traversal test coverage of its own). However, `jarvis_core/core/sandbox.py` has no corresponding evidence of ever being invoked by a live Executor run — its correctness is currently only as trustworthy as unexercised code can be.

### 4.2 `jarvis_core/core/signing.py` — HMAC-SHA256 command signing

Implements zero-trust signing for PC-brain-to-phone-executor traffic over the shared MQTT broker:

- `sign_envelope()` attaches a `nonce` (16 random bytes, hex-encoded), a `ts` (Unix timestamp), and an HMAC-SHA256 signature over the canonical JSON encoding of `{payload, nonce, ts}`, keyed by a 32-byte secret from `$JARVIS_HMAC_KEY`.
- `verify_envelope()` rejects envelopes with no `sig`/`ts`, rejects clock skew beyond `MAX_SKEW_SECONDS = 120`, and compares signatures with `hmac.compare_digest()` (correct — constant-time, unlike `bridge_server.py`'s bearer-token check in Section 3.3).
- The nonce + timestamp + signature combination is designed specifically to defeat replay of a captured command from "an adversarial client on the same broker" (per the module docstring) — directly relevant given Section 5's finding that the current MQTT ACL model would let a leaked credential publish arbitrary commands on any topic.

This is the most rigorous authentication primitive in the repository. It is also, like the rest of `jarvis_core`, never-exercised: there is no evidence of a live `$JARVIS_HMAC_KEY` exchange or a signed envelope actually traversing a real broker anywhere in the repo's history, logs, or test output. **If `jarvis_prod`'s MQTT transport (which currently has no message-level signing of its own — see `protocol.py`) is adopted as the transport backbone per the recommended architecture in [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md), `jarvis_core/core/signing.py`'s envelope scheme is the strongest existing candidate to graft on for command authenticity, and should be validated end-to-end during that merge rather than designed a third time.**

---

## 5. MQTT Transport Security — Flat ACL, No TLS

### 5.1 The ACL model today

`hugginggpt/server/jarvis_prod/mosquitto.conf.example` documents the intended broker configuration. Key excerpts:

```
listener 1883 100.64.0.1        # bound to Tailscale interface, not 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/jarvis.acl
```

And the ACL file contents, embedded as a comment block in the same example file:

```
#   # Admin (the PC brain) can do anything under jarvis/.
#   user jarvis
#   topic readwrite jarvis/#
#
#   # If you split creds per-node (recommended in future):
#   # user ian-phone
#   # topic write    jarvis/node/ian-phone/response
#   # topic write    jarvis/node/ian-phone/event
#   # topic write    jarvis/node/ian-phone/presence
#   # topic write    jarvis/node/ian-phone/state
#   # topic write    jarvis/node/ian-phone/heartbeat
#   # topic read     jarvis/node/ian-phone/cmd
```

Only the flat, single-tier grant is active — one shared `jarvis` user with `readwrite` on the entire `jarvis/#` topic tree. The per-node ACL split (one credential per node, scoped to that node's own `cmd`/`response`/`event`/`presence`/`state`/`heartbeat` topics) is present only as a **commented-out example**, explicitly marked "recommended in future."

### 5.2 Why this is a real risk, not a theoretical one

Because every credential currently issued grants `readwrite` on `jarvis/#`, a single leaked credential (from any one node — the PC brain, the Nothing Phone 3a Lite, or any future OPPO/Vivo/Arduino/ESP32 node per `CLAUDE.md`'s hardware inventory) is sufficient to:

- Publish arbitrary commands on any other node's `cmd` topic (full impersonation of the Brain toward any node).
- Publish forged `response`/`event`/`presence`/`state`/`heartbeat` messages as any node (full impersonation of that node toward the Brain).
- Subscribe to and read every topic in the tree, including any node's private event/state stream.

This is compounded by the finding in Section 4.2: `jarvis_prod`'s transport (`protocol.py`) has no message-level signing of its own today, so MQTT-level authorization is the *only* control standing between a leaked credential and full command forgery — there is no HMAC or equivalent secondary check the way `jarvis_core/core/signing.py` provides. Layering `signing.py`'s envelope scheme on top of `jarvis_prod`'s transport (Section 4.2) would mean a leaked MQTT credential alone is insufficient to forge commands — but that layering does not exist yet.

### 5.3 No TLS

`mosquitto.conf.example` configures a plaintext `listener 1883` with no `cafile`/`certfile`/`keyfile` directives. The entire confidentiality and integrity guarantee for MQTT traffic rests on Tailscale's own WireGuard-based encryption of the tunnel, not on MQTT/TLS itself. This is a reasonable interim posture for a single-operator Tailscale mesh (traffic never leaves the tailnet), but it means a compromised or misconfigured Tailscale node has no secondary transport-layer defense, and it should be stated explicitly as a known, accepted risk rather than left implicit.

### 5.4 Never actually deployed

As documented in [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md), `jarvis_prod` has been smoke-tested (`_smoke.py`, a real Brain plus a fake phone agent through a real local broker) but has **never had a live Tailscale run** — the listener IP `100.64.0.1` in `mosquitto.conf.example` is an unconfirmed placeholder, not a real, bound Tailscale address. The ACL gap above is therefore a design-time finding, not yet an incident — but it must be closed before Phase 4 (first live Tailscale deployment) in [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md).

---

## 6. Prior Security Incident: Three Leaked Secrets, History Rewritten, Rotation Unconfirmed

This is not a hypothetical risk — it is a real, already-occurred incident visible in the repository's own documentation.

### 6.1 What happened

Per `README.md` (lines ~317, ~799-800):

> "earlier history of this repo had three live secrets committed (Gmail password, ElevenLabs key, an upstream GitHub PAT). They've been purged from HEAD **and** from full git history via `git filter-repo`. Rotate all three regardless — they were public for a few hours before the rewrite."

The three secrets:

| Secret | Where it was found |
|---|---|
| Gmail App Password | Committed in earlier history |
| ElevenLabs API key | Committed in earlier history |
| GitHub PAT (upstream) | `easytool/data_toolbench/tool_instruction/toolbench_tool_instruction.json` |

Remediation performed: `git filter-repo --replace-text`, force-pushed to both the current origin (`Yash-200608/Jarvis-2.0`) and the preserved `jarvis-old` remote.

### 6.2 Why "history rewritten" is not "incident closed"

Rewriting git history removes the secrets from any *future* clone of the repository, but **does not invalidate the credentials themselves**. Anyone who cloned, forked, mirrored, or otherwise accessed the repository during the window the secrets were live — README.md states "a few hours" — retains a copy of those exact credential values regardless of what HEAD looks like today. `README.md` itself states this correctly ("Rotate all three regardless") — this is an open advisory in the project's own documentation, not a new finding by this document.

### 6.3 Current status

Per the audit that produced this document set: **rotation status is unconfirmed.** This document does not assert the secrets have or have not been rotated — that must be confirmed directly with the credential owner. Until confirmed:

- Treat the Gmail App Password, ElevenLabs API key, and GitHub PAT referenced above as **compromised and unusable for any decision that assumes they are secret**, per the README's own advisory.
- This is listed as a **P0, do-first item** in [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), on the same priority tier as the Section 2 executor bug fix, precisely because it is low-effort (routine credential rotation is ops work, not engineering work) and high-impact (an unrotated leaked credential is a live compromise, not a design gap).

**Status: TBD — confirm rotation status with user before treating this as closed.**

---

## 7. Target Risk-Tiering Scheme

This section defines the risk model the project should converge on. It generalizes `core/tools.py`'s existing three-tier scheme (`low`/`medium`/`high`) into four explicit tiers, because the existing three-tier scheme conflates "reversible but consequential" (e.g. `move_to_trash`) with "trivially safe" (e.g. `read_file`) under the same `low`/`medium` boundary in a way that has already contributed to the Section 2 bug — a rewrite that changes an action's *reversibility* was treated as equivalent to changing its *risk tier*, when they are different axes.

| Tier | Definition | Examples (from `core/tools.py`, `bridge_server.py`, `phone_bridge.py`) | Confirmation required? |
|---|---|---|---|
| **Safe** | Read-only, no state change, no side effects outside the query itself | `read_file`, `get_clipboard`, `get_system_info`, `list_processes`, `list_directory` | No |
| **Reversible** | Changes state, but a known inverse operation exists and is registered | `move_to_trash` (inverse: `restore_from_trash`, per `core/undo.py`), `open_app`, `set_volume`, `set_clipboard` | No, but must be undo-logged |
| **Destructive** | Changes state with no automatic inverse; consequential but not catastrophic | `send_sms`, `send_email`, `send_whatsapp`, `write_file`, `kill_process`, `move_file` | **Yes** — explicit confirmation round-trip required |
| **Irreversible** | Cannot be undone by any means available to the system; safety- or availability-critical | `delete_file` (real deletion, not the `move_to_trash` rewrite), `shutdown_device`, `restart_device`, `run_command`/`pc.shell.run` (arbitrary shell execution) | **Yes** — explicit confirmation round-trip required, and should be logged with higher retention/visibility than Destructive-tier actions |

Design rules that follow directly from this scheme, and directly address Section 2's bug:

1. **Risk tier must be evaluated on the action the caller actually requested, never on a post-rewrite substitute.** If a "soften a destructive action into a reversible one" step is desired (as `apply_safety()` attempts for `delete_file` → `move_to_trash`), that softening must be a *policy choice presented to the confirmation step*, not a silent substitution that happens before risk is evaluated. Concretely: ask the user "delete permanently, or move to trash?" — do not decide it for them and then skip confirmation because the decided-for-them version happens to be lower risk.
2. **`run_command`/shell-execution actions are Irreversible by default**, not evaluated case-by-case, because their effects are unbounded and cannot be statically classified. This directly targets `bridge_server.py`'s `run_command` action and the `pc.shell.run` skill name referenced (but never implemented) throughout `jarvis_prod` per [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md).
3. **Unknown tools default to Irreversible**, not `low`. This matches `core/risk.py`'s existing fail-closed default (`get_risk_level()` returns `"high"` for any tool absent from the `TOOLS` registry) — that pattern is correct and should be preserved verbatim when the registry is extended to `pc.*` and `phone.*` skill names.
4. **Confirmation must be a genuine round-trip**, not a client-supplied flag trusted on first submission. `core/executor.py`'s existing two-step model (first call returns `confirmation_required` with a fingerprint; a second call with `confirmed: true` and a matching fingerprint is required to proceed) is architecturally correct and should be preserved — the bug in Section 2 is in the risk *evaluation* feeding into this model, not in the round-trip mechanism itself, which is sound and already covered by `test_idempotency_repeated_command_is_ignored`-style tests.

---

## 8. Recommendations

Ordered by dependency (each depends on the ones above it being done first), consistent with the phased sequencing in [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md):

1. **Fix the `core/executor.py` ordering bug (Section 2.4).** Evaluate `risk.get_risk_level()` on the pre-rewrite action name. Validate against the three currently-failing tests. This is the single highest-priority item in this document — nothing else here should be built on top of `core/` until it is fixed.
2. **Rename root `core/`** to remove its `sys.path` collision with `jarvis_core/core/` (see [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) for the full collision mechanics — `jarvis_core/tests/conftest.py` inserts `jarvis_core/` onto `sys.path` specifically to keep its own `from core.sandbox import` resolving internally). A careless future import in either island could otherwise silently resolve to the wrong safety kernel. Do this before Recommendation 3.
3. **Wire the fixed, renamed `core/` in as the permission/risk/undo layer in front of every device action in both stacks** — `bridge_server.py`/`phone_bridge.py` (the currently-daily-use surfaces, Section 3) and whatever emerges from the `jarvis_prod`/`jarvis_core` merge (Section 4). Concretely: `bridge_server.py`'s `/execute` handler should call `core.executor.execute_task()` (post-fix, post-rename) instead of dispatching directly to `shutil.rmtree()`/`os.remove()`/etc. This is the most impactful single change available, since it takes a kernel that is currently proven only by its own test suite and puts it in front of real, exploitable destructive actions.
4. **Adopt the four-tier risk scheme in Section 7** as the schema for `core/tools.py` (or its successor registry), and extend it to cover every `pc.*` and `phone.*` skill name referenced across `brain_pc.py`, `telegram_gateway.py`, `schedule.example.json`, and `bridge_server.py`'s action list — not just the eight generic tools `core/tools.py` currently defines.
5. **Add per-node MQTT ACLs**, replacing `mosquitto.conf.example`'s flat `jarvis/#` grant with the already-drafted (but commented-out) per-node topic scoping shown in Section 5.1. This should land before Phase 4's first live Tailscale deployment, not after.
6. **Layer `jarvis_core/core/signing.py`'s HMAC envelope scheme on top of `jarvis_prod`'s MQTT transport** as a second, credential-independent authenticity check — so that a leaked MQTT password alone is insufficient to forge commands (Section 4.2, Section 5.2).
7. **Move Telegram PII masking to the transport layer.** Today, `_mask_params()`/`_lf_mask()` in `hugginggpt/server/jarvis_prod/brain_pc.py` mask phone numbers and message bodies only for Langfuse trace spans (`input={"skill": skill, "params": _mask_params(params), ...}` and the SDK-level `mask=_lf_mask` callback). `telegram_gateway.py`'s `tg_send()` calls (e.g. the `/status`, `/skills`, and skill-result reply paths) send `body`/`reply` content to the Telegram chat **unmasked** — the same SMS bodies and phone numbers carefully redacted before they reach Langfuse are sent in the clear to the Telegram thread itself. Masking needs to move to (or be duplicated at) the point where content leaves the process toward Telegram, not remain solely at the observability boundary.
8. **Fix the non-constant-time bearer-token comparison** in `bridge_server.py` (`auth != f"Bearer {token}"`) to use `hmac.compare_digest()`, matching the pattern already used correctly in `jarvis_core/core/signing.py`'s `verify_envelope()`.
9. **Confirm rotation status of the three previously-leaked secrets** (Section 6) directly with the credential owner, and update this document's Section 6.3 status once confirmed.
10. **Add TLS to the MQTT listener** once the above are in place, so the transport does not depend solely on Tailscale's tunnel encryption for confidentiality (Section 5.3). Lower priority than 1-9 since Tailscale-only encryption is a defensible interim posture for a single-operator mesh, but should not remain the permanent design.

---

## 9. Open Questions

- **TBD — confirm with user:** Has the Gmail App Password, ElevenLabs API key, and GitHub PAT referenced in Section 6 actually been rotated since the `git filter-repo` history rewrite?
- **TBD — confirm with user:** Is there an intended owner/process for issuing per-node MQTT credentials (Section 5.1) once per-node ACLs are implemented — e.g. does each new node (OPPO, Vivo, Arduino UNO R4 WiFi, future ESP32 nodes per `CLAUDE.md`'s hardware inventory) get a credential provisioned manually, or is there a planned enrollment flow?
- **TBD — confirm with user:** Should `bridge_server.py`'s single `BRIDGE_TOKEN` per device be split into scoped tokens (e.g. a read-only token vs. a destructive-action token) as an interim step before the full `core/` risk-kernel integration (Recommendation 3) lands, given that integration is nontrivial engineering work and the token-scoping fix is comparatively cheap?

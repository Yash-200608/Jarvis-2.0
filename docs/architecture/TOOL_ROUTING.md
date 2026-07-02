# Tool / Action Routing

Status: Planning phase — pre-implementation
Last updated: 2026-07-01

## Purpose and Scope

This document describes how "do a thing on a device" requests are dispatched
today across the JARVIS repo's fragmented islands, and specifies the target
architecture for a single, unified Brain-level tool/action registry. It is a
companion to [`SYSTEM_ARCHITECTURE.md`](./SYSTEM_ARCHITECTURE.md) (which
names the canonical Brain candidate) and
[`AGENT_ARCHITECTURE.md`](./AGENT_ARCHITECTURE.md) (which describes the
Planner/Critic/Executor reasoning path that will sit on top of this routing
layer). Security implications of routing and permission-checking are
detailed in [`SECURITY_MODEL.md`](./SECURITY_MODEL.md). See
[`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) for the live
state of each island referenced below, and
[`context/DECISION_LOG.md`](../context/DECISION_LOG.md) for the eventual
architecture decision record naming the canonical Brain.

Per repo-wide labeling convention, this document uses these names verbatim
and does not reinterpret their maturity: **legacy stack** (daily-use
ADB/`awesome_chat.py`), **jarvis_prod** (smoke-tested MQTT stack, never
live), **jarvis_core** (never-run Reflexion prototype), **root `core/`**
(dormant safety kernel with a live confirmed bug).

## Summary of Current Reality

There is no unified tool/action registry anywhere in the repo. Three
independent dispatch mechanisms exist, each with its own action catalog, its
own schema, its own transport, and its own (partial or absent) permission
model. None of them share a registry, a validator, or a safety gate. A
fourth component — root `core/tools.py` + `core/validator.py` — implements
exactly the kind of schema-validated, risk-scored registry the project
needs, but it is wired into neither live stack today.

| Island | Dispatch mechanism | Action catalog size | Schema validation | Risk/permission gate | Live today? |
|---|---|---|---|---|---|
| Legacy stack | LLM free-form JSON via `parse_device_command` | ~45 actions across phone+computer (prompt-defined, not code-defined) | None (LLM output trusted as-is) | None — prompt explicitly forbids refusal/confirmation | Yes, daily use over ADB |
| jarvis_prod | MQTT `Command`/`Response` correlator | 10 implemented (`phone.*`) + 14 referenced-but-undispatched (`pc.*`) | Dataclass field presence only (`protocol.py`) | None | Smoke-tested only, never live |
| jarvis_core | N/A — no PC-side skill executor confirmed wired at dispatch time (see [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md)) | N/A | N/A | Sandbox `safe_path()` checkpoint exists in design | Never run end-to-end |
| Root `core/` | In-process function call (`executor.execute_task`) | 8 tools (`core/tools.py`) | Full schema validation (`core/validator.py`) | Risk-scored (`core/risk.py`), but ordering bug bypasses it (see below) | Imported by nothing outside its own tests |

## 1. Legacy Stack — LLM-Parsed Free-Form Dispatch

**Files:** `hugginggpt/server/awesome_chat.py`, `hugginggpt/server/device_integration.py`, `hugginggpt/server/bridge_server.py`, `hugginggpt/server/phone_bridge.py`

This is the only dispatch path in daily use. It has no static action
registry at all — the catalog of valid actions exists only as prose inside
an LLM system prompt.

### Flow

1. Every inbound chat message reaches `chat_huggingface()` in
   `awesome_chat.py`, which calls `parse_device_command(text)`
   (`awesome_chat.py:450`) for **every** message, with no keyword
   pre-filter — the LLM itself decides `is_device_command: true/false`.
2. `parse_device_command`'s system prompt (`awesome_chat.py:465-603`)
   enumerates two flat lists of action names and parameter shapes inline in
   natural language: `=== PHONE actions ===` (23 actions: `open_app`,
   `close_app`, `take_screenshot`, `send_sms`, `send_whatsapp`,
   `send_telegram`, `send_instagram_dm`, `send_message`, `make_call`,
   `lookup_contact`, `end_call`, `tap`, `swipe`, `type_text`, `press_key`,
   `set_volume`, `get_battery`, `get_device_info`, `list_apps`,
   `lock_device`, `unlock_device`, `push_file`, `pull_file`,
   `record_screen`) and `=== COMPUTER actions ===` (~27 actions including
   `run_command` — arbitrary shell execution — `delete_file` recursive on
   directories, `shutdown_device`, `kill_process`, `send_email`).
3. The prompt explicitly instructs the LLM: "NEVER refuse, NEVER ask for
   confirmation, NEVER add safety caveats" and "Never emit
   `is_device_command:false` just because an action is destructive"
   (`awesome_chat.py:469-470, 601`). There is no code-level permission
   check anywhere in this path — the LLM's own compliance with the prompt
   is the only gate.
4. The LLM returns a single JSON object:
   `{"reasoning": "...", "is_device_command": bool, "commands": [{"target": "phone"|"computer", "action": "...", "params": {...}}]}`.
   `parse_device_command` extracts this via a brace-counting fallback
   parser (`_extract_first_json_object`, `awesome_chat.py:674+`) if the LLM
   wraps JSON in prose.
5. `chat_huggingface()` (`awesome_chat.py:1315-1337`) takes the returned
   `commands` list and calls `DEVICE_ORCHESTRATOR.execute_multi(device_commands)`.
6. `DeviceOrchestrator` (`device_integration.py:74-128`) groups commands by
   `target`, running different-target commands in parallel threads and
   same-target commands sequentially (with a 4.0s delay after
   `open_app`/`open_url` and 0.8s otherwise, to let apps load before the
   next action — `device_integration.py:69-71, 109-117`).
7. Each command is dispatched via `EndpointDeviceAdapter.execute(action, params)`
   (`device_integration.py:22-65`), which does a blind `POST {base_url}/execute`
   with `{"action": ..., "params": ...}` as the JSON body and a bearer
   token — no client-side schema check of the action name or params before
   sending.
8. The adapters are wired at startup (`awesome_chat.py:190-203`) from
   `config["integrations"]`: `adapters["phone"]` → `PHONE_CFG.base_url`,
   `adapters["computer"]` → `COMPUTER_CFG.base_url`.

### Receiving bridges

- `hugginggpt/server/bridge_server.py` — the **computer** bridge. Started
  with `python bridge_server.py --device computer --port 8092`; binds
  `--port` default `8092` (`bridge_server.py:803, 818`, env override
  `BRIDGE_PORT`). This process receives the `run_command`, `delete_file`,
  `shutdown_device`, etc. POSTs and actually executes them on the PC.
- `hugginggpt/server/phone_bridge.py` — the **phone** bridge. Started with
  `python phone_bridge.py --port 8091`; binds `--port` default `8091`
  (`phone_bridge.py:845, 862`, env override `BRIDGE_PORT`). Receives the
  phone-side actions (`send_sms`, `tap`, `swipe`, etc.) and executes them
  over ADB.

Neither bridge validates the incoming `action` name against a shared
schema — each is presumably a large `if action == "..."` / dict-dispatch
internally (not audited in this document; see
[`COMPUTER_CONTROL.md`](../specifications/COMPUTER_CONTROL.md) for the
per-bridge command surface). The action catalog therefore exists in three
unsynchronized places: the LLM prompt string, `bridge_server.py`'s internal
dispatch, and `phone_bridge.py`'s internal dispatch. There is no single
source of truth, and no compile-time or runtime guarantee that a name the
LLM invents actually exists on the receiving bridge — a typo or
hallucinated action name fails only at the bridge, if at all.

## 2. jarvis_prod — MQTT Command/Response Correlator

**Files:** `hugginggpt/server/jarvis_prod/protocol.py`, `brain_pc.py`, `agent_phone.py`

### Wire protocol

`protocol.py` defines the shared envelope (`SCHEMA = 1`) used by both
`brain_pc.py` and `agent_phone.py` — the file is meant to be copied
byte-identical to both machines. Key pieces:

- `Command` dataclass: `skill: str`, `params: dict`, `to: str`, `from_: str`,
  `id: str` (UUID4 hex, `_new_id()`), `ttl: float = 30.0`, `type: str = "cmd"`.
- `Response` dataclass: `id: str` (echoes the `Command.id`), `ok: bool`,
  `result: Any`, `error: str | None`.
- Topic builder `T` — one source of truth for topic strings, all namespaced
  `jarvis/node/<node>/...`: `T.cmd(node)`, `T.response(node)`,
  `T.event(node)`, `T.presence(node)`, `T.state(node)`, `T.heartbeat(node)`.
  `cmd`/`response`/`event`/`heartbeat` are not retained; `presence`/`state`
  are retained. QoS 1 for everything except heartbeat (QoS 0).

### Dispatch flow (Brain side)

`brain_pc.py`'s `Agent.send_command` (around `brain_pc.py:296-369`) is the
correlator:

1. Checks a Reflector circuit breaker (`self.jobs.should_skip(skill, target)`)
   — if a skill has failed repeatedly on that target, the command is
   rejected immediately with a `reflector: skill ... has failed N× in a
   row` error, without ever publishing.
2. Builds a `Command(skill=skill, params=params, to=target, from_=self.node_id, ttl=timeout)`.
3. Creates an `asyncio.Future` and stores it in `self._pending[cmd.id] = fut`
   — this dict (`node_id/req_id → Future[Response]`, `brain_pc.py:164-165`)
   is the entire correlator: a UUID-keyed map from outstanding command IDs
   to the asyncio Future that will be resolved when a matching `Response`
   arrives.
4. Publishes to `T.cmd(target)` (i.e. `jarvis/node/<target>/cmd`) with
   `qos=QOS` (1).
5. Awaits the Future with `asyncio.wait_for(fut, timeout=timeout)`. On a
   separate message-loop task, an incoming `Response` on
   `jarvis/node/+/response` is matched by `resp.id` against
   `self._pending` (`brain_pc.py:668`) and used to resolve the
   corresponding Future.
6. On timeout, returns a `Response(ok=False, error="timeout ...")`, with a
   different hint depending on whether the target's last known presence
   was offline (`brain_pc.py:355-367`).

### Dispatch flow (phone-side skill table)

`agent_phone.py` defines a flat dict, `SKILLS: dict[str, SkillFn]`
(`agent_phone.py:282-293`), mapping **exactly 10 implemented skill names**
to async handler functions:

```
phone.battery        -> skill_battery
phone.tts             -> skill_tts
phone.sms.send        -> skill_sms_send
phone.torch           -> skill_torch
phone.vibrate         -> skill_vibrate
phone.notify          -> skill_notify
phone.ring            -> skill_ring
phone.location         -> skill_location
phone.app.open        -> skill_open_app
phone.whatsapp.send    -> skill_whatsapp_send
```

`Agent._handle_cmd` (`agent_phone.py:451+`) looks up
`fn = SKILLS.get(cmd.skill)` and, if found, calls it and publishes a
`Response` back on `T.response(node_id)`. If `cmd.skill` is not in `SKILLS`,
the phone agent presumably returns an error `Response` (not confirmed by
this audit — see `context/CURRENT_STATUS.md`) — but crucially, this table
only ever receives commands addressed `to="phone"` (or whatever the phone
node's id is). It has no knowledge of, and does not need to handle, `pc.*`
skills.

### The `pc.*` gap — confirmed, no receiving dispatcher exists

`brain_pc.py`'s own `_known_skills` set (`brain_pc.py:196-205`) advertises
**14 `pc.*` skills** as if they were real, dispatchable capabilities:

```
pc.volume.set, pc.volume.mute, pc.media.control, pc.type, pc.hotkey,
pc.click, pc.screenshot, pc.app.open, pc.url.open, pc.process.list,
pc.process.kill, pc.system.info, pc.system.lock, pc.system.shutdown,
pc.shell.run
```

This set is used only to populate `list_skills=lambda: sorted(self._known_skills)`
for the `/skills` Telegram command (`brain_pc.py:460`) — i.e., it is
advertised to the user as available, not validated against any real
dispatch table. Corroborating references to the same non-existent surface:

- `telegram_gateway.py:13` — comment: "prompt that ends up in `phone.*` or
  `pc.*` skill calls."
- `telegram_gateway.py:382` — help text example: `` `!pc.volume.set level=20` ``.
- `schedule.example.json:31` — a scheduled job with `"skill": "pc.system.lock"`.
- `requirements_pc.txt` (per synthesis audit) pins `pyautogui`/`psutil`,
  dependencies that would be needed by a PC-side executor — but no file in
  `jarvis_prod` subscribes to `jarvis/node/pc/cmd` or implements any
  `SKILLS`-equivalent dict keyed by `pc.*` names.

**Runtime consequence:** if `brain_pc.py`'s `send_command("pc.system.lock", ...)`
is ever called with `target="pc"` (or whatever node id a PC agent would
use), `Agent.send_command` will publish a valid `Command` to
`jarvis/node/pc/cmd`, register a `Future` in `self._pending`, and then wait
— but because no process subscribes to that topic and dispatches `pc.*`
skills, no `Response` is ever published, and the call fails only via the
generic 30-second `ttl` timeout path (`brain_pc.py:355-367`), returning
`ok=False, error="timeout 30.0s. ..."`. This is silent from the catalog's
perspective: `_known_skills` and the Telegram help text present `pc.*` as a
working capability, and the only signal that it isn't is a timeout
indistinguishable from "PC node is offline." There is no fast-fail, no
"unknown skill" error, no schema validation catching this before the
30-second wait.

### Schema strength

`protocol.py`'s `Command`/`Response` dataclasses validate only structural
shape (field presence/types via Python dataclass construction,
`from_bytes`/`to_bytes` round-trip) — they do not validate that
`cmd.skill` is a member of any registry, nor that `cmd.params` matches an
expected shape for that skill. That validation, where it exists at all, is
implicit in each `SkillFn`'s own parameter access (e.g., `skill_sms_send`
presumably KeyErrors or defaults if a param is missing — not audited here).

## 3. jarvis_core — No Confirmed PC-Side Dispatcher

**Files:** `jarvis_core/` (Planner/Critic/Executor Reflexion loop)

Per the synthesis audit, `jarvis_core` has never been run end-to-end: no
`.env`, no `history.log`, no `logs/`, no `brain/chroma/`,
`knowledge_graph.md` still template-only, no `NEED_ATTENTION.md` ever
written. Its design includes a `sandbox.safe_path()` chokepoint intended to
gate both the Executor and a post-Critic-approval re-check, which is
architecturally the closest existing design to a permission-checked
dispatcher — but because the module has never executed against a live
Ollama instance or a live MQTT broker, this document cannot confirm what
its action catalog or dispatch mechanism looks like in practice, only what
exists in source. Treat any claim about jarvis_core's tool routing as
unverified until Phase 2 validation (see
[`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md)) produces
observed runtime evidence. TBD — confirm with user once jarvis_core has a
live run to audit.

## 4. Root `core/` — Schema-Validated Registry, Currently Unused

**Files:** `core/tools.py`, `core/validator.py`, `core/executor.py`, `core/risk.py`, `core/safety.py`

This is architecturally the strongest piece of the four islands from a
routing-correctness standpoint, and is imported by nothing outside its own
three test files
(`core/tests/test_execution_safety.py`, `tests/test_adversarial_execution.py`,
`tests/test_executor_safety.py`).

### Registry

`core/tools.py` defines `TOOLS: dict[str, dict]` — exactly **8 tools**,
each with a `params` list, a `param_types` dict, and a static `risk` level:

| Tool | Params | Risk |
|---|---|---|
| `open_app` | `app_name: str` | low |
| `delete_file` | `path: str` | high |
| `move_to_trash` | `path: str` | medium |
| `send_sms` | `phone_number: str`, `message: str` | medium |
| `send_email` | `to: str`, `subject: str`, `body: str` | medium |
| `send_whatsapp` | `phone_number: str`, `message: str` | medium |
| `shutdown_device` | (none) | high |
| `read_file` | `path: str` | low |

`get_tool_schema(name)` (`core/tools.py:45-46`) is the single lookup
function other modules use to resolve a tool name to its schema.

### Validation

`core/validator.py`'s `validate_action(action: dict) -> bool` checks, in
order: `action` is a dict with `name` and `params` keys; `name` is a
non-empty string; `params` is a dict; `get_tool_schema(name)` resolves
(returns `False` — i.e., rejects — if the tool name is unknown); the
param set exactly matches the schema's required params (`set(params) ==
set(required_params)`); each param's runtime type matches
`param_types` (defaulting to `str`); and non-empty-string enforcement for
string params. This is real schema validation — the strongest of any
island in this document — but it only runs against the 8-tool `TOOLS`
registry, and only within `core/executor.py`'s own call path.

### The confirmed live bug in the risk-gating order

`core/executor.py:execute_task()` runs, in this order (`core/executor.py:77-98`):

```
77  if not validator.validate_action(action):        # schema check
78      return _fail(...)
80  action = safety.apply_safety(action)              # REWRITES action["name"]
83  action_risk = risk.get_risk_level(action["name"])  # risk check on REWRITTEN name
84  if action_risk == "high": ...                      # confirmation gate
```

`safety.apply_safety()` (`core/safety.py:4`) rewrites certain high-risk
actions to lower-risk equivalents (confirmed: `delete_file` →
`move_to_trash`) **before** `risk.get_risk_level()` (`core/risk.py:4`) is
ever consulted. Because `action["name"]` has already been mutated to
`move_to_trash` (medium risk) by the time the risk check runs, the
high-risk confirmation gate at line 84 never triggers for what was
originally a `delete_file` call — a nominally high-risk destructive action
executes with zero confirmation. This is reproduced directly by calling
`executor.execute_task("cmd-1", {"name": "delete_file", "params": {"path": ...}})`
and observing `status: "success"` with no `confirmation_required` step.
Three tests in the module's own adversarial suite currently fail against
this behavior:
`test_high_risk_action_requires_confirmation_before_safety_rewrite`,
`test_confirmation_bypass_attack_is_rejected`,
`test_external_confirmation_does_not_bypass_safeguards`.

This bug must be fixed (risk evaluated on the pre-rewrite action name, or
the ordering reversed) before `core/` is wired into any live dispatch path
— see Phase 0 in
[`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md). It is called
out here specifically because the unified registry proposed below is
recommended to be built on top of this module, and the bug must not be
inherited into the unified Brain-level router.

### Naming collision

Root `core/` and `jarvis_core/core/` are both importable as bare `import
core` depending on `sys.path` order.
`jarvis_core/tests/conftest.py` inserts `jarvis_core/` onto `sys.path`
specifically so its own `from core.sandbox import ...` resolves
internally, never touching root `core/` — this happens to work today by
accident of test isolation, not by design, and is a latent footgun for any
future merge. Root `core/` should be renamed before being adopted as the
unified kernel (see Recommendation below and
[`SECURITY_MODEL.md`](./SECURITY_MODEL.md)).

## Comparison Table — Action Catalogs Side-by-Side

| | Legacy stack | jarvis_prod (`phone.*`) | jarvis_prod (`pc.*`) | Root `core/` |
|---|---|---|---|---|
| Where the catalog lives | LLM system prompt text (`awesome_chat.py`) | `SKILLS` dict (`agent_phone.py`) | `_known_skills` set (`brain_pc.py`) — advertised only | `TOOLS` dict (`core/tools.py`) |
| Count | ~50 (prose-defined) | 10 | 14 (referenced, zero implemented) | 8 |
| Enforced by code at dispatch time? | No — LLM output trusted | Yes — dict lookup, `KeyError`/`None` on miss | No receiving dispatcher exists at all | Yes — `validate_action()` |
| Params schema-checked? | No | Implicit per-handler only | N/A | Yes — types + required-set match |
| Risk/permission gate? | No — explicitly disabled in prompt | No | No | Yes — but currently bypassable (bug above) |
| Confirmation flow? | No — explicitly forbidden in prompt | No | No | Yes — `confirmation_required` status for high-risk actions |

## Recommendation: Unified Brain-Level Tool/Action Registry

The current state is four independent, duplicated, non-interoperable action
catalogs, one of which (`pc.*`) is advertised but entirely unimplemented,
and one of which (root `core/`) has the correct shape but a live safety bug
and zero production usage. The target architecture replaces all four with
**one schema, one router, one permission check**, owned by the Brain layer
per [`SYSTEM_ARCHITECTURE.md`](./SYSTEM_ARCHITECTURE.md) and
[`specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md).

### Design

1. **One schema.** Adopt (and extend) root `core/tools.py`'s `TOOLS`
   registry shape — `name → {params, param_types, risk}` — as the single
   source of truth for every dispatchable action in the system, phone-side
   and PC-side alike. Every action currently expressed as free prose in the
   legacy stack's LLM prompt, every `phone.*` skill in
   `agent_phone.py`'s `SKILLS` dict, and every advertised-but-missing
   `pc.*` skill in `brain_pc.py`'s `_known_skills` must be re-expressed as
   an entry in this one registry before it can be dispatched. This closes
   the `pc.*` gap by construction: a `pc.system.lock` entry cannot be
   advertised (e.g. via `/skills` in Telegram) unless it also has a
   registered schema and a registered handler — the current failure mode
   (advertised in three places, implemented in zero) becomes structurally
   impossible once advertisement and dispatch read from the same registry.
2. **One router.** A single Brain-side dispatcher resolves `{skill/tool
   name, params, target}` triples against the unified registry, regardless
   of whether the request originated from the legacy stack's LLM parser,
   jarvis_prod's MQTT `Command` correlator, jarvis_core's Reflexion
   Executor, or a future thin client. Concretely, this means:
   - The legacy stack's `parse_device_command` LLM call should stop
     returning free-form `{target, action, params}` guessed from prose and
     instead be constrained (e.g. via function-calling / structured output
     against the unified schema) so `DeviceOrchestrator.execute` validates
     against the same registry before it ever reaches
     `EndpointDeviceAdapter.execute`'s blind POST.
   - jarvis_prod's `Agent.send_command` should validate `skill` against the
     unified registry (schema + risk) **before** publishing to
     `T.cmd(target)`, instead of discovering "no such skill" only via a
     30-second MQTT timeout — this alone fixes the `pc.*` silent-timeout
     symptom even before a PC-side executor exists, by fast-failing with an
     explicit "unregistered skill" error.
   - A new PC-side executor process (the currently-missing piece — see
     `roadmap/MASTER_ROADMAP.md` Phase 3, "Build the missing pc.* skill
     executor") subscribes to `jarvis/node/pc/cmd` and dispatches through
     the same unified registry/router that phone-side and legacy-stack
     actions go through, rather than inventing a fifth ad hoc dispatch
     table.
3. **One permission check.** Every dispatch — regardless of origin —
   passes through root `core/`'s (renamed, post-fix) safety kernel:
   `validator.validate_action()` for schema conformance,
   `risk.get_risk_level()` evaluated **before** any name-rewriting
   (fixing the confirmed bug above), `safety.apply_safety()` for
   rewrites, and the existing `idempotency`/`sandbox`/`undo`/`retry`
   machinery for execution safety. This retires three separate,
   inconsistent (or absent) permission models — the legacy stack's "never
   refuse" prompt instruction, jarvis_prod's total absence of a risk gate,
   and jarvis_core's as-yet-unverified `safe_path()` design — in favor of
   the one module in the repo that has an adversarial test suite proving
   out TOCTOU protection, confirmation-bypass rejection, and sandbox
   escape resistance (565 lines of tests across
   `core/tests/test_execution_safety.py`,
   `tests/test_adversarial_execution.py`,
   `tests/test_executor_safety.py`).
4. **Clients register, they do not each define.** Per the AI-OS thin-client
   vision, the web dashboard, CLI, voice interface, and any future AR/VR
   client should never carry their own action catalog. They call into the
   Brain's unified router with `{skill, params, target}` and receive a
   structured `Response`/rejection — mirroring jarvis_prod's `Command`/
   `Response` envelope shape, which is otherwise sound and reusable
   (see [`specifications/VOICE_SYSTEM.md`](../specifications/VOICE_SYSTEM.md)
   and [`specifications/UI_SPEC.md`](../specifications/UI_SPEC.md) for
   client-side consumption of this contract).

### What this replaces

| Current catalog | Fate under unified registry |
|---|---|
| Legacy stack's LLM-prompt action list (`awesome_chat.py`) | Re-expressed as registry entries; LLM output constrained/validated against the registry instead of trusted as free JSON |
| jarvis_prod `SKILLS` dict (`agent_phone.py`, 10 `phone.*` skills) | Migrated into the unified registry as `phone.*` entries with their existing handlers |
| jarvis_prod `_known_skills` `pc.*` set (14 skills, 0 implemented) | Either implemented for real against the unified registry (new PC-side executor), or removed from advertisement until implemented — never left as advertise-only again |
| Root `core/tools.py` `TOOLS` (8 tools) | Becomes the literal seed/shape of the unified registry, once the safety-ordering bug is fixed and the module is renamed to avoid the `jarvis_core/core/` sys.path collision |

### Sequencing note

Building the unified registry is explicitly **not** a Phase 0 task. Per
[`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), Phase 0 only
fixes the safety-ordering bug and repo hygiene issues in place; the
unified registry is a Phase 3 ("Merge Transport + Reasoning") deliverable,
built only after Phase 1's architecture decision record names the
canonical Brain and Phase 2 proves jarvis_core can run end-to-end. Standing
up a fifth action catalog before that decision is made would add a fourth
(or fifth) unreconciled island rather than resolving the existing three.

## Open Questions

- TBD — confirm with user: what identifier will the PC-side executor
  register as on the MQTT node topics (`jarvis/node/<?>/cmd`) — `"pc"`,
  the machine hostname, or something else? `brain_pc.py`'s `_known_skills`
  implies a target named `pc` but this is not confirmed anywhere in code.
- TBD — confirm with user: whether the legacy stack's ~50-action prompt
  surface will be fully ported into the unified registry, or trimmed —
  several actions (`run_command` with arbitrary shell, `delete_file`
  recursive on directories) significantly overlap with root `core/`'s
  existing `TOOLS` risk categories and may be intentionally narrowed during
  migration.
- TBD — confirm with user: jarvis_core's actual dispatch mechanism, once a
  live run produces observable evidence (Phase 2). This document does not
  claim to know how (or whether) jarvis_core would integrate with a PC-side
  executor.

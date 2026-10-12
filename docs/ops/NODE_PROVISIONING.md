# Chimera Node Provisioning

Status: Current — Priority #4 Milestone 9 (2026-07-05)
Scope: everything an operator needs to bring a new JARVIS node onto the
execution spine, and to provision the Brain-side credentials it talks to.
This closes the "API-key provisioning process is undocumented" gap flagged
in Brain's `docs/audits/PRIORITY-2-READINESS-REVIEW.md` (blocker 6).

---

## 1. Secrets inventory

| Secret | Lives where | Used for |
|---|---|---|
| Brain API keys (`JARVIS_API_KEYS`) | `Brain/.env` (gitignored) | HTTP callers (dashboard, curl) authenticating to Brain's API |
| Spine HMAC key (`JARVIS_MQTT_HMAC_KEY` on Brain, `CHIMERA_HMAC_KEY` on nodes) | `Brain/.env` + each node's environment | Signing/verifying every `ChimeraEnvelope` on the wire |
| Approver key | registered via `IdentityService.register_key` (see §3) | Approving risk-gated device actions (NP-7) |

Generate any of them with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Never commit a secret; never pass one as a CLI flag (process lists and
shell history leak them). Environment variables or a chmod-restricted env
file only.

## 2. Brain-side provisioning

1. **API key for a client (e.g. the dashboard):** add to `Brain/.env`:

   ```
   JARVIS_API_KEYS={"<token>": "owner"}
   ```

   A key configured this way resolves to `default_scopes()` — everything
   except `admin` and `devices.approve`. Restart `brain-api` to pick it up.

2. **Spine HMAC key:** add to `Brain/.env`:

   ```
   JARVIS_MQTT_ENABLED=true
   JARVIS_MQTT_HMAC_KEY=<64-hex-token>
   ```

   Every node must be given the SAME key (as `CHIMERA_HMAC_KEY`). Rotating
   it is a coordinated restart of Brain and all nodes.

3. **Approver key (NP-7):** approval privilege is deliberately not
   grantable via `JARVIS_API_KEYS` (those keys get default scopes). An
   approver key is registered in code — e.g. a short startup hook or a
   one-off script run inside Brain's venv:

   ```python
   from identity import (SCOPE_DEVICES_APPROVE, SCOPE_DEVICES_READ,
                         IdentityService, Principal, get_identity_service)
   get_identity_service().register_key(
       "<approver-token>",
       Principal(user_id="owner", client_id="api",
                 scopes=frozenset({SCOPE_DEVICES_APPROVE, SCOPE_DEVICES_READ}),
                 metadata={"key_id": "approver-1"}),
   )
   ```

   Note the distinct `key_id`: the approval flow rejects an approver whose
   identity tuple matches the requester's, so approver and requester keys
   must carry different key_ids. (Keys seeded from `JARVIS_API_KEYS` get a
   token-digest key_id automatically.)

## 3. PC node (Windows)

```powershell
$env:CHIMERA_NODE_ID  = "jarvis-core-pc"
$env:CHIMERA_HMAC_KEY = "<the spine key>"
# optional: $env:CHIMERA_BROKER_HOST, $env:CHIMERA_BROKER_PORT, $env:CHIMERA_CEILINGS
python run_chimera_node.py
```

Or with an env file: `python run_chimera_node.py --env-file C:\chimera\node.env`.

**Operator ceilings (NP-8):** create a JSON file and point
`CHIMERA_CEILINGS` at it to change the node's local policy:

```json
{"max_auto_risk": "medium", "disabled_skills": []}
```

Defaults (no file) are fail-closed: `max_auto_risk=medium`, so
`pc.shell.run` (high) is refused locally no matter what Brain sends.
Raising to `"high"` is an explicit operator act; changes need a node
restart (deliberate — policy is not hot-reloaded).

**Auto-restart:** Task Scheduler ("At startup" + restart-on-failure), or
NSSM: `nssm install ChimeraNode python C:\...\run_chimera_node.py` with
the CHIMERA_* variables set in the service environment.

## 4. Phone node (Termux on the Nothing Phone 3a Lite)

Physical steps only the operator can perform:

1. Install **Termux** and **Termux:API** (F-Droid builds — Play Store
   builds are stale) and the Tailscale Android app; `tailscale up` and
   note the PC's Tailscale IP (the broker host).
2. Bind the PC's Mosquitto to the Tailscale interface (see §5).
3. Choose a runtime (§4a native Termux, or §4b proot Ubuntu when Termux
   ships Python 3.14 and `pydantic` cannot install).
4. Verify from the PC: the dashboard's Devices panel shows
   `nothing-phone-3a` online with its declared skills, and
   `phone.battery` answers with real battery data.
5. Disable Android battery optimization for Termux or Doze will kill the
   node.

### 4a. Native Termux (Python ≤ 3.13)

When `python3 -V` reports 3.13 or older, use the direct path:

```sh
cd ~/Jarvis-2.0
bash scripts/termux_install.sh
python run_chimera_node.py --env-file ~/.chimera/node.env
```

On 3.13 without a pydantic wheel, try the prebuilt Android index first:

```sh
pip install pydantic-core==2.46.3 \
  --extra-index-url https://eutalix.github.io/android-pydantic-core/
pip install pydantic aiomqtt paho-mqtt loguru python-dotenv
```

**Auto-restart:** use the proot path below (§4b) and its service installer;
termux-services reads `$PREFIX/var/service`, not `~/.config/sv`.

### 4b. proot Ubuntu (recommended when Termux is Python 3.14)

Termux's default Python 3.14 has no `pydantic-core` wheel for Android;
building from source with Rust fails on the `aarch64-linux-android` target.
Ubuntu inside **proot-distro** uses normal `linux_aarch64` wheels instead.

One-time setup (from native Termux, repo at `~/Jarvis-2.0`):

```sh
cd ~/Jarvis-2.0
git pull   # needs scripts/termux_proot_setup.sh from this repo
bash scripts/termux_proot_setup.sh
```

That installs `proot-distro` + Ubuntu, creates `.venv-proot`, installs
dependencies, and reuses `~/.chimera/node.env` if you already created it.

Start the node (always from **native Termux**, not inside the Ubuntu shell):

```sh
bash ~/Jarvis-2.0/scripts/run_chimera_proot.sh
```

`phone.*` skills call Termux:API binaries; the wrapper puts
`/data/data/com.termux/files/usr/bin` on `PATH` inside proot so
`termux-battery-status` and friends still work.

**Auto-restart (termux-services):**

```sh
bash ~/Jarvis-2.0/scripts/termux_service_install.sh   # --uninstall to remove
# restart Termux once so runsvdir starts, then:
sv status chimera-node        # sv down / sv up to stop / start
tail -f $PREFIX/var/log/sv/chimera-node/current
```

The service holds a wake lock (`termux-wake-lock`) so Doze does not freeze
the node, and restarts it if it exits. Don't also start
`run_chimera_proot.sh` by hand — two nodes with one `CHIMERA_NODE_ID` fight
over the MQTT client id. *Status: written 2026-10-12, syntax-checked only;
not yet exercised on the phone (it was offline).*

**Stop the PC sim first:** if `nothing-phone-3a` is already running on the
PC (sim env file), kill that process before starting the real phone node —
duplicate MQTT client IDs fight each other.

## 5. Broker binding for Tailscale

The local Mosquitto currently listens on localhost with anonymous access
(fine while everything is one machine). For the phone to reach it, add to
`mosquitto.conf`:

```
listener 1883 <PC-tailscale-IP>
allow_anonymous true
```

and restart Mosquitto. The Tailscale interface is the trust boundary
(only your tailnet devices can reach it); envelope signatures are the
message-layer authentication on top (NP-4). Per-node broker ACLs/TLS are
deliberately Priority #5 hardening (frozen definition, Section 7 item 14).

## 6. Adding a future node type

A new node needs exactly: a unique `CHIMERA_NODE_ID`, the shared
`CHIMERA_HMAC_KEY`, reachability to the broker, and skills registered in a
`SkillRegistry` + declared via `publish_capabilities()`. Brain requires no
code change to plan over it (NP-5 / ecosystem architecture §9) — its
declared skills become plannable the moment the declaration lands.

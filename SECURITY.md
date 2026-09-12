# Security Policy

## Reporting a vulnerability

Do **not** open a public issue for a vulnerability, leaked credential, or
bridge/MQTT bypass.

Use GitHub **Security Advisories** (Privately report a vulnerability) on
[`Yash-200608/Jarvis-2.0`](https://github.com/Yash-200608/Jarvis-2.0). Include:

- Affected island (legacy bridge, `jarvis_prod`, `jarvis_core`, `core/`,
  `jarvis_node_sdk`)
- Impact (what an unauthenticated or under-privileged caller can do)
- Reproduction at a high level — enough to confirm, not a weaponized PoC
- Whether any secret was exposed and should be rotated

You should hear back. Valid reports are fixed before any public write-up.

Non-security bugs belong in GitHub Issues.

## Operator rules

- **No secrets in YAML.** `hugginggpt/server/configs/config.default.yaml`
  uses `${VAR}` placeholders resolved at load time.
- **Copy [`.env.example`](.env.example)** (and
  [`jarvis_core/.env.example`](jarvis_core/.env.example) if you run that
  tree). Never commit a filled `.env`.
- **Stage explicit paths.** `git add .` has already swept secrets into
  history once.
- **Device bridges are privileged.** `BRIDGE_TOKEN` (legacy) and HMAC keys
  (MQTT / node SDK) gate OS- and phone-level actions. Treat a leaked token
  as full host access on that node.
- **Dry-run first.** `BRIDGE_DRY_RUN=true` for legacy bridges.

The audited model — including known gaps in the legacy bridges and the
confirmation-order bug in root `core/executor.py` — is
[docs/architecture/SECURITY_MODEL.md](docs/architecture/SECURITY_MODEL.md).
This file is the reporting policy, not that audit.

## Historical leak

Git history was rewritten (`git filter-repo`) after a Gmail app password,
an ElevenLabs API key, and an upstream GitHub PAT were committed. Those
values were public for a few hours. **Rotate them** even though HEAD and
rewritten history no longer contain them.

Ecosystem trust rules (fail-closed, signed envelopes, last-line safety at
the node) live in Brain's
[CHIMERA_ECOSYSTEM_ARCHITECTURE.md](https://github.com/Yash-200608/Brain/blob/main/docs/CHIMERA_ECOSYSTEM_ARCHITECTURE.md)
§5–§6.

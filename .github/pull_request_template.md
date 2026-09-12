## Summary

<!-- What changed and why. Link the issue if one exists. -->

## Island

<!-- jarvis_node_sdk / legacy / jarvis_prod / jarvis_core / core/ / docs -->

## Test plan

- [ ] `pytest jarvis_node_sdk/tests -q` (if the node SDK or contract changed)
- [ ] Legacy / jarvis_prod tests if those trees changed
- [ ] `BRIDGE_DRY_RUN=true` before any live bridge action

## Boundary

- [ ] No new planner, cross-session memory store, or Brain-package import
- [ ] Chimera traffic stays on `chimera/<node>/<verb>` and signed envelopes
- [ ] Destructive device actions stay dry-run or confirmation-gated by default

## Secrets and docs

- [ ] No `.env`, tokens, HMAC keys, or app passwords in the diff
- [ ] Staged with explicit paths (not `git add .`)
- [ ] Docs / `.env.example` / `CHANGELOG.md` updated if operators or env vars changed

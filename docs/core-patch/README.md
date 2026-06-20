# Core Patch: internal_session_wake_v1

This directory contains a clean, upstream-candidate patch artifact that
implements the host capability required by the self-wake plugin.

The patch is generated from Zoe's local Hermes wake commits and is included as
a reviewable artifact only. The plugin never applies or monkeypatches Hermes
core by itself.

- Patch file: `0001-internal-session-wake-v1.patch`
- Base commit: `b7f0c9cd52febc32f4d2fb6205f3291c9e7bcf98`
- Source commits: `3d122a1ac`, `62e0f2e52`, `489720101`, `f45057c48`, `4a9087b67`, `1d5b98c39`

## What the patch does

Adds a generic `GatewayRunner.wake_session(...)` primitive to Hermes core:
- Resolves target sessions by key or ID
- Creates/updates durable receipts
- Dispatches through the platform adapter pipeline
- Handles active-session queueing without interruption
- Supports dedupe by key

## Patch series

1. `feat(gateway): add internal session wake primitive`
2. `feat(kanban): route session-targeted notify subscriptions through internal wake`
3. `feat(send_message): wake mirrored target session after outbound delivery`
4. `feat(cron): optionally wake target session after delivery`
5. `docs/tests: capability contract and E2E fixtures`

## How to apply

```bash
cd $HERMES_HOME/hermes-agent
git apply docs/core-patch/0001-internal-session-wake-v1.patch
# Run tests
scripts/run_tests.sh tests/
```

## Upstream PR

See `upstream-pr.md` for the PR description and checklist.

## Test plan

See `test-plan.md` for verification steps.

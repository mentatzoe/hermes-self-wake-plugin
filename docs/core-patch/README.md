# Core Patch: internal_session_wake_v1

This directory contains a clean, upstream-ready patch series that
implements the host capability required by the self-wake plugin.

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

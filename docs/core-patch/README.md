# Core Patch: internal_session_wake_v1 (optional reference / upstream candidate)

This directory contains an **optional** reference patch artifact that
implements the host capability required by the self-wake plugin.

> **This patch is NOT required to use the plugin.** The bundled compat shim
> (`self_wake/compat_shim.py`, enabled via `self_wake.compat_shim_enabled:
> true`) provides the plugin-required Kanban wake surfaces on vanilla Hermes at runtime without
> patching core. This patch is the **upstream-candidate** artifact: the
> cleanest native implementation, and the source the shim's implementations
> are derived from verbatim. Apply it when you want the native capability
> (no monkeypatching) plus the active-session queueing refinement and
> cron/send-message wake that the shim does not provide.

The plugin never applies or monkeypatches Hermes core by default; the shim is
opt-in and the patch is operator-applied.

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

It also adds Kanban notifier routing, cron-delivery wake, and send-message
mirror wake. The compat shim provides the first four (wake primitive + receipts
+ lookup + Kanban notifier routing) but NOT the active-session queueing
refinement, cron wake, or send-message wake — see [`../compatibility.md`](../compatibility.md) for
the honest gap list.

## Patch series

1. `feat(gateway): add internal session wake primitive`
2. `feat(kanban): route session-targeted notify subscriptions through internal wake`
3. `feat(send_message): wake mirrored target session after outbound delivery`
4. `feat(cron): optionally wake target session after delivery`
5. `docs/tests: capability contract and in-process fixture tests`

## How to apply (optional)

```bash
cd $HERMES_HOME/hermes-agent
git apply --check /path/to/hermes-self-wake-plugin/docs/core-patch/0001-internal-session-wake-v1.patch
git apply /path/to/hermes-self-wake-plugin/docs/core-patch/0001-internal-session-wake-v1.patch
# Run core tests
scripts/run_tests.sh tests/
```

When the native capability is present after applying, the compat shim
auto-detects it and does not install (even if `compat_shim_enabled: true`).

## Upstream PR

See `upstream-pr.md` for the PR description and checklist.

## Test plan

See `test-plan.md` for verification steps.

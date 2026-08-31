---
name: kanban-self-wake
description: "Use when Kanban or configured cron delivery must wake an existing Hermes session with durable receipts."
---

# Kanban and Cron Self-Wake

## Health gate

Run `self_wake_doctor` before creating or trusting wake routes.

- `kanban_wake_capability: ok` proves Kanban.
- `cron_delivery_capability: ok` proves configured cron delivery.
- Top-level `mode=full` is Kanban-scoped and never proves cron.
- If `cron.wake_agent_on_delivery=true` and cron routing is absent, doctor must
  report `ok=false`, `mode=degraded`, and restart/update remediation.
- If cron policy is unreadable/unparseable or absent and routing is absent,
  policy is unknown (not off): require `ok=false`, `mode=degraded`, and exact
  config-read/routing-verification remediation.

## Kanban workflow

1. Resolve the exact target with `self_wake_sessions`.
2. Dry-run `self_wake_subscribe_kanban`.
3. Subscribe the task with an exact `session_key` or `session_id`.
4. After a terminal event, inspect `self_wake_receipts(source_kind="kanban")`.
5. Treat `agent_responded` as strongest evidence; confirm persistent `queued`
   receipts in the target transcript.

## Cron workflow

1. Set `self_wake.compat_shim_enabled: true`.
2. Set `cron.wake_agent_on_delivery: true` only when autonomous continuation is intended.
3. Restart the gateway from an outside shell.
4. Require `cron_delivery_capability: ok`, source `shim` or `native`.
5. Trigger a harmless delivery to an existing session.
6. Require a durable receipt with `source_kind=cron_delivery` and target response.

The plugin supports the exact host and two-wrapper seam set listed in
`docs/compatibility.md`. Both wrapper markers and live wake prerequisites must
be adopted. Host drift must fail closed; never force a private adapter across a
digest mismatch. The
old core patch is retired and is not remediation.

## Failure interpretation

- `capability_missing`: adapter disabled/not adopted or host unsupported.
- `host_drift`: install a matching plugin or pin the supported host, then restart.
- no cron receipt after visible delivery: verify per-surface doctor result and
  exact platform/chat/thread-to-existing-session match.
- `dispatched_unconfirmed`: do not retry blindly; injection already occurred.
- broadcast with no existing target session: intentionally visible-only.

See `docs/operator-runbook.md` and `docs/compatibility.md` in the plugin repo.

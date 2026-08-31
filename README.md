# hermes-self-wake-plugin

[![CI](https://github.com/mentatzoe/hermes-self-wake-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/mentatzoe/hermes-self-wake-plugin/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Portable internal session wake for Hermes Kanban and cron delivery.**

A visible platform notification tells the human that something happened. An
internal wake resumes the existing Hermes session that owns the work, injects
the event through its live adapter path, and writes a durable receipt.

Version 1.3.0 provides two independently diagnosed capability surfaces:

- **Kanban:** terminal events for `session:` / `session_id:` subscriptions call
  `GatewayRunner.wake_session`.
- **Cron delivery:** an outer delivery wrapper carries execution/content state
  to a second wrapper on the host's per-target success helper. Each successful
  actual platform/chat/thread/user route wakes independently with
  `source_kind=cron_delivery`, even when transcript mirroring is disabled or a
  later fanout target makes the aggregate delivery return/raise an error.

`mode=full` describes the Kanban surface only. It never implies cron delivery
is wired. `self_wake_doctor` reports `kanban_wake_capability` and
`cron_delivery_capability` separately; if cron wake is enabled but its adapter
is absent or drifted, doctor returns `ok=false` and `mode=degraded` with exact
restart/update remediation.

## Install

```bash
hermes plugins install https://github.com/mentatzoe/hermes-self-wake-plugin.git --enable

# ~/.hermes/config.yaml
self_wake:
  compat_shim_enabled: true
cron:
  wake_agent_on_delivery: true  # only if autonomous continuation is intended

# Append self_wake to existing platform toolset lists.
# platform_toolsets:
#   default: [web, terminal, file, self_wake]

# Restart from an outside shell so the running gateway adopts the adapter.
hermes gateway restart
```

Then run:

```text
/self-wake doctor
```

For cron wake enabled on the supported host, require:

- `ok: true`
- `mode: full`
- `kanban_wake_capability: ok`, source `shim` or `native`
- `cron_delivery_capability: ok`, source `shim` or `native`
- `cron_wake_config: ok`, `wake_agent_on_delivery=true`

A repository update or copied plugin directory is not runtime adoption. The
running gateway must restart before doctor can see the new wrapper.

## Current exact-host support

The 1.3.0 cron adapter supports Hermes commit
`21895bd39d9bc8a1cda307c9b0a0eb6fc98a8844`, with exact
stable private callable SHA-256 values:

- `_deliver_result`: `dda5f6f956ec1a6069d2f227ca4d2ba012cfc72600947c82cfca5b468051140f`
- `_maybe_mirror_cron_delivery`: `6f51d0efb37d1613380fece7e2c7ac7d1035d54825e4c8b45bb2dd843ba16f6c`
- `cron.scheduler.load_config`: `8680b219e570cf6db520ee21c4a043ad29a1cec19525162395e0c4dd15b54dbb`
- `SessionStore.list_sessions`: `1ce4bdb06b080936723a6beaf13eadc76734d1eceb154d033138da322362d5fb`

It refuses installation if any stable seam differs or either wrapper cannot be
adopted. Plugin discovery occurs before the active runner exists, so live
`wake_session` and session-store prerequisites are validated by the runtime
probe/doctor after `GatewayRunner` construction. Runtime runner identity is
validated structurally, never hashed. See `docs/compatibility.md` and
`docs/compatibility-manifest.json`.

The former `docs/core-patch/0001...` artifact targeted an obsolete host and has
been retired. It is not an install path. The product dependency is the plugin
artifact and its runtime compatibility applicator, not a developer checkout or
an upstream NousResearch PR.

## Use

```text
/self-wake sessions [query] [--platform discord] [--chat-id ID] [--thread-id ID]
/self-wake subscribe TASK --board BOARD --session-key KEY [--reset-cursor] [--dry-run]
/self-wake receipts [--session-key KEY | --session-id ID] [--source-kind kanban|cron_delivery]
/self-wake doctor
```

Model-facing equivalents are `self_wake_sessions`,
`self_wake_subscribe_kanban`, `self_wake_receipts`, and `self_wake_doctor`.

## Verification

```bash
python -m pytest tests -q
python scripts/current_host_cron_smoke.py \
  --host-checkout /path/to/hermes-agent-at-21895bd39d
```

The current-host smoke requires both owned wrappers, then uses the real Hermes cron scheduler, delivery router,
session/event types, and a disposable `SessionDB`; only external platform I/O is
replaced with a deterministic local transport. It proves successful delivery →
internal wake → durable `cron_delivery` receipt → target existing session user
and assistant rows.

## Documentation

- `docs/install-use.md` — install and adoption
- `docs/operator-runbook.md` — operations, receipts, diagnosis, rollback
- `docs/compatibility.md` — canonical surface/source matrix and drift behavior
- `docs/architecture.md` — authoritative boundary and trust model
- `docs/core-patch/README.md` — retirement notice for the unsafe stale patch

## License

Apache-2.0.

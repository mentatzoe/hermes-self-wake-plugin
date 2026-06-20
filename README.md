# hermes-self-wake-plugin

**Formal operator tools for Hermes internal session wake routing.**

This plugin provides tools for discovering sessions, managing Kanban wake
subscriptions, inspecting wake receipts, and running diagnostics — all related
to Hermes' internal session wake capability.

## Status

**Implemented (v1, Option 2)** — plugin logic implemented per the architecture
design (Option 2: standalone plugin + documented `internal_session_wake_v1`
core dependency + clean upstream core patch artifact). Unit tests pass against
a temp `HERMES_HOME`. No Hermes core files are touched by the plugin; the host
wake kernel lives in the separate patch under `docs/core-patch/`.

## Host Dependency

This plugin requires the Hermes host capability `internal_session_wake_v1`.
Without it, wake-mutating operations (subscribe with a `session:`/`session_id:`
marker) fail closed with `capability_missing`. Session discovery, receipt
inspection (when the table exists), and diagnostics work in degraded
(`inspect_only`) mode. Receipt inspection returns `capability_missing` only
when the `session_wake_receipts` table is absent.

See `docs/compatibility.md` for the full compatibility matrix.

## Architecture

Two deliverables live in this repo:

1. **The formal plugin** — installed/managed as a Hermes plugin. Never edits
   core files and does not monkeypatch by default.
2. **A clean core patch artifact** — under `docs/core-patch/`, intended to
   become an upstream Hermes PR. Defines the host capability the plugin
   requires for full operation.

## Install

```bash
hermes plugins install <private-repo-url> --enable
hermes plugins enable self-wake
```

Add `self_wake` to relevant platform toolsets, then restart Hermes/gateway.

## Tools

| Tool | Purpose |
|------|---------|
| `self_wake_sessions` | Resolve candidate target sessions |
| `self_wake_subscribe_kanban` | Create/upgrade Kanban wake subscriptions |
| `self_wake_receipts` | Inspect wake receipts |
| `self_wake_doctor` | End-to-end diagnostics |

## Slash Command

`/self-wake` wraps the same handlers: `sessions`, `subscribe`, `receipts`,
`doctor`, `help`.

## Compatibility Modes

| Mode | Host state | Behavior |
|------|-----------|----------|
| `full` | `internal_session_wake_v1` present | All tools available |
| `inspect_only` | Wake primitive missing, DBs readable | Discovery + partial doctor |
| `unsupported` | Required files/DBs missing | Doctor reports setup issue |

## License

MIT — same as Hermes Agent.

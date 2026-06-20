# hermes-self-wake-plugin

**Formal operator tools for Hermes internal session wake routing.**

This plugin provides tools for discovering sessions, managing Kanban wake subscriptions, inspecting wake receipts, and running diagnostics — all related to Hermes' internal session wake capability.

## Status

**Implemented (v1, Option 2)** — plugin logic implemented per the architecture design (Option 2: standalone plugin + documented `internal_session_wake_v1` core dependency + clean upstream core patch artifact). Unit tests pass against a temp `HERMES_HOME`. No Hermes core files are touched by the plugin; the host wake kernel lives in the separate patch under `docs/core-patch/`.

## Critical Invariant: Visible Notification ≠ Internal Wake

The most important invariant for operators:

- A **visible platform notification** sends a chat message to the user. It does NOT wake the agent.
- An **internal wake** injects a trusted event into an existing Hermes session so the agent can act on it.

For Kanban wake to work, the `kanban_notify_subs.user_id` must be set to `session:<session_key>` or `session_id:<session_id>`. A plain visible-only row (platform/chat/thread with no marker) delivers a platform notification but does **not** wake the agent.

See `docs/operator-runbook.md` for the full operational guide.

## Host Dependency

This plugin requires the Hermes host capability `internal_session_wake_v1`. Without it, wake-mutating operations (subscribe with a `session:`/`session_id:` marker) fail closed with `capability_missing`. Session discovery, receipt inspection (when the table exists), and diagnostics work in degraded (`inspect_only`) mode. Receipt inspection returns `capability_missing` only when the `session_wake_receipts` table is absent.

See `docs/compatibility.md` for the full compatibility matrix.

## Architecture

Two deliverables live in this repo:

1. **The formal plugin** — installed/managed as a Hermes plugin. Never edits core files and does not monkeypatch by default.
2. **A clean core patch artifact** — under `docs/core-patch/`, intended to become an upstream Hermes PR. Defines the host capability the plugin requires for full operation.

## Install

```bash
# 1. Install from the private repo
hermes plugins install <private-repo-url> --enable

# 2. Enable the plugin
hermes plugins enable self-wake

# 3. Add the toolset to your platform toolsets in ~/.hermes/config.yaml
#    under the relevant platform entries, e.g.:
#    platform_toolsets:
#      default: [web, terminal, file, self_wake]
#      discord: [web, terminal, file, self_wake]

# 4. Restart Hermes / gateway for plugin registration and toolset changes to take effect
hermes gateway restart
# or exit and relaunch the CLI
```

See `docs/install-use.md` for a complete step-by-step install guide.

## Tools

| Tool | Purpose | Capability required |
|------|---------|-------------------|
| `self_wake_sessions` | Resolve candidate target sessions from `sessions.json` + `state.db` | None (read-only) |
| `self_wake_subscribe_kanban` | Create/upgrade Kanban wake subscriptions with `session:` or `session_id:` markers | `internal_session_wake_v1` |
| `self_wake_receipts` | Inspect wake receipts from `session_wake_receipts` | Receipt table present |
| `self_wake_doctor` | End-to-end diagnostics: capability, session index, receipts, Kanban DB, cron config | None (degraded reporting) |

## Slash Command

`/self-wake` wraps the same handlers for human operators:

```
/self-wake sessions [query] [--platform discord] [--chat-id ID] [--thread-id ID]
/self-wake subscribe TASK --board BOARD --session-key KEY [--reset-cursor] [--dry-run]
/self-wake receipts [--session-key KEY | --session-id ID] [--source-kind kanban]
/self-wake doctor
/self-wake help
```

## Compatibility Modes

| Mode | Host state | Behavior |
|------|-----------|----------|
| `full` | `internal_session_wake_v1` present + receipt table | All tools available |
| `inspect_only` | Wake primitive missing, DBs readable | Discovery + partial doctor |
| `unsupported` | Required files/DBs missing/unreadable | Doctor reports setup issue |

## Documentation

- `docs/install-use.md` — step-by-step install for a fresh Hermes setup
- `docs/operator-runbook.md` — how to resolve sessions, subscribe Kanban tasks, verify receipts, diagnose failures
- `docs/compatibility.md` — version requirements, fail-closed behavior, upgrade path, core-patch status
- `docs/architecture.md` — system design and host contract
- `docs/core-patch/` — upstream-candidate core patch artifact

## License

MIT — same as Hermes Agent.

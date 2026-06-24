# hermes-self-wake-plugin

**Portable operator tools for Hermes internal session wake routing.**

This plugin provides tools for discovering sessions, managing Kanban wake
subscriptions, inspecting wake receipts, and running diagnostics — all related
to Hermes' internal session wake capability. It is designed to be **shareable
across Hermes upgrades without depending on upstream accepting a core patch**:
the plugin carries its own compatibility shim that provides the wake capability
on vanilla Hermes when enabled.

## Status

**Implemented (v1.1)** — plugin logic + a bundled compat shim that provides
``internal_session_wake_v1`` on vanilla Hermes at runtime (opt-in). 110 tests
pass against a temp `HERMES_HOME`. No Hermes core files are touched unless the
operator explicitly chooses the optional reference core patch.

## Session discovery boundary

`self_wake_sessions` exposes a host-session resolver, not a public dependency on
Hermes' current cache file. Current Hermes does not yet expose a stable plugin
resolver API, so this PR includes a read-only **current-Hermes adapter** over the
gateway current-session cache (`$HERMES_HOME/sessions/sessions.json`) plus
`state.db` metadata. That cache is active gateway routing state, not a canonical
history ledger and not the plugin contract. If Hermes later exposes a native
resolver or `session_surfaces` table, the adapter should move behind the same
`self_wake.sessions` helper surface.

## How the wake capability is provided (portability model)

The plugin needs the Hermes host capability `internal_session_wake_v1`. It can
be provided three ways, **in preference order**:

1. **Compat shim (portable, recommended for sharing)** — set
   `self_wake.compat_shim_enabled: true` in `config.yaml`. The plugin installs
   `wake_session`, the receipt methods, session lookup, the receipts table, and
   Kanban notifier routing at runtime via class-level monkeypatching. **No core
   patch required.** The shim fails closed on internal drift and defers to a
   native capability when present. See `docs/compatibility.md`.
2. **Native (upstream or optional core patch)** — upstream Hermes ships the
   capability, or the operator applies the optional reference patch under
   `docs/core-patch/`. No monkeypatching. The shim auto-detects this and does
   not install.
3. **Absent** — the plugin loads in `inspect_only` mode and fails closed
   (`capability_missing`) for wake-mutating operations. It never creates
   "looks subscribed but will never wake" state.

The core patch under `docs/core-patch/` is an **optional reference / upstream
candidate**, not the required path. The reason is portability + shareability:
the plugin must work across Hermes upgrades without waiting on upstream.

## Critical Invariant: Visible Notification ≠ Internal Wake

- A **visible platform notification** sends a chat message to the user. It does NOT wake the agent.
- An **internal wake** injects a trusted event into an existing Hermes session so the agent can act on it.

For Kanban wake to work, the `kanban_notify_subs.user_id` must be set to `session:<session_key>` or `session_id:<session_id>`. A plain visible-only row (platform/chat/thread with no marker) delivers a platform notification but does **not** wake the agent.

See `docs/operator-runbook.md` for the full operational guide.

## Install

```bash
# 1. Install from the repo
hermes plugins install <repo-url> --enable

# 2. Enable the plugin
hermes plugins enable self-wake

# 3. Add the toolset to your platform toolsets in ~/.hermes/config.yaml:
#    platform_toolsets:
#      default: [web, terminal, file, self_wake]

# 4. (Portable path) Enable the compat shim — no core patch needed:
#    self_wake:
#      compat_shim_enabled: true

# 5. Restart Hermes / gateway for plugin registration and shim install to take effect
hermes gateway restart
```

See `docs/install-use.md` for a complete step-by-step install guide covering
both the shim path and the optional core-patch path.

## Tools

| Tool | Purpose | Capability required |
|------|---------|-------------------|
| `self_wake_sessions` | Resolve candidate target sessions through the host-session resolver (current adapter: gateway current-session cache + `state.db`) | None (read-only) |
| `self_wake_subscribe_kanban` | Create/upgrade Kanban wake subscriptions with `session:` or `session_id:` markers | `internal_session_wake_v1` (native or shim) |
| `self_wake_receipts` | Inspect wake receipts from `session_wake_receipts` | Receipt table present (shim or native) |
| `self_wake_doctor` | End-to-end diagnostics: capability, shim, session resolver, receipts, Kanban DB, cron config | None (degraded reporting) |

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
| `full` | `internal_session_wake_v1` present (native **or** shim) + receipt table + notifier routing | All tools available; `source` field reports `native` or `shim` |
| `inspect_only` | Wake primitive absent, DBs readable | Discovery + partial doctor; subscribe/receipts fail closed |
| `unsupported` | Required files/DBs missing/unreadable | Doctor reports setup issue |

## Documentation

- `docs/install-use.md` — step-by-step install (shim path + optional core-patch path)
- `docs/operator-runbook.md` — resolve sessions, subscribe Kanban tasks, verify receipts, diagnose failures
- `docs/compatibility.md` — capability sources, fail-closed behavior, drift handling, shim internals, upgrade path
- `docs/architecture.md` — system design and host contract
- `docs/core-patch/` — **optional** reference / upstream-candidate core patch artifact

## License

MIT — same as Hermes Agent.

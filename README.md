# hermes-self-wake-plugin

[![CI](https://github.com/mentatzoe/hermes-self-wake-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/mentatzoe/hermes-self-wake-plugin/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Portable operator tools for Hermes internal session wake routing.**

This plugin provides tools for discovering sessions, managing Kanban wake
subscriptions, inspecting wake receipts, and running diagnostics — all related
to Hermes' internal session wake capability. It is designed to be **shareable
across Hermes upgrades without depending on upstream accepting a core patch**:
the plugin carries its own compatibility shim that provides the wake capability
on vanilla Hermes when enabled.

## Status

Implemented and released — see the
[releases page](https://github.com/mentatzoe/hermes-self-wake-plugin/releases)
for the changelog. The full test suite runs in CI on Python 3.10–3.13 against
a temp `HERMES_HOME`, including the real plugin-loader path and in-process
contract tests covering wake dispatch and receipts (fake host, not a live
gateway smoke). No Hermes core files are touched unless the operator
explicitly chooses the optional reference core patch.

## Failure behaviour (what the code does when things go wrong)

These are design rules, each enforced by tests. If you find a path around
one, that is a bug I want reported.

- Subscribing does not guess the delivery platform: it is resolved from the
  target session's cached origin or its session key, and when neither yields
  one the call fails with `platform_required`. Targets not found in the
  session cache are flagged (`resolved_from_cache: false`) with a warning.
- Each wake attempt writes a receipt, so "did it actually wake, and did the
  agent respond?" is a query (`self_wake_receipts`) rather than a guess. One
  caveat an operator should know: a wake delivered into an already-active
  session is receipted `queued`, and on hosts without the queued-finalization
  refinement it can remain `queued` after the agent picks it up — treat that
  as delivered-unconfirmed, not failed (details in the runbook).
- A wake whose post-dispatch bookkeeping fails is recorded as
  `dispatched_unconfirmed` and is not retried, to avoid re-injecting a
  payload that already landed; concurrent wakes sharing a dedupe key are
  deduplicated against in-flight receipts.
- The shim checks the host internals it depends on before installing; on
  drift it refuses with a structured reason rather than installing anyway.
  On partial-native hosts it fills only the missing methods and reports what
  it skipped (`skipped_native_methods`), leaving native implementations in
  place.
- Re-subscribing a wake row with the visible-only flag preserves the wake
  marker by design; the response says `downgrade_ignored` and warns that
  wakes keep firing, rather than implying they stopped.

## Session discovery boundary

`self_wake_sessions` exposes a host-session resolver, not a public dependency on
Hermes' current cache file. Current Hermes does not yet expose a stable plugin
resolver API, so this plugin includes a read-only **current-Hermes adapter** over the
gateway current-session cache (`$HERMES_HOME/sessions/sessions.json`) plus
`state.db` metadata. That cache is active gateway routing state, not a canonical
history ledger and not the plugin contract. If Hermes later exposes a native
resolver or `session_surfaces` table, the adapter should move behind the same
`self_wake.sessions` helper surface.

## How the wake capability is provided

The plugin needs the Hermes host capability `internal_session_wake_v1`.
Three provider paths exist. Two different orderings apply, and they are not
the same thing:

- **Runtime precedence** (what the code prefers when both exist):
  native > shim > absent. The shim always defers to a native capability and
  fills only missing methods on partial-native hosts.
- **Install path** (what to choose when your Hermes has no native
  capability): the shim is the low-friction option because it patches
  nothing on disk; the core patch is the full-capability option.

1. **Native** — upstream Hermes ships the capability, or the operator applies
   the reference patch under `docs/core-patch/`. No monkeypatching. This is
   the full wake surface: Kanban, cron-delivery, and cross-session message
   wakes, plus native active-session queueing.
2. **Compat shim** — set `self_wake.compat_shim_enabled: true` in
   `config.yaml`. No core patch; survives Hermes upgrades; installs at
   runtime via class-level monkeypatching with drift checks that refuse to
   install on host changes. **Scope: Kanban wake subscriptions, receipts,
   and session lookup only. The shim does not provide cron-delivery or
   cross-session message wakes, and active-session handling follows vanilla
   busy semantics.** The capability matrix in `docs/compatibility.md` is
   canonical — read it before choosing this path.
3. **Absent** — the plugin loads in `inspect_only` mode and fails closed
   (`capability_missing`) for wake-mutating operations, refusing to write
   subscription markers that the host could not act on.

## Critical Invariant: Visible Notification ≠ Internal Wake

- A **visible platform notification** sends a chat message to the user. It does NOT wake the agent.
- An **internal wake** injects a trusted event into an existing Hermes session so the agent can act on it.

For Kanban wake to work, the `kanban_notify_subs.user_id` must be set to `session:<session_key>` or `session_id:<session_id>`. A plain visible-only row (platform/chat/thread with no marker) delivers a platform notification but does **not** wake the agent.

See `docs/operator-runbook.md` for the full operational guide.

## Install

```bash
# 1. Install from the repo (--enable also enables it)
hermes plugins install https://github.com/mentatzoe/hermes-self-wake-plugin.git --enable

# 2. (Only if you installed without --enable)
hermes plugins enable self-wake

# 3. APPEND self_wake to your existing platform_toolsets lists in
#    ~/.hermes/config.yaml — do not replace the lists with this example:
#    platform_toolsets:
#      default: [web, terminal, file, self_wake]   # keep your existing entries

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

The plugin also registers one `pre_llm_call` hook: it appends the current
session's identity to a local diagnostics cache at
`$HERMES_HOME/self-wake/recent_sessions.json` (recent-session sightings for
`doctor`; atomic writes; no prompt injection; not used for wake correctness;
safe to delete).

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

Apache-2.0.

---
name: kanban-self-wake
description: "Operational guide for Hermes Kanban session-wake subscriptions."
version: "1.0.0"
metadata:
  hermes:
    tags: [kanban, self-wake, subscriptions, wake-routing]
---

# Kanban Self-Wake

Operational guide for using the self-wake plugin to subscribe Hermes agent sessions to Kanban terminal events via internal wake routing.

## Key Invariant

For Kanban wake to work, the `kanban_notify_subs.user_id` must be set to `session:<session_key>` or `session_id:<session_id>`. A visible-only row (platform/chat/thread) does NOT wake the agent.

## Prerequisites

- Hermes with `internal_session_wake_v1`, provided **either** by:
  - the bundled **compat shim** (`self_wake.compat_shim_enabled: true` in config.yaml — portable, no core patch), **or**
  - the **optional core patch** under `docs/core-patch/` / upstream Hermes (native)
- `self-wake` plugin installed and enabled
- `self_wake` toolset added to platform_toolsets in config.yaml
- Hermes/gateway restarted after enablement

## Quick Reference

### Check capability and health

```
/self-wake doctor
```

Expected `full` mode for wake to work. `inspect_only` means the host capability is missing.

### Resolve a target session

```
/self-wake sessions --platform discord --chat-id <channel_id>
```

Or by query:

```
/self-wake sessions --query "kanban worker"
```

### Subscribe a Kanban task

Dry-run first:

```
/self-wake subscribe --task-id <task_id> --session-key <session_key> --dry-run
```

Then commit:

```
/self-wake subscribe --task-id <task_id> --session-key <session_key>
```

Optional `--reset-cursor` to replay already-claimed events (use with care).

### Inspect receipts

```
/self-wake receipts --source-kind kanban --status agent_responded
```

### Subscribe by session_id

If you only know the session_id:

```
/self-wake subscribe --task-id <task_id> --session-id <session_id>
```

The plugin resolves to a session_key when possible; otherwise writes a `session_id:<id>` marker.

## Failure Modes

### `capability_missing`

The host lacks `internal_session_wake_v1`. Enable the compat shim (`self_wake.compat_shim_enabled: true` in config.yaml — portable, no core patch), apply the optional core patch from `docs/core-patch/`, or upgrade Hermes. The plugin never writes wake markers on an unsupported host.

### `ambiguous_session`

Filters matched multiple sessions. Pass an explicit `session_key` or `session_id` to disambiguate.

### `kanban_unavailable`

The Kanban DB backend (`hermes_cli.kanban_db`) is not importable. This usually means the plugin is running outside a Hermes gateway/CLI process. Restart Hermes.

### `chat_id_required`

The target session has no stored origin and no `chat_id` was passed explicitly. Pass `chat_id` (and `thread_id` if needed) on the command line.

## Compatibility

The plugin requires Hermes host capability `internal_session_wake_v1`, provided by the bundled compat shim (`self_wake.compat_shim_enabled: true` — portable, no core patch) or the optional core patch / upstream Hermes (native). Without it, subscribe and receipt operations return `capability_missing`. Session discovery and diagnostics work in `inspect_only` mode. `/self-wake doctor` reports the active `source` (native/shim/absent).

## See Also

- Plugin repo `docs/operator-runbook.md` — full operational procedures
- Plugin repo `docs/compatibility.md` — capability sources, shim internals, drift handling, upgrade path
- Plugin repo `docs/install-use.md` — fresh install steps (shim + optional core-patch)
- Plugin repo `docs/core-patch/` — **optional** reference / upstream-candidate core patch artifact
- Plugin repo `self_wake/compat_shim.py` — the compat shim implementation

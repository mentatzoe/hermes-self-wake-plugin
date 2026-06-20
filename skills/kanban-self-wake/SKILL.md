---
name: kanban-self-wake
description: "Operational guide for Hermes Kanban session-wake subscriptions."
version: "1.0.0"
metadata:
  hermes:
    tags: [kanban, self-wake, subscriptions, wake-routing]
---

# Kanban Self-Wake

Operational guide for using the self-wake plugin to subscribe Hermes agent
sessions to Kanban terminal events via internal wake routing.

## Key Invariant

For Kanban wake to work, the `kanban_notify_subs.user_id` must be set to
`session:<session_key>` or `session_id:<session_id>`. A visible-only row
(platform/chat/thread) does NOT wake the agent.

## Usage

### Check capability and health

```
/self-wake doctor
```

### Resolve a target session

```
/self-wake sessions --platform discord --chat-id <channel_id>
```

### Subscribe a Kanban task

```
/self-wake subscribe --task-id <task_id> --session-key <session_key>
```

Use `--dry-run` first to preview without writing.

### Inspect receipts

```
/self-wake receipts --source-kind kanban --status agent_responded
```

## Compatibility

The plugin requires Hermes host capability `internal_session_wake_v1`.
Without it, subscribe and receipt operations will return
`capability_missing`.

See the plugin repo for full documentation:
- `docs/architecture.md` — system design
- `docs/compatibility.md` — compatibility modes
- `docs/operator-runbook.md` — operational procedures

# Operator Runbook

## Install

1. Ensure Hermes has `internal_session_wake_v1` (apply core patch or use
   a Hermes version that includes it).
2. Install plugin: `hermes plugins install <repo-url> --enable`
3. Enable: `hermes plugins enable self-wake`
4. Add `self_wake` to platform toolsets in `config.yaml`.
5. Restart Hermes/gateway.

## Verify

Run `/self-wake doctor` to check:
- Plugin loaded and enabled
- Toolset exposed for platform
- Core capability present
- Session index readable
- Receipt table exists
- Kanban DB reachable

## Common Operations

### Subscribe a session to Kanban completion

```
/self-wake sessions --query "kanban worker"
/self-wake subscribe --task-id t_abc123 --session-key "agent:default:..." --dry-run
/self-wake subscribe --task-id t_abc123 --session-key "agent:default:..."
```

### Check if a wake fired

```
/self-wake receipts --task-id t_abc123
/self-wake receipts --source-kind kanban --status agent_responded
```

### Troubleshoot

- `capability_missing`: Apply the core patch from `docs/core-patch/`
- Receipts empty: Check the session has a stored origin
- Subscribe fails silently: Run doctor to check Kanban DB connectivity

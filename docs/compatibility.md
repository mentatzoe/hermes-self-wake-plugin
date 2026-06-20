# Compatibility

## Compatibility Modes

| Mode | Host State | Behavior |
|------|-----------|----------|
| `full` | `internal_session_wake_v1` present + receipt table | All tools available |
| `inspect_only` | Wake primitive missing, DBs readable | Discovery + partial doctor |
| `unsupported` | Required files/DBs missing/unreadable | Doctor reports setup issue |

## Hermes Version Requirements

| Feature | Minimum Requirement |
|---------|-------------------|
| Plugin install + doctor | Any recent Hermes |
| Session discovery | Hermes with sessions.json + state.db |
| Kanban wake subscribe | `internal_session_wake_v1` + `session_wake_receipts` table |
| Receipt inspection | `session_wake_receipts` table |
| Send-message wake | Core patch commit for send_message |
| Cron wake | Core patch commit for cron + `cron.wake_agent_on_delivery` |

## Upgrade Path

1. **Short term**: Apply `docs/core-patch/0001-internal-session-wake-v1.patch`
2. **Medium term**: Wait for upstream MR to NousResearch/hermes-agent
3. **Long term**: Plugin uses stable public capability probe once available

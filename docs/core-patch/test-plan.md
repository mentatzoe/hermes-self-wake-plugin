> **Operator verification guide.** Run these checks on YOUR host after
> applying the patch — they are not open TODOs; the equivalent behavior is
> covered by the plugin's CI suite for the shim path.

# Test Plan: internal_session_wake_v1

## Unit Tests

- `wake_session` with valid session_key resolves target
- `wake_session` with valid session_id resolves target
- `wake_session` with neither key nor id returns error
- `wake_session` with missing session returns appropriate error
- Receipt created with status `requested` before dispatch
- Receipt updated to `dispatched` after adapter dispatch
- Receipt updated to `agent_responded` when response observed
- Receipt updated to `queued` for active sessions
- Receipt updated to `failure` on error with error message
- Dedupe by (target_session_key, dedupe_key) works
- Existing receipt reused for dedupe key (status: `deduped`)

## Integration Tests

- Kanban subscribe + terminal event wakes agent
- Cron delivery with `wake_agent_on_delivery` wakes agent
- Send-message mirror wakes target session
- Active session not interrupted (queued as follow-up)
- Multiple wakes dedupe correctly

## Plugin contract / operator smoke checks

- Plugin doctor reports `full` mode with cap present
- Plugin doctor reports `inspect_only` without cap
- Plugin subscribe fails closed without cap
- Plugin receipts handles missing table gracefully

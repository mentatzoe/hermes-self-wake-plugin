# Operator Runbook

## Verify capability and health

Run end-to-end diagnostics:

```
/self-wake doctor
```

The doctor checks:
- `core_capability` — `internal_session_wake_v1` present and version
- `session_index` — `sessions.json` readable and non-empty
- `receipt_table` — `session_wake_receipts` present and counts
- `kanban_db` — Kanban DB reachable and existing subscriptions
- `cron_wake_config` — `cron.wake_agent_on_delivery` state

Output fields:
- `ok` — true only in full mode with zero failures
- `mode` — `full`, `inspect_only`, or `unsupported`
- `failures` — blocking issues
- `warnings` — non-blocking concerns
- `remediation` — suggested fixes

## Resolve a target session

Resolve by session key (preferred when known):

```
/self-wake sessions --session-key "agent:main:discord:thread:1511162896762142980:1511162896762142980"
```

Resolve by query:

```
/self-wake sessions --query "kanban worker"
```

Filter by platform, chat, or thread:

```
/self-wake sessions --platform discord --chat-id 123456 --thread-id 789012
```

The output includes `session_key`, `session_id`, `platform`, `chat_id`, `thread_id`, `display_name`, `title`, and `origin`.

### Discord thread session-key shape

Discord thread keys follow this shape:

```
agent:<profile>:discord:thread:<thread_id>:<thread_id>
```

For example:

```
agent:main:discord:thread:1511162896762142980:1511162896762142980
```

## Subscribe a Kanban task to internal wake

### Dry-run first

Always preview before writing:

```
/self-wake subscribe --task-id t_abc123 --session-key "agent:main:discord:thread:1511162896762142980:1511162896762142980" --dry-run
```

The dry-run reports:
- `target_kind` — `session_key` or `session_id`
- `user_id_marker` — the `session:` or `session_id:` marker that would be written
- `before` / `after` — row state before and after (null in dry-run)
- `capability_mode` — `full`, `inspect_only`, or `unsupported`

### Real subscribe

If the dry-run looks correct:

```
/self-wake subscribe --task-id t_abc123 --session-key "agent:main:discord:thread:1511162896762142980:1511162896762142980"
```

Optional flags:
- `--reset-cursor` — sets `last_event_id=0` after subscribing so already-claimed terminal events replay. Use only after checking for duplicate risk.
- `--notifier-profile default` — override the notifier owner profile (defaults to active profile)
- `--board default` — target a non-default Kanban board slug

### Subscribe by session_id instead of session_key

```
/self-wake subscribe --task-id t_abc123 --session-id "20260101_120000_aaaaaa"
```

The plugin resolves the session_id to a session_key when possible; if not resolvable, it writes a `session_id:<id>` marker.

### Result shapes

**Success (full mode):**
- `success: true`
- `user_id_marker: "session:agent:main:discord:thread:..."`
- `internal_wake_enabled: true`
- `before` and `after` show the row mutation

**Fail-closed (inspect_only mode):**
- `success: false`
- `error: "capability_missing"`
- `remediation: "Apply the core patch from docs/core-patch/ or upgrade Hermes"`

**Ambiguous session:**
- `success: false`
- `error: "ambiguous_session"`
- `matches` lists the candidates
- Pass an explicit `session_key` or `session_id` to disambiguate

## Verify via receipts

Inspect receipts to confirm a wake actually queued/dispatched/responded:

```
/self-wake receipts --source-kind kanban --status agent_responded
```

Filter by session:

```
/self-wake receipts --session-key "agent:main:discord:thread:1511162896762142980:1511162896762142980"
```

Filter by session_id:

```
/self-wake receipts --session-id "20260101_120000_aaaaaa"
```

Filter by status:

```
/self-wake receipts --status failure
```

Receipt statuses:
- `requested` — receipt reserved before dispatch
- `dispatched` — event handed to adapter pipeline
- `queued` — target session already active; event queued as follow-up
- `agent_responded` — a response was observed
- `failure` — dispatch/wake failed
- `deduped` — existing receipt reused for dedupe key

Receipt payload previews are truncated to 200 characters by default. Full payloads are not echoed.

## Diagnose failures

### `capability_missing` on subscribe

**Symptom:** `/self-wake subscribe` returns `error: capability_missing`.

**Cause:** The host lacks `internal_session_wake_v1`.

**Fix:**
1. Apply the core patch: `cd $HERMES_HOME/hermes-agent && git apply docs/core-patch/0001-internal-session-wake-v1.patch`
2. Or upgrade to a Hermes version that includes the capability
3. Restart Hermes / gateway
4. Re-run `/self-wake doctor` to verify `full` mode

### Receipts empty but subscribe succeeded

**Symptom:** Subscribe reports success, but receipts show nothing.

**Cause:** The terminal event has not yet fired, or the target session has no stored origin.

**Fix:**
1. Verify the Kanban task actually reached terminal state (done, blocked, etc.)
2. Check `kanban_notify_subs` has the correct `user_id=session:...` marker
3. Run `/self-wake receipts --source-kind kanban` without status filter to see all rows
4. Run `/self-wake doctor` and check `kanban_db` and `session_index`

### Subscribe fails silently

**Symptom:** Subscribe returns success but no wake occurs.

**Cause:** The row may be `visible_only` (no session marker), or the Kanban DB is unreachable.

**Fix:**
1. Run `/self-wake doctor` — check `kanban_db` status
2. Re-subscribe with `--dry-run` and inspect `user_id_marker`
3. If `visible_only: true`, the host capability is missing; see `capability_missing` fix above
4. If the backend reports `kanban_unavailable`, ensure `hermes_cli.kanban_db` is importable in the Hermes process

### Doctor reports `kanban_db` fail

**Symptom:** `kanban_db` check shows `fail`.

**Cause:** `hermes_cli.kanban_db` is not importable, or the board DB is not reachable.

**Fix:**
1. Ensure Hermes is running inside a gateway or CLI process where `hermes_cli` is importable
2. Check that `~/.hermes/kanban.db` (or the board-specific DB) exists
3. Restart Hermes / gateway

### Session index empty

**Symptom:** `session_index` check shows `warn` with 0 sessions.

**Cause:** `sessions.json` is missing or empty.

**Fix:**
1. Ensure at least one gateway session has been created
2. Check that `$HERMES_HOME/sessions/sessions.json` exists and is a dict keyed by session_key
3. Restart the gateway to regenerate the index

## Rollback

To stop a Kanban task from waking a session:

1. Re-subscribe with `force_degraded_visible_only` to downgrade to a visible-only row:
   ```
   /self-wake subscribe --task-id t_abc123 --session-key "..." --force-degraded-visible-only
   ```
2. Or manually delete the row from `kanban_notify_subs` via the Kanban CLI
3. Or disable the plugin entirely: `hermes plugins disable self-wake` and restart

Disabling the plugin does not remove core capability or existing subscriptions.

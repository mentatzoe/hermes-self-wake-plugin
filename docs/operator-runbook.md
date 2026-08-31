# Operator Runbook

## 1. Verify runtime adoption

Run after every install, plugin update, host update, or gateway restart:

```text
/self-wake doctor
```

Doctor checks:

- `kanban_wake_capability` — Kanban wake primitive, receipts, lookup, and notifier routing;
- `cron_delivery_capability` — successful cron delivery actually routes to internal wake;
- `core_capability` — backward-compatible alias explicitly scoped to Kanban;
- `compat_shim` — installed, disabled, native-present, or drift refusal;
- `session_resolver`, `receipt_table`, `kanban_db`;
- `cron_wake_config`.

When `cron.wake_agent_on_delivery=true`, accept health only when:

```text
ok=true
mode=full
kanban_wake_capability=ok
cron_delivery_capability=ok source=shim|native
```

Never infer cron health from top-level `mode=full`; that field remains the
Kanban mode for compatibility. `mode=degraded` + `ok=false` means Kanban is
healthy but configured cron wake is unavailable.

If cron config is unreadable/unparseable or the cron section is absent, policy
is unknown. With routing absent, require `ok=false`, `mode=degraded`, and the
remediation “Config could not be read and cron routing cannot be verified.” It
must never be described as policy off.

## 2. Resolve a target session

```text
/self-wake sessions --session-key "agent:main:discord:thread:1511162896762142980:1511162896762142980"
/self-wake sessions --query "kanban worker"
/self-wake sessions --platform discord --chat-id 123456 --thread-id 789012
```

The current resolver adapts the gateway current-session cache and `state.db`.
The cache path is private adapter detail, not a durable public contract.

## 3. Subscribe Kanban

Dry-run first:

```text
/self-wake subscribe --task-id t_abc123 --session-key "agent:main:discord:thread:..." --dry-run
```

Then write:

```text
/self-wake subscribe --task-id t_abc123 --session-key "agent:main:discord:thread:..."
```

Useful flags:

- `--reset-cursor` replays already-claimed events; assess duplicate risk first;
- `--notifier-profile default` overrides notifier owner;
- `--board <slug>` selects a board.

A capability-missing host fails closed and does not write a session marker.
Re-subscribing with visible-only does not downgrade an existing wake marker.

## 4. Verify receipts

```text
/self-wake receipts --source-kind kanban
/self-wake receipts --source-kind cron_delivery
/self-wake receipts --session-key "agent:main:discord:thread:..."
```

Statuses:

- `requested` — receipt reserved;
- `dispatched` — event handed to adapter pipeline;
- `queued` — delivered to an already-busy session;
- `agent_responded` — assistant row observed;
- `dispatched_unconfirmed` — injection happened but bookkeeping failed; do not retry blindly;
- `failure` — failed before injection; retryable;
- deduped response — existing dedupe receipt reused.

A persistent `queued` row can be delivered-but-unfinalized on shim hosts; check
the target transcript before classifying it as failure.

## 5. Harmless current-host canary

```bash
python scripts/current_host_cron_smoke.py \
  --host-checkout /path/to/hermes-agent-at-21895bd39d
```

Require JSON fields:

- `ok=true`;
- exact host commit;
- startup installation retained both wrappers while the active runner was not
  yet available, followed by healthy adoption after runner construction;
- both adoption wrappers true (`_deliver_result` and
  `_maybe_mirror_cron_delivery`);
- `delivery_count=1`;
- `internal_event_count=1`;
- receipt `source_kind=cron_delivery`, `status=agent_responded`;
- message roles `[user, assistant]` in the same existing session.

This uses a disposable `/tmp` `HERMES_HOME` and local transport; it does not
restart the live gateway or call Discord/Telegram.

## 6. Diagnose cron failures

### `cron_delivery_capability: fail` + `plugin cron adapter not adopted`

Cause: matching code may be on disk, but the running gateway still has the old
function object.

Fix:

1. verify installed plugin is version 1.3.0;
2. verify `self_wake.compat_shim_enabled: true`;
3. restart the gateway from an outside shell;
4. rerun doctor and require cron source `shim` or `native`.

### `cron_delivery_capability: fail` + `host_drift`

Cause: `_deliver_result`, `_maybe_mirror_cron_delivery`, `load_config`, or
`SessionStore.list_sessions` does not match the closed compatibility manifest,
or a required active-runner attribute is absent. The detail names the seam.

Fix:

1. do **not** force the wrapper;
2. update to a self-wake version matching the host, or pin Hermes to the exact
   supported commit;
3. restart;
4. rerun doctor and the receipt-backed canary.

The retired core patch is not remediation.

### Delivery succeeded but no `cron_delivery` receipt

1. confirm doctor sees `wake_agent_on_delivery=true` and cron capability `ok`;
2. confirm each successful actual delivery route maps to exactly one existing
   session with matching platform/chat/thread/user origin;
3. confirm the gateway loop and adapter are live;
4. inspect gateway logs for `target existing session unavailable`, scheduling,
   or wake failure;
5. rerun receipts without a status filter.

Broadcast/fan-out targets that have no existing session are intentionally
visible-only. The adapter never wakes the creator lane by guess.

Partial fanout is per-target: every successful target can have a receipt even if
a later target makes `_deliver_result` return or raise an aggregate error. A
target that never reached the host's per-success helper must not have a wake.

## 7. Diagnose Kanban failures

### `capability_missing`

1. set `self_wake.compat_shim_enabled: true`;
2. restart the gateway;
3. require `kanban_wake_capability: ok`.

### `compat_shim: fail`

A private SessionStore, SessionDB, GatewayRunner, Kanban watcher, or cron seam
drifted. Update/pin; do not force. Rerun doctor after restart.

### Receipts empty after subscription

1. verify the task reached terminal state;
2. verify `kanban_notify_subs.user_id` contains the expected `session:` marker;
3. run unfiltered Kanban receipts;
4. check `kanban_db` and `session_resolver` doctor checks.

## 8. Rollback

Disable autonomous cron wake:

```yaml
cron:
  wake_agent_on_delivery: false
```

Disable all plugin-owned adapters:

```yaml
self_wake:
  compat_shim_enabled: false
```

Then restart from an outside shell. To stop one Kanban subscription, delete its
notify row; visible-only re-subscribe is not a downgrade mechanism. Existing
receipts remain durable.

## Diagnostics hook

The observer-only `pre_llm_call` hook maintains a bounded recent-session cache
at `$HERMES_HOME/self-wake/recent_sessions.json`. It injects nothing into the
prompt, is not a correctness ledger, and is safe to delete.

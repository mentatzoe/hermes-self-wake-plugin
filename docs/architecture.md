# Self-Wake Plugin Architecture

## Product boundary

Self-wake is a standalone, plugin-owned behavior layer. Operators do not need a
developer Hermes checkout, a permanent fork, or an upstream NousResearch PR.
The plugin carries its private compatibility mechanism, exact host identity,
adoption probe, rollback, tests, and runbook.

Components:

1. `self_wake.compat_shim` — opt-in Kanban wake/session/receipt class adapters;
2. `self_wake.cron_adapter` — exact-source-checked, transactional two-wrapper
   runtime adapter for the host cron delivery boundary;
3. `self_wake.capabilities` — independent Kanban and cron probes;
4. `self_wake.doctor` — policy-aware health: configured cron without wiring is
   a failure/degraded state;
5. operator tools, command, skill, receipt reader, and session resolver.

The stale source patch previously stored under `docs/core-patch/` is retired.

## Authoritative path

Cron delivery is synchronous host code running outside the gateway loop. After
all stable private callable digests match the closed manifest, the adapter:

1. wraps `_deliver_result` to establish a `ContextVar` carrying the exact job
   object, content, loop, and execution/fire identity for that call;
2. wraps `_maybe_mirror_cron_delivery`, which the supported host calls after
   **each actual successful target**, including with `enabled=False`;
3. observes the helper's platform/chat/thread/user route, correcting the exact
   host's `raw_response.thread_fallback` stale requested thread to no-thread;
4. checks `cron.wake_agent_on_delivery`, resolves the active runner, and matches
   one existing session by the full route identity (ambiguity fails closed);
5. schedules `GatewayRunner.wake_session` on the live gateway loop and logs the
   non-raising optional-wake outcome.

Because observation is per successful target, successful members of partial
fanout wake before a later aggregate result or exception. The outer wrapper
returns or raises exactly what the original host function did.

Wake then traverses the same trusted internal `MessageEvent(internal=True)`
adapter pipeline as Kanban and writes the same durable receipt table.

Visible delivery is authoritative and is never rolled back by optional wake
failure. Once wake injection happens, bookkeeping failure becomes
`dispatched_unconfirmed`, not retryable failure, preventing duplicate injection.

## Host contract

```python
async def GatewayRunner.wake_session(
    *,
    payload: str,
    source_kind: str,
    session_key: str | None = None,
    session_id: str | None = None,
    dedupe_key: str | None = None,
) -> dict: ...
```

Supporting surfaces are session lookup, receipt create/update/table, Kanban
notifier routing, and cron post-delivery routing.

## Separate capability surfaces

Kanban mode is `full`, `inspect_only`, or `unsupported`. Cron delivery is `full`
or `unavailable`, independently sourced as `native`, `shim`, or `absent`.
Top-level `mode` remains Kanban-scoped for compatibility. Doctor overlays
`degraded` when cron wake is configured on but unavailable, or when cron policy
cannot be read and routing is absent.

This prevents the previous false positive: `mode=full source=shim` could prove
Kanban while cron deliveries remained visible-only.

## Compatibility transaction

Kanban and cron preflights are independent: cron drift never removes a healthy
Kanban shim. During cron installation, the adapter:

- pins `_deliver_result`, `_maybe_mirror_cron_delivery`, `load_config`, and
  `SessionStore.list_sessions` source digests;
- stores both original functions and installs both marker-bearing wrappers as
  one transaction;
- proves ownership of both wrappers without requiring the active runner, which
  does not yet exist during plugin discovery;
- restores both originals if either assignment or wrapper adoption fails;
- exposes install report and active source;
- restores only wrappers it still owns on uninstall, preserving successor
  wrappers after ownership loss.

After `GatewayRunner` construction, the runtime probe/doctor validates
`_gateway_runner_ref`, active-runner `wake_session`, and active session-store
attributes without hashing runtime object identity. Until those live
prerequisites exist, the wrappers remain installed but the cron capability is
reported unavailable rather than healthy.

The exact current seam is in `docs/compatibility-manifest.json`.

## Session identity

A delivery wakes only when exactly one existing live session has a stored origin
matching the delivered platform/chat/thread/user identity. Persisted
`session_key` or `session_id` values in `job.origin` are ignored as routing
authority; they cannot bypass the full route/user match. The adapter does not
synthesize sessions and does not wake a creator session for a different
broadcast target.

## Active-session behavior

The plugin does not replace the host platform busy-session implementation.
Already-active targets follow host queue/interrupt policy and receive a `queued`
receipt. The event remains delivered; only terminal receipt finalization may be
unavailable.

## Security model

The plugin assumes one trusted Hermes operator and inherits host platform
allowlists/tool access. Session keys are operator-internal identifiers. Receipts
store bounded previews plus hashes, not arbitrary full payload copies. The
adapter changes no authentication or authorization decision and never creates a
new session identity.

## Verification boundary

The current-host smoke exercises real scheduler delivery, DeliveryRouter,
MessageEvent, SessionDB schema/rows, wake receipt lifecycle, and existing-session
response. External platform network I/O is replaced by a deterministic local
transport. Live adoption still requires an outside gateway restart and a
post-restart canary; source tests cannot prove the already-running process
changed.

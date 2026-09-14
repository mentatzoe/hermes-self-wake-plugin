# Compatibility

## Capability surfaces are independent

`internal_session_wake_v1` is not one all-or-nothing health bit. The plugin
reports these surfaces separately:

| Surface | Healthy condition | Source |
|---|---|---|
| `kanban` | wake primitive, receipt methods/table, session lookup, and Kanban notifier routing all active | `native`, `shim`, or `absent` |
| `cron_delivery` | successful cron delivery is actively wrapped/routed to `wake_session` | `native`, `shim`, or `absent` |

The legacy top-level `mode=full` and `available=true` describe **Kanban** for
backward compatibility. They do not claim cron wake. Doctor derives an effective
`mode=degraded` when Kanban is full but the cron surface is unavailable and the
cron policy is either enabled or unreadable/absent. An explicit, readable
`wake_agent_on_delivery=false` remains healthy.

## Version 1.3.0 compatibility matrix

| Hermes host | Kanban shim | Cron adapter | Claim |
|---|---:|---:|---|
| Exact commit `21895bd39d9bc8a1cda307c9b0a0eb6fc98a8844`, expected private shapes | Supported | Supported; all stable seam digests and both wrappers required | Kanban and configured cron wake supported after restart/adoption |
| Native host exposing both wake primitives and cron routing | Native wins | Native wins | Supported when all structural probes pass |
| Host with Kanban wake but no cron adapter/routing | Supported Kanban only | Unavailable | Doctor healthy only when cron wake config is off; `degraded` failure when on |
| Any host where a Kanban private target drifted | Refused | Diagnosed independently | Kanban fails closed; cron drift never causes a healthy Kanban install to roll back |
| Any host where a cron stable seam or runtime prerequisite drifted | Kanban remains independently usable | Refused | Cron source absent/degraded; update/pin rather than forcing |
| No readable session state | Unavailable | Unavailable | `unsupported` |

Current exact seam identity is also recorded in
`docs/compatibility-manifest.json`:

- host commit: `21895bd39d9bc8a1cda307c9b0a0eb6fc98a8844`
- `cron.scheduler._deliver_result`:
  `dda5f6f956ec1a6069d2f227ca4d2ba012cfc72600947c82cfca5b468051140f`
- `cron.scheduler._maybe_mirror_cron_delivery`:
  `6f51d0efb37d1613380fece7e2c7ac7d1035d54825e4c8b45bb2dd843ba16f6c`
- `cron.scheduler.load_config`:
  `8680b219e570cf6db520ee21c4a043ad29a1cec19525162395e0c4dd15b54dbb`
- `gateway.session.SessionStore.list_sessions`:
  `1ce4bdb06b080936723a6beaf13eadc76734d1eceb154d033138da322362d5fb`

`gateway.run._gateway_runner_ref`, the active runner, its `wake_session`
signature, and active `session_store.list_sessions` are runtime prerequisites.
Plugin discovery happens before that runner exists, so installation proves the
two wrapper identities and leaves them adopted; the runtime probe/doctor then
validates the live prerequisites after `GatewayRunner` construction. The weakref
target is runtime state, not a stable source identity, and is never source-hashed.

## Provider precedence

For each surface, runtime precedence is `native > shim > absent`.

The bundled opt-in compatibility layer (`self_wake.compat_shim_enabled: true`)
provides:

- `SessionStore.lookup_by_session_key`;
- durable receipt methods and `session_wake_receipts` table;
- `GatewayRunner.wake_session`;
- Kanban `session:` / `session_id:` notifier routing;
- the companion `self_wake.cron_adapter` two-wrapper per-success adapter.

The cron adapter is a plugin-owned runtime applicator. It does not edit host
files. It preflights all stable private seam digests, installs the outer
`_deliver_result` and per-success `_maybe_mirror_cron_delivery` wrappers as one
transaction, and restores only wrappers it still owns. Installation proves
wrapper ownership without requiring the not-yet-constructed active runner;
doctor proves the live prerequisites after startup.
Assignment/wrapper-adoption failure rolls back both; uninstall
preserves any successor wrapper after ownership loss. A successful delivery
remains successful if optional wake resolution/dispatch fails.

## Cron wake semantics

A wake is attempted only when all are true:

1. the host called `_maybe_mirror_cron_delivery` for a target after that target
   succeeded (the helper's `enabled` flag may be false);
2. `cron.wake_agent_on_delivery=true`;
3. a live gateway runner and running gateway loop exist;
4. the actual platform/chat/thread/user route resolves unambiguously to one
   **existing** session.

The adapter never creates a synthetic session and never wakes the job creator
for an unrelated broadcast target. It uses `job.execution_id` as the fire
identity; when absent, one nonce is shared by all targets in that one
`_deliver_result` call. Thus repeated identical output from later executions is
not deduped forever, while duplicate observation inside one execution is. It
calls:

```python
await runner.wake_session(
    session_key=...,
    payload=...,
    source_kind="cron_delivery",
    dedupe_key=...,
)
```

Receipts therefore use `source_kind=cron_delivery` and the same lifecycle as
Kanban (`requested`, `dispatched`, `queued`, `agent_responded`,
`dispatched_unconfirmed`, `failure`, or deduped response).

## Fail-closed diagnostics

When cron wake config is on but the surface is absent, doctor emits:

- `ok=false`;
- `mode=degraded` when Kanban remains full;
- `cron_delivery_capability: fail`;
- `source=absent` plus the exact adoption/drift reason;
- remediation to install the matching plugin, enable the shim, restart the
  gateway, and require source `shim` or `native` on recheck.

When config is off, an absent cron surface is `info` and does not make doctor
unhealthy.

When config cannot be read/parsed, or the cron section is absent, policy is
unknown—not off. If routing is also absent, doctor emits `ok=false`,
`mode=degraded`, both cron checks fail, and remediation says: “Config could not
be read and cron routing cannot be verified.”

## Exact-route caveat closed on the supported host

The supported host normally passes the actual mutated thread to the per-success
helper for dedicated continuable threads and in-channel flattening. One branch
reports a stale requested thread after `raw_response.thread_fallback`. Because
the exact `_deliver_result` digest pins that branch and its local variables, the
wrapper reads that raw-response fact from the immediate host frame and changes
the wake route to no-thread. If this source shape changes, compatibility fails
closed; the plugin does not claim exact routing on an unpinned frame shape.

## Active-session caveat

For ordinary native `delivery_mode=wake` / `notify+wake` rows, see
[Native Kanban return repair](native-kanban-return.md). This separately
exact-host-gated path uses confirmed injection evidence, keeps uncertain
receipts pending and defers busy receivers. Structural doctor health alone
does not establish native delivery.

The legacy runtime shim does not replace `gateway/platforms/base.py`. A wake to an
already-active session follows the host's busy-session behavior and is receipted
`queued`. On hosts without queued-finalization, a queued receipt can persist
after processing; confirm the target transcript before classifying it as failed.

## Session resolver boundary

`self_wake_sessions` currently adapts Hermes' current-session cache
(`$HERMES_HOME/sessions/sessions.json`) and `state.db` metadata. That cache path
is a private adapter substrate, not the public tool contract. A future native
resolver can replace it without changing operator commands.

## Retired core patch

The former `docs/core-patch/0001-internal-session-wake-v1.patch` was based on
`b7f0c9c...` and does not apply to the supported host. It has been removed so an
operator cannot mistake stale bytes for a safe installer. No NousResearch PR is
required or opened. `docs/core-patch/README.md` records the retirement and points
to the plugin-owned applicator.

## Upgrade/adoption gate

After every Hermes or plugin update:

1. restart the gateway from an outside shell;
2. run `/self-wake doctor`;
3. record both surface sources;
4. if cron wake is enabled, require `cron_delivery_capability: ok`;
5. trigger a harmless delivery and require a `cron_delivery` receipt plus target
   session response.

Code present on disk without a gateway restart is **not adopted**.

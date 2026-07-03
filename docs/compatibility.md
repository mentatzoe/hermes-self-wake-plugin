# Compatibility

## Capability Sources (preference order)

The plugin needs the Hermes host capability `internal_session_wake_v1`. It can
be provided three ways:

1. **Compat shim** (`source: "shim"`) — the bundled `self_wake.compat_shim`
   installs the capability at runtime on vanilla Hermes when the operator opts
   in (`self_wake.compat_shim_enabled: true`). **This is the portable,
   shareable path: no core patch required.** The shim is the recommended way to
   share the plugin across Hermes installs and upgrades.
2. **Native** (`source: "native"`) — upstream Hermes ships the capability, or
   the operator applies the optional reference patch under `docs/core-patch/`.
3. **Absent** (`source: "absent"`) — neither present. The plugin runs in
   `inspect_only` mode and fails closed for wake-mutating operations.

When both the shim and a native capability are available, **native wins**: the
shim detects the native capability via the structural probe and does not
install. This lets an operator enable the shim defensively and then remove it
once upstream ships the capability natively, with no behavior change.

`self_wake_doctor` and `probe_wake_capability` report the active `source`
(`native` / `shim` / `absent`) so operators can see which path is providing the
capability.

## Compatibility Modes

| Mode | Host State | Behavior |
|------|-----------|----------|
| `full` | `internal_session_wake_v1` present (native **or** shim) + receipt table + notifier routing | All tools available |
| `inspect_only` | Wake primitive absent, session resolver or DB metadata readable | Discovery + partial doctor |
| `unsupported` | Required files/DBs missing/unreadable | Doctor reports setup issue |

A `full`-mode determination requires **both** the capability surface
(`wake_session` + receipts + lookup + table) **and** notifier routing (the
Kanban notifier actually calls `wake_session` for `session:` markers). This is
the fail-closed guard against a silent half-wake: a host or shim that provides
the surface but not the routing is reported as `inspect_only`, so
`self_wake_subscribe_kanban` refuses to write a marker that would never fire.

## Hermes Version Requirements

| Feature | Minimum Requirement |
|---------|-------------------|
| Plugin install + doctor | Any recent Hermes |
| Session discovery | A host session resolver. Current bundled adapter reads Hermes' gateway current-session cache (`sessions/sessions.json`) plus `state.db`; that cache path is not a public plugin contract. |
| Kanban wake subscribe | `internal_session_wake_v1` (native or **shim**) + `session_wake_receipts` table |
| Receipt inspection | `session_wake_receipts` table (created by shim or native) |
| Cron wake | Optional core patch only (not provided by the shim) |
| Send-message mirror wake | Optional core patch only (not provided by the shim) |

## Session resolver boundary

`self_wake_sessions` is intentionally resolver-neutral at the tool boundary. The
current implementation includes a fallback adapter for today's Hermes gateway
current-session cache (`$HERMES_HOME/sessions/sessions.json`) and uses `state.db`
only for extra title/preview metadata. That cache is active routing state, not a
canonical history ledger and not a public contract for all Hermes operators. A
future native resolver or `session_surfaces` adapter should replace the cache
adapter behind `self_wake.sessions` without changing the operator-facing tool.

## Fail-Closed Behavior

On hosts without `internal_session_wake_v1` (and without the shim enabled):

- `self_wake_subscribe_kanban` does NOT write `session:` or `session_id:` markers. It returns `capability_missing` with remediation instead.
- `self_wake_receipts` returns `capability_missing` when the `session_wake_receipts` table is absent.
- `self_wake_doctor` reports `inspect_only` or `unsupported` mode and lists the specific probes that failed.
- `self_wake_sessions` works read-only in all modes.

On an unsupported host the plugin refuses to write subscription markers the host could not act on. This is enforced at tool-call time, not at plugin load time, so the plugin can still install and report diagnostics.

## The Compat Shim

### What it does

When enabled (`self_wake.compat_shim_enabled: true`) and no native capability
is present, `self_wake.compat_shim.install_shim` monkeypatches four Hermes
classes at plugin load time:

| Target | What the shim adds | Why |
|--------|-------------------|-----|
| `gateway.session.SessionStore` | `lookup_by_session_key(session_key)` | `wake_session` resolves targets by key. `lookup_by_session_id` already exists on vanilla Hermes. |
| `hermes_state.SessionDB` | `create_session_wake_receipt` / `update_session_wake_receipt` + creates the `session_wake_receipts` table (idempotent `CREATE TABLE IF NOT EXISTS`) | Durable receipts for each wake, with dedupe. |
| `gateway.run.GatewayRunner` | `wake_session(*, payload, source_kind, session_key=None, session_id=None, dedupe_key=None)` + helpers | The wake primitive: resolves an existing session, injects a trusted `MessageEvent(internal=True)`, records a receipt. |
| `gateway.kanban_watchers.GatewayKanbanWatchersMixin` | `_kanban_internal_wake_target` + replaces `_kanban_notifier_watcher` | Routes `session:` / `session_id:` markers through `wake_session` instead of visible `adapter.send`. **This is the wiring that makes wakes actually fire** — without it the plugin would report full but wakes would never happen (silent half-wake). |

The `wake_session` / receipt / lookup implementations are carried verbatim
(comments trimmed) from `docs/core-patch/0001-internal-session-wake-v1.patch` so
the shim provides behavior identical to the core patch.

### Fail-closed on drift

Before installing, the shim verifies each private internal's source shape
matches what it expects (`_drift_check` in `self_wake/compat_shim.py`):

- `SessionStore.__init__` references `self._entries` and has `_ensure_loaded_locked`.
- `SessionDB` has `_execute_write` and `get_messages`.
- `GatewayRunner.__init__` references `session_store`, `_session_db`, `adapters`.
- `_kanban_notifier_watcher` source contains the vanilla send pattern (`await adapter.send(` + `sub["chat_id"], msg, metadata=metadata`) and does NOT already route wake markers.
- Extended anchors: every other host internal the vendored code calls is
  presence-checked — the mixin helpers (`_kanban_advance`, `_kanban_rewind`,
  `_kanban_unsub`, `_deliver_kanban_artifacts`), the `hermes_cli.kanban_db`
  function surface, `MessageEvent`'s `internal` field and `MessageType`, and
  `build_session_key`'s per-user session kwargs. A rename of any of these
  refuses install instead of failing open inside the notifier tick.

If any target has drifted (renamed, refactored, signature changed), the shim
**refuses to install** and returns a structured error naming the drifted
target. The plugin then stays in `inspect_only` mode. There is no silent
half-wake: the capability probe's `notifier_routing` check is an independent
second guard.

When drift is detected, `self_wake_doctor` reports a `compat_shim` check with
status `fail` and remediation pointing at updating the plugin or applying the
optional core patch.

### Native preference

If the host already has `wake_session` (upstream accepted the patch, or the
operator applied `docs/core-patch/`), `install_shim` detects the native
capability and does NOT install. The shim can be disabled by setting
`self_wake.compat_shim_enabled: false` (the default). When upstream later
exposes the capability natively, operators disable the shim (or it auto-skips)
and remove it — no plugin behavior changes because the capability probes are
identical.

### What the shim does NOT provide

The core patch also refines active-session wake queueing in
`gateway/platforms/base.py` (internal wakes queue without interrupting a
running agent). **The shim does not monkeypatch `base.py`.** On vanilla Hermes
with the shim, a wake to an already-active session is handled by the host's
default busy-session policy (interrupt or queue per config). The wake event is
always delivered and receipted — only the active-session queuing semantics
differ. Cron-delivery wake (`cron.wake_agent_on_delivery`) and send-message
mirror wake are likewise not wired by the shim; they require the optional core
patch or a future upstream capability. These gaps are documented honestly
rather than silently faked.

## Optional Core Patch (reference / upstream candidate)

The patch at `docs/core-patch/0001-internal-session-wake-v1.patch` is an
**optional reference artifact and upstream PR candidate**, not the required
install path. It is the cleanest way to provide the capability natively (no
monkeypatching, full active-session queueing refinement, cron/send-message
wake) and is what the shim's implementations are derived from.

- **Base commit:** `b7f0c9cd52febc32f4d2fb6205f3291c9e7bcf98`
- **Source commits:** `3d122a1ac`, `62e0f2e52`, `489720101`, `f45057c48`, `4a9087b67`, `1d5b98c39`

### How to apply (optional)

```bash
cd $HERMES_HOME/hermes-agent
git apply docs/core-patch/0001-internal-session-wake-v1.patch
scripts/run_tests.sh tests/
```

After applying, the shim auto-detects the native capability and does not
install (even if `compat_shim_enabled: true`).

## Upgrade Path

1. **Now (portable):** enable the compat shim (`self_wake.compat_shim_enabled: true`). No core patch.
2. **Optional:** apply `docs/core-patch/` for the full active-session queueing refinement and cron/send-message wake. The shim defers to the native capability.
3. **Future:** when upstream Hermes ships `internal_session_wake_v1` natively, disable the shim (`compat_shim_enabled: false`) or simply leave it — it auto-skips when native is present.

### Migration from first-cut plugin

If you used the pre-v1 first-cut plugin (`~/.hermes/plugins/self-wake/` version 0.1.0):

- Tool names are preserved (`self_wake_sessions`, `self_wake_subscribe_kanban`, `self_wake_receipts`, `self_wake_doctor`)
- Existing `kanban_notify_subs` rows are read as-is
- Visible-only rows (no `session:` marker) will not wake; re-subscribe with a target session to upgrade
- `session_wake_receipts` data is preserved across patch/shim upgrades

## Capability Probing Limitations

The `version` field reported by `self_wake_doctor` / `probe_wake_capability` is **structural, not a negotiated version**. `version=1` is reported whenever every structural probe passes (including `notifier_routing`). The `source` field distinguishes `native` vs `shim` so operators know which path is active.

A future host whose `wake_session` semantically drifted but kept the same parameter names would still pass the probe and report `version=1`. The probe includes a conservative partial return-shape check that inspects source for `"status"` and `"receipt_id"` key literals, but this can miss drift and declines to fail when source is unavailable. When a real public `hermes_cli.capabilities` (or equivalent) version API lands upstream, the structural probes should be replaced by real version negotiation.

## Shareability

The plugin is shareable to another operator on vanilla Hermes by:

1. `hermes plugins install <repo> --enable`
2. `self_wake.compat_shim_enabled: true` in `config.yaml`
3. Restart the gateway.

On partial-native hosts, methods the host already provides are never
overwritten: the shim fills only the gaps and reports what it skipped in
`skipped_native_methods` (the notifier pair is always replaced — routing wake
markers is the point, and the drift check refuses if the native watcher
already routes them).

No core patch, no Hermes source modification. The shim's drift detection will
refuse to install (and the plugin will report `inspect_only`) if the target
Hermes version's private internals have changed — at which point the operator
updates the plugin or applies the optional core patch.

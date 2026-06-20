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

## Fail-Closed Behavior

On hosts without `internal_session_wake_v1`:

- `self_wake_subscribe_kanban` does NOT write `session:` or `session_id:` markers. It returns `capability_missing` with remediation instead.
- `self_wake_receipts` returns `capability_missing` when the `session_wake_receipts` table is absent.
- `self_wake_doctor` reports `inspect_only` or `unsupported` mode and lists the specific probes that failed.
- `self_wake_sessions` works read-only in all modes.

The plugin never creates "looks subscribed but will never wake" state on an unsupported host. This is enforced at tool-call time, not at plugin load time, so the plugin can still install and report diagnostics.

## Core Patch Artifact

The patch is located at `docs/core-patch/0001-internal-session-wake-v1.patch`.

- **Base commit:** `b7f0c9cd52febc32f4d2fb6205f3291c9e7bcf98`
- **Source commits:** `3d122a1ac`, `62e0f2e52`, `489720101`, `f45057c48`, `4a9087b67`, `1d5b98c39`

The patch adds:
1. `GatewayRunner.wake_session(...)` — internal session wake primitive
2. `session_wake_receipts` table and `SessionDB` receipt methods
3. `SessionStore.lookup_by_session_key(...)` / `lookup_by_session_id(...)`
4. Kanban notifier routing for `session:` / `session_id:` markers
5. Cron delivery wake (`cron.wake_agent_on_delivery`)
6. Send-message mirror-to-wake scheduling

### How to apply

```bash
cd $HERMES_HOME/hermes-agent
git apply docs/core-patch/0001-internal-session-wake-v1.patch
scripts/run_tests.sh tests/
```

### How to verify

After applying the patch and restarting:

```
/self-wake doctor
```

Expected: `mode: full`, `core_capability: ok`.

## Upgrade Path

1. **Short term**: Apply `docs/core-patch/0001-internal-session-wake-v1.patch` to your Hermes install.
2. **Medium term**: Wait for upstream MR to NousResearch/hermes-agent.
3. **Long term**: Plugin uses stable public capability probe once available upstream.

### Migration from first-cut plugin

If you used the pre-v1 first-cut plugin (`~/.hermes/plugins/self-wake/` version 0.1.0):

- Tool names are preserved (`self_wake_sessions`, `self_wake_subscribe_kanban`, `self_wake_receipts`, `self_wake_doctor`)
- Existing `kanban_notify_subs` rows are read as-is
- Visible-only rows (no `session:` marker) will not wake; re-subscribe with a target session to upgrade
- `session_wake_receipts` data is preserved across patch upgrades

## Capability Probing Limitations

The `version` field reported by `self_wake_doctor` / `probe_wake_capability` is **structural, not a negotiated version**. There is no runtime version negotiation today: `version=1` is reported whenever every structural probe passes, and `requires_capabilities: internal_session_wake: ">=1"` in `plugin.yaml` is advisory only (Hermes has no manifest-level capability enforcement).

A future host whose `wake_session` semantically drifted (e.g. changed queueing behavior) but kept the same parameter names would still pass the probe and report `version=1`. The probe includes a conservative partial return-shape check that inspects source for `"status"` and `"receipt_id"` key literals, but this can miss drift and declines to fail when source is unavailable.

When a real public `hermes_cli.capabilities` (or equivalent) version API lands upstream, the structural probes should be replaced by real version negotiation.

## Shareability

The plugin is shareable to another operator only when:

1. The host Hermes has `internal_session_wake_v1` (patched or upstreamed), **or**
2. The operator accepts degraded `inspect_only` mode for discovery/doctor only.

The plugin does **not** include a monkeypatch shim to fake the core capability on vanilla Hermes. An experimental disabled-by-default shim exists under `experimental/compat_shim/` but is not recommended for production use.

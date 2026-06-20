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

## Capability Probing Limitations

The `version` field reported by `self_wake_doctor` / `probe_wake_capability` is
**structural, not a negotiated version**. There is no runtime version
negotiation today: `version=1` is reported whenever every structural probe
passes, and `requires_capabilities: internal_session_wake: ">=1"` in
`plugin.yaml` is advisory only (Hermes has no manifest-level capability
enforcement). A future host whose `wake_session` semantically drifted (e.g.
changed queueing behavior) but kept the same parameter names would still pass
the probe and report `version=1`.

To make return-contract drift at least partially detectable, the capability
probe includes a `_probe_wake_session_return_shape` check: it inspects the
source of `GatewayRunner.wake_session` for the v1 return-contract key literals
`"status"` and `"receipt_id"`. This is a conservative *partial* detector:

- It can miss a drifted host that still happens to contain those literals.
- It declines to fail when the source cannot be retrieved (`inspect.getsource`
  unavailable for C extensions / frozen modules) rather than false-positive on
  a real host.
- It cannot detect runtime behavioral drift (queueing, dedupe, dispatch
  semantics) — only that the literal return keys are present in source.

When a real public `hermes_cli.capabilities` (or equivalent) version API lands
upstream, the structural probes — including this return-shape check — should be
replaced by a real version negotiation.

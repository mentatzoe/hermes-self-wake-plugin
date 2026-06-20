# Self-Wake Plugin Architecture

This document is a distilled version of the formal architecture design
for repo users and plugin installers.

## System Boundary

Two deliverables:

1. **The formal plugin** — installed/managed as a Hermes plugin. Never edits
   core files and does not monkeypatch by default.

2. **A clean core patch artifact** — under `docs/core-patch/`, intended to
   become an upstream Hermes PR defining the host capability the plugin
   requires for full operation.

## Host Contract: internal_session_wake_v1

The plugin requires the Hermes host to expose:

```python
async def GatewayRunner.wake_session(
    *,
    payload: str,
    source_kind: str,
    session_key: str | None = None,
    session_id: str | None = None,
    dedupe_key: str | None = None,
) -> dict:
    """Inject a trusted internal event into an existing gateway session."""
```

## Plugin Tools

- `self_wake_sessions` — resolve candidate target sessions
- `self_wake_subscribe_kanban` — create/upgrade Kanban wake subscriptions
- `self_wake_receipts` — inspect wake receipts
- `self_wake_doctor` — end-to-end diagnostics

## Fail-Closed Behavior

When the host capability is missing:
- Wake-mutating operations return `capability_missing`
- `self_wake_subscribe_kanban` does NOT write `session:` markers
- Receipt queries detect missing table and return clear remediation
- Doctor reports `inspect_only` or `unsupported` mode

## Security

- Same-profile wake only (cross-profile disabled by default)
- Receipts store bounded preview + hash, not full payloads
- Logs include target/receipt identifiers, not full payloads/secrets

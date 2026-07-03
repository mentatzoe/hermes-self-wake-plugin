# Self-Wake Plugin Architecture

This document is a distilled version of the formal architecture design
for repo users and plugin installers.

## System Boundary

The plugin is **portable and shareable**: it does not require operators to patch
Hermes core. The wake capability can be provided by the bundled compat shim
(runtime monkeypatch, opt-in) or by a native upstream/patched capability.

These deliverables live in this repo:

1. **The formal plugin** — installed/managed as a Hermes plugin. Never edits
   core files. Provides tools, slash command, hook, and bundled skill.

2. **The compat shim** (`self_wake/compat_shim.py`) — an opt-in runtime
   compatibility layer that provides `internal_session_wake_v1` on vanilla
   Hermes via class-level monkeypatching. Fails closed on internal drift;
   defers to a native capability when present. This is the portable path.

3. **A current-Hermes session-resolver adapter** — `self_wake/sessions.py`
   resolves wake targets through a host-session resolver abstraction. Today that
   abstraction reads the gateway current-session cache and `state.db` metadata;
   that cache path is adapter detail, not the plugin's public contract.

4. **An optional reference core patch** — under `docs/core-patch/`, an
   upstream-candidate artifact. **Optional**, not required. Provides the
   native capability plus active-session queueing refinement and cron/send-message wake that the shim does not. The shim's implementations are derived
   verbatim from this patch.

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

Plus `SessionStore.lookup_by_session_key`, `SessionDB.create_session_wake_receipt` /
`update_session_wake_receipt`, the `session_wake_receipts` table, and Kanban
notifier routing for `session:` / `session_id:` markers.

This contract is provided by **either** the compat shim **or** the native
(upstream/patched) capability. The capability probe reports `source` =
`native` | `shim` | `absent`.

## Capability Mode Selection

`probe_wake_capability` determines the mode structurally:

- **full** — wake primitive + receipts + lookup + table + notifier routing all
  present (native or shim). `source` distinguishes the provider.
- **inspect_only** — wake primitive absent but host session resolver or state.db readable.
- **unsupported** — no readable session surfaces.

The `notifier_routing` probe is the fail-closed guard against a silent
half-wake: a host that provides `wake_session` + receipts but does NOT wire the
notifier to route `session:` markers is reported as `inspect_only`, not `full`.
This prevents `self_wake_subscribe_kanban` from writing a marker that would
never fire.

## Plugin Tools

- `self_wake_sessions` — resolve candidate target sessions
- `self_wake_subscribe_kanban` — create/upgrade Kanban wake subscriptions
- `self_wake_receipts` — inspect wake receipts
- `self_wake_doctor` — end-to-end diagnostics (includes shim status)

## Fail-Closed Behavior

When the capability is absent (no native, shim disabled):

- Wake-mutating operations return `capability_missing`
- `self_wake_subscribe_kanban` does NOT write `session:` markers
- Receipt queries detect missing table and return clear remediation
- Doctor reports `inspect_only` or `unsupported` mode + `compat_shim: info`

When the shim is enabled but drift is detected:

- The shim refuses to install (fail closed)
- Doctor reports `compat_shim: fail` with the drifted target
- The plugin stays `inspect_only` — no silent half-wake

## Security and trust model

The plugin assumes a **single trusted operator per Hermes install**. It adds
no access control of its own; everything below is inherited from the host.

- Who can invoke the tools and `/self-wake`: whoever Hermes lets talk to the
  agent on a platform where the `self_wake` toolset (tools) or the plugin
  (command) is active. Gate access with Hermes' own platform allowlists; the
  plugin does not check caller identity.
- `wake_session` is not profile-gated: any caller who can reach the tools can
  wake any session whose key or id they know. A leaked `session_key` means a
  holder who can reach the tools can inject internal events into that
  session. Treat session keys as operator-internal identifiers.
- `notifier_profile` is a routing filter (which notifier instance collects a
  subscription), not an ownership or permission boundary.
- Cross-profile wakes are possible by design when the caller supplies the
  target's key; nothing in the plugin forbids them.
- Kanban notifier routing filters subscriptions by owning profile by default (`--notifier-profile` overrides); `wake_session` itself is not profile-gated
- Receipts store bounded preview + hash, not full payloads
- Logs include target/receipt identifiers, not full payloads/secrets
- The shim monkeypatches only the four documented classes; it does not touch
  auth, session-creation, or message-routing policy beyond the wake routing

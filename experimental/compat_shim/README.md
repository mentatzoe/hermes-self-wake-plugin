# Compat Shim

This directory previously held an experimental, documentation-only compat
shim. **The shim is now a real, supported implementation** at
`self_wake/compat_shim.py`.

See:
- `docs/compatibility.md` — what the shim provides, fail-closed drift behavior,
  native preference, and the honest list of what it does NOT provide.
- `self_wake/compat_shim.py` — the implementation (module docstring documents
  every private Hermes internal it touches and why).

Enable it in `config.yaml`:

```yaml
self_wake:
  compat_shim_enabled: true
```

The shim provides `internal_session_wake_v1` on vanilla Hermes at runtime
(`wake_session`, receipt methods, session lookup, receipts table, and Kanban
notifier routing) without patching Hermes core. It fails closed if private
Hermes internals drift, and defers to a native capability when present.

# Experimental Compatibility Shim

**This directory is intentionally disabled-by-default.**

If enabled, it provides a monkeypatch-based compatibility layer for
vanilla Hermes installations that lack `internal_session_wake_v1`.

## Warnings

- This is NOT the recommended install path
- Monkeypatches private gateway/session/cron internals
- Highly sensitive to Hermes version changes
- Silently violates prompt-caching and role-alternation invariants
- Must be explicitly enabled via config: `self_wake.compat_shim.enabled: true`
- Fail-closed if target method signatures/source hashes are unknown

## Recommended path

Apply the clean core patch from `docs/core-patch/` instead, or upgrade
to a Hermes version that includes `internal_session_wake_v1`.

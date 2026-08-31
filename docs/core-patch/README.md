# Core Patch Artifact — Retired

The former `0001-internal-session-wake-v1.patch` was generated against Hermes
base `b7f0c9cd52febc32f4d2fb6205f3291c9e7bcf98` and no longer applies to the
supported host (`21895bd39d9bc8a1cda307c9b0a0eb6fc98a8844`). It was removed in
self-wake 1.3.0. **Do not recover or apply it.**

Self-wake is now delivered as a plugin-owned runtime compatibility adapter:

- `self_wake.compat_shim` provides Kanban wake, receipt, and lookup surfaces;
- `self_wake.cron_adapter` transactionally wraps the exact supported
  `_deliver_result` and per-target `_maybe_mirror_cron_delivery` shapes after
  stable-seam SHA-256 preflight;
- installation is in-process, adoption-probed, rollback-capable, and visible in
  doctor per capability surface;
- host drift refuses installation;
- no developer checkout and no upstream NousResearch PR is a product dependency.

Canonical compatibility identity lives in:

- `self_wake/cron_adapter.py` (`SUPPORTED_HOSTS`);
- `docs/compatibility-manifest.json`;
- `docs/compatibility.md`.

Operator path:

1. install self-wake 1.3.0;
2. set `self_wake.compat_shim_enabled: true`;
3. set `cron.wake_agent_on_delivery: true` only if intended;
4. restart the gateway from outside the running gateway;
5. run `self_wake_doctor` and require both Kanban and cron surfaces healthy;
6. run `scripts/current_host_cron_smoke.py` or a harmless live canary.

If native Hermes later ships equivalent surfaces, the native probes take
precedence and this compatibility adapter can retire without restoring this
stale patch.

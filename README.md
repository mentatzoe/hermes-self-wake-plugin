# hermes-self-wake-plugin

[![CI](https://github.com/mentatzoe/hermes-self-wake-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/mentatzoe/hermes-self-wake-plugin/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Portable operator tools for Hermes internal session wake routing.**

A visible platform notification tells the *human* something happened. An
**internal wake** resumes the *session that owns the work*, with the event
injected and a receipt written. This plugin provides the operator surface
for internal wakes — session discovery, Kanban wake subscriptions, wake
receipts, diagnostics — and bundles an opt-in compat shim so it works on
vanilla Hermes without patching core.

## What you get

- Kanban wake subscriptions: a card's terminal events wake the subscribed
  session instead of stalling on a visible-only notification.
- Wake receipts: every attempt is recorded and usually answers whether it
  dispatched and whether the agent responded; active-session `queued`
  receipts may need transcript confirmation on hosts without
  queued-finalization.
- Diagnostics: `self_wake_doctor` reports the capability mode, its source,
  and exactly which probe failed when something is off.
- Session discovery: resolve wake targets through a host-session resolver.

Scope depends on how your host gets the wake capability: the bundled shim
covers **Kanban wakes**; **cron-delivery and cross-session message wakes**
come with the optional core patch (upstream Hermes does not currently ship
the capability). The capability matrix in `docs/compatibility.md` is
canonical.

## Install

```bash
# 1. Install (--enable also enables it)
hermes plugins install https://github.com/mentatzoe/hermes-self-wake-plugin.git --enable

# 2. APPEND self_wake to your existing platform_toolsets lists in
#    ~/.hermes/config.yaml — do not replace the lists:
#    platform_toolsets:
#      default: [web, terminal, file, self_wake]   # keep your existing entries

# 3. (Portable path) Enable the compat shim — no core patch needed:
#    self_wake:
#      compat_shim_enabled: true

# 4. Restart the gateway so registration and the shim take effect
hermes gateway restart
```

Full walkthrough, including the optional core-patch path for cron and
message wakes: `docs/install-use.md`.

## Use

```
/self-wake sessions [query] [--platform discord] [--chat-id ID] [--thread-id ID]
/self-wake subscribe TASK --board BOARD --session-key KEY [--reset-cursor] [--dry-run]
/self-wake receipts [--session-key KEY | --session-id ID] [--source-kind kanban]
/self-wake doctor
```

The model-facing tools mirror these: `self_wake_sessions`,
`self_wake_subscribe_kanban`, `self_wake_receipts`, `self_wake_doctor`
(exposed on platforms where `self_wake` is in the toolset; the slash command
is registered by the enabled plugin independently of toolsets). The plugin
also registers one `pre_llm_call` hook that maintains a small local
diagnostics cache — details in the runbook.

Day-to-day operations — verifying wakes, reading receipt statuses,
troubleshooting, rollback: `docs/operator-runbook.md`.

## How it works

Three capability provider paths: **native** (real code in core — today that
means the reference patch under `docs/core-patch/`, since upstream doesn't
ship it), the **compat shim** (opt-in, runtime, class-level monkeypatching
with drift checks that refuse to install when host internals changed, never
overwriting native methods), or **absent** (inspect-only; wake-mutating
operations fail closed). Runtime precedence is always native > shim >
absent.

The failure posture in one line: fail closed rather than pretend, write a
receipt for every attempt (with the documented `queued` caveat), never
re-inject a delivered wake, never imply a
downgrade happened when it didn't. The full behaviour contract — enforced by
the test suite — lives in `docs/compatibility.md`; the design and trust
model in `docs/architecture.md`.

## Status

Implemented and released — see the
[releases page](https://github.com/mentatzoe/hermes-self-wake-plugin/releases)
for the changelog. The suite runs in CI on Python 3.10–3.13 against a temp
`HERMES_HOME`, including the real plugin-loader path and in-process contract
tests covering wake dispatch and receipts.

## Documentation

- `docs/install-use.md` — step-by-step install (shim path + optional core-patch path)
- `docs/operator-runbook.md` — operations: subscribe, verify, receipt statuses, troubleshooting, rollback, the diagnostics hook
- `docs/compatibility.md` — **canonical**: capability sources and matrix, modes, fail-closed contract, shim internals, session-resolver boundary, upgrade path
- `docs/architecture.md` — system design, host contract, security and trust model
- `docs/core-patch/` — optional reference / upstream-candidate core patch

## License

Apache-2.0.

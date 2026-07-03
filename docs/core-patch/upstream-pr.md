# Upstream PR: internal_session_wake_v1

## Description

Add a generic internal session wake capability to Hermes gateway.
This enables plugins and internal systems to inject trusted events into
existing sessions without breaking prompt caching or role alternation.

## Motivation

Operators need agents to resume work after external events (Kanban
completion, cron delivery, outbound message delivery). Currently this
requires ad-hoc monkeypatching of gateway internals.

A clean, host-owned primitive ensures:
- Prompt caching is preserved
- Role alternation is maintained
- Active sessions are not interrupted
- Receipts provide observability

## Changes

adds `GatewayRunner.wake_session` with the v1 keyword contract, the `session_wake_receipts` table with receipt-write methods, session-store lookup, and Kanban notifier wake routing (see `0001-internal-session-wake-v1.patch` for the full diff)

## Checklist

- [ ] All tests pass
- [ ] No prompt-caching violation
- [ ] No role-alternation violation
- [ ] Active-session queueing tested
- [ ] Receipt states tested
- [ ] Dedupe tested
- [ ] Capability probe function added
- [ ] Documentation updated

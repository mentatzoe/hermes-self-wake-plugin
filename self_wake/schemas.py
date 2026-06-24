"""JSON schemas for self-wake plugin tools.

Each schema follows the Hermes tool schema shape: ``name``, ``description``,
and ``parameters`` (JSON Schema). Handlers return JSON strings.
"""
from __future__ import annotations

SELF_WAKE_SESSIONS = {
    "name": "self_wake_sessions",
    "description": (
        "Resolve Hermes gateway sessions and their session_key/session_id/origin "
        "metadata for internal wake routing. Use before subscribing Kanban or "
        "diagnosing self-wake delivery. Read-only; works in inspect-only mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Exact Hermes session id to resolve."},
            "session_key": {"type": "string", "description": "Exact gateway session key to resolve."},
            "platform": {"type": "string", "description": "Platform filter, e.g. discord, telegram."},
            "chat_id": {"type": "string", "description": "Chat/channel/thread id filter from the gateway origin."},
            "thread_id": {"type": "string", "description": "Thread/topic id filter from the gateway origin."},
            "query": {"type": "string", "description": "Case-insensitive substring over session key, display name, title, chat id, thread id, or session id."},
            "limit": {"type": "integer", "description": "Maximum sessions to return (default 10, max 50)."},
        },
        "required": [],
    },
}

SELF_WAKE_SUBSCRIBE_KANBAN = {
    "name": "self_wake_subscribe_kanban",
    "description": (
        "Subscribe or upgrade a Kanban task's terminal-event notification so it "
        "wakes an existing Hermes session internally via session:<session_key> "
        "or session_id:<session_id>, instead of merely sending a visible chat "
        "notification. Fails closed with capability_missing on hosts lacking "
        "internal_session_wake_v1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Kanban task id, e.g. t_3114f216."},
            "board": {"type": "string", "description": "Kanban board slug. Defaults to 'default'."},
            "session_key": {"type": "string", "description": "Target gateway session key. Preferred when known."},
            "session_id": {"type": "string", "description": "Target Hermes session id. Used to resolve session_key when possible, otherwise session_id marker."},
            "platform": {"type": "string", "description": "Delivery platform override. Defaults from target session origin or discord."},
            "chat_id": {"type": "string", "description": "Notifier delivery chat id override. Defaults from target session origin."},
            "thread_id": {"type": "string", "description": "Notifier thread/topic id override. Defaults from target session origin, else empty."},
            "notifier_profile": {"type": "string", "description": "Notifier owner profile. Defaults to active Hermes profile."},
            "reset_cursor": {"type": "boolean", "description": "If true, set last_event_id=0 after subscribing so already-claimed terminal events replay. Use only after duplicate-risk check."},
            "dry_run": {"type": "boolean", "description": "If true, resolve and report the row that would be written without modifying the board DB. Allowed in any capability mode."},
            "force_degraded_visible_only": {"type": "boolean", "description": "Explicit opt-in to write a visible-only (non-waking) row even on a capable host. Loudly flagged in output. Default false."},
        },
        "required": ["task_id"],
    },
}

SELF_WAKE_RECEIPTS = {
    "name": "self_wake_receipts",
    "description": (
        "Inspect session_wake_receipts rows to verify whether self-wake actually "
        "queued/dispatched/responded, filtered by target session, source, status, "
        "or dedupe key. Returns capability_missing when the receipt table is absent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "receipt_id": {"type": "integer", "description": "Exact receipt id to inspect."},
            "session_key": {"type": "string", "description": "Filter by target session key."},
            "session_id": {"type": "string", "description": "Filter by target session id."},
            "source_kind": {"type": "string", "description": "Filter by source kind, e.g. kanban, cron_delivery, send_message."},
            "status": {"type": "string", "description": "Filter by receipt status. Valid values: requested, dispatched, queued, agent_responded, failure, deduped."},
            "dedupe_key": {"type": "string", "description": "Filter by exact dedupe key."},
            "limit": {"type": "integer", "description": "Maximum receipts to return (default 50, max 200)."},
        },
        "required": [],
    },
}

SELF_WAKE_DOCTOR = {
    "name": "self_wake_doctor",
    "description": (
        "Run end-to-end self-wake diagnostics: plugin/capability mode, session "
        "index, receipt table, Kanban DB, cron wake config, and existing "
        "subscriptions. Returns structured JSON with ok, mode, failures, "
        "warnings, and remediation."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

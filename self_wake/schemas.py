"""JSON schemas for self-wake plugin tools."""

# TODO: define input/output schemas for each tool
# - self_wake_sessions
# - self_wake_subscribe_kanban
# - self_wake_receipts
# - self_wake_doctor

SESSIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {"type": "string", "description": "Filter by session ID"},
        "session_key": {"type": "string", "description": "Filter by session key"},
        "platform": {"type": "string", "description": "Filter by platform"},
        "chat_id": {"type": "string", "description": "Filter by chat ID"},
        "thread_id": {"type": "string", "description": "Filter by thread ID"},
        "query": {"type": "string", "description": "Free-text search query"},
        "limit": {"type": "integer", "description": "Max results", "default": 10},
    },
}

SUBSCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "Kanban task ID to subscribe to"},
        "board": {"type": "string", "description": "Kanban board slug"},
        "session_key": {"type": "string", "description": "Target session key"},
        "session_id": {"type": "string", "description": "Target session ID"},
        "platform": {"type": "string", "description": "Platform override"},
        "chat_id": {"type": "string", "description": "Chat ID override"},
        "thread_id": {"type": "string", "description": "Thread ID override"},
        "reset_cursor": {"type": "boolean", "description": "Reset event cursor", "default": False},
        "dry_run": {"type": "boolean", "description": "Preview without writing", "default": False},
        "force_degraded_visible_only": {
            "type": "boolean",
            "description": "Force visible-only mode",
            "default": False,
        },
    },
    "required": ["task_id"],
}

RECEIPTS_SCHEMA = {
    "type": "object",
    "properties": {
        "receipt_id": {"type": "string", "description": "Filter by receipt ID"},
        "session_key": {"type": "string", "description": "Filter by target session key"},
        "session_id": {"type": "string", "description": "Filter by target session ID"},
        "source_kind": {
            "type": "string",
            "description": "Filter by source kind (kanban, cron, send_message)",
        },
        "status": {"type": "string", "description": "Filter by receipt status"},
        "dedupe_key": {"type": "string", "description": "Filter by dedupe key"},
        "limit": {"type": "integer", "description": "Max results", "default": 50},
    },
}

DOCTOR_SCHEMA = {
    "type": "object",
    "properties": {},
}

"""Tool handlers for self-wake plugin.

Each handler receives (args: dict, **kwargs) and returns a JSON string.
Handlers must catch all errors and return structured error responses.
"""

import json


def sessions_handler(args: dict, **kwargs) -> str:
    """Resolve candidate target sessions.

    Works in degraded mode by reading session index and state DB.
    """
    # TODO: implement session discovery
    # - Read $HERMES_HOME/sessions/sessions.json
    # - Query $HERMES_HOME/state.db
    # - Use SessionStore data if host capability present
    # - Return structured results with source paths, match confidence, origin info
    return json.dumps({
        "success": False,
        "error": "not_implemented",
        "tool": "self_wake_sessions",
    })


def subscribe_handler(args: dict, **kwargs) -> str:
    """Create or upgrade a Kanban wake subscription.

    Fails closed with capability_missing if host lacks internal_session_wake_v1.
    """
    # TODO: implement
    # - Probe host capability
    # - If missing and not dry_run, return capability_missing error
    # - Resolve target session
    # - If ambiguous, return matches without writing
    # - Use hermes_cli.kanban_db.add_notify_sub when available
    # - Parameterized SQL only for cursor reset/verification
    # - Report before/after for upgrades
    return json.dumps({
        "success": False,
        "error": "not_implemented",
        "tool": "self_wake_subscribe_kanban",
    })


def receipts_handler(args: dict, **kwargs) -> str:
    """Inspect wake receipts.

    Returns capability_missing if session_wake_receipts table is absent.
    """
    # TODO: implement
    # - Check session_wake_receipts table exists
    # - If missing, return capability_missing with remediation
    # - Query receipts with filters
    # - Redact/truncate payload preview
    return json.dumps({
        "success": False,
        "error": "not_implemented",
        "tool": "self_wake_receipts",
    })


def doctor_handler(args: dict, **kwargs) -> str:
    """End-to-end diagnostics for self-wake setup.

    Checks: plugin enabled, toolset enabled, core capability, session index,
    receipt table, Kanban DB, cron config, wake subscriptions, gateway adapters.
    """
    # TODO: implement
    # - Check plugin loaded/enabled
    # - Check toolset exposed for current platform
    # - Probe host capability (version)
    # - Check session index readability
    # - Check state.db receipt table
    # - Check Kanban DB reachable
    # - Check cron config wake_agent_on_delivery
    # - List existing wake subscriptions
    # - Check target session origin completeness
    # - Check live gateway/adapters when in gateway
    # - Return structured JSON: ok, mode, failures, warnings, remediation
    return json.dumps({
        "success": False,
        "error": "not_implemented",
        "tool": "self_wake_doctor",
        "mode": "unsupported",
        "ok": False,
        "failures": ["plugin not yet implemented"],
        "warnings": [],
        "remediation": "implement tool handlers per architecture doc",
    })

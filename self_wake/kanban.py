"""Kanban subscription read/write/migration helpers for self-wake plugin.

Manages kanban_notify_subs rows with session: or session_id: markers
for internal wake routing.
"""


def list_wake_subscriptions(board: str | None = None) -> list[dict]:
    """List existing Kanban wake subscriptions.

    Args:
        board: Optional board slug filter.

    Returns:
        List of subscription dicts with marker type (visible/session/session_id).
    """
    # TODO: implement
    return []


def create_wake_subscription(
    task_id: str,
    target_session_key: str | None = None,
    target_session_id: str | None = None,
    board: str | None = None,
    dry_run: bool = False,
    reset_cursor: bool = False,
) -> dict:
    """Create or upgrade a Kanban wake subscription.

    Args:
        task_id: Kanban task ID.
        target_session_key: Target session key (session: marker).
        target_session_id: Target session ID (session_id: marker).
        board: Board slug.
        dry_run: Preview without writing.
        reset_cursor: Reset event cursor (explicit opt-in).

    Returns:
        Result dict with before/after state.
    """
    # TODO: implement
    # - Probe capability first
    # - If missing and not dry_run, return capability_missing
    # - Use hermes_cli.kanban_db.add_notify_sub when available
    # - Parameterized SQL only for cursor operations
    # - Report before/after for upgrades
    return {"success": False, "error": "not_implemented"}


def migrate_visible_rows(
    board: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Find visible-only rows that should be upgraded to session-wake.

    Args:
        board: Optional board filter.
        dry_run: If True, only report; if False, upgrade.

    Returns:
        Migration report with rows found and actions taken.
    """
    # TODO: implement
    return {"success": False, "error": "not_implemented", "rows_found": 0}

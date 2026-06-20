"""Receipt query and table-missing handling for self-wake plugin.

Reads session_wake_receipts from state.db with graceful degradation
when the table does not exist.
"""


def query_receipts(
    receipt_id: str | None = None,
    session_key: str | None = None,
    session_id: str | None = None,
    source_kind: str | None = None,
    status: str | None = None,
    dedupe_key: str | None = None,
    limit: int = 50,
    hermes_home: str | None = None,
) -> dict:
    """Query session wake receipts.

    Handles missing session_wake_receipts table gracefully.

    Returns:
        dict with success, mode, receipts list, and counts.
        If table missing: returns capability_missing with remediation.
    """
    # TODO: implement
    # - Check session_wake_receipts table exists
    # - If missing, return capability_missing
    # - Query with filters
    # - Redact/truncate payload preview (bounded preview + hash only)
    return {
        "success": False,
        "error": "not_implemented",
        "mode": "unsupported",
        "receipts": [],
    }

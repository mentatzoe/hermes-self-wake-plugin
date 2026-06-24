"""Tests for the receipt query module (table-missing handling, filters, truncation)."""
from __future__ import annotations

from self_wake import receipts


def test_query_no_state_db_returns_capability_missing(hermes_home):
    """No state.db at all → capability_missing, unsupported."""
    result = receipts.query_receipts()
    assert result["success"] is False
    assert result["error"] == "capability_missing"
    assert result["mode"] == "unsupported"
    assert result["count"] == 0
    assert "remediation" in result


def test_query_table_missing_returns_capability_missing(
        hermes_home, sqlite_state_db_without_receipts):
    """state.db exists but session_wake_receipts table absent → capability_missing."""
    result = receipts.query_receipts()
    assert result["success"] is False
    assert result["error"] == "capability_missing"
    assert result["mode"] == "inspect_only"
    assert "session_wake_receipts table absent" in result["reason"]


def test_query_returns_receipts_when_table_present(state_db_with_receipts):
    result = receipts.query_receipts()
    assert result["success"] is True
    assert result["mode"] == "full"
    assert result["count"] == 2
    kinds = {r["source_kind"] for r in result["receipts"]}
    assert kinds == {"kanban", "cron_delivery"}


def test_query_filters_by_source_kind(state_db_with_receipts):
    result = receipts.query_receipts(source_kind="kanban")
    assert result["success"] is True
    assert result["count"] == 1
    assert result["receipts"][0]["source_kind"] == "kanban"


def test_query_filters_by_status(state_db_with_receipts):
    result = receipts.query_receipts(status="failure")
    assert result["count"] == 1
    assert result["receipts"][0]["status"] == "failure"
    assert result["receipts"][0]["error"] == "adapter unavailable"


def test_query_filters_by_target_session_key(state_db_with_receipts):
    key = "agent:main:discord:thread:1511162896762142980:1511162896762142980"
    result = receipts.query_receipts(session_key=key)
    assert result["count"] == 1
    assert result["receipts"][0]["target_session_key"] == key


def test_query_filters_by_dedupe_key(state_db_with_receipts):
    result = receipts.query_receipts(dedupe_key="kanban:t_abc")
    assert result["count"] == 1


def test_query_truncates_long_preview(state_db_with_receipts):
    """The cron receipt has a 300-char preview; output is truncated to 200."""
    result = receipts.query_receipts(source_kind="cron_delivery")
    preview = result["receipts"][0]["payload_preview"]
    assert len(preview) <= 201  # 200 + ellipsis
    assert preview.endswith("…")
    assert result["preview_max_chars"] == 200


def test_query_invalid_receipt_id_returns_error(state_db_with_receipts):
    result = receipts.query_receipts(receipt_id="not-a-number")
    assert result["success"] is False
    assert result["error"] == "invalid_receipt_id"


def test_query_limit_bounded(state_db_with_receipts):
    result = receipts.query_receipts(limit=1)
    assert result["count"] == 1


def test_receipt_status_counts(state_db_with_receipts):
    counts = receipts.receipt_status_counts()
    assert counts["available"] is True
    assert counts["failure_count"] == 1
    assert "kanban" in counts["by_source_kind"]
    assert counts["by_source_kind"]["kanban"]["agent_responded"] == 1


def test_receipt_status_counts_unavailable(hermes_home):
    counts = receipts.receipt_status_counts()
    assert counts["available"] is False

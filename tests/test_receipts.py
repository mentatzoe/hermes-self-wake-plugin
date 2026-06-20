"""Tests for receipt query module."""

from self_wake.receipts import query_receipts


def test_query_receipts_returns_structured():
    """query_receipts returns a structured result dict."""
    result = query_receipts()
    assert isinstance(result, dict)
    assert "success" in result


def test_query_with_filters():
    """Receipt query accepts filter parameters."""
    result = query_receipts(
        source_kind="kanban",
        status="agent_responded",
        limit=5,
    )
    assert isinstance(result, dict)

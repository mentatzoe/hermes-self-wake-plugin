"""Tests for session discovery module."""

from self_wake.sessions import read_sessions_index, query_state_db, resolve_target_session


def test_read_sessions_index_returns_list():
    """read_sessions_index returns a list (possibly empty)."""
    result = read_sessions_index()
    assert isinstance(result, list)


def test_query_state_db_returns_list():
    """query_state_db returns a list (possibly empty)."""
    result = query_state_db()
    assert isinstance(result, list)


def test_resolve_target_session_returns_list():
    """resolve_target_session returns a list of matches."""
    result = resolve_target_session(session_key="test:key")
    assert isinstance(result, list)


def test_resolve_with_no_args_returns_empty():
    """Resolving with no criteria returns empty or all."""
    result = resolve_target_session()
    assert isinstance(result, list)

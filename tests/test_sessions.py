"""Tests for session discovery / resolution."""
from __future__ import annotations

from self_wake import sessions


def test_read_sessions_index_returns_dict_keyed_by_session_key(hermes_home):
    """sessions.json is a dict keyed by session_key (real Hermes format)."""
    idx = sessions.read_sessions_index()
    assert isinstance(idx, dict)
    assert "agent:main:discord:thread:1511162896762142980:1511162896762142980" in idx
    # Each value is an entry dict with session_id.
    entry = idx["agent:main:discord:thread:1511162896762142980:1511162896762142980"]
    assert entry["session_id"] == "20260101_120000_aaaaaa"


def test_read_sessions_index_missing_file(tmp_path, monkeypatch):
    """Missing sessions.json yields an empty dict, never raises."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "nope"))
    assert sessions.read_sessions_index() == {}


def test_resolve_by_exact_session_key(hermes_home):
    """Exact session_key resolves to a single entry."""
    key = "agent:main:discord:thread:1511162896762142980:1511162896762142980"
    matches = sessions.resolve_target_session(session_key=key)
    assert len(matches) == 1
    assert matches[0]["session_key"] == key
    assert matches[0]["session_id"] == "20260101_120000_aaaaaa"
    assert matches[0]["platform"] == "discord"
    assert matches[0]["origin"]["thread_id"] == "1511162896762142980"


def test_resolve_discord_thread_key_shape(hermes_home):
    """Discord thread key shape agent:main:discord:thread:<id>:<id> resolves."""
    matches = sessions.resolve_target_session(
        platform="discord", chat_id="1511162896762142980",
        thread_id="1511162896762142980")
    assert len(matches) == 1
    assert "discord:thread" in matches[0]["session_key"]


def test_resolve_by_session_id(hermes_home):
    matches = sessions.resolve_target_session(session_id="20260101_120000_cccccc")
    assert len(matches) == 1
    assert matches[0]["platform"] == "telegram"
    assert matches[0]["chat_type"] == "dm"


def test_resolve_by_platform_filter(hermes_home):
    matches = sessions.resolve_target_session(platform="discord")
    assert len(matches) == 2
    keys = {m["session_key"] for m in matches}
    assert "agent:main:discord:thread:1511162896762142980:1511162896762142980" in keys
    assert "agent:main:discord:channel:123:456" in keys


def test_resolve_by_query_substring(hermes_home):
    """Free-text query matches across the entry summary."""
    matches = sessions.resolve_target_session(query="LTM")
    assert len(matches) == 1
    assert "thread" in matches[0]["session_key"]


def test_resolve_unknown_key_preserves_marker_target(hermes_home):
    """A caller-supplied key not in the index is preserved for subscribe."""
    matches = sessions.resolve_target_session(session_key="agent:main:discord:dm:999")
    assert len(matches) == 1
    assert matches[0]["session_key"] == "agent:main:discord:dm:999"
    assert matches[0].get("resolved_from_index") is False
    assert matches[0]["origin"] == {}


def test_resolve_no_criteria_returns_all(hermes_home):
    matches = sessions.resolve_target_session()
    assert len(matches) == 3


def test_is_ambiguous(hermes_home):
    assert sessions.is_ambiguous([]) is False
    single = sessions.resolve_target_session(session_id="20260101_120000_aaaaaa")
    assert sessions.is_ambiguous(single) is False
    multi = sessions.resolve_target_session(platform="discord")
    assert sessions.is_ambiguous(multi) is True


def test_query_state_db_enforces_hard_limit(hermes_home):
    """limit is bounded by HARD_LIMIT."""
    out = sessions.query_state_db(limit=99999)
    assert len(out) <= sessions.HARD_LIMIT


def test_resolve_with_state_db_titles(state_db_with_receipts):
    """When state.db has session titles, they are merged into the summary."""
    matches = sessions.resolve_target_session(session_id="20260101_120000_aaaaaa")
    assert len(matches) == 1
    assert matches[0]["title"] == "LTM repair work"
    assert matches[0]["message_count"] == 5

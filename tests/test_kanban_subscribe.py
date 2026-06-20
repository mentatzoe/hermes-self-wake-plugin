"""Tests for the Kanban wake subscription module (fail-closed behavior)."""
from __future__ import annotations

import json

from self_wake import kanban as kanban_mod

DISCORD_THREAD_KEY = "agent:main:discord:thread:1511162896762142980:1511162896762142980"


# --------------------------------------------------------------------------- #
# list_wake_subscriptions
# --------------------------------------------------------------------------- #
def test_list_subscriptions_empty(kanban_backend, hermes_home):
    assert kanban_mod.list_wake_subscriptions(backend=kanban_backend) == []


def test_list_subscriptions_classifies_markers(kanban_backend, full_capability,
                                               state_db_with_receipts, hermes_home):
    kanban_mod.create_wake_subscription(
        task_id="t_1", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    rows = kanban_mod.list_wake_subscriptions(backend=kanban_backend)
    assert len(rows) == 1
    assert rows[0]["marker_type"] == "session"


# --------------------------------------------------------------------------- #
# create_wake_subscription — fail-closed
# --------------------------------------------------------------------------- #
def test_subscribe_requires_task_id(kanban_backend, full_capability, hermes_home):
    result = kanban_mod.create_wake_subscription(
        task_id="", target_session_key=DISCORD_THREAD_KEY, backend=kanban_backend)
    assert result["success"] is False
    assert result["error"] == "task_id is required"


def test_subscribe_fails_closed_without_capability(
        kanban_backend, inspect_only_capability, hermes_home):
    """On a host without wake capability, a real (non-dry-run) subscribe must
    NOT write a session: marker — it returns capability_missing instead."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_abc", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    assert result["success"] is False
    assert result["error"] == "capability_missing"
    assert result["would_write_marker"] == f"session:{DISCORD_THREAD_KEY}"
    # CRITICAL: no row was written.
    assert kanban_mod.list_wake_subscriptions(backend=kanban_backend) == []


def test_subscribe_dry_run_allowed_without_capability(
        kanban_backend, inspect_only_capability, hermes_home):
    """dry_run previews the marker even on an incapable host, without writing."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_abc", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend, dry_run=True)
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["user_id_marker"] == f"session:{DISCORD_THREAD_KEY}"
    assert result["capability_mode"] == "inspect_only"
    assert result["capability_ok"] is False
    # No row written.
    assert kanban_mod.list_wake_subscriptions(backend=kanban_backend) == []


def test_subscribe_writes_session_marker_in_full_mode(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    result = kanban_mod.create_wake_subscription(
        task_id="t_abc", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    assert result["success"] is True
    assert result["dry_run"] is False
    assert result["target_kind"] == "session_key"
    assert result["user_id_marker"] == f"session:{DISCORD_THREAD_KEY}"
    assert result["after"]["user_id"] == f"session:{DISCORD_THREAD_KEY}"
    assert result["verification"]["internal_wake_enabled"] is True
    assert result["capability_mode"] == "full"


def test_subscribe_writes_session_id_marker_when_no_key(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    """When session_id does not resolve to a known key, uses session_id: marker.

    Per the skill, session_key is preferred when known; session_id: is the
    fallback when only an id is available (e.g. a session not in this
    profile's index yet).
    """
    result = kanban_mod.create_wake_subscription(
        task_id="t_sid", target_session_id="20260101_999999_unknown",
        platform="discord", chat_id="555666", backend=kanban_backend)
    assert result["success"] is True
    assert result["target_kind"] == "session_id"
    assert result["user_id_marker"] == "session_id:20260101_999999_unknown"


def test_subscribe_ambiguous_returns_matches_no_write(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    """Multiple matches → ambiguous error, no write."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_amb", platform="discord", backend=kanban_backend)
    assert result["success"] is False
    assert result["error"] == "ambiguous_session"
    assert len(result["matches"]) == 2
    assert kanban_mod.list_wake_subscriptions(backend=kanban_backend) == []


def test_subscribe_reset_cursor_only_when_true(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    """reset_cursor=true sets last_event_id=0 after subscribing."""
    # First subscribe without reset.
    r1 = kanban_mod.create_wake_subscription(
        task_id="t_rst", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    assert r1["success"] is True
    assert r1["reset_cursor_applied"] is False
    # Simulate a non-zero cursor by writing directly.
    conn = kanban_backend.connect()
    conn.execute("UPDATE kanban_notify_subs SET last_event_id=42 WHERE task_id='t_rst'")
    conn.commit()
    kanban_backend.close(conn)
    # Re-subscribe with reset_cursor=true.
    r2 = kanban_mod.create_wake_subscription(
        task_id="t_rst", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend, reset_cursor=True)
    assert r2["success"] is True
    assert r2["reset_cursor_applied"] is True
    assert r2["after"]["last_event_id"] == 0


def test_subscribe_does_not_downgrade_existing_session_marker(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    """A second subscribe must not downgrade an existing session: marker.

    Mirrors hermes_cli.kanban_db.add_notify_sub's non-downgrade guarantee.
    """
    kanban_mod.create_wake_subscription(
        task_id="t_keep", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    # Now re-subscribe the same identity with force_degraded_visible_only=True.
    r2 = kanban_mod.create_wake_subscription(
        task_id="t_keep", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend, force_degraded_visible_only=True)
    assert r2["success"] is True
    # The existing session: marker is preserved (not downgraded to visible-only).
    assert r2["after"]["user_id"] == f"session:{DISCORD_THREAD_KEY}"


def test_subscribe_force_degraded_visible_only_writes_visible_row(
        kanban_backend, inspect_only_capability, hermes_home):
    """Explicit visible-only opt-in writes a non-waking row even without capability."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_vis", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend, force_degraded_visible_only=True)
    assert result["success"] is True
    assert result["visible_only"] is True
    assert result["user_id_marker"] is None
    assert result["verification"]["internal_wake_enabled"] is False
    assert result["verification"]["warning"] is not None
    rows = kanban_mod.list_wake_subscriptions(backend=kanban_backend)
    assert rows[0]["marker_type"] == "visible_only"


def test_subscribe_chat_id_required_without_origin(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    """A bare unknown session_key with no origin/chat_id → chat_id_required."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_noid", target_session_key="agent:main:discord:dm:does-not-exist",
        backend=kanban_backend)
    assert result["success"] is False
    assert result["error"] == "chat_id_required"


def test_subscribe_before_after_reported(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    """First subscribe reports before=None, after populated."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_ba", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    assert result["before"] is None
    assert result["after"] is not None
    assert result["after"]["user_id"].startswith("session:")


def test_subscribe_json_serializable(kanban_backend, full_capability,
                                     state_db_with_receipts, hermes_home):
    """The result dict must be JSON-serializable (tool handlers dump it)."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_json", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend)
    json.dumps(result)  # must not raise


# --------------------------------------------------------------------------- #
# migrate_visible_rows
# --------------------------------------------------------------------------- #
def test_migrate_visible_rows_dry_run_reports(
        kanban_backend, full_capability, state_db_with_receipts, hermes_home):
    # Create a visible-only row.
    kanban_mod.create_wake_subscription(
        task_id="t_vis", target_session_key=DISCORD_THREAD_KEY,
        backend=kanban_backend, force_degraded_visible_only=True)
    report = kanban_mod.migrate_visible_rows(
        backend=kanban_backend, dry_run=True)
    assert report["success"] is True
    assert report["rows_found"] == 1
    assert report["upgraded"] == 0

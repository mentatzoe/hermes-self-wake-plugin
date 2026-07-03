"""Regression tests for the 2026-07 pre-launch adversarial review.

Each test encodes a reproduced finding:
- H1: silent platform default wrote subscriptions that could never fire
- M1: reset_cursor applied in a transaction that rolled back on close
- M2: doctor reported an unreachable board DB as ok
- M3: config coercion (int bools, negative ints)
- downgrade requests on existing wake rows reported contradictory output
"""
from __future__ import annotations

import sqlite3


from self_wake import config as config_mod
from self_wake import doctor as doctor_mod
from self_wake import kanban as kanban_mod


# --------------------------------------------------------------------------- #
# H1 — platform must never be silently defaulted
# --------------------------------------------------------------------------- #
def test_unknown_session_key_parses_platform_from_key(
        hermes_home, kanban_backend, full_capability):
    """A session key absent from the cache carries its platform in the key
    prefix; the subscription must use it, not a hardcoded default."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_h1a", board="default",
        target_session_key="agent:main:telegram:dm:999",
        chat_id="999", backend=kanban_backend, dry_run=False,
    )
    assert result["success"] is True
    assert result["platform"] == "telegram"
    assert result["resolved_from_cache"] is False
    assert "not found in the current-session cache" in (
        result["verification"]["warning"] or "")


def test_unresolvable_platform_fails_closed(
        hermes_home, kanban_backend, full_capability):
    """No cache entry, no explicit platform, unparseable key: refuse rather
    than write a row no notifier will ever collect."""
    result = kanban_mod.create_wake_subscription(
        task_id="t_h1b", board="default",
        target_session_id="20260703_000000_deadbeef",
        chat_id="123", backend=kanban_backend, dry_run=False,
    )
    assert result["success"] is False
    assert result["error"] == "platform_required"
    # Fail closed means no row was written.
    conn = kanban_backend.connect(board="default")
    try:
        rows = conn.execute(
            "SELECT * FROM kanban_notify_subs WHERE task_id='t_h1b'").fetchall()
    finally:
        kanban_backend.close(conn)
    assert rows == []


# --------------------------------------------------------------------------- #
# M1 — cursor reset must survive connection close
# --------------------------------------------------------------------------- #
def test_reset_cursor_persists_after_close(
        hermes_home, kanban_backend, full_capability):
    """The test backend is transactional (not autocommit) on purpose: a reset
    that only lives inside the open transaction must not report success."""
    kanban_mod.create_wake_subscription(
        task_id="t_m1", board="default",
        target_session_key="agent:main:discord:dm:1", platform="discord",
        chat_id="1", backend=kanban_backend, dry_run=False,
    )
    conn = kanban_backend.connect(board="default")
    conn.execute(
        "UPDATE kanban_notify_subs SET last_event_id=42 WHERE task_id='t_m1'")
    conn.commit()
    kanban_backend.close(conn)

    result = kanban_mod.create_wake_subscription(
        task_id="t_m1", board="default",
        target_session_key="agent:main:discord:dm:1", platform="discord",
        chat_id="1", backend=kanban_backend, dry_run=False, reset_cursor=True,
    )
    assert result["reset_cursor_applied"] is True

    fresh = kanban_backend.connect(board="default")
    try:
        fresh.row_factory = sqlite3.Row
        row = fresh.execute(
            "SELECT last_event_id FROM kanban_notify_subs "
            "WHERE task_id='t_m1'").fetchone()
    finally:
        kanban_backend.close(fresh)
    assert row["last_event_id"] == 0, (
        "reset_cursor reported success but the write was rolled back")


# --------------------------------------------------------------------------- #
# downgrade requests must not report contradictory success
# --------------------------------------------------------------------------- #
def test_downgrade_on_existing_wake_row_is_flagged(
        hermes_home, kanban_backend, full_capability):
    kanban_mod.create_wake_subscription(
        task_id="t_dg", board="default",
        target_session_key="agent:main:discord:dm:2", platform="discord",
        chat_id="2", backend=kanban_backend, dry_run=False,
    )
    result = kanban_mod.create_wake_subscription(
        task_id="t_dg", board="default",
        target_session_key="agent:main:discord:dm:2", platform="discord",
        chat_id="2", backend=kanban_backend, dry_run=False,
        force_degraded_visible_only=True,
    )
    assert result["success"] is True
    assert result["downgrade_ignored"] is True
    assert result["verification"]["internal_wake_enabled"] is True
    assert "KEEP FIRING" in result["verification"]["warning"]


# --------------------------------------------------------------------------- #
# M2 — doctor must not report an unreachable board DB as ok
# --------------------------------------------------------------------------- #
class _BrokenBackend:
    def connect(self, board=None):
        raise PermissionError("board DB unreadable")

    def close(self, conn):
        pass

    def add_notify_sub(self, *a, **k):
        raise PermissionError("board DB unreadable")


def test_doctor_reports_unreachable_kanban_db(hermes_home, full_capability):
    report = doctor_mod.run_diagnostics(backend=_BrokenBackend())
    kanban_checks = [c for c in report["checks"] if c["name"] == "kanban_db"]
    assert kanban_checks, "doctor must always emit a kanban_db check"
    assert kanban_checks[0]["status"] == "fail"
    assert "unreachable" in kanban_checks[0]["detail"]


# --------------------------------------------------------------------------- #
# M3 — config coercion
# --------------------------------------------------------------------------- #
def test_get_bool_coerces_yaml_int(monkeypatch):
    monkeypatch.setattr(config_mod, "_load_user_config",
                        lambda: {"compat_shim_enabled": 1})
    assert config_mod.get_bool("compat_shim_enabled", False) is True


def test_get_int_rejects_negative(monkeypatch):
    monkeypatch.setattr(config_mod, "_load_user_config",
                        lambda: {"receipt_preview_max_chars": -5})
    assert config_mod.get_int("receipt_preview_max_chars", 200) == 200


# --------------------------------------------------------------------------- #
# H3 — partial-native hosts keep their native methods
# --------------------------------------------------------------------------- #
def test_shim_never_clobbers_native_methods(hermes_home, monkeypatch):
    """A host with native wake_session but no receipt methods (an upstream
    intermediate) gets the shim for the gaps only; native methods survive."""
    import sqlite3 as _sq
    from tests import test_compat_shim as tcs
    from self_wake import compat_shim as shim

    async def _native_wake(self, **kwargs):  # the host's own implementation
        return {"status": "native"}

    class _PartialNativeRunner(tcs._FakeGatewayRunner):
        wake_session = _native_wake

    db = hermes_home / "state.db"
    conn = _sq.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    tcs._install_fake_modules(monkeypatch, gateway_runner=_PartialNativeRunner)
    monkeypatch.setattr(shim, "_shim_config_enabled", lambda: True)

    report = shim.install_shim()
    try:
        assert report["installed"] is True
        assert "_PartialNativeRunner.wake_session" in report["skipped_native_methods"]
        assert _PartialNativeRunner.wake_session is _native_wake, (
            "shim overwrote a native method")
        # The notifier pair is the point of the shim and is always replaced.
        assert tcs._FakeKanbanMixin._kanban_notifier_watcher is shim._shim_kanban_notifier_watcher
    finally:
        if shim.is_installed():
            shim.uninstall_shim()

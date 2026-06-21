"""Shared pytest fixtures for the self-wake plugin.

All tests run against a temp HERMES_HOME — never the real ~/.hermes. The
fixtures here provide:
  - a populated current-session cache at sessions/sessions.json
  - a state.db with the session_wake_receipts / sessions / messages tables
  - an in-memory SQLite KanbanBackend double mirroring hermes_cli.kanban_db
  - monkeypatchable capability-probe helpers for full / inspect-only / unsupported
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Kanban notify_subs schema (mirrors hermes_cli.kanban_db)
# --------------------------------------------------------------------------- #
KANBAN_NOTIFY_SUBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS kanban_notify_subs (
    task_id       TEXT NOT NULL,
    platform      TEXT NOT NULL,
    chat_id       TEXT NOT NULL,
    thread_id     TEXT NOT NULL DEFAULT '',
    user_id       TEXT,
    notifier_profile TEXT,
    created_at    INTEGER NOT NULL,
    last_event_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, platform, chat_id, thread_id)
);
"""

RECEIPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_wake_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    requested_at REAL NOT NULL,
    dispatched_at REAL,
    responded_at REAL,
    source_kind TEXT NOT NULL,
    target_session_key TEXT NOT NULL,
    target_session_id TEXT NOT NULL,
    origin_snapshot TEXT,
    payload_hash TEXT NOT NULL,
    payload_preview TEXT,
    payload_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    injected_message_id INTEGER,
    assistant_message_id INTEGER,
    error TEXT,
    dedupe_key TEXT
);
"""


class SQLiteKanbanBackend:
    """Test KanbanBackend double operating on a temp sqlite file.

    Faithfully mirrors hermes_cli.kanban_db.add_notify_sub semantics:
    INSERT OR IGNORE on identity, then conditionally upgrade user_id (never
    downgrade an existing session:/session_id: marker) and backfill
    notifier_profile only when unset.
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        conn = sqlite3.connect(self._path)
        try:
            conn.executescript(KANBAN_NOTIFY_SUBS_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def connect(self, board: Optional[str] = None) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_notify_sub(self, conn: sqlite3.Connection, *, task_id: str,
                       platform: str, chat_id: str, thread_id: Optional[str] = None,
                       user_id: Optional[str] = None,
                       notifier_profile: Optional[str] = None) -> None:
        now = int(time.time())
        conn.execute(
            "INSERT OR IGNORE INTO kanban_notify_subs "
            "(task_id, platform, chat_id, thread_id, user_id, notifier_profile, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, platform, chat_id, thread_id or "", user_id, notifier_profile, now),
        )
        if user_id:
            conn.execute(
                "UPDATE kanban_notify_subs SET user_id=? "
                "WHERE task_id=? AND platform=? AND chat_id=? AND thread_id=? "
                "AND (user_id IS NULL OR user_id='' OR ? LIKE 'session:%' OR ? LIKE 'session_id:%')",
                (user_id, task_id, platform, chat_id, thread_id or "", user_id, user_id),
            )
        if notifier_profile:
            conn.execute(
                "UPDATE kanban_notify_subs SET notifier_profile=? "
                "WHERE task_id=? AND platform=? AND chat_id=? AND thread_id=? "
                "AND (notifier_profile IS NULL OR notifier_profile='')",
                (notifier_profile, task_id, platform, chat_id, thread_id or ""),
            )
        conn.commit()

    def close(self, conn: sqlite3.Connection) -> None:
        conn.close()


# --------------------------------------------------------------------------- #
# Core fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    """A temp HERMES_HOME with a populated gateway current-session cache."""
    home = tmp_path / "hermes"
    (home / "sessions").mkdir(parents=True)
    sessions_data = json.loads((FIXTURES / "sessions.json").read_text(encoding="utf-8"))
    (home / "sessions" / "sessions.json").write_text(
        json.dumps(sessions_data, indent=2), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Reset cached kanban backend so each test re-resolves.
    from self_wake import kanban as kanban_mod
    kanban_mod.set_default_backend(None)
    yield home
    kanban_mod.set_default_backend(None)


@pytest.fixture()
def state_db_with_receipts(hermes_home):
    """Create state.db with sessions/messages/session_wake_receipts tables + sample receipts."""
    db = hermes_home / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                message_count INTEGER DEFAULT 0,
                started_at REAL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                content TEXT,
                timestamp REAL
            );
        """)
        conn.executescript(RECEIPT_SCHEMA)
        # Populate session titles for the three fixture sessions.
        for sid, title in [
            ("20260101_120000_aaaaaa", "LTM repair work"),
            ("20260101_120000_bbbbbb", "ops channel session"),
            ("20260101_120000_cccccc", "Zoe DM session"),
        ]:
            conn.execute(
                "INSERT INTO sessions (id, title, message_count, started_at) VALUES (?,?,?,?)",
                (sid, title, 5, 1704110400),
            )
        # Two sample receipts.
        now = time.time()
        conn.execute(
            "INSERT INTO session_wake_receipts "
            "(created_at, updated_at, requested_at, source_kind, target_session_key, "
            " target_session_id, payload_hash, payload_preview, payload_bytes, status, dedupe_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, now, "kanban",
             "agent:main:discord:thread:1511162896762142980:1511162896762142980",
             "20260101_120000_aaaaaa", "deadbeef",
             "Kanban task t_abc completed", 26, "agent_responded", "kanban:t_abc"),
        )
        conn.execute(
            "INSERT INTO session_wake_receipts "
            "(created_at, updated_at, requested_at, source_kind, target_session_key, "
            " target_session_id, payload_hash, payload_preview, payload_bytes, status, error, dedupe_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, now, "cron_delivery",
             "agent:main:telegram:dm:777888", "20260101_120000_cccccc", "cafef00d",
             "x" * 300, 300, "failure", "adapter unavailable", None),
        )
        conn.commit()
    finally:
        conn.close()
    return db


@pytest.fixture()
def kanban_backend(tmp_path):
    """A SQLiteKanbanBackend double on a temp file."""
    return SQLiteKanbanBackend(tmp_path / "kanban.db")


@pytest.fixture()
def sqlite_state_db_without_receipts(hermes_home):
    """state.db with sessions/messages tables but NO session_wake_receipts table.

    Simulates vanilla Hermes (core patch not applied): the receipt query must
    fail closed with capability_missing rather than raising.
    """
    db = hermes_home / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, content TEXT, timestamp REAL);
        """)
        conn.commit()
    finally:
        conn.close()
    return db


# --------------------------------------------------------------------------- #
# Capability probe overrides
# --------------------------------------------------------------------------- #
def _set_capability(monkeypatch, mode: str, hermes_home):
    """Monkeypatch the structural probe helpers to force a capability mode."""
    from self_wake import capabilities as caps

    if mode == "full":
        monkeypatch.setattr(caps, "_probe_gateway_wake_session",
                            lambda: {"probe": "gateway.wake_session", "available": True})
        monkeypatch.setattr(caps, "_probe_wake_session_return_shape",
                            lambda: {"probe": "wake_session.return_shape", "available": True})
        monkeypatch.setattr(caps, "_probe_session_db_receipt_methods",
                            lambda: {"probe": "session_db.receipt_methods", "available": True})
        monkeypatch.setattr(caps, "_probe_session_store_lookup",
                            lambda: {"probe": "session_store.lookup", "available": True})
        monkeypatch.setattr(caps, "_probe_receipt_table",
                            lambda hh=None: {"probe": "receipt_table", "available": True})
        monkeypatch.setattr(caps, "_probe_session_resolver_readable",
                            lambda hh=None: {"probe": "session_resolver", "available": True})
        monkeypatch.setattr(caps, "_probe_notifier_routing",
                            lambda: {"probe": "notifier_routing", "available": True})
    elif mode == "inspect_only":
        # Wake primitive absent, but session resolver + state.db readable.
        monkeypatch.setattr(caps, "_probe_gateway_wake_session",
                            lambda: {"probe": "gateway.wake_session", "available": False,
                                     "reason": "wake_session not present on GatewayRunner"})
        monkeypatch.setattr(caps, "_probe_wake_session_return_shape",
                            lambda: {"probe": "wake_session.return_shape", "available": False,
                                     "reason": "wake_session not present on GatewayRunner"})
        monkeypatch.setattr(caps, "_probe_session_db_receipt_methods",
                            lambda: {"probe": "session_db.receipt_methods", "available": False,
                                     "reason": "missing methods: ['create_session_wake_receipt', 'update_session_wake_receipt']"})
        monkeypatch.setattr(caps, "_probe_session_store_lookup",
                            lambda: {"probe": "session_store.lookup", "available": False,
                                     "reason": "missing methods: ['lookup_by_session_key', 'lookup_by_session_id']"})
        # receipt_table probe left real: true when state_db_with_receipts present,
        # false otherwise.
        monkeypatch.setattr(caps, "_probe_session_resolver_readable",
                            lambda hh=None: {"probe": "session_resolver", "available": True})
        monkeypatch.setattr(caps, "_probe_notifier_routing",
                            lambda: {"probe": "notifier_routing", "available": False,
                                     "reason": "_kanban_internal_wake_target not present on mixin"})
    elif mode == "unsupported":
        monkeypatch.setattr(caps, "_probe_gateway_wake_session",
                            lambda: {"probe": "gateway.wake_session", "available": False,
                                     "reason": "gateway.run.GatewayRunner not importable"})
        monkeypatch.setattr(caps, "_probe_wake_session_return_shape",
                            lambda: {"probe": "wake_session.return_shape", "available": False,
                                     "reason": "gateway.run.GatewayRunner not importable"})
        monkeypatch.setattr(caps, "_probe_session_db_receipt_methods",
                            lambda: {"probe": "session_db.receipt_methods", "available": False,
                                     "reason": "hermes_state.SessionDB not importable"})
        monkeypatch.setattr(caps, "_probe_session_store_lookup",
                            lambda: {"probe": "session_store.lookup", "available": False,
                                     "reason": "gateway.session.SessionStore not importable"})
        monkeypatch.setattr(caps, "_probe_receipt_table",
                            lambda hh=None: {"probe": "receipt_table", "available": False,
                                             "reason": "state.db not found"})
        monkeypatch.setattr(caps, "_probe_session_resolver_readable",
                            lambda hh=None: {"probe": "session_resolver", "available": False,
                                             "reason": "gateway current-session cache not found"})
        monkeypatch.setattr(caps, "_probe_notifier_routing",
                            lambda: {"probe": "notifier_routing", "available": False,
                                     "reason": "gateway.kanban_watchers.GatewayKanbanWatchersMixin not importable"})
    return mode


@pytest.fixture()
def full_capability(monkeypatch, hermes_home):
    return _set_capability(monkeypatch, "full", hermes_home)


@pytest.fixture()
def inspect_only_capability(monkeypatch, hermes_home):
    return _set_capability(monkeypatch, "inspect_only", hermes_home)


@pytest.fixture()
def unsupported_capability(monkeypatch, hermes_home):
    return _set_capability(monkeypatch, "unsupported", hermes_home)

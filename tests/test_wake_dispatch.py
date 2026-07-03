"""End-to-end tests for the shim wake dispatch path.

These close the review's remaining test debt: the receipt state machine
(dispatched / agent_responded / dispatched_unconfirmed / failure / deduped)
is exercised through the real ``_shim_wake_session`` coroutine against the
fake vanilla host, not just reviewed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum
import sqlite3
import time
from pathlib import Path

import pytest

from self_wake import compat_shim as shim
from tests import test_compat_shim as tcs


class _FakePlatform(enum.Enum):
    DISCORD = "discord"


@dataclasses.dataclass
class _FakeOrigin:
    platform: _FakePlatform = _FakePlatform.DISCORD
    chat_id: str = "123"
    thread_id: str = ""

    def to_dict(self):
        return {"platform": self.platform.value, "chat_id": self.chat_id,
                "thread_id": self.thread_id}


class _FakeEntry:
    def __init__(self, session_key: str, session_id: str):
        self.session_key = session_key
        self.session_id = session_id
        self.origin = _FakeOrigin()


class _FakeAdapter:
    def __init__(self, fail_dispatch: bool = False):
        self.fail_dispatch = fail_dispatch
        self.handled: list = []
        self._active_sessions: dict = {}
        self._session_tasks: dict = {}
        self.config = None

    async def handle_message(self, event):
        if self.fail_dispatch:
            raise RuntimeError("adapter exploded before injection")
        self.handled.append(event)


@pytest.fixture()
def wake_world(hermes_home, monkeypatch):
    """Shim installed on the fake vanilla host + a runner wired with one
    session entry and one adapter, backed by the real temp state.db."""
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    tcs._install_fake_modules(monkeypatch)
    monkeypatch.setattr(shim, "_shim_config_enabled", lambda: True)
    report = shim.install_shim()
    assert report["installed"] is True

    runner = tcs._FakeGatewayRunner()
    store = tcs._FakeSessionStore()
    entry = _FakeEntry("agent:main:discord:dm:123", "20260703_120000_cafe01")
    store._entries[entry.session_key] = entry
    runner.session_store = store
    runner._session_db = tcs._FakeSessionDB(db)
    adapter = _FakeAdapter()
    runner.adapters = {_FakePlatform.DISCORD: adapter}

    yield runner, adapter, entry, db
    if shim.is_installed():
        shim.uninstall_shim()


def _wake(runner, entry, dedupe_key="k1", payload="wake up"):
    return asyncio.run(shim._shim_wake_session(
        runner, payload=payload, source_kind="kanban",
        session_key=entry.session_key, dedupe_key=dedupe_key))


def _receipt_status(db: Path, receipt_id: int) -> str:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT status FROM session_wake_receipts WHERE id=?",
            (receipt_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def test_wake_dispatches_and_receipts(wake_world):
    runner, adapter, entry, db = wake_world
    result = _wake(runner, entry)
    assert result["status"] == "dispatched"
    assert len(adapter.handled) == 1
    event = adapter.handled[0]
    assert event.internal is True
    assert event.text == "wake up"
    assert _receipt_status(db, result["receipt_id"]) == "dispatched"


def test_post_dispatch_failure_is_unconfirmed_and_not_retried(wake_world):
    """The payload was injected; bookkeeping failed afterwards. The receipt
    must say dispatched_unconfirmed and the wake must NOT re-inject."""
    runner, adapter, entry, db = wake_world
    runner._wake_message_ids_after = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bookkeeping boom")))

    result = _wake(runner, entry, dedupe_key="pd1")
    assert result["status"] == "dispatched_unconfirmed"
    assert len(adapter.handled) == 1
    assert _receipt_status(db, result["receipt_id"]) == "dispatched_unconfirmed"

    retry = _wake(runner, entry, dedupe_key="pd1")
    assert retry["status"] == "deduped"
    assert len(adapter.handled) == 1, "an already-injected wake was re-injected"


def test_pre_dispatch_failure_is_retryable(wake_world):
    runner, adapter, entry, db = wake_world
    adapter.fail_dispatch = True
    first = _wake(runner, entry, dedupe_key="f1")
    assert first["status"] == "failure"
    assert adapter.handled == []
    assert _receipt_status(db, first["receipt_id"]) == "failure"

    adapter.fail_dispatch = False
    second = _wake(runner, entry, dedupe_key="f1")
    assert second["status"] == "dispatched"
    assert len(adapter.handled) == 1


def test_inflight_requested_receipt_is_not_double_dispatched(wake_world):
    runner, adapter, entry, db = wake_world
    first = _wake(runner, entry, dedupe_key="inflight")
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE session_wake_receipts SET status='requested', "
                 "updated_at=? WHERE id=?", (time.time(), first["receipt_id"]))
    conn.commit()
    conn.close()

    result = _wake(runner, entry, dedupe_key="inflight")
    assert result["status"] == "deduped"
    assert len(adapter.handled) == 1


def test_stale_requested_receipt_is_retried(wake_world):
    runner, adapter, entry, db = wake_world
    first = _wake(runner, entry, dedupe_key="stale")
    stale_ts = time.time() - (shim._INFLIGHT_RETRY_SECONDS + 60)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE session_wake_receipts SET status='requested', "
                 "updated_at=?, requested_at=? WHERE id=?",
                 (stale_ts, stale_ts, first["receipt_id"]))
    conn.commit()
    conn.close()

    result = _wake(runner, entry, dedupe_key="stale")
    assert result["status"] == "dispatched"
    assert len(adapter.handled) == 2, "a crashed pre-dispatch attempt blocked retry"


def test_queued_when_target_session_active(wake_world):
    runner, adapter, entry, db = wake_world
    adapter._active_sessions = {entry.session_key: object()}
    result = _wake(runner, entry, dedupe_key="q1")
    assert result["status"] == "queued"
    assert len(adapter.handled) == 1
    assert _receipt_status(db, result["receipt_id"]) == "queued"


def test_agent_responded_when_assistant_message_appears(wake_world):
    runner, adapter, entry, db = wake_world
    payload = "wake up"
    calls = {"n": 0}

    def _messages(session_id):
        # Empty on the pre-dispatch snapshot; injected + response afterwards.
        calls["n"] += 1
        if calls["n"] == 1:
            return []
        return [
            {"id": 1, "role": "user", "content": payload},
            {"id": 2, "role": "assistant", "content": "on it"},
        ]

    runner._session_db.get_messages = _messages
    result = _wake(runner, entry, dedupe_key="ar1", payload=payload)
    assert result["status"] == "agent_responded"
    assert result["assistant_message_id"] == 2
    assert _receipt_status(db, result["receipt_id"]) == "agent_responded"

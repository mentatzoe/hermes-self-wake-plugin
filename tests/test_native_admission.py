"""Native admission and durable receipt ownership, with barrier-controlled races."""
import asyncio
import dataclasses
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from self_wake import compat_shim as shim
from self_wake import native_wake
from tests.test_wake_dispatch import wake_world  # noqa: F401
from tests.test_wake_host_composition import AsyncProxy


@pytest.fixture
def native(wake_world, monkeypatch):
    runner, adapter, entry, db = wake_world
    runner._session_db._conn.close()
    runner._session_db._conn = sqlite3.connect(db, check_same_thread=False)
    runner._session_db._conn.row_factory = sqlite3.Row
    runner._session_db._lock = threading.RLock()
    runner._session_db = AsyncProxy(runner._session_db)
    runner._authorization_adapter = lambda platform, profile=None: adapter
    runner._session_key_for_source = lambda source: entry.session_key if source.chat_id == "123" else "wrong"
    runner._draining = False
    messages = []
    runner._session_db._db.get_messages = lambda sid: list(messages)
    adapter._message_handler = object()

    def start(event, key):
        async def consume():
            adapter.handled.append(event)
            messages.append({"id": len(messages) + 1, "role": "user", "content": event.text,
                             "platform_message_id": event.message_id})
        task = asyncio.create_task(consume())
        adapter._session_tasks[key] = task
        return True

    adapter._start_session_processing = start
    # The production admission adapter is exact-host gated; this explicitly
    # admits only the local fixture, never changes a real host allowlist.
    monkeypatch.setattr(native_wake, "check_host", lambda runner, adapter: None, raising=False)
    return runner, adapter, entry, db, messages


async def attempt(world, key="native"):
    runner, _, entry, _, _ = world
    return await shim._shim_wake_session(runner, payload="terminal event", source_kind="kanban",
                                         session_key=entry.session_key, dedupe_key=key, defer_if_busy=True)


def test_native_rejects_unrelated_transcript_rows(native):
    runner, adapter, entry, db, messages = native

    async def unrelated(event):
        messages.extend([{"id": 1, "role": "user", "content": "different turn"},
                         {"id": 2, "role": "assistant", "content": "different response"}])
    adapter.handle_message = unrelated
    adapter._start_session_processing = lambda event, key: False
    result = asyncio.run(attempt(native))
    assert result["status"] in {"failure", "pending"}
    assert not result.get("injected_message_id")
    assert not result.get("assistant_message_id")


@pytest.mark.parametrize("race", ["busy", "draining", "reset", "route"])
def test_native_rechecks_after_async_receipt_write(native, race):
    runner, adapter, entry, db, messages = native
    original = runner._session_db._db._execute_write

    def write(fn):
        result = original(fn)
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT status FROM session_wake_receipts").fetchone()
        if row and row[0] in {"dispatched", "dispatching"}:
            if race == "busy":
                adapter._active_sessions[entry.session_key] = object()
            elif race == "draining":
                runner._draining = True
            elif race == "reset":
                entry.session_id = "reset-successor"
            else:
                entry.origin.chat_id = "different"
        return result

    runner._session_db._db._execute_write = write
    result = asyncio.run(attempt(native))
    assert result["status"] == "failure"
    assert not adapter.handled
    assert not messages


def test_native_receipt_requires_exact_platform_message_id(native):
    result = asyncio.run(attempt(native))
    assert result["status"] == "dispatched"
    assert result["injected_message_id"] == 1
    assert not result.get("assistant_message_id")
    assert asyncio.run(attempt(native))["status"] == "deduped"
    assert len(native[1].handled) == 1


def test_native_origin_metadata_is_pinned(native):
    result = asyncio.run(attempt(native))
    assert result["status"] == "dispatched"
    event = native[1].handled[0]
    assert event.metadata == {"gateway_session_key": native[2].session_key,
                              "gateway_session_id": native[2].session_id,
                              "gateway_session_strict": True}
    assert not event.allow_gateway_control


@pytest.mark.parametrize("prior", ["failure", "requested"])
def test_concurrent_retry_has_one_owner(native, prior):
    runner, adapter, entry, db, messages = native

    async def run():
        first = await attempt(native)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE session_wake_receipts SET status=?,updated_at=0,requested_at=0", (prior,))
        adapter.handled.clear()
        messages.clear()
        results = await asyncio.gather(attempt(native), attempt(native))
        assert len(adapter.handled) == 1
        assert sum(r["status"] == "dispatched" for r in results) == 1
        assert {r["status"] for r in results} <= {"dispatched", "pending", "deduped"}
        assert all(r["receipt_id"] == first["receipt_id"] for r in results)
    asyncio.run(run())


def test_fresh_request_is_pending_not_completed_dedupe(native):
    runner, adapter, entry, db, messages = native

    async def run():
        first = await attempt(native)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE session_wake_receipts SET status='requested' WHERE id=?", (first["receipt_id"],))
        result = await attempt(native)
        assert result["status"] == "pending"
    asyncio.run(run())


def test_host_drift_prevents_any_admission(native, monkeypatch):
    def drift(*args):
        raise RuntimeError("native admission host drift")
    monkeypatch.setattr(native_wake, "check_host", drift, raising=False)
    result = asyncio.run(attempt(native))
    assert result["status"] == "failure"
    assert "host drift" in result["error"]
    assert not native[1].handled


def test_dedupe_key_payload_conflict_is_not_success(native):
    async def run():
        await attempt(native)
        runner, _, entry, _, _ = native
        result = await shim._shim_wake_session(runner, payload="different payload", source_kind="kanban",
                                               session_key=entry.session_key, dedupe_key="native", defer_if_busy=True)
        assert result["status"] == "failure"
        assert "conflict" in result["error"]
    asyncio.run(run())


def test_admitted_turn_with_no_exact_row_stays_pending_on_replay(native):
    runner, adapter, entry, db, messages = native
    def start(event, key):
        async def reject():
            adapter.handled.append(event)
            messages.extend([{"id": 1, "role": "user", "content": event.text},
                             {"id": 2, "role": "assistant", "content": "unrelated"}])
        adapter._session_tasks[key] = asyncio.create_task(reject())
        return True
    adapter._start_session_processing = start
    assert asyncio.run(attempt(native))["status"] == "pending"
    assert asyncio.run(attempt(native))["status"] == "pending"
    assert len(adapter.handled) == 1


def test_busy_fifo_is_never_entered_and_idle_retries(native):
    runner, adapter, entry, db, messages = native
    async def wrong_path(event):
        pytest.fail("native wake entered lossy busy queue")
    adapter.handle_message = wrong_path
    adapter._active_sessions[entry.session_key] = object()
    assert asyncio.run(attempt(native))["status"] == "failure"
    adapter._active_sessions.clear()
    assert asyncio.run(attempt(native))["status"] == "dispatched"
    assert len(adapter.handled) == 1


def test_subscription_route_mismatch_cannot_reuse_same_key(native):
    async def run():
        runner, _, entry, _, _ = native
        expected = dataclasses.replace(entry.origin)
        expected.chat_id = "different"
        result = await native_wake.wake(runner, payload="terminal event", source_kind="kanban",
                                        session_key=entry.session_key, dedupe_key="route",
                                        expected_source=expected)
        assert result["status"] == "failure"
        assert not native[1].handled
    asyncio.run(run())


def test_exception_after_start_is_uncertain_not_retryable(native):
    runner, adapter, entry, db, messages = native
    start = adapter._start_session_processing
    def uncertain(event, key):
        start(event, key)
        raise RuntimeError("error after a task was created")
    adapter._start_session_processing = uncertain
    result = asyncio.run(attempt(native))
    assert result["status"] == "pending"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM session_wake_receipts").fetchone()[0] != "failure"


def test_cancelled_caller_does_not_cancel_admitted_turn(native):
    runner, adapter, entry, db, messages = native
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        def start(event, key):
            async def consume():
                entered.set()
                await release.wait()
                adapter.handled.append(event)
                messages.append({"id": 1, "role": "user", "platform_message_id": event.message_id})
            adapter._session_tasks[key] = asyncio.create_task(consume())
            return True
        adapter._start_session_processing = start
        caller = asyncio.create_task(attempt(native))
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not adapter._session_tasks[entry.session_key].cancelled()
        release.set()
        await asyncio.gather(*runner._self_wake_settlements)
        assert (await attempt(native))["status"] == "deduped"
        assert len(adapter.handled) == 1
    asyncio.run(run())


def test_transcript_evidence_comes_from_target_profile_store(native):
    runner, adapter, entry, db, messages = native
    target = SimpleNamespace(get_messages=lambda sid: list(messages))
    selected = []
    runner.session_store._db_for_key = lambda key: selected.append(key) or target
    runner._session_db._db.get_messages = lambda sid: []
    result = asyncio.run(attempt(native))
    assert result["status"] == "dispatched"
    assert selected and set(selected) == {entry.session_key}


@pytest.mark.parametrize("requested,accepted", [("beta", True), ("other", False), ("default", False)])
def test_unstamped_source_uses_active_profile_not_literal_default(native, requested, accepted):
    runner, adapter, entry, db, messages = native
    @dataclasses.dataclass
    class ProfileOrigin(type(entry.origin)):
        profile: str | None = None

        def to_dict(self):
            return dict(super().to_dict(), profile=self.profile)

    entry.origin = ProfileOrigin()
    runner._active_profile_name = lambda: "beta"
    runner.config = SimpleNamespace(multiplex_profiles=False)
    expected = dataclasses.replace(entry.origin, profile=requested)
    result = asyncio.run(native_wake.wake(runner, payload="event", source_kind="kanban",
        session_key=entry.session_key, dedupe_key="named", expected_source=expected))
    assert (result["status"] == "dispatched") is accepted
    assert bool(adapter.handled) is accepted

"""Regressions from the independent modernization review."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from self_wake import native_wake, outbox
from tests.test_native_admission import native, attempt  # noqa: F401
from tests.test_wake_dispatch import wake_world  # noqa: F401


def test_cancel_after_dispatch_write_keeps_admission_owner(native, monkeypatch):
    runner, adapter, _, _, _ = native
    original = native_wake.transition
    async def exercise():
        written, release = asyncio.Event(), asyncio.Event()
        async def transition(*args, **kwargs):
            result = await original(*args, **kwargs)
            if args[3] == "dispatching":
                written.set()
                await release.wait()
            return result
        monkeypatch.setattr(native_wake, "transition", transition)
        caller = asyncio.create_task(attempt(native))
        await written.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await asyncio.gather(*runner._self_wake_admissions)
        await asyncio.gather(*runner._self_wake_settlements)
        assert len(adapter.handled) == 1
        assert (await attempt(native))["status"] == "deduped"
    asyncio.run(exercise())


def test_nonblocking_admission_leaves_owned_turn_running(native):
    runner, adapter, entry, _, messages = native
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        def start(event, key):
            async def consume():
                entered.set()
                await release.wait()
                messages.append({"id": 1, "role": "user", "platform_message_id": event.message_id})
            adapter._session_tasks[key] = asyncio.create_task(consume())
            return True
        adapter._start_session_processing = start
        result = await native_wake.wake(runner, payload="event", source_kind="kanban", session_key=entry.session_key,
                                        dedupe_key="one", wait_for_completion=False)
        assert result["status"] == "pending"
        await entered.wait()
        assert not adapter._session_tasks[entry.session_key].done()
        release.set()
        await asyncio.gather(*runner._self_wake_settlements)
    asyncio.run(exercise())


def test_recovery_persists_exact_injection_before_dedupe(native):
    runner, _, _, db, _ = native
    first = asyncio.run(attempt(native))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE session_wake_receipts SET status='pending',injected_message_id=NULL")
    assert asyncio.run(attempt(native))["status"] == "deduped"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status,injected_message_id FROM session_wake_receipts").fetchone() == ("dispatched", first["injected_message_id"])


def test_unrelated_mirror_is_not_an_agent_response():
    db = SimpleNamespace(get_messages=lambda sid: [
        {"id": 1, "role": "user"},
        {"id": 2, "role": "assistant", "content": "unrelated mirror"},
    ])
    assert asyncio.run(native_wake.exact_response(db, "sid", 1)) is None
    assert asyncio.run(native_wake.exact_response(db, "sid", 1, "actual reply")) is None


def test_response_observer_scopes_and_restores_handler():
    event, other = SimpleNamespace(), SimpleNamespace()
    async def handler(incoming):
        return "owned reply" if incoming is event else "ordinary reply"
    adapter = SimpleNamespace(_message_handler=handler)
    release = native_wake.observe_response(adapter, event)
    assert asyncio.run(adapter._message_handler(other)) == "ordinary reply"
    assert not hasattr(other, "_self_wake_final_response")
    assert asyncio.run(adapter._message_handler(event)) == "owned reply"
    assert event._self_wake_final_response == "owned reply"
    release()
    assert adapter._message_handler is handler


def test_multiplex_inbox_fails_closed_and_reports_reason(monkeypatch, tmp_path):
    from self_wake import cron_adapter
    runner = SimpleNamespace(config=SimpleNamespace(multiplex_profiles=True),
                             _self_wake_inbox_task=SimpleNamespace(done=lambda: False))
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    monkeypatch.setattr(outbox, "request_allowed", lambda kind: True)
    status = outbox.diagnostics(tmp_path)
    assert not status["available"] and "multiplex" in status["reason"]


def test_retry_scan_rotates_past_full_batch(monkeypatch):
    rows = [{"id": str(n)} for n in range(51)]
    runner = SimpleNamespace(_running=True)
    monkeypatch.setattr(outbox, "pending_requests", lambda: rows)
    async def exercise():
        reached = asyncio.Event()
        async def drain(runner, row):
            if row["id"] == "50":
                reached.set()
        monkeypatch.setattr(outbox, "drain_request", drain)
        task = asyncio.create_task(outbox.watch(runner, interval=0.001))
        await asyncio.wait_for(reached.wait(), 1)
        runner._running = False
        await task
    asyncio.run(exercise())

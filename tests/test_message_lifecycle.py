"""Permission, receiver lifecycle and CLI regressions for session messages."""
import argparse
import asyncio
from types import SimpleNamespace

import pytest

from self_wake import outbox, send_cli


def test_no_grant_cannot_enqueue(monkeypatch, tmp_path):
    monkeypatch.setattr(outbox, "request_allowed", lambda kind: False)
    with pytest.raises(ValueError, match="allow_gateway_injection"):
        outbox.send(session_id="target", payload="text", dedupe_key="key", hermes_home=tmp_path)
    assert not (tmp_path / "self-wake").exists()


def test_revocation_prevents_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(outbox, "request_allowed", lambda kind: False)
    runner = SimpleNamespace(session_store=None)
    asyncio.run(outbox.drain_request(runner, {"source_kind": "session_message"}, hermes_home=tmp_path))
    assert not (tmp_path / "self-wake").exists()


def test_cli_registration_and_errors(monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    send_cli.setup(parser)
    monkeypatch.setattr(outbox, "get", lambda request_id: None)
    assert send_cli.run(parser.parse_args(["status", "missing"])) == 1
    assert "request not found" in capsys.readouterr().out


def test_slow_receiver_does_not_block_new_work(monkeypatch):
    runner = SimpleNamespace(_running=True)
    rows = [{"id": "slow"}]
    monkeypatch.setattr(outbox, "pending_requests", lambda: list(rows))

    async def exercise():
        entered, delivered, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def drain(runner, row):
            if row["id"] == "slow":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            else:
                delivered.set()
                rows.remove(row)
        monkeypatch.setattr(outbox, "drain_request", drain)
        task = asyncio.create_task(outbox.watch(runner, interval=0.001))
        await asyncio.wait_for(entered.wait(), 1)
        rows.append({"id": "fast"})
        await asyncio.wait_for(delivered.wait(), 1)
        runner._running = False
        await asyncio.wait_for(task, 1)
        assert cancelled.is_set()
    asyncio.run(exercise())


def test_inbox_runs_without_kanban_and_stops_with_parent(monkeypatch):
    from self_wake import compat_shim
    runner = SimpleNamespace(_running=True)
    async def exercise():
        started, stopped = asyncio.Event(), asyncio.Event()
        async def no_kanban(runner, interval):
            return
        async def inbox(runner, interval):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        monkeypatch.setattr(compat_shim, "_shim_kanban_events_watcher", no_kanban)
        monkeypatch.setattr(outbox, "watch", inbox)
        task = asyncio.create_task(compat_shim._shim_kanban_notifier_watcher(runner))
        await started.wait()
        assert not task.done() and runner._self_wake_inbox_task is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set() and runner._self_wake_inbox_task is None
    asyncio.run(exercise())

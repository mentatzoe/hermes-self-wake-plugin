"""Durable cross-process requests retry busy receivers without changing targets."""
import asyncio


import pytest

from self_wake import outbox
from tests.test_native_admission import native  # noqa: F401
from tests.test_wake_dispatch import wake_world  # noqa: F401


@pytest.fixture(autouse=True)
def permission(monkeypatch):
    monkeypatch.setattr(outbox, "request_allowed", lambda kind: True)


@pytest.fixture(autouse=True)
def authorized_native(request):
    if "native" in request.fixturenames:
        request.getfixturevalue("native")[0]._is_user_authorized = lambda source, **kwargs: True


def test_submit_requires_existing_exact_session(hermes_home):
    with pytest.raises(ValueError, match="existing"):
        outbox.send(session_id="missing", payload="hello", dedupe_key="one", hermes_home=hermes_home)


def test_send_duplicate_and_conflict(hermes_home):
    kwargs = dict(session_id="20260101_120000_cccccc", payload="hello", dedupe_key="one", hermes_home=hermes_home)
    first = outbox.send(**kwargs)
    assert first["status"] == "queued"
    assert outbox.send(**kwargs)["request_id"] == first["request_id"]
    with pytest.raises(ValueError, match="conflict"):
        outbox.send(**dict(kwargs, payload="different"))


def test_busy_survives_reopen_then_delivers_once(native, tmp_path):
    runner, adapter, entry, db, messages = native
    home = tmp_path / "home"
    request = outbox.enqueue(entry=entry, payload="hello", source_kind="session_message", dedupe_key="one", hermes_home=home)
    adapter._active_sessions[entry.session_key] = object()
    asyncio.run(outbox.drain_once(runner, hermes_home=home))
    assert not adapter.handled
    assert outbox.get(request["request_id"], hermes_home=home)["status"] == "queued"
    adapter._active_sessions.clear()
    asyncio.run(outbox.drain_once(runner, hermes_home=home))
    assert outbox.get(request["request_id"], hermes_home=home)["status"] == "dispatched"
    asyncio.run(outbox.drain_once(runner, hermes_home=home))
    assert len(adapter.handled) == 1


@pytest.mark.parametrize("change", ["session", "route"])
def test_pending_message_never_follows_reset_or_route_change(native, tmp_path, change):
    runner, adapter, entry, db, messages = native
    request = outbox.enqueue(entry=entry, payload="hello", source_kind="session_message", dedupe_key="one", hermes_home=tmp_path)
    if change == "session":
        entry.session_id = "successor"
    else:
        entry.origin.chat_id = "different"
    asyncio.run(outbox.drain_once(runner, hermes_home=tmp_path))
    assert not adapter.handled
    assert outbox.get(request["request_id"], hermes_home=tmp_path)["status"] == "target_changed"


def test_profiles_have_separate_queues(native, tmp_path):
    entry = native[2]
    request = outbox.enqueue(entry=entry, payload="hello", source_kind="session_message", dedupe_key="one", hermes_home=tmp_path / "a")
    assert outbox.get(request["request_id"], hermes_home=tmp_path / "b") is None


def test_transient_failure_retries_and_uncertain_does_not_reinject(native, tmp_path, monkeypatch):
    runner, _, entry, _, _ = native
    from self_wake import native_wake
    calls = []
    async def wake(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "failure" if len(calls) == 1 else "pending", "receipt_id": 1}
    monkeypatch.setattr(native_wake, "wake", wake)
    outbox.enqueue(entry=entry, payload="hello", source_kind="cron_delivery", dedupe_key="one", hermes_home=tmp_path)
    for _ in range(3):
        asyncio.run(outbox.drain_once(runner, hermes_home=tmp_path))
    # pending is reconciled by native_wake's existing receipt claim, never reset.
    assert len(calls) == 3
    assert calls[0]["dedupe_key"] == calls[2]["dedupe_key"]

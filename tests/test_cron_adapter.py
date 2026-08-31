"""Cron-delivery compatibility adapter tests (plugin-owned host seam)."""
from __future__ import annotations

import asyncio
import sys
import threading
import types
from dataclasses import dataclass
from enum import Enum

import pytest

from self_wake import cron_adapter


class _Platform(Enum):
    DISCORD = "discord"


@dataclass
class _Origin:
    platform: _Platform
    chat_id: str
    thread_id: str | None = None
    user_id: str | None = None


@dataclass
class _Entry:
    session_key: str
    session_id: str
    origin: _Origin


class _Store:
    def __init__(self, entries):
        self._entries = entries

    def list_sessions(self, active_minutes=None):
        return list(self._entries)


class _Runner:
    def __init__(self, entries):
        self.session_store = _Store(entries)
        self.calls = []

    async def wake_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "agent_responded", "receipt_id": 41}


@pytest.fixture(autouse=True)
def _reset_cron_adapter(monkeypatch):
    cron_adapter.uninstall()
    # Unit tests exercise wrapper behavior with explicit fake runners. Exact
    # host prerequisite adoption is covered separately; do not let ambient
    # ~/.hermes imports influence these isolated tests.
    monkeypatch.setattr(
        cron_adapter,
        "_operational_prerequisites",
        lambda: {"ok": True, "reason": "", "targets": {"test_fixture": True}},
        raising=False,
    )
    yield
    cron_adapter.uninstall()


def _running_loop():
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait(timeout=2)
    return loop, thread


def test_successful_delivery_wakes_matching_existing_session(monkeypatch):
    entry = _Entry(
        session_key="agent:main:discord:thread:149:149",
        session_id="sid-1",
        origin=_Origin(_Platform.DISCORD, "149", "149"),
    )
    runner = _Runner([entry])
    loop, thread = _running_loop()
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    delivered = []

    def original_mirror(*args, **kwargs):
        return None

    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        original_mirror, scheduler
    )

    def original(job, content, adapters=None, loop=None):
        delivered.append((job["id"], content))
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id="149", enabled=False,
        )
        return None

    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    wrapper = cron_adapter._make_delivery_wrapper(original, scheduler)
    try:
        result = wrapper(
            {"id": "cron-1", "name": "Return monitor", "origin": {
                "platform": "discord", "chat_id": "149", "thread_id": "149"
            }},
            "[AZAZIE RETURN — POSITIVE] prepare the attached PDF",
            adapters={},
            loop=loop,
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert result is None
    assert delivered == [("cron-1", "[AZAZIE RETURN — POSITIVE] prepare the attached PDF")]
    assert len(runner.calls) == 1
    wake = runner.calls[0]
    assert wake["session_key"] == entry.session_key
    assert wake["source_kind"] == "cron_delivery"
    assert "AZAZIE RETURN" in wake["payload"]
    assert wake["dedupe_key"].startswith("cron_delivery:cron-1:")


def test_mirror_disabled_recovers_origin_user_id_without_cross_user_wake(monkeypatch):
    """Host omits helper user_id when mirroring is off; exact origin restores it."""
    zoe = _Entry(
        "agent:main:discord:thread:149:149:user-zoe", "sid-zoe",
        _Origin(_Platform.DISCORD, "149", "149", "user-zoe"),
    )
    other = _Entry(
        "agent:main:discord:thread:149:149:user-other", "sid-other",
        _Origin(_Platform.DISCORD, "149", "149", "user-other"),
    )
    runner = _Runner([other, zoe])
    loop, thread = _running_loop()
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)

    def original(job, content, adapters=None, loop=None):
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id="149",
            user_id=None, enabled=False,
        )
        return None

    wrapper = cron_adapter._make_delivery_wrapper(original, scheduler)
    try:
        result = wrapper(
            {
                "id": "cron-mirror-off",
                "execution_id": "exec-mirror-off",
                "origin": {
                    "platform": "discord", "chat_id": "149",
                    "thread_id": "149", "user_id": "user-zoe",
                },
            },
            "payload", loop=loop,
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert result is None
    assert [call["session_key"] for call in runner.calls] == [zoe.session_key]


def test_delivery_failure_never_injects_wake(monkeypatch):
    runner = _Runner([])
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    wrapper = cron_adapter._make_delivery_wrapper(
        lambda *a, **k: "discord delivery failed", scheduler
    )

    assert wrapper({"id": "cron-2"}, "payload") == "discord delivery failed"
    assert runner.calls == []


def test_config_off_preserves_delivery_without_wake(monkeypatch):
    runner = _Runner([])
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": False}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)

    def original(job, content, adapters=None, loop=None):
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id=None, enabled=False,
        )
        return None

    wrapper = cron_adapter._make_delivery_wrapper(original, scheduler)

    assert wrapper({"id": "cron-off"}, "payload") is None
    assert runner.calls == []


def test_unmatched_delivery_target_does_not_wake_creator_session(monkeypatch):
    creator = _Entry(
        session_key="agent:main:discord:thread:creator:creator",
        session_id="sid-creator",
        origin=_Origin(_Platform.DISCORD, "creator", "creator"),
    )
    runner = _Runner([creator])
    loop, thread = _running_loop()
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)

    def original(job, content, adapters=None, loop=None):
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "broadcast", "", thread_id=None, enabled=False,
        )
        return None

    wrapper = cron_adapter._make_delivery_wrapper(original, scheduler)
    try:
        assert wrapper({"id": "cron-broadcast"}, "payload", loop=loop) is None
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()
    assert runner.calls == []


def test_resolve_wake_target_matches_user_id_and_fails_closed_on_ambiguity():
    zoe = _Entry(
        session_key="agent:main:discord:group:149:user-zoe",
        session_id="sid-zoe",
        origin=_Origin(_Platform.DISCORD, "149", None, "user-zoe"),
    )
    ben = _Entry(
        session_key="agent:main:discord:group:149:user-ben",
        session_id="sid-ben",
        origin=_Origin(_Platform.DISCORD, "149", None, "user-ben"),
    )
    runner = _Runner([ben, zoe])  # wrong user first must never influence routing
    target = {
        "platform": "discord", "chat_id": "149", "thread_id": None,
        "user_id": "user-zoe",
    }

    assert cron_adapter._resolve_wake_target({}, target, runner) == {
        "session_key": zoe.session_key,
    }

    duplicate = _Entry(
        session_key="agent:other:discord:group:149:user-zoe",
        session_id="sid-zoe-2",
        origin=_Origin(_Platform.DISCORD, "149", None, "user-zoe"),
    )
    runner.session_store = _Store([duplicate, zoe])
    assert cron_adapter._resolve_wake_target({}, target, runner) is None


def test_embedded_origin_session_key_cannot_bypass_full_route_validation():
    intended = _Entry(
        session_key="user-a-key",
        session_id="user-a-id",
        origin=_Origin(_Platform.DISCORD, "149", None, "user-a"),
    )
    poisoned = _Entry(
        session_key="user-b-key",
        session_id="user-b-id",
        origin=_Origin(_Platform.DISCORD, "149", None, "user-b"),
    )
    runner = _Runner([poisoned, intended])
    target = {
        "platform": "discord", "chat_id": "149", "thread_id": None,
        "user_id": "user-a",
    }
    job = {
        "origin": {
            **target,
            "session_key": poisoned.session_key,
            "session_id": poisoned.session_id,
        }
    }

    assert cron_adapter._resolve_wake_target(job, target, runner) == {
        "session_key": intended.session_key,
    }


def test_identical_output_from_distinct_executions_gets_distinct_dedupe_keys():
    entry = _Entry(
        session_key="agent:main:discord:thread:149:149",
        session_id="sid-1",
        origin=_Origin(_Platform.DISCORD, "149", "149"),
    )
    runner = _Runner([entry])
    loop, thread = _running_loop()
    target = {"platform": "discord", "chat_id": "149", "thread_id": "149"}
    try:
        for execution_id in ("exec-1", "exec-2"):
            result = cron_adapter._schedule_wake(
                job={"id": "recurring", "execution_id": execution_id},
                target=target,
                content="same output",
                runner=runner,
                loop=loop,
            )
            assert result["status"] == "agent_responded"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert len(runner.calls) == 2
    assert runner.calls[0]["dedupe_key"] != runner.calls[1]["dedupe_key"]
    assert ":exec-1:" in runner.calls[0]["dedupe_key"]
    assert ":exec-2:" in runner.calls[1]["dedupe_key"]


def test_actual_success_route_drives_wake_even_when_mirroring_disabled(monkeypatch):
    """The helper call carries the post-mutation route and per-target success."""
    entry = _Entry(
        session_key="agent:main:discord:thread:actual-thread:user-zoe",
        session_id="sid-actual",
        origin=_Origin(_Platform.DISCORD, "149", "actual-thread", "user-zoe"),
    )
    runner = _Runner([entry])
    loop, thread = _running_loop()
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)

    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )

    def original_mirror(*args, **kwargs):
        return None

    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        original_mirror, scheduler
    )

    def original_delivery(job, content, adapters=None, loop=None):
        # The host calls this only after this target succeeded. Its thread id is
        # the actual continuable-thread route, not the configured origin route.
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id="actual-thread",
            user_id="user-zoe", enabled=False,
        )
        return "another fanout target failed"

    wrapper = cron_adapter._make_delivery_wrapper(original_delivery, scheduler)
    try:
        result = wrapper(
            {"id": "cron-partial", "execution_id": "exec-partial"},
            "authoritative content from ContextVar",
            adapters={},
            loop=loop,
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert result == "another fanout target failed"
    assert len(runner.calls) == 1
    wake = runner.calls[0]
    assert wake["session_key"] == entry.session_key
    assert "authoritative content from ContextVar" in wake["payload"]
    assert "actual-thread" in wake["dedupe_key"]


def test_partial_fanout_wakes_each_successful_actual_route_before_aggregate_raises(monkeypatch):
    entries = [
        _Entry("agent:main:discord:group:149:user-zoe", "sid-zoe",
               _Origin(_Platform.DISCORD, "149", None, "user-zoe")),
        _Entry("agent:main:discord:thread:150:opened", "sid-opened",
               _Origin(_Platform.DISCORD, "150", "opened", None)),
    ]
    runner = _Runner(entries)
    loop, thread = _running_loop()
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )

    class AggregateDeliveryError(RuntimeError):
        pass

    def original_delivery(job, content, adapters=None, loop=None):
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id=None,
            user_id="user-zoe", enabled=False,
        )
        # A second successful target calls the helper before later aggregation.
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "150", "", thread_id="opened",
            user_id=None, enabled=False,
        )
        raise AggregateDeliveryError("later fanout aggregation failed")

    wrapper = cron_adapter._make_delivery_wrapper(original_delivery, scheduler)
    try:
        with pytest.raises(AggregateDeliveryError, match="aggregation failed"):
            wrapper({"id": "fanout", "execution_id": "exec-fanout"}, "payload", loop=loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert [call["session_key"] for call in runner.calls] == [
        entries[0].session_key, entries[1].session_key,
    ]
    assert all(call["source_kind"] == "cron_delivery" for call in runner.calls)


def test_in_channel_success_uses_actual_flat_no_thread_route(monkeypatch):
    flat = _Entry(
        "agent:main:discord:group:149:user-zoe", "sid-flat",
        _Origin(_Platform.DISCORD, "149", None, "user-zoe"),
    )
    stale_thread = _Entry(
        "agent:main:discord:thread:149:stale", "sid-stale",
        _Origin(_Platform.DISCORD, "149", "stale", "user-zoe"),
    )
    runner = _Runner([stale_thread, flat])
    loop, thread = _running_loop()
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )

    def original_delivery(job, content, adapters=None, loop=None):
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id=None,
            user_id="user-zoe", enabled=False,
        )

    wrapper = cron_adapter._make_delivery_wrapper(original_delivery, scheduler)
    try:
        wrapper({"id": "flat", "execution_id": "exec-flat"}, "payload", loop=loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert [call["session_key"] for call in runner.calls] == [flat.session_key]
    assert ":discord:149::" in runner.calls[0]["dedupe_key"]


def test_live_thread_fallback_uses_raw_response_actual_no_thread_route(monkeypatch):
    flat = _Entry(
        "agent:main:discord:group:149:user-zoe", "sid-flat",
        _Origin(_Platform.DISCORD, "149", None, "user-zoe"),
    )
    requested = _Entry(
        "agent:main:discord:thread:149:missing", "sid-requested",
        _Origin(_Platform.DISCORD, "149", "missing", "user-zoe"),
    )
    runner = _Runner([requested, flat])
    loop, thread = _running_loop()
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )

    def original_delivery(job, content, adapters=None, loop=None):
        # These are the exact current-host locals after a confirmed live send
        # whose raw response reports thread fallback. The host helper still gets
        # the stale requested thread; the adapter must correct or fail closed.
        delivered = True
        send_raw_response = {
            "thread_fallback": True,
            "requested_thread_id": "missing",
        }
        timed_out = False
        assert delivered and send_raw_response and not timed_out
        scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id="missing",
            user_id="user-zoe", enabled=False,
        )

    wrapper = cron_adapter._make_delivery_wrapper(original_delivery, scheduler)
    try:
        wrapper({"id": "fallback", "execution_id": "exec-fallback"}, "payload", loop=loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert [call["session_key"] for call in runner.calls] == [flat.session_key]


def test_no_wake_for_unsuccessful_target_when_success_helper_not_called(monkeypatch):
    runner = _Runner([])
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: None, scheduler
    )
    expected = "delivery error: failed target"
    wrapper = cron_adapter._make_delivery_wrapper(
        lambda *a, **k: expected, scheduler
    )

    assert wrapper({"id": "failed"}, "payload") == expected
    assert runner.calls == []


def test_wake_failure_never_escapes_or_changes_visible_delivery_result(monkeypatch):
    entry = _Entry(
        "agent:main:discord:thread:149:149", "sid-1",
        _Origin(_Platform.DISCORD, "149", "149"),
    )

    class RaisingRunner(_Runner):
        def wake_session(self, **kwargs):
            raise RuntimeError("optional wake exploded")

    runner = RaisingRunner([entry])
    monkeypatch.setattr(cron_adapter, "_active_gateway_runner", lambda: runner)
    scheduler = types.SimpleNamespace(
        load_config=lambda: {"cron": {"wake_agent_on_delivery": True}},
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        lambda *a, **k: "original-mirror-result", scheduler
    )
    visible_result = {"delivery": "unchanged"}

    def original_delivery(job, content, adapters=None, loop=None):
        mirror_result = scheduler._maybe_mirror_cron_delivery(
            job, "discord", "149", "", thread_id="149", enabled=False,
        )
        assert mirror_result == "original-mirror-result"
        return visible_result

    wrapper = cron_adapter._make_delivery_wrapper(original_delivery, scheduler)
    loop = types.SimpleNamespace(is_running=lambda: True)

    assert wrapper({"id": "wake-fails"}, "payload", loop=loop) is visible_result


def test_timeout_after_wake_started_is_non_raising_and_does_not_close_coroutine(monkeypatch):
    entry = _Entry(
        session_key="agent:main:discord:thread:149:149",
        session_id="sid-1",
        origin=_Origin(_Platform.DISCORD, "149", "149"),
    )

    class TimeoutRunner(_Runner):
        def __init__(self):
            super().__init__([entry])
            self.coro = None

        def wake_session(self, **kwargs):
            async def _running_wake():
                await asyncio.sleep(0)
                return {"status": "agent_responded"}

            self.coro = _running_wake()
            return self.coro

    class InFlightFuture:
        cancel_called = False

        def result(self, timeout=None):
            raise TimeoutError("wake confirmation timeout")

        def cancel(self):
            self.cancel_called = True
            return True

        def add_done_callback(self, callback):
            self.callback = callback

    runner = TimeoutRunner()
    future = InFlightFuture()
    monkeypatch.setattr(
        cron_adapter.asyncio, "run_coroutine_threadsafe", lambda coro, loop: future
    )
    loop = types.SimpleNamespace(is_running=lambda: True)

    result = cron_adapter._schedule_wake(
        job={"id": "cron-timeout", "execution_id": "exec-timeout"},
        target={"platform": "discord", "chat_id": "149", "thread_id": "149"},
        content="visible delivery already succeeded",
        runner=runner,
        loop=loop,
    )

    assert result["status"] == "in_flight"
    assert future.cancel_called is False
    assert runner.coro is not None and runner.coro.cr_frame is not None
    runner.coro.close()


def test_install_rolls_back_outer_wrapper_when_target_wrapper_assignment_fails():
    def original_delivery(job, content, adapters=None, loop=None):
        return None

    def original_mirror(*args, **kwargs):
        return None

    class FailingModule:
        def __init__(self):
            object.__setattr__(self, "_deliver_result", original_delivery)
            object.__setattr__(self, "_maybe_mirror_cron_delivery", original_mirror)
            object.__setattr__(self, "load_config", lambda: {})

        def __setattr__(self, name, value):
            if name == "_maybe_mirror_cron_delivery" and getattr(
                value, "_self_wake_cron_target_v2", False
            ):
                raise RuntimeError("second assignment rejected")
            object.__setattr__(self, name, value)

    scheduler = FailingModule()
    report = cron_adapter.install(module=scheduler, force=True)

    assert report["installed"] is False
    assert report["reason"] == "install_failed"
    assert scheduler._deliver_result is original_delivery
    assert scheduler._maybe_mirror_cron_delivery is original_mirror


def test_uninstall_preserves_both_successor_wrappers_and_reports_ownership_loss():
    def original_delivery(job, content, adapters=None, loop=None):
        return None

    def original_mirror(*args, **kwargs):
        return None

    scheduler = types.SimpleNamespace(
        _deliver_result=original_delivery,
        _maybe_mirror_cron_delivery=original_mirror,
        load_config=lambda: {},
    )
    report = cron_adapter.install(module=scheduler, force=True)
    assert report["installed"] is True

    def successor_delivery(*args, **kwargs):
        return "successor-delivery"

    def successor_mirror(*args, **kwargs):
        return None

    scheduler._deliver_result = successor_delivery
    scheduler._maybe_mirror_cron_delivery = successor_mirror
    result = cron_adapter.uninstall()

    assert result["uninstalled"] is False
    assert result["reason"] == "ownership_lost"
    assert "cron.scheduler._deliver_result" in result["ownership_lost"]
    assert "cron.scheduler._maybe_mirror_cron_delivery" in result["ownership_lost"]
    assert scheduler._deliver_result is successor_delivery
    assert scheduler._maybe_mirror_cron_delivery is successor_mirror


def test_probe_is_unavailable_when_adapter_not_adopted():
    scheduler = types.SimpleNamespace(_deliver_result=lambda *a, **k: None)
    result = cron_adapter.probe(module=scheduler)
    assert result["available"] is False
    assert result["source"] == "absent"
    assert "not adopted" in result["reason"]


def test_probe_reports_shim_only_after_both_wrappers_are_active():
    scheduler = types.SimpleNamespace(
        load_config=lambda: {},
        _deliver_result=lambda *a, **k: None,
        _maybe_mirror_cron_delivery=lambda *a, **k: None,
    )
    scheduler._deliver_result = cron_adapter._make_delivery_wrapper(
        scheduler._deliver_result, scheduler
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        scheduler._maybe_mirror_cron_delivery, scheduler
    )
    result = cron_adapter.probe(module=scheduler)
    assert result["available"] is True
    assert result["source"] == "shim"
    assert all(result["wrappers"].values())


def test_install_survives_startup_before_gateway_runner_exists(monkeypatch):
    scheduler = types.SimpleNamespace(
        load_config=lambda: {},
        _deliver_result=lambda *a, **k: None,
        _maybe_mirror_cron_delivery=lambda *a, **k: None,
    )
    monkeypatch.setattr(
        cron_adapter,
        "_operational_prerequisites",
        lambda: {
            "ok": False,
            "reason": "active GatewayRunner unavailable",
            "targets": {"active_gateway_runner": False},
        },
    )

    report = cron_adapter.install(module=scheduler, force=True)

    assert report["installed"] is True
    assert getattr(scheduler._deliver_result, "_self_wake_cron_outer_v2", False)
    assert getattr(
        scheduler._maybe_mirror_cron_delivery, "_self_wake_cron_target_v2", False
    )
    startup_probe = cron_adapter.probe(module=scheduler)
    assert startup_probe["available"] is False
    assert "active GatewayRunner unavailable" in startup_probe["reason"]

    monkeypatch.setattr(
        cron_adapter,
        "_operational_prerequisites",
        lambda: {"ok": True, "reason": "", "targets": {"active_gateway_runner": True}},
    )
    assert cron_adapter.probe(module=scheduler)["available"] is True


def test_compatibility_names_missing_per_target_success_seam(monkeypatch):
    def exact_delivery(job, content, adapters=None, loop=None):
        return None

    digest = cron_adapter._function_digest(exact_delivery)
    monkeypatch.setitem(
        cron_adapter.SUPPORTED_HOSTS,
        digest,
        {"commit": "test", "function": "cron.scheduler._deliver_result"},
    )
    scheduler = types.SimpleNamespace(_deliver_result=exact_delivery)

    result = cron_adapter.check_compatibility(module=scheduler)

    assert result["compatible"] is False
    assert "cron.scheduler._maybe_mirror_cron_delivery" in result["detail"]


def test_adoption_probe_requires_both_owned_wrappers():
    scheduler = types.SimpleNamespace(_deliver_result=lambda *a, **k: None)

    def original_mirror(*args, **kwargs):
        return None

    scheduler._maybe_mirror_cron_delivery = original_mirror
    scheduler._deliver_result = cron_adapter._make_delivery_wrapper(
        scheduler._deliver_result, scheduler
    )

    result = cron_adapter.probe(module=scheduler)

    assert result["available"] is False
    assert "_maybe_mirror_cron_delivery" in result["reason"]


def test_adoption_probe_names_missing_outer_wrapper():
    scheduler = types.SimpleNamespace(
        load_config=lambda: {},
        _deliver_result=lambda *a, **k: None,
        _maybe_mirror_cron_delivery=lambda *a, **k: None,
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        scheduler._maybe_mirror_cron_delivery, scheduler
    )

    result = cron_adapter.probe(module=scheduler)

    assert result["available"] is False
    assert "cron.scheduler._deliver_result" in result["reason"]


def test_adoption_probe_requires_operational_wake_prerequisites(monkeypatch):
    scheduler = types.SimpleNamespace(
        load_config=lambda: {},
        _deliver_result=lambda *a, **k: None,
        _maybe_mirror_cron_delivery=lambda *a, **k: None,
    )
    scheduler._deliver_result = cron_adapter._make_delivery_wrapper(
        scheduler._deliver_result, scheduler
    )
    scheduler._maybe_mirror_cron_delivery = cron_adapter._make_mirror_wrapper(
        scheduler._maybe_mirror_cron_delivery, scheduler
    )
    monkeypatch.setattr(
        cron_adapter,
        "_operational_prerequisites",
        lambda: {
            "ok": False,
            "reason": "gateway.run.GatewayRunner.wake_session missing",
            "targets": {"gateway.run.GatewayRunner.wake_session": False},
        },
    )

    result = cron_adapter.probe(module=scheduler)

    assert result["available"] is False
    assert "GatewayRunner.wake_session" in result["reason"]


def test_exact_host_shape_check_rejects_drift():
    scheduler = types.SimpleNamespace(_deliver_result=lambda *a, **k: None)
    result = cron_adapter.check_compatibility(module=scheduler)
    assert result["compatible"] is False
    assert result["status"] == "host_drift"
    assert result["remediation"]


@pytest.mark.parametrize(
    "drifted_seam",
    [
        "cron.scheduler._maybe_mirror_cron_delivery",
        "cron.scheduler.load_config",
        "gateway.session.SessionStore.list_sessions",
    ],
)
def test_compatibility_names_each_drifted_stable_private_seam(
        monkeypatch, drifted_seam):
    def exact_delivery(job, content, adapters=None, loop=None):
        return None

    def exact_mirror(
        job, platform_name, chat_id, mirror_text, thread_id=None, user_id=None,
        *, enabled=False,
    ):
        return None

    def exact_load_config():
        return {}

    def exact_list_sessions(self, active_minutes=None):
        return []

    def drifted_callable(*args, **kwargs):
        return "drift"

    class FakeStore:
        list_sessions = exact_list_sessions

    scheduler = types.SimpleNamespace(
        _deliver_result=exact_delivery,
        _maybe_mirror_cron_delivery=exact_mirror,
        load_config=exact_load_config,
    )
    gateway_pkg = types.ModuleType("gateway")
    session_mod = types.ModuleType("gateway.session")
    setattr(session_mod, "SessionStore", FakeStore)
    setattr(gateway_pkg, "session", session_mod)
    monkeypatch.setitem(sys.modules, "gateway", gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.session", session_mod)

    monkeypatch.setitem(
        cron_adapter.SUPPORTED_HOSTS,
        cron_adapter._function_digest(exact_delivery),
        {"commit": "test", "function": "cron.scheduler._deliver_result"},
    )
    exact_seams = {
        "cron.scheduler._maybe_mirror_cron_delivery": exact_mirror,
        "cron.scheduler.load_config": exact_load_config,
        "gateway.session.SessionStore.list_sessions": exact_list_sessions,
    }
    for seam_name, fn in exact_seams.items():
        monkeypatch.setitem(
            cron_adapter.SUPPORTED_PRIVATE_SEAMS,
            seam_name,
            {cron_adapter._function_digest(fn)},
        )

    if drifted_seam == "cron.scheduler._maybe_mirror_cron_delivery":
        scheduler._maybe_mirror_cron_delivery = drifted_callable
    elif drifted_seam == "cron.scheduler.load_config":
        scheduler.load_config = drifted_callable
    else:
        setattr(FakeStore, "list_sessions", drifted_callable)

    result = cron_adapter.check_compatibility(module=scheduler)

    assert result["compatible"] is False
    assert result["status"] == "host_drift"
    assert drifted_seam in result["detail"]

"""Regression coverage for native-mode rows consumed by the plugin shim.

Storage and transport doubles deliberately avoid mutating a real Kanban board.
Full board-to-parent integration remains separate and unverified.
"""
from __future__ import annotations

import asyncio
from enum import Enum
import sys
from types import ModuleType, SimpleNamespace

import pytest

from self_wake import compat_shim as shim


class Platform(Enum):
    DISCORD = "discord"
    SLACK = "slack"


@pytest.fixture
def world(monkeypatch):
    sub = {"task_id": "t_probe", "platform": "discord", "chat_id": "parent", "thread_id": "", "chat_type": "dm", "user_id": "real-user", "notifier_profile": "default", "delivery_mode": "wake"}
    task = SimpleNamespace(title="Probe", assignee=None, result="", status="done", session_id="worker-not-parent")
    event = SimpleNamespace(kind="completed", payload={"summary": "Probe completed"}, id=2)
    state = {"sub": sub, "cursor": 1, "sent": [], "wakes": [], "unsubscribed": False, "claims": 0, "rewinds": 0}
    kb = ModuleType("hermes_cli.kanban_db")
    kb.list_boards = lambda **kwargs: [{"slug": "probe", "db_path": "/tmp/probe-only.db"}]
    kb.connect = lambda **kwargs: SimpleNamespace(close=lambda: None)
    kb.list_notify_subs = lambda conn: [sub]
    def claim(conn, **kwargs):
        state["claims"] += 1
        old = state["cursor"]
        if old >= event.id:
            return old, old, []
        state["cursor"] = event.id
        return old, event.id, [event]

    kb.claim_unseen_events_for_sub = claim
    kb.unseen_events_for_sub = lambda conn, **kwargs: (event.id, [event]) if state["cursor"] < event.id else (state["cursor"], [])
    kb.get_task = lambda conn, tid: task
    config = ModuleType("hermes_cli.config")
    config.load_config = lambda: {"kanban": {"dispatch_in_gateway": True}}
    gateway_config = ModuleType("gateway.config")
    gateway_config.Platform = Platform
    cli = ModuleType("hermes_cli")
    cli.kanban_db = kb
    gateway = ModuleType("gateway")
    for name, module in {"hermes_cli": cli, "hermes_cli.config": config, "hermes_cli.kanban_db": kb, "gateway": gateway, "gateway.config": gateway_config}.items():
        monkeypatch.setitem(sys.modules, name, module)

    async def send(chat, text, metadata=None):
        state["sent"].append((chat, text))
        return SimpleNamespace(success=True)

    async def wake(**kwargs):
        state["wakes"].append(kwargs)
        return {"status": "agent_responded", "receipt_id": 1}

    async def artifacts(**kwargs):
        return None

    def advance(sub, cursor, board):
        state["cursor"] = cursor

    monkeypatch.setattr(shim, "_advance_native", advance)

    def rewind(sub, cursor, old, board):
        state["rewinds"] += 1
        state["cursor"] = old

    def unsub(sub, board):
        state["unsubscribed"] = True

    adapter = SimpleNamespace(send=send)
    runner = SimpleNamespace(_running=True, adapters={Platform.DISCORD: adapter}, _kanban_notifier_profile="default", _kanban_advance=advance, _kanban_rewind=rewind, _kanban_unsub=unsub, _deliver_kanban_artifacts=artifacts, wake_session=wake)
    runner._kanban_internal_wake_target = lambda row: shim._shim_kanban_internal_wake_target(runner, row)
    # This seam is the real host's canonical source/key resolver in integration.
    runner._session_key_for_source = lambda source: f"agent:main:discord:{source.chat_type}:{source.chat_id}"
    session = ModuleType("gateway.session")
    session.SessionSource = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "gateway.session", session)
    runner._authorization_adapter = lambda platform, profile=None: adapter
    runner._profile_adapters = {}

    real_sleep = asyncio.sleep

    async def tick_sleep(delay):
        if delay == 5:
            return
        runner._running = False
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", tick_sleep)
    return runner, state, task, event


def tick(world):
    asyncio.run(shim._shim_kanban_notifier_watcher(world[0], interval=1))


@pytest.mark.parametrize("kind", ["completed", "blocked", "crashed", "gave_up", "timed_out"])
def test_native_wake_targets_parent_without_passive_ping(world, kind):
    runner, state, task, event = world
    event.kind = kind
    task.status = "done" if kind == "completed" else "blocked"
    tick(world)
    assert state["sent"] == []
    assert len(state["wakes"]) == 1
    assert state["wakes"][0]["session_key"] == "agent:main:discord:dm:parent"
    assert state["wakes"][0]["dedupe_key"] == "kanban:probe:t_probe:2"
    assert not state["unsubscribed"]


def test_native_notify_remains_passive(world):
    runner, state, task, event = world
    state["sub"]["delivery_mode"] = "notify"
    task.status = "blocked"
    tick(world)
    assert len(state["sent"]) == 1
    assert not state["wakes"]


def test_native_notify_and_wake_does_both(world):
    runner, state, task, event = world
    state["sub"]["delivery_mode"] = "notify+wake"
    task.status = "blocked"
    tick(world)
    assert len(state["sent"]) == 1
    assert len(state["wakes"]) == 1


def test_native_failure_retains_event_beyond_old_drop_cap(world):
    runner, state, task, event = world

    async def failed(**kwargs):
        return {"status": "failure", "error": "receiver unavailable"}

    runner.wake_session = failed
    for _ in range(4):
        runner._running = True
        tick(world)
    assert not state["unsubscribed"]
    assert state["cursor"] == 1
    assert state["sent"] == []


def test_foreign_owner_is_untouched(world):
    runner, state, task, event = world
    state["sub"]["notifier_profile"] = "other-profile"
    tick(world)
    assert not state["sent"] and not state["wakes"]
    assert state["cursor"] == 1
    assert not state["unsubscribed"]


def test_legacy_marker_still_wakes_exact_session(world):
    runner, state, task, event = world
    state["sub"]["delivery_mode"] = "notify"
    state["sub"]["user_id"] = "session:existing-research-parent"
    task.status = "blocked"
    tick(world)
    assert not state["sent"]
    assert state["wakes"][0]["session_key"] == "existing-research-parent"


@pytest.mark.parametrize("status", ["pending", "requested", "queued", "dispatched_unconfirmed", "unexpected"])
def test_native_unconfirmed_result_does_not_ack(world, status):
    runner, state, _, _ = world

    async def pending(**kwargs):
        return {"status": status}

    runner.wake_session = pending
    tick(world)
    assert state["cursor"] == 1
    assert not state["unsubscribed"]


@pytest.mark.parametrize("scope_key", ["scope_id", "slack_team_id", "team_id"])
def test_native_source_keeps_persisted_profile_and_scope(world, scope_key):
    runner, state, _, _ = world
    state["sub"].update(notifier_profile="beta", delivery_metadata={scope_key: "workspace"})
    captured = []
    runner._session_key_for_source = lambda source: captured.append(source) or "exact"
    assert runner._kanban_internal_wake_target(state["sub"]) == ("session_key", "exact")
    assert captured[0].profile == "beta"
    assert captured[0].scope_id == "workspace"


def test_native_scope_falls_back_to_selected_adapter(world):
    runner, state, _, _ = world
    adapter = runner.adapters[Platform.DISCORD]
    adapter.scope_id_for_chat = lambda chat: "live-workspace"
    runner._session_key_for_source = lambda source: source.scope_id
    assert runner._kanban_internal_wake_target(state["sub"]) == ("session_key", "live-workspace")


def test_served_profile_only_platform_is_delivered_by_its_adapter(world):
    runner, state, _, _ = world
    chosen = []
    adapter = runner.adapters[Platform.DISCORD]
    runner.adapters = {}
    runner._profile_adapters = {"beta": {Platform.SLACK: adapter}}
    runner._authorization_adapter = lambda platform, profile=None: chosen.append((platform, profile)) or runner._profile_adapters.get(profile, {}).get(platform)
    state["sub"].update(platform="slack", notifier_profile="beta", delivery_mode="notify+wake")
    tick(world)
    assert len(state["wakes"]) == len(state["sent"]) == 1
    assert chosen and all(pair == (Platform.SLACK, "beta") for pair in chosen)


def test_archive_without_another_terminal_event_removes_subscription(world):
    _, state, task, event = world
    state["cursor"] = event.id
    task.status = "archived"
    tick(world)
    assert state["unsubscribed"]


def test_native_collection_does_not_preack_before_delivery(world):
    runner, state, _, _ = world
    observed = []
    async def wake(**kwargs):
        observed.append(state["cursor"])
        return {"status": "pending"}
    runner.wake_session = wake
    tick(world)
    assert observed == [1]
    assert state["claims"] == state["rewinds"] == 0
    assert state["cursor"] == 1


def test_cancellation_during_native_delivery_retains_unclaimed_event(world):
    runner, state, _, _ = world
    async def cancelled(**kwargs):
        raise asyncio.CancelledError
    runner.wake_session = cancelled
    with pytest.raises(asyncio.CancelledError):
        tick(world)
    assert state["cursor"] == 1


def test_legacy_claim_actually_preadvances_and_rewinds_on_failure(world):
    runner, state, _, _ = world
    state["sub"].update(delivery_mode="notify", user_id="session:legacy")
    observed = []
    async def rejected(**kwargs):
        observed.append(state["cursor"])
        return {"status": "failure"}
    runner.wake_session = rejected
    tick(world)
    assert observed == [2]
    assert state["claims"] == state["rewinds"] == 1
    assert state["cursor"] == 1


def test_partial_native_signature_is_not_called_with_unknown_keyword(world):
    runner, state, _, _ = world
    async def old_native(*, payload, source_kind, session_key=None, session_id=None, dedupe_key=None):
        raise AssertionError("partial native path must stay pending")
    runner.wake_session = old_native
    tick(world)
    assert state["cursor"] == 1
    assert not state["sent"]

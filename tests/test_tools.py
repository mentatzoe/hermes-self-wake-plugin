"""Tests for tool handler JSON contract + fail-closed behavior at the handler layer."""
from __future__ import annotations

import json

from self_wake import tools


def _parse(handler_result: str) -> dict:
    """Every handler must return a JSON string."""
    assert isinstance(handler_result, str)
    return json.loads(handler_result)


def test_sessions_handler_returns_json(hermes_home):
    result = _parse(tools.sessions_handler({"query": "LTM"}))
    assert result["success"] is True
    assert result["tool"] == "self_wake_sessions"
    assert result["count"] == 1
    assert "capability_mode" in result
    assert result["resolver_source"]["kind"] == "current_session_cache_adapter"
    assert "sessions_file" not in result


def test_sessions_handler_no_uncaught_exception_on_bad_args(hermes_home):
    """Handlers must not raise even on malformed args."""
    result = _parse(tools.sessions_handler({"limit": "not-a-number"}))
    assert result["success"] is True  # degraded gracefully


def test_subscribe_handler_fail_closed_without_capability(
        inspect_only_capability, kanban_backend, hermes_home, monkeypatch):
    """Handler-layer fail-closed: capability_missing, no write."""
    from self_wake import kanban as kanban_mod
    kanban_mod.set_default_backend(kanban_backend)
    try:
        result = _parse(tools.subscribe_handler({
            "task_id": "t_handler",
            "session_key": "agent:main:discord:thread:1511162896762142980:1511162896762142980",
        }))
        assert result["success"] is False
        assert result["error"] == "capability_missing"
        assert kanban_mod.list_wake_subscriptions() == []
    finally:
        kanban_mod.set_default_backend(None)


def test_subscribe_handler_full_mode_writes_marker(
        full_capability, state_db_with_receipts, kanban_backend, hermes_home, monkeypatch):
    from self_wake import kanban as kanban_mod
    kanban_mod.set_default_backend(kanban_backend)
    try:
        result = _parse(tools.subscribe_handler({
            "task_id": "t_handler_full",
            "session_key": "agent:main:discord:thread:1511162896762142980:1511162896762142980",
        }))
        assert result["success"] is True
        assert result["after"]["user_id"].startswith("session:")
    finally:
        kanban_mod.set_default_backend(None)


def test_subscribe_handler_dry_run(hermes_home, inspect_only_capability, kanban_backend, monkeypatch):
    from self_wake import kanban as kanban_mod
    kanban_mod.set_default_backend(kanban_backend)
    try:
        result = _parse(tools.subscribe_handler({
            "task_id": "t_dry",
            "session_key": "agent:main:discord:thread:1511162896762142980:1511162896762142980",
            "dry_run": True,
        }))
        assert result["success"] is True
        assert result["dry_run"] is True
    finally:
        kanban_mod.set_default_backend(None)


def test_receipts_handler_capability_missing(hermes_home):
    """No state.db → capability_missing from the handler."""
    result = _parse(tools.receipts_handler({}))
    assert result["success"] is False
    assert result["error"] == "capability_missing"


def test_receipts_handler_returns_receipts(state_db_with_receipts):
    result = _parse(tools.receipts_handler({"source_kind": "kanban"}))
    assert result["success"] is True
    assert result["count"] == 1


def test_doctor_handler_returns_structured(hermes_home):
    result = _parse(tools.doctor_handler({}))
    assert result["tool"] == "self_wake_doctor"
    assert "ok" in result and "mode" in result
    assert result["mode"] in ("full", "inspect_only", "unsupported")


def test_doctor_handler_no_uncaught_exception(monkeypatch, hermes_home):
    """Even if a sub-check raises, the handler returns a structured error."""
    from self_wake import doctor
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(doctor, "run_diagnostics", _boom)
    result = _parse(tools.doctor_handler({}))
    assert result["success"] is False
    assert result["error"] == "boom"
    assert result["ok"] is False


def test_subscribe_handler_missing_task_id(hermes_home, full_capability,
                                           state_db_with_receipts, kanban_backend, monkeypatch):
    from self_wake import kanban as kanban_mod
    kanban_mod.set_default_backend(kanban_backend)
    try:
        result = _parse(tools.subscribe_handler({}))
        assert result["success"] is False
        assert result["error"] == "task_id is required"
    finally:
        kanban_mod.set_default_backend(None)

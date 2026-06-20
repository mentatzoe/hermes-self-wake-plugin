"""Tests for the /self-wake slash command dispatcher."""
from __future__ import annotations

import json

from self_wake.cli import handle_self_wake_command


def _parse(output: str) -> dict:
    # The command pretty-prints JSON; parse it back when it is JSON.
    return json.loads(output)


def test_help_with_no_args():
    out = handle_self_wake_command("")
    assert "self-wake commands" in out
    assert "sessions" in out and "subscribe" in out and "receipts" in out


def test_help_keyword():
    assert "self-wake commands" in handle_self_wake_command("help")


def test_sessions_subcommand(hermes_home):
    out = handle_self_wake_command("sessions LTM")
    result = _parse(out)
    assert result["success"] is True
    assert result["count"] == 1


def test_sessions_with_filters(hermes_home):
    out = handle_self_wake_command("sessions --platform discord")
    result = _parse(out)
    assert result["count"] == 2


def test_subscribe_subcommand_dry_run(inspect_only_capability, kanban_backend,
                                      hermes_home, monkeypatch):
    from self_wake import kanban as kanban_mod
    kanban_mod.set_default_backend(kanban_backend)
    try:
        out = handle_self_wake_command(
            "subscribe t_cli --session-key "
            "agent:main:discord:thread:1511162896762142980:1511162896762142980 --dry-run")
        result = _parse(out)
        assert result["success"] is True
        assert result["dry_run"] is True
    finally:
        kanban_mod.set_default_backend(None)


def test_receipts_subcommand(hermes_home):
    out = handle_self_wake_command("receipts")
    result = _parse(out)
    assert result["success"] is False
    assert result["error"] == "capability_missing"


def test_doctor_subcommand(hermes_home):
    out = handle_self_wake_command("doctor")
    result = _parse(out)
    assert result["tool"] == "self_wake_doctor"
    assert "mode" in result


def test_unknown_subcommand():
    out = handle_self_wake_command("frobnicate")
    assert "unknown subcommand" in out


def test_unparseable_args():
    out = handle_self_wake_command("sessions --platform 'unterminated")
    assert "could not parse arguments" in out

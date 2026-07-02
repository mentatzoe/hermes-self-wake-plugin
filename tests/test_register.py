"""Tests for plugin registration (register(ctx) surface)."""
from __future__ import annotations

from pathlib import Path

from self_wake import __version__, register


class FakeCtx:
    """Captures register_* calls for assertion."""

    def __init__(self):
        self.tools = []
        self.hooks = []
        self.commands = []
        self.skills = []

    def register_tool(self, name, toolset, schema, handler, description=""):
        self.tools.append({"name": name, "toolset": toolset, "schema": schema,
                           "handler": handler, "description": description})

    def register_hook(self, name, handler):
        self.hooks.append({"name": name, "handler": handler})

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands.append({"name": name, "handler": handler,
                              "description": description, "args_hint": args_hint})

    def register_skill(self, name, path, description=""):
        self.skills.append({"name": name, "path": path, "description": description})


def test_register_registers_expected_tools():
    ctx = FakeCtx()
    register(ctx)
    tool_names = {t["name"] for t in ctx.tools}
    assert tool_names == {"self_wake_sessions", "self_wake_subscribe_kanban",
                          "self_wake_receipts", "self_wake_doctor"}
    for t in ctx.tools:
        assert t["toolset"] == "self_wake"
        assert callable(t["handler"])
        assert t["schema"]["name"] == t["name"]
        assert "parameters" in t["schema"]


def test_register_registers_pre_llm_call_hook():
    ctx = FakeCtx()
    register(ctx)
    assert len(ctx.hooks) == 1
    assert ctx.hooks[0]["name"] == "pre_llm_call"
    assert callable(ctx.hooks[0]["handler"])


def test_register_registers_self_wake_command():
    ctx = FakeCtx()
    register(ctx)
    assert len(ctx.commands) == 1
    cmd = ctx.commands[0]
    assert cmd["name"] == "self-wake"
    assert callable(cmd["handler"])
    assert "sessions" in cmd["args_hint"]


def test_register_registers_bundled_skill():
    ctx = FakeCtx()
    register(ctx)
    assert len(ctx.skills) == 1
    skill = ctx.skills[0]
    assert skill["name"] == "kanban-self-wake"
    assert Path(skill["path"]).exists()


def test_register_hooks_returns_none(hermes_home):
    """The pre_llm_call hook must return None (prompt-cache safe, no injection)."""
    ctx = FakeCtx()
    register(ctx)
    hook = ctx.hooks[0]["handler"]
    # With no session_id, returns None immediately.
    assert hook() is None
    # With a session_id, still returns None (observer-only).
    assert hook(session_id="20260101_120000_aaaaaa", platform="discord") is None


def test_version_is_set():
    assert __version__ == "1.1.1"

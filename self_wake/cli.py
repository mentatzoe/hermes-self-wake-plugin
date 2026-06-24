"""Slash command / CLI dispatcher: ``/self-wake <subcommand>``.

Shares implementation with the model-facing tool handlers. Parses a raw arg
string into a handler call and returns pretty-printed JSON for human reading.
"""
from __future__ import annotations

import json
import shlex
from typing import Any

from . import tools


def _plain(obj: Any) -> str:
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return obj
    return json.dumps(obj, indent=2, ensure_ascii=False)


_HELP = (
    "self-wake commands:\n"
    "  /self-wake sessions [query] [--platform discord] [--chat-id ID] [--thread-id ID]\n"
    "  /self-wake subscribe TASK --board BOARD --session-key KEY [--reset-cursor] [--dry-run]\n"
    "  /self-wake receipts [--session-key KEY | --session-id ID] [--source-kind kanban]\n"
    "  /self-wake doctor\n"
    "Use the model-facing tools for structured calls: "
    "self_wake_sessions, self_wake_subscribe_kanban, self_wake_receipts, self_wake_doctor."
)


def handle_self_wake_command(raw_args: str) -> str:
    """Human-facing ``/self-wake`` command dispatcher."""
    try:
        parts = shlex.split(raw_args or "")
    except ValueError as exc:
        return f"self-wake: could not parse arguments: {exc}"
    if not parts or parts[0] in {"help", "-h", "--help"}:
        return _HELP

    cmd = parts.pop(0).lower()
    args: dict[str, Any] = {}
    positional: list[str] = []
    it = iter(parts)
    for token in it:
        if token.startswith("--"):
            key = token[2:].replace("-", "_")
            if key in {"reset_cursor", "dry_run", "force_degraded_visible_only"}:
                args[key] = True
            else:
                try:
                    args[key] = next(it)
                except StopIteration:
                    return f"self-wake: --{key.replace('_', '-')} needs a value"
        else:
            positional.append(token)

    if cmd in {"sessions", "session", "resolve"}:
        if positional:
            args.setdefault("query", " ".join(positional))
        return _plain(tools.sessions_handler(args))
    if cmd in {"subscribe", "sub", "kanban"}:
        if positional:
            args.setdefault("task_id", positional[0])
        return _plain(tools.subscribe_handler(args))
    if cmd in {"receipts", "receipt", "status"}:
        if positional:
            if positional[0].isdigit():
                args.setdefault("receipt_id", int(positional[0]))
            else:
                args.setdefault("session_key", positional[0])
        return _plain(tools.receipts_handler(args))
    if cmd in {"doctor", "diag", "diagnostics"}:
        return _plain(tools.doctor_handler(args))
    return f"self-wake: unknown subcommand {cmd!r}; try /self-wake help"

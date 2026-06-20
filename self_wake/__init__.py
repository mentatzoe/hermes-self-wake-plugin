"""Hermes self-wake plugin: formal operator tools for session wake routing.

Implements Option 2 of the architecture design: a standalone plugin that owns
operator policy and diagnostics (session discovery, Kanban wake subscription
management, receipt inspection, doctor) while depending on a host-owned
``internal_session_wake_v1`` core capability (applied via the separate clean
patch under ``docs/core-patch/``).

The plugin never edits Hermes core files and never monkeypatches by default.
On hosts without the wake capability it loads in ``inspect_only`` mode and
fails closed (clear ``capability_missing`` errors) for any wake-mutating
operation, so it never creates "looks subscribed but will never wake" state.

See ``docs/architecture.md`` for the full design and ``docs/compatibility.md``
for the compatibility/version story.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import cli, schemas, tools

__version__ = "1.0.0"

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register self-wake tools, hook, slash command, and bundled skill.

    Called once at Hermes startup. Registration itself is unconditional (the
    plugin loads on any host); capability gating happens at tool-call time so
    unsupported hosts get clear ``capability_missing`` errors instead of a
    silent no-op.
    """
    ctx.register_tool(
        name="self_wake_sessions",
        toolset="self_wake",
        schema=schemas.SELF_WAKE_SESSIONS,
        handler=tools.sessions_handler,
        description="Resolve Hermes gateway sessions for internal wake routing.",
    )
    ctx.register_tool(
        name="self_wake_subscribe_kanban",
        toolset="self_wake",
        schema=schemas.SELF_WAKE_SUBSCRIBE_KANBAN,
        handler=tools.subscribe_handler,
        description="Subscribe Kanban terminal events to wake an existing Hermes session.",
    )
    ctx.register_tool(
        name="self_wake_receipts",
        toolset="self_wake",
        schema=schemas.SELF_WAKE_RECEIPTS,
        handler=tools.receipts_handler,
        description="Inspect Hermes internal wake receipts.",
    )
    ctx.register_tool(
        name="self_wake_doctor",
        toolset="self_wake",
        schema=schemas.SELF_WAKE_DOCTOR,
        handler=tools.doctor_handler,
        description="Run self-wake end-to-end diagnostics.",
    )

    # Observer-only pre_llm_call hook. Returns None so it never injects prompt
    # context (prompt-cache safe); it only maintains a local recent-sessions
    # ledger for operator diagnostics.
    ctx.register_hook("pre_llm_call", tools.record_recent_session)

    ctx.register_command(
        "self-wake",
        cli.handle_self_wake_command,
        description="Resolve and verify Hermes self-wake routes.",
        args_hint="sessions|subscribe|receipts|doctor ...",
    )

    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    skill_md = skills_dir / "kanban-self-wake" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill(
            "kanban-self-wake",
            skill_md,
            description="Use Kanban terminal events to internally wake existing Hermes sessions.",
        )

    logger.info("self-wake plugin loaded")

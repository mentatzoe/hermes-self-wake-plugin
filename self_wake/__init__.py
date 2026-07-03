"""Hermes self-wake plugin: formal operator tools for session wake routing.

A standalone plugin that owns operator policy and diagnostics (session
discovery, Kanban wake subscription management, receipt inspection, doctor) and
depends on a host-owned ``internal_session_wake_v1`` capability.

The capability can be provided three ways, in preference order:

1. **Native** — upstream Hermes ships ``internal_session_wake_v1``, or the
   operator applies the optional reference core patch under
   ``docs/core-patch/``.  No monkeypatching.
2. **Compat shim** — the bundled ``self_wake.compat_shim`` installs the
   capability at runtime on vanilla Hermes when the operator opts in
   (``self_wake.compat_shim_enabled: true``).  This is the portable/shareable
   path: the plugin carries its own compatibility layer and never requires
   patching Hermes core.  The shim fails closed on internal drift and defers
   to a native capability when present.
3. **Absent** — the plugin loads in ``inspect_only`` mode and fails closed
   (clear ``capability_missing`` errors) for any wake-mutating operation, so it
   never creates "looks subscribed but will never wake" state.

See ``docs/architecture.md`` for the full design and ``docs/compatibility.md``
for the compatibility/version story.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import cli, compat_shim, schemas, tools

__version__ = "1.2.0"

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register self-wake tools, hook, slash command, and bundled skill.

    Called once at Hermes startup. Registration itself is unconditional (the
    plugin loads on any host); capability gating happens at tool-call time so
    unsupported hosts get clear ``capability_missing`` errors instead of a
    silent no-op.  After registration, the compat shim is installed if the
    operator has enabled it (``self_wake.compat_shim_enabled: true``) and no
    native capability is present.
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
    # context (prompt-cache safe); it only maintains a local recent-session
    # diagnostics cache for operators.
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

    # Install the compat shim if the operator has opted in and no native
    # capability is present.  Best-effort: a refusal (disabled / drift /
    # native-present) is logged, not raised — the plugin still loads and the
    # capability probe reports the resulting mode honestly.
    try:
        report = compat_shim.install_shim()
        reason = report.get("reason", "")
        if report.get("installed"):
            logger.info("self-wake compat shim installed: %s", reason)
        elif reason in {"native_capability_present", "shim_disabled_by_config",
                        "already_installed"}:
            logger.debug("self-wake compat shim not installed: %s", reason)
        else:
            logger.warning("self-wake compat shim not installed: %s", reason)
    except Exception as exc:  # noqa: BLE001
        logger.warning("self-wake compat shim install raised: %s", exc)

    logger.info("self-wake plugin loaded")

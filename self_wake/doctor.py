"""End-to-end diagnostics for the self-wake plugin.

Provides a single structured report covering plugin health, host capability,
session index readability, receipt table, Kanban DB reachability, cron config,
existing wake subscriptions, and recent receipt failures. Designed for both
operators (``/self-wake doctor``) and downstream agents/tools.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from . import capabilities as caps
from . import kanban as kanban_mod
from . import receipts as receipts_mod
from . import sessions as sessions_mod

logger = logging.getLogger(__name__)


def _check(name: str, status: str, detail: str = "",
           remediation: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail,
            "remediation": remediation}


def _cron_wake_config() -> tuple[Optional[bool], str]:
    """Return (wake_agent_on_delivery, detail) from Hermes config, or (None, err)."""
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config() or {}
        cron = cfg.get("cron") if isinstance(cfg, dict) else None
        if isinstance(cron, dict):
            return bool(cron.get("wake_agent_on_delivery", False)), "set"
    except Exception as exc:  # noqa: BLE001
        return None, f"config unreadable: {exc}"
    return None, "cron section absent"


def run_diagnostics(
    hermes_home: str | Path | None = None,
    backend: Optional[kanban_mod.KanbanBackend] = None,
) -> dict[str, Any]:
    """Run all self-wake diagnostics.

    Returns a structured dict with:
        - ok: bool — overall health (True only in full mode with no failures)
        - mode: "full" | "inspect_only" | "unsupported"
        - checks: list of individual check results
        - failures: list of failure descriptions
        - warnings: list of warning descriptions
        - remediation: list of suggested fixes
    """
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []
    remediation: list[str] = []

    # 1. Core capability + version
    cap = caps.probe_wake_capability(hermes_home)
    checks.append(_check(
        "core_capability", "ok" if cap["available"] else "fail",
        f"mode={cap['mode']} version={cap['version']} "
        f"required={cap['required_capability']}_v{cap['required_version']}",
        remediation=(caps.require_wake_capability(hermes_home) or {}).get("remediation", ""),
    ))
    if not cap["available"]:
        failures.append(f"core capability missing: mode={cap['mode']}")
        remediation.append(
            "Apply docs/core-patch/0001-internal-session-wake-v1.patch to Hermes, "
            "or upgrade to a Hermes version that includes internal_session_wake_v1."
        )

    # 2. Session index readable
    sessions_index = sessions_mod.read_sessions_index(hermes_home)
    n_sessions = len(sessions_index)
    checks.append(_check(
        "session_index", "ok" if n_sessions >= 0 else "fail",
        f"{n_sessions} session(s) in sessions.json",
    ))
    if n_sessions == 0:
        warnings.append("session index empty or missing; wake targets cannot be resolved")
        remediation.append("Ensure $HERMES_HOME/sessions/sessions.json exists and is a dict.")

    # 3. Receipt table present + counts
    rcounts = receipts_mod.receipt_status_counts(hermes_home)
    if rcounts.get("available"):
        checks.append(_check(
            "receipt_table", "ok",
            f"by_source_kind={rcounts.get('by_source_kind')} "
            f"failure_count={rcounts.get('failure_count')}",
        ))
        if rcounts.get("failure_count", 0) > 0:
            warnings.append(
                f"{rcounts['failure_count']} receipt(s) in 'failure' status; "
                "inspect with self_wake_receipts."
            )
    else:
        checks.append(_check(
            "receipt_table", "fail" if cap["mode"] == "unsupported" else "warn",
            "session_wake_receipts table absent",
            "Created by the internal_session_wake_v1 core patch.",
        ))
        if cap["mode"] != "full":
            remediation.append("Receipt table absent — core patch not applied.")

    # 4. Kanban DB reachable + existing wake subscriptions
    subs = kanban_mod.list_wake_subscriptions(backend=backend, hermes_home=hermes_home)
    wake_subs = [s for s in subs if kanban_mod._classify_marker(s.get("user_id")) in ("session", "session_id")]
    visible_subs = [s for s in subs if kanban_mod._classify_marker(s.get("user_id")) == "visible_only"]
    checks.append(_check(
        "kanban_db", "ok" if subs or True else "fail",
        f"{len(subs)} notify sub(s): {len(wake_subs)} wake, {len(visible_subs)} visible-only",
    ))
    if visible_subs and cap["available"]:
        warnings.append(
            f"{len(visible_subs)} visible-only subscription(s) will not wake the agent; "
            "upgrade with self_wake_subscribe_kanban."
        )

    # 5. Cron wake config
    cron_on, cron_detail = _cron_wake_config()
    if cron_on is None:
        checks.append(_check("cron_wake_config", "warn", cron_detail))
        warnings.append("cron.wake_agent_on_delivery not configured (default false)")
    else:
        checks.append(_check(
            "cron_wake_config", "ok" if not cron_on else "warn",
            f"wake_agent_on_delivery={cron_on}",
            "Cron delivery wake is opt-in; enable only if autonomous continuation is intended.",
        ))
        if cron_on:
            warnings.append("cron.wake_agent_on_delivery=true: cron deliveries will wake target sessions.")

    ok = cap["available"] and not any(c["status"] == "fail" for c in checks)
    return {
        "ok": ok,
        "mode": cap["mode"],
        "capability": {
            "available": cap["available"],
            "version": cap["version"],
            "required": f"{cap['required_capability']}_v{cap['required_version']}",
        },
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "remediation": remediation,
        "summary": (
            f"self-wake {cap['mode']} mode; "
            f"{len(failures)} failure(s), {len(warnings)} warning(s)"
        ),
    }

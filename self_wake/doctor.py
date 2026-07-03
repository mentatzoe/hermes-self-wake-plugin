"""End-to-end diagnostics for the self-wake plugin.

Provides a single structured report covering plugin health, host capability,
session resolver readability, receipt table, Kanban DB reachability, cron config,
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


# Categories returned by :func:`_classify_capability_failure`. They distinguish
# "Hermes was upgraded and internal module paths changed" from "the core patch
# was never applied" from "the receipt table is absent" — three failure modes
# that previously all looked like an undifferentiated ``core capability
# missing`` and would lead an operator to re-apply the patch when the real
# issue was a module-path change on upgrade.
CAP_FAIL_IMPORT_CHANGED = "import_changed"
CAP_FAIL_ATTRIBUTE_MISSING = "attribute_missing"
CAP_FAIL_RECEIPT_TABLE_ABSENT = "receipt_table_absent"
CAP_FAIL_UNKNOWN = "unknown"


def _classify_capability_failure(details: list[dict[str, Any]]) -> tuple[str, str]:
    """Categorize why the wake capability probe failed.

    Returns ``(category, summary)``. ``summary`` is a short human-readable
    string naming the first failing probe and its reason. When the capability
    is fully available the category is ``CAP_FAIL_UNKNOWN`` with an empty
    summary — callers should only invoke this when ``available`` is False.
    """
    for d in details:
        if d.get("available"):
            continue
        probe = str(d.get("probe") or "")
        reason = str(d.get("reason") or "")
        if "not importable" in reason:
            return CAP_FAIL_IMPORT_CHANGED, f"{probe}: {reason}"
        if ("not present" in reason or "missing methods" in reason
                or "missing required params" in reason
                or "signature not introspectable" in reason
                or "return contract drift" in reason):
            return CAP_FAIL_ATTRIBUTE_MISSING, f"{probe}: {reason}"
        if ("table absent" in reason or "state.db not found" in reason
                or "state.db unreadable" in reason):
            return CAP_FAIL_RECEIPT_TABLE_ABSENT, f"{probe}: {reason}"
        return CAP_FAIL_UNKNOWN, f"{probe}: {reason}"
    return CAP_FAIL_UNKNOWN, ""


def _capability_remediation(category: str) -> str:
    """Return category-specific operator guidance for a missing capability."""
    if category == CAP_FAIL_IMPORT_CHANGED:
        return (
            "Host capability probe failed on import — Hermes may have been "
            "upgraded and the internal module paths the plugin probes "
            "(gateway.run, hermes_state, gateway.session) may have changed. "
            "Re-check the plugin against the new Hermes version, or pin Hermes "
            "to a commit compatible with internal_session_wake_v1. Re-applying "
            "the core patch will NOT fix a module-path change."
        )
    if category == CAP_FAIL_ATTRIBUTE_MISSING:
        return (
            "Enable the bundled compat shim (self_wake.compat_shim_enabled: "
            "true in config.yaml) to provide internal_session_wake_v1 on "
            "vanilla Hermes without patching core, apply the optional core "
            "patch from docs/core-patch/, or upgrade to a Hermes version that "
            "includes it. The host imports resolved, but the wake_session "
            "primitive / receipt methods are not present."
        )
    if category == CAP_FAIL_RECEIPT_TABLE_ABSENT:
        return (
            "The session_wake_receipts table is absent from state.db. Enable "
            "the compat shim (self_wake.compat_shim_enabled: true) which "
            "creates it, apply the optional core patch from docs/core-patch/, "
            "or upgrade Hermes."
        )
    return (
        "Enable the bundled compat shim (self_wake.compat_shim_enabled: true "
        "in config.yaml), apply the optional core patch from docs/core-patch/, "
        "or upgrade to a Hermes version that includes internal_session_wake_v1."
    )


def _shim_status() -> dict:
    """Report compat shim status for the doctor.

    Returns ``{status, detail, remediation?}`` where status is one of:
    ``ok`` (shim installed and providing the capability), ``info`` (shim not
    installed for a benign reason: disabled by config, native capability
    present, or already-patched host), ``fail`` (shim was attempted but
    refused on drift — the operator must act).
    """
    try:
        from . import compat_shim
        from . import config as cfg_mod
    except Exception as exc:  # noqa: BLE001
        return {"status": "info", "detail": f"shim module unavailable: {exc}"}

    status = compat_shim.shim_status()
    report = status.get("report") or {}
    reason = report.get("reason", "")

    if status.get("installed"):
        return {"status": "ok",
                "detail": "compat shim installed; providing internal_session_wake_v1"}
    if reason == "native_capability_present":
        return {"status": "info",
                "detail": "native capability present; shim not needed"}
    if reason == "shim_disabled_by_config":
        enabled = cfg_mod.get_bool("compat_shim_enabled", False)
        return {
            "status": "info",
            "detail": "shim disabled by config (self_wake.compat_shim_enabled="
                      f"{enabled})",
            "remediation": (
                "Set self_wake.compat_shim_enabled: true in config.yaml to "
                "provide the wake capability on vanilla Hermes without patching "
                "core, then restart the gateway."
            ),
        }
    if reason == "drift_detected":
        drift = report.get("drift", {})
        return {
            "status": "fail",
            "detail": (
                "shim refused to install: private Hermes internals drifted "
                f"({drift.get('reason', 'unknown')}). Plugin stays "
                "inspect-only; update the plugin or apply the core patch."
            ),
            "remediation": (
                "The shim's drift check failed — Hermes internals changed. "
                "Update the self-wake plugin to a version matching this Hermes, "
                "or apply the optional core patch from docs/core-patch/."
            ),
        }
    return {"status": "info", "detail": f"shim not installed ({reason or 'idle'})"}


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
    cap_category, cap_summary = (
        _classify_capability_failure(cap.get("details", []))
        if not cap["available"] else (CAP_FAIL_UNKNOWN, "")
    )
    cap_source = cap.get("source", "absent")
    cap_detail = (
        f"mode={cap['mode']} version={cap['version']} source={cap_source} "
        f"required={cap['required_capability']}_v{cap['required_version']}"
    )
    if not cap["available"]:
        cap_detail = f"{cap_detail} — {cap_category}: {cap_summary}"
    checks.append(_check(
        "core_capability", "ok" if cap["available"] else "fail",
        cap_detail,
        remediation=(_capability_remediation(cap_category)
                     if not cap["available"] else ""),
    ))
    if not cap["available"]:
        failures.append(
            f"core capability missing: mode={cap['mode']} "
            f"({cap_category}: {cap_summary})"
        )
        remediation.append(_capability_remediation(cap_category))

    # 1b. Compat shim status (informational): reports whether the shim is
    # installed, disabled, refused on drift, or not needed (native present).
    shim_info = _shim_status()
    shim_detail = shim_info["detail"]
    checks.append(_check(
        "compat_shim", shim_info["status"], shim_detail,
        remediation=shim_info.get("remediation", ""),
    ))
    if shim_info["status"] == "fail":
        warnings.append(shim_detail)

    # 2. Host session resolver readable
    resolver_entries = sessions_mod.read_current_session_cache(hermes_home)
    n_sessions = len(resolver_entries)
    checks.append(_check(
        "session_resolver", "ok" if n_sessions > 0 else "warn",
        f"{n_sessions} session(s) via current_session_cache_adapter",
    ))
    if n_sessions == 0:
        warnings.append("session resolver empty or unavailable; wake targets cannot be resolved")
        remediation.append(
            "Ensure the Hermes gateway has at least one current session and its "
            "current-session cache exists; future host resolver adapters can "
            "replace this current-Hermes fallback."
        )

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
            "Created by the internal_session_wake_v1 capability (native or shim).",
        ))
        if cap["mode"] != "full":
            # The capability-failure remediation already covers the root cause
            # (import_changed vs attribute_missing vs receipt_table_absent);
            # keep this note table-specific so it never contradicts that
            # categorization.
            remediation.append(
                "Receipt table absent — created by the internal_session_wake_v1 "
                "core patch; see the core_capability remediation for the "
                "specific failure category."
            )

    # 4. Kanban DB reachable + existing wake subscriptions
    # Resolve the backend explicitly so a missing/unreachable Kanban DB is
    # reported as a failure rather than masked by list_wake_subscriptions
    # swallowing all errors and returning [] (the old
    # ``subs or True`` check could never report "fail").
    kb_backend, kb_err = (
        (backend, None) if backend is not None
        else kanban_mod.try_default_backend()
    )
    if kb_backend is None:
        checks.append(_check(
            "kanban_db", "fail",
            f"kanban backend unavailable: {kb_err or 'unknown'}",
            "Ensure hermes_cli.kanban_db is importable and the board DB "
            "is reachable in this Hermes process.",
        ))
        subs: list[dict[str, Any]] = []
        wake_subs: list[dict[str, Any]] = []
        visible_subs: list[dict[str, Any]] = []
    else:
        subs = kanban_mod.list_wake_subscriptions(backend=kb_backend, hermes_home=hermes_home)
        wake_subs = [s for s in subs if kanban_mod._classify_marker(s.get("user_id")) in ("session", "session_id")]
        visible_subs = [s for s in subs if kanban_mod._classify_marker(s.get("user_id")) == "visible_only"]
        checks.append(_check(
            "kanban_db", "ok",
            f"{len(subs)} notify sub(s): {len(wake_subs)} wake, "
            f"{len(visible_subs)} visible-only",
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

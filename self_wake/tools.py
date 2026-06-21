"""Tool handlers for the self-wake plugin.

Each handler receives ``(args: dict, **kwargs)`` and returns a JSON string.
Handlers catch all errors and return structured error responses — they never
raise, per the Hermes plugin tool contract.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import capabilities as caps
from . import doctor as doctor_mod
from . import kanban as kanban_mod
from . import receipts as receipts_mod
from . import sessions as sessions_mod

logger = logging.getLogger(__name__)


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"success": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def sessions_handler(args: dict[str, Any], **kwargs: Any) -> str:
    """Resolve candidate target sessions (read-only; any capability mode)."""
    del kwargs
    try:
        matches = sessions_mod.query_host_sessions(
            session_id=args.get("session_id") or None,
            session_key=args.get("session_key") or None,
            platform=args.get("platform") or None,
            chat_id=args.get("chat_id") or None,
            thread_id=args.get("thread_id") or None,
            query=args.get("query") or None,
            limit=args.get("limit") or sessions_mod.DEFAULT_LIMIT,
        )
        cap = caps.probe_wake_capability()
        return _ok({
            "success": True,
            "tool": "self_wake_sessions",
            "count": len(matches),
            "capability_mode": cap["mode"],
            "resolver_source": sessions_mod.resolver_source(),
            "sessions": matches,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception("self_wake_sessions failed")
        return _err(str(exc), tool="self_wake_sessions")


def subscribe_handler(args: dict[str, Any], **kwargs: Any) -> str:
    """Create or upgrade a Kanban wake subscription (fail-closed)."""
    del kwargs
    try:
        result = kanban_mod.create_wake_subscription(
            task_id=args.get("task_id"),
            target_session_key=args.get("session_key") or None,
            target_session_id=args.get("session_id") or None,
            board=args.get("board") or None,
            platform=args.get("platform") or None,
            chat_id=args.get("chat_id") or None,
            thread_id=args.get("thread_id") or None,
            notifier_profile=args.get("notifier_profile") or None,
            reset_cursor=bool(args.get("reset_cursor")),
            dry_run=bool(args.get("dry_run")),
            force_degraded_visible_only=bool(args.get("force_degraded_visible_only")),
        )
        result.setdefault("tool", "self_wake_subscribe_kanban")
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("self_wake_subscribe_kanban failed")
        return _err(str(exc), tool="self_wake_subscribe_kanban")


def receipts_handler(args: dict[str, Any], **kwargs: Any) -> str:
    """Inspect wake receipts (capability_missing if table absent)."""
    del kwargs
    try:
        result = receipts_mod.query_receipts(
            receipt_id=args.get("receipt_id"),
            session_key=args.get("session_key") or None,
            session_id=args.get("session_id") or None,
            source_kind=args.get("source_kind") or None,
            status=args.get("status") or None,
            dedupe_key=args.get("dedupe_key") or None,
            limit=args.get("limit") or receipts_mod.DEFAULT_LIMIT,
        )
        result.setdefault("tool", "self_wake_receipts")
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("self_wake_receipts failed")
        return _err(str(exc), tool="self_wake_receipts")


def doctor_handler(args: dict[str, Any], **kwargs: Any) -> str:
    """End-to-end diagnostics for self-wake setup."""
    del kwargs
    try:
        report = doctor_mod.run_diagnostics()
        report.setdefault("tool", "self_wake_doctor")
        return _ok(report)
    except Exception as exc:  # noqa: BLE001
        logger.exception("self_wake_doctor failed")
        return _err(str(exc), tool="self_wake_doctor", ok=False,
                    failures=[f"doctor crashed: {exc}"], warnings=[],
                    remediation=["report this crash to the plugin maintainer"])


def record_recent_session(session_id: str = "", platform: str = "",
                          sender_id: str = "", **kwargs: Any) -> None:
    """Best-effort ``pre_llm_call`` recent-session diagnostics cache.

    Returns ``None`` so it never injects prompt context (prompt-cache safe).
    The cache is local operator diagnostics only; it is not a canonical ledger
    and is not required for wake correctness. Failures are swallowed.
    """
    del kwargs
    if not session_id:
        return None
    try:
        import json as _json
        from datetime import datetime, timezone

        sessions = sessions_mod.read_current_session_cache()
        found_key = ""
        found_entry = None
        for key, entry in sessions.items():
            if str(entry.get("session_id") or "") == str(session_id):
                found_key = key
                found_entry = entry
                break
        path = sessions_mod._hermes_home() / "self-wake" / "recent_sessions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            data = []
        if not isinstance(data, list):
            data = []
        summary = sessions_mod._entry_summary(
            found_key, found_entry or {"session_id": session_id, "platform": platform})
        summary["observed_at"] = datetime.now(timezone.utc).isoformat()
        summary["sender_id"] = sender_id or ""
        data = [x for x in data
                if not (isinstance(x, dict) and x.get("session_id") == session_id)]
        data.insert(0, summary)
        path.write_text(_json.dumps(data[:100], indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("self-wake: recent session hook failed: %s", exc)
    return None

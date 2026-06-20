"""Kanban subscription read/write/migration helpers for the self-wake plugin.

Manages ``kanban_notify_subs`` rows whose ``user_id`` is a wake marker:
``session:<session_key>`` or ``session_id:<session_id>``. A plain row with no
marker delivers a visible platform notification but does NOT wake the agent.

Fail-closed contract:
    - ``create_wake_subscription`` refuses to write a ``session:`` marker when
      the host lacks ``internal_session_wake_v1`` (it would create a row that
      looks subscribed but will never wake). It returns ``capability_missing``
      instead. ``dry_run`` is always allowed and reports the capability mode.
    - The only way to write a visible-only row (no marker) on a host without
      wake capability is the explicit ``force_degraded_visible_only`` opt-in,
      which is loudly flagged in the result.

DB access is isolated behind :class:`KanbanBackend`. The production backend
delegates to ``hermes_cli.kanban_db``; tests inject a SQLite double. Cursor
reset and verification reads use parameterized SQL run on the backend's
connection, mirroring the design's "parameterized SQL only for cursor
reset/verification" rule.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional, Protocol

from . import capabilities as caps
from . import sessions as sessions_mod

logger = logging.getLogger(__name__)

DEFAULT_BOARD = "default"


class KanbanBackend(Protocol):
    """Abstract kanban DB access. Production delegates to hermes_cli.kanban_db."""

    def connect(self, board: Optional[str] = None) -> sqlite3.Connection: ...

    def add_notify_sub(
        self, conn: sqlite3.Connection, *, task_id: str, platform: str,
        chat_id: str, thread_id: Optional[str] = None, user_id: Optional[str] = None,
        notifier_profile: Optional[str] = None,
    ) -> None: ...

    def close(self, conn: sqlite3.Connection) -> None: ...


class HermesKanbanBackend:
    """Production backend: delegates to ``hermes_cli.kanban_db``.

    Import is lazy so the plugin loads on hosts where the kanban module is not
    yet available; ``get_default_backend`` surfaces the failure as a structured
    error rather than crashing registration.
    """

    def __init__(self) -> None:
        from hermes_cli import kanban_db as kb  # type: ignore

        self._kb = kb

    def connect(self, board: Optional[str] = None) -> sqlite3.Connection:
        return self._kb.connect(board=board)

    def add_notify_sub(self, conn: sqlite3.Connection, *, task_id: str,
                       platform: str, chat_id: str, thread_id: Optional[str] = None,
                       user_id: Optional[str] = None,
                       notifier_profile: Optional[str] = None) -> None:
        self._kb.add_notify_sub(
            conn, task_id=task_id, platform=platform, chat_id=chat_id,
            thread_id=thread_id, user_id=user_id, notifier_profile=notifier_profile,
        )

    def close(self, conn: sqlite3.Connection) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


_default_backend: Optional[KanbanBackend] = None
_default_backend_error: Optional[str] = None


def get_default_backend() -> KanbanBackend:
    """Return the cached production backend, raising only if import failed.

    Callers that want graceful degradation should call
    :func:`try_default_backend` instead.
    """
    global _default_backend, _default_backend_error
    if _default_backend is None:
        if _default_backend_error is not None:
            raise RuntimeError(_default_backend_error)
        try:
            _default_backend = HermesKanbanBackend()
        except Exception as exc:  # noqa: BLE001
            _default_backend_error = str(exc)
            raise
    return _default_backend


def try_default_backend() -> tuple[Optional[KanbanBackend], Optional[str]]:
    """Return ``(backend, error)``. ``backend`` is None when kanban is unavailable."""
    global _default_backend, _default_backend_error
    if _default_backend is not None:
        return _default_backend, None
    if _default_backend_error is not None:
        return None, _default_backend_error
    try:
        _default_backend = HermesKanbanBackend()
        return _default_backend, None
    except Exception as exc:  # noqa: BLE001
        _default_backend_error = str(exc)
        return None, _default_backend_error


def set_default_backend(backend: Optional[KanbanBackend]) -> None:
    """Test hook: inject or clear the default backend."""
    global _default_backend, _default_backend_error
    _default_backend = backend
    _default_backend_error = None if backend is not None else "backend cleared"


def _resolve_backend(backend: Optional[KanbanBackend]) -> tuple[Optional[KanbanBackend], Optional[str]]:
    if backend is not None:
        return backend, None
    return try_default_backend()


def _classify_marker(user_id: Any) -> str:
    """Classify a notify sub row's wake marker type."""
    if not user_id:
        return "visible_only"
    s = str(user_id)
    if s.startswith("session_id:"):
        return "session_id"
    if s.startswith("session:"):
        return "session"
    return "visible_only"


def _fetch_notify_row(conn: sqlite3.Connection, task_id: str, platform: str,
                      chat_id: str, thread_id: str) -> Optional[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM kanban_notify_subs "
        "WHERE task_id=? AND platform=? AND chat_id=? AND thread_id=?",
        (task_id, platform, chat_id, thread_id or ""),
    ).fetchone()
    return dict(row) if row else None


def _reset_notify_cursor(conn: sqlite3.Connection, task_id: str, platform: str,
                         chat_id: str, thread_id: str) -> int:
    """Set last_event_id=0 for a notify sub row. Returns rows affected."""
    cur = conn.execute(
        "UPDATE kanban_notify_subs SET last_event_id = 0 "
        "WHERE task_id=? AND platform=? AND chat_id=? AND thread_id=?",
        (task_id, platform, chat_id, thread_id or ""),
    )
    return cur.rowcount or 0


def _active_profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name  # type: ignore

        return str(get_active_profile_name() or "default")
    except Exception:
        import os

        return os.getenv("HERMES_PROFILE") or "default"


def list_wake_subscriptions(
    board: Optional[str] = None,
    backend: Optional[KanbanBackend] = None,
    hermes_home: str | Path | None = None,
) -> list[dict[str, Any]]:
    """List existing Kanban notify subscriptions, classified by marker type.

    Returns ``[]`` when the kanban DB is unavailable or the table is missing.
    Never raises.
    """
    backend, err = _resolve_backend(backend)
    if backend is None:
        logger.debug("self-wake: kanban backend unavailable: %s", err)
        return []
    try:
        conn = backend.connect(board=board)
        try:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM kanban_notify_subs ORDER BY created_at DESC"
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            cap = caps.probe_wake_capability(hermes_home)
            result = []
            for r in rows:
                d = dict(r)
                d["marker_type"] = _classify_marker(d.get("user_id"))
                result.append(d)
            return result
        finally:
            backend.close(conn)
    except Exception as exc:  # noqa: BLE001
        logger.debug("self-wake: list_wake_subscriptions failed: %s", exc)
        return []


def create_wake_subscription(
    task_id: Optional[str],
    target_session_key: Optional[str] = None,
    target_session_id: Optional[str] = None,
    board: Optional[str] = None,
    platform: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    notifier_profile: Optional[str] = None,
    reset_cursor: bool = False,
    dry_run: bool = False,
    force_degraded_visible_only: bool = False,
    capability_report: Optional[dict] = None,
    backend: Optional[KanbanBackend] = None,
    hermes_home: str | Path | None = None,
) -> dict[str, Any]:
    """Create or upgrade a Kanban wake subscription.

    See module docstring for the fail-closed contract. Returns a structured
    dict (never raises) with ``success``, ``dry_run``, the resolved marker,
    before/after row state, and a verification block.
    """
    task_id = (task_id or "").strip()
    if not task_id:
        return {"success": False, "error": "task_id is required"}
    board = (board or DEFAULT_BOARD).strip() or DEFAULT_BOARD

    cap = capability_report if capability_report is not None else caps.probe_wake_capability(hermes_home)
    capability_ok = bool(cap.get("available") and cap.get("version") and cap["version"] >= 1)

    # Resolve target session (read-only; allowed in any mode).
    matches = sessions_mod.resolve_target_session(
        session_key=target_session_key, session_id=target_session_id,
        platform=platform, chat_id=chat_id, thread_id=thread_id,
        hermes_home=hermes_home,
    )
    if sessions_mod.is_ambiguous(matches):
        return {
            "success": False,
            "error": "ambiguous_session",
            "capability_mode": cap["mode"],
            "matches": matches,
            "remediation": "session filters matched multiple entries; "
                           "pass an explicit session_key or session_id.",
        }

    entry = matches[0] if matches else {}
    resolved_key = (entry.get("session_key") or target_session_key or "").strip()
    resolved_session_id = (entry.get("session_id") or target_session_id or "").strip()

    origin = entry.get("origin") or {}
    platform = str(platform or entry.get("platform") or origin.get("platform") or "discord").strip().lower()
    chat_id = str(chat_id or origin.get("chat_id") or "").strip()
    thread_id = str(thread_id or origin.get("thread_id") or "").strip()

    if not chat_id:
        return {
            "success": False,
            "error": "chat_id_required",
            "capability_mode": cap["mode"],
            "remediation": "chat_id is required when the target session has no "
                           "stored origin; pass chat_id explicitly.",
        }

    # Determine the marker.
    visible_only = bool(force_degraded_visible_only)
    if visible_only:
        marker = None
        target_kind = "visible_only"
        target_value = None
    elif resolved_key:
        marker = f"session:{resolved_key}"
        target_kind = "session_key"
        target_value = resolved_key
    elif resolved_session_id:
        marker = f"session_id:{resolved_session_id}"
        target_kind = "session_id"
        target_value = resolved_session_id
    else:
        return {
            "success": False,
            "error": "unresolved_target",
            "capability_mode": cap["mode"],
            "remediation": "could not resolve a target session; pass session_key, "
                           "session_id, or enough platform/chat/thread filters.",
        }

    # Fail-closed gate: a session:/session_id: marker requires the wake
    # capability. dry_run is always allowed (it previews only).
    if not dry_run and not visible_only and not capability_ok:
        err = caps.require_wake_capability(hermes_home) or {}
        return {
            "success": False,
            "error": "capability_missing",
            "mode": cap["mode"],
            "required_capability": err.get("required_capability", caps.REQUIRED_CAPABILITY),
            "required_version": err.get("required_version", caps.REQUIRED_VERSION),
            "would_write_marker": marker,
            "remediation": err.get("remediation", ""),
        }

    notifier_profile = (notifier_profile or _active_profile_name()).strip() or "default"

    # Resolve backend (needed for before/after even in dry_run, but degrade if
    # unavailable — dry_run still reports the planned marker).
    kanban_backend, kb_err = _resolve_backend(backend)

    before: Optional[dict[str, Any]] = None
    if kanban_backend is not None:
        try:
            conn = kanban_backend.connect(board=board)
            try:
                before = _fetch_notify_row(conn, task_id, platform, chat_id, thread_id)
            finally:
                kanban_backend.close(conn)
        except Exception as exc:  # noqa: BLE001
            logger.debug("self-wake: before-row fetch failed: %s", exc)

    after: Optional[dict[str, Any]] = None
    reset_applied = False
    if not dry_run:
        if kanban_backend is None:
            return {
                "success": False,
                "error": "kanban_unavailable",
                "capability_mode": cap["mode"],
                "detail": kb_err or "kanban backend unavailable",
                "remediation": "ensure hermes_cli.kanban_db is importable in "
                               "this Hermes process.",
            }
        try:
            conn = kanban_backend.connect(board=board)
            try:
                kanban_backend.add_notify_sub(
                    conn, task_id=task_id, platform=platform, chat_id=chat_id,
                    thread_id=thread_id or None, user_id=marker,
                    notifier_profile=notifier_profile,
                )
                if reset_cursor:
                    reset_applied = _reset_notify_cursor(
                        conn, task_id, platform, chat_id, thread_id) > 0
                after = _fetch_notify_row(conn, task_id, platform, chat_id, thread_id)
            finally:
                kanban_backend.close(conn)
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "error": "kanban_write_failed",
                "capability_mode": cap["mode"],
                "detail": str(exc),
            }

    written_user_id = (after or {}).get("user_id") if not dry_run else marker
    internal_wake_enabled = bool(
        written_user_id and str(written_user_id).startswith(("session:", "session_id:"))
    )

    return {
        "success": True,
        "dry_run": dry_run,
        "board": board,
        "task_id": task_id,
        "platform": platform,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "target_kind": target_kind,
        "target_value": target_value,
        "user_id_marker": marker,
        "notifier_profile": notifier_profile,
        "reset_cursor": reset_cursor,
        "reset_cursor_applied": reset_applied,
        "capability_mode": cap["mode"],
        "capability_ok": capability_ok,
        "visible_only": visible_only,
        "before": before,
        "after": after,
        "verification": {
            "internal_wake_enabled": internal_wake_enabled,
            "warning": (
                "visible-only subscription: agent will NOT be woken; this row "
                "only sends a platform notification."
            ) if visible_only else None,
            "check_receipts_with": {
                "target_session_key": resolved_key or None,
                "target_session_id": resolved_session_id or None,
                "source_kind": "kanban",
            },
        },
    }


def migrate_visible_rows(
    board: Optional[str] = None,
    dry_run: bool = True,
    backend: Optional[KanbanBackend] = None,
    capability_report: Optional[dict] = None,
    hermes_home: str | Path | None = None,
) -> dict[str, Any]:
    """Find visible-only notify rows and report (or upgrade) them to wake rows.

    Upgrade requires the wake capability. In dry_run (default) or when the
    capability is missing, only reports. Never raises.
    """
    cap = capability_report if capability_report is not None else caps.probe_wake_capability(hermes_home)
    capability_ok = bool(cap.get("available") and cap.get("version") and cap["version"] >= 1)
    rows = list_wake_subscriptions(board=board, backend=backend, hermes_home=hermes_home)
    visible = [r for r in rows if _classify_marker(r.get("user_id")) == "visible_only"]

    if dry_run or not capability_ok:
        return {
            "success": True,
            "dry_run": dry_run,
            "capability_mode": cap["mode"],
            "capability_ok": capability_ok,
            "rows_found": len(visible),
            "visible_only_rows": visible,
            "upgraded": 0,
            "remediation": (
                "Re-run with dry_run=false on a host with internal_session_wake_v1 "
                "to upgrade visible-only rows to wake subscriptions."
            ) if not capability_ok else None,
        }

    # Non-dry_run upgrade path: re-subscribe each visible row targeting its own
    # session. We cannot infer a session_key from a visible-only row alone, so
    # upgrade is only possible for rows whose task_id maps to a resolvable
    # session — left to the operator via create_wake_subscription. Report only.
    return {
        "success": True,
        "dry_run": False,
        "capability_mode": cap["mode"],
        "capability_ok": capability_ok,
        "rows_found": len(visible),
        "visible_only_rows": visible,
        "upgraded": 0,
        "remediation": "Automatic upgrade is not safe without a target session. "
                       "Call create_wake_subscription per row with an explicit "
                       "session_key/session_id to upgrade each visible-only row.",
    }

"""Receipt query and table-missing handling for the self-wake plugin.

Reads ``session_wake_receipts`` from ``$HERMES_HOME/state.db`` with graceful
degradation when the table (or the whole capability) is absent. The receipts
table is created by the host wake capability (native or compat shim); on vanilla Hermes it does not exist,
so queries return a structured ``capability_missing`` response rather than a
raw SQL exception.

Receipts carry bounded payload previews + hashes (not full payloads). This
module further truncates the preview in operator output to
``receipt_preview_max_chars`` (default 200) so cron/send_message bodies are
not echoed verbatim by default.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from .capabilities import _state_db_path
from .config import get_int

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
HARD_LIMIT = 200


def _safe_int(value: Any, default: int, *, lo: int = 1, hi: int = HARD_LIMIT) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _receipt_table_exists(db: Path) -> bool:
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='session_wake_receipts'"
            ).fetchone()
        finally:
            conn.close()
        return bool(row)
    except Exception:  # noqa: BLE001
        return False


def _truncate_preview(preview: Any, max_chars: int) -> str:
    if preview is None:
        return ""
    s = str(preview)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…"


def query_receipts(
    receipt_id: str | int | None = None,
    session_key: str | None = None,
    session_id: str | None = None,
    source_kind: str | None = None,
    status: str | None = None,
    dedupe_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    hermes_home: str | Path | None = None,
) -> dict[str, Any]:
    """Query session wake receipts.

    Returns a dict with ``success``, ``mode``, ``receipts`` list, and
    ``count``. When the ``session_wake_receipts`` table is absent (vanilla
    Hermes, inspect-only mode), returns ``capability_missing`` with
    remediation instead of raising.
    """
    db = _state_db_path(hermes_home)
    if not db.exists():
        return {
            "success": False,
            "error": "capability_missing",
            "mode": "unsupported",
            "reason": f"state.db not found at {db}",
            "receipts": [],
            "count": 0,
            "remediation": (
                "state.db does not exist. The self-wake receipt table is created "
                "by the internal_session_wake_v1 capability. Enable the compat shim "
                "(self_wake.compat_shim_enabled: true) or apply docs/core-patch/."
            ),
        }
    if not _receipt_table_exists(db):
        return {
            "success": False,
            "error": "capability_missing",
            "mode": "inspect_only",
            "reason": "session_wake_receipts table absent",
            "receipts": [],
            "count": 0,
            "remediation": (
                "The session_wake_receipts table is created by the Hermes "
                "internal_session_wake_v1 capability, which is not present on "
                "this host. Apply the patch from docs/core-patch/ or upgrade "
                "Hermes. Without it, no wake receipts are ever recorded."
            ),
        }

    where: list[str] = []
    params: list[Any] = []
    rid = receipt_id
    if rid not in (None, ""):
        try:
            where.append("id = ?")
            params.append(int(rid))
        except (TypeError, ValueError):
            return {"success": False, "error": "invalid_receipt_id",
                    "receipt_id": rid, "receipts": [], "count": 0}

    filters = {
        "target_session_key": session_key,
        "target_session_id": session_id,
        "source_kind": source_kind,
        "status": status,
        "dedupe_key": dedupe_key,
    }
    for col, val in filters.items():
        v = str(val or "").strip()
        if v:
            where.append(f"{col} = ?")
            params.append(v)

    sql = (
        "SELECT id, source_kind, status, target_session_key, target_session_id, "
        "injected_message_id, assistant_message_id, "
        "datetime(created_at,'unixepoch') AS created_at, "
        "datetime(updated_at,'unixepoch') AS updated_at, "
        "datetime(dispatched_at,'unixepoch') AS dispatched_at, "
        "datetime(responded_at,'unixepoch') AS responded_at, "
        "payload_hash, payload_preview, payload_bytes, error, dedupe_key "
        "FROM session_wake_receipts"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(_safe_int(limit, DEFAULT_LIMIT))

    max_chars = get_int("receipt_preview_max_chars", 200)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": "receipt_query_failed",
            "detail": str(exc),
            "receipts": [],
            "count": 0,
        }

    receipts = []
    for r in rows:
        d = dict(r)
        d["payload_preview"] = _truncate_preview(d.get("payload_preview"), max_chars)
        receipts.append(d)

    return {
        "success": True,
        "mode": "full",
        "receipts": receipts,
        "count": len(receipts),
        "preview_max_chars": max_chars,
    }


def receipt_status_counts(hermes_home: str | Path | None = None) -> dict[str, Any]:
    """Return aggregated receipt counts by source_kind/status for diagnostics.

    Returns ``{available: False}`` when the table is absent (used by doctor).
    """
    db = _state_db_path(hermes_home)
    if not db.exists() or not _receipt_table_exists(db):
        return {"available": False}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT source_kind, status, COUNT(*) AS n "
                "FROM session_wake_receipts GROUP BY source_kind, status"
            ).fetchall()
            recent_failures = conn.execute(
                "SELECT COUNT(*) FROM session_wake_receipts WHERE status='failure'"
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {"available": False}

    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        sk, st, n = r[0], r[1], r[2]
        counts.setdefault(sk or "unknown", {})[st or "unknown"] = int(n)
    return {
        "available": True,
        "by_source_kind": counts,
        "failure_count": int(recent_failures[0]) if recent_failures else 0,
    }

"""Profile-local durable inbox for cron results and explicit session messages.

The database is local IPC for trusted processes in the SAME Hermes home. It
is not a network endpoint or a cross-profile permission mechanism. Admission,
identity checks and deduplication stay in the receipt-backed native adapter.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import logging
import sqlite3
import time
import uuid
from types import SimpleNamespace

from .capabilities import _hermes_home

logger = logging.getLogger(__name__)



def request_allowed(source_kind):
    """Recheck operator permission at submission and before every retry."""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        if source_kind == "session_message":
            return config.get("plugins", {}).get("entries", {}).get("self-wake", {}).get("allow_gateway_injection") is True
        if source_kind == "cron_delivery":
            return config.get("cron", {}).get("wake_agent_on_delivery") is True
    except Exception:
        pass
    return False


@contextmanager
def connect(hermes_home=None):
    home = _hermes_home(hermes_home)
    root = home / "self-wake"
    if root.is_symlink():
        raise ValueError("self-wake state directory must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "outbox.sqlite3"
    if path.is_symlink():
        raise ValueError("self-wake database must not be a symlink")
    conn = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS requests ("
                     "id TEXT PRIMARY KEY, session_key TEXT NOT NULL, session_id TEXT NOT NULL,"
                     "origin TEXT NOT NULL, payload TEXT NOT NULL, source_kind TEXT NOT NULL,"
                     "dedupe_key TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,"
                     "receipt_id INTEGER, error TEXT, UNIQUE(session_key,dedupe_key))")
        with conn:
            yield conn
    finally:
        conn.close()


def enqueue(*, entry, payload, source_kind, dedupe_key, hermes_home=None):
    if source_kind not in {"session_message", "cron_delivery"}:
        raise ValueError("unsupported request source")
    if not isinstance(payload, str) or not payload.strip() or len(payload.encode()) > 100_000:
        raise ValueError("payload must contain 1–100000 bytes of text")
    if not isinstance(dedupe_key, str) or not dedupe_key.strip() or len(dedupe_key) > 500:
        raise ValueError("dedupe_key must contain 1–500 characters")
    origin = entry.origin.to_dict() if hasattr(entry.origin, "to_dict") else entry.origin
    if not entry.session_key or not entry.session_id or not origin:
        raise ValueError("an existing session with a stored origin is required")
    snapshot = json.dumps(origin, sort_keys=True)
    request_id = uuid.uuid4().hex
    with connect(hermes_home) as conn:
        conn.execute("INSERT OR IGNORE INTO requests "
                     "(id,session_key,session_id,origin,payload,source_kind,dedupe_key,status,created_at) "
                     "VALUES (?,?,?,?,?,?,?,'queued',?)",
                     (request_id, entry.session_key, entry.session_id, snapshot, payload,
                      source_kind, dedupe_key, time.time()))
        row = conn.execute("SELECT * FROM requests WHERE session_key=? AND dedupe_key=?",
                           (entry.session_key, dedupe_key)).fetchone()
        if any(row[k] != v for k, v in {"session_id": entry.session_id, "origin": snapshot,
                                       "payload": payload, "source_kind": source_kind}.items()):
            raise ValueError("dedupe key conflict: target, payload or source changed")
        return {"request_id": row["id"], "status": row["status"], "receipt_id": row["receipt_id"]}


def send(*, session_id, payload, dedupe_key, hermes_home=None):
    if not request_allowed("session_message"):
        raise ValueError("enable plugins.entries.self-wake.allow_gateway_injection for session messages")
    try:
        from gateway.config import load_gateway_config
    except ImportError:
        pass
    else:
        if load_gateway_config().multiplex_profiles:
            raise ValueError("explicit session messages require a single-profile gateway; multiplex mode is unsupported")
    from .sessions import read_routing_index
    matches = [dict(value, session_key=key) for key, value in read_routing_index(hermes_home).items()
               if value.get("session_id") == session_id]
    if len(matches) != 1:
        raise ValueError("exactly one existing session must match session_id")
    target = matches[0]
    entry = SimpleNamespace(session_key=target["session_key"], session_id=session_id,
                            origin=target.get("origin"))
    return enqueue(entry=entry, payload=payload, source_kind="session_message",
                   dedupe_key=dedupe_key, hermes_home=hermes_home)


def get(request_id, *, hermes_home=None):
    with connect(hermes_home) as conn:
        row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in ("id", "session_id", "source_kind", "status", "receipt_id", "error")}


def pending_requests(hermes_home=None):
    with connect(hermes_home) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM requests WHERE status IN ('queued','pending') ORDER BY created_at")]


async def drain_request(runner, row, *, hermes_home=None):
    """Receipt claims, not the queue scan, own turn admission."""
    from . import native_wake
    if getattr(runner, "_draining", False) or not request_allowed(row["source_kind"]):
        return
    if getattr(getattr(runner, "config", None), "multiplex_profiles", False):
        with connect(hermes_home) as conn:
            conn.execute("UPDATE requests SET error=? WHERE id=?", ("multiplex gateway inbox unsupported", row["id"]))
        return
    entry = runner.session_store.lookup_by_session_key(row["session_key"])
    if (entry is None or entry.session_id != row["session_id"]
            or json.dumps(entry.origin.to_dict(), sort_keys=True) != row["origin"]):
        result = {"status": "target_changed", "error": "target reset or route changed; not redirected"}
    else:
        try:
            result = await native_wake.wake(
                runner, payload=row["payload"], source_kind=row["source_kind"],
                session_key=row["session_key"], expected_session_id=row["session_id"],
                expected_source=entry.origin, dedupe_key="outbox:" + row["id"],
                require_authorization=True)
        except Exception as exc:
            result = {"status": "failure", "error": str(exc)}
    status = result.get("status", "pending")
    if status == "failure":
        status = "queued"
    with connect(hermes_home) as conn:
        conn.execute("UPDATE requests SET status=?,receipt_id=?,error=? WHERE id=? "
                     "AND status IN ('queued','pending')",
                     (status, result.get("receipt_id"), result.get("error"), row["id"]))


async def drain_once(runner, *, hermes_home=None):
    """One bounded probe pass; the gateway uses the non-blocking watcher."""
    for row in pending_requests(hermes_home)[:50]:
        await drain_request(runner, row, hermes_home=hermes_home)


async def watch(runner, interval=5.0):
    """Run alongside the gateway-owned notifier; stop with its lifecycle."""
    tasks = {}
    attempted = {}
    try:
        while getattr(runner, "_running", False):
            try:
                for request_id, task in list(tasks.items()):
                    if task.done():
                        tasks.pop(request_id)
                        if not task.cancelled() and task.exception():
                            logger.error("self-wake request failed; retained: %s", task.exception())
                rows = pending_requests()
                pending_ids = {row["id"] for row in rows}
                attempted = {key: when for key, when in attempted.items() if key in pending_ids}
                # Rotate failures; a busy or long-running receiver must not
                # prevent unrelated sessions from receiving newly queued work.
                for row in sorted(rows, key=lambda row: attempted.get(row["id"], 0)):
                    if len(tasks) >= 16:
                        break
                    if row["id"] not in tasks:
                        attempted[row["id"]] = time.monotonic()
                        tasks[row["id"]] = asyncio.create_task(drain_request(runner, row))
            except Exception:
                logger.exception("self-wake inbox retry failed; requests retained")
            await asyncio.sleep(interval)
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


def diagnostics(hermes_home=None):
    from .cron_adapter import _active_gateway_runner
    runner = _active_gateway_runner()
    task = getattr(runner, "_self_wake_inbox_task", None)
    running = task is not None and not task.done()
    reason = ""
    if getattr(getattr(runner, "config", None), "multiplex_profiles", False):
        running = False
        reason = "durable inbox requires a single-profile gateway; multiplex mode unsupported"
    enabled = request_allowed("session_message")
    path = _hermes_home(hermes_home) / "self-wake" / "outbox.sqlite3"
    counts = {}
    if path.exists():
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            counts = dict(conn.execute("SELECT status,COUNT(*) FROM requests GROUP BY status"))
        finally:
            conn.close()
    return {"available": running, "enabled": enabled, "source": "plugin_inbox_native_admission",
            "reason": reason,
            "mode": "full" if running and enabled else "disabled" if not enabled else "unavailable",
            "state_path": str(path), "request_counts": counts,
            "command": "hermes self-wake send --session-id ID --dedupe-key KEY --file FILE"}

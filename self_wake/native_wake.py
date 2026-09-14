"""Native Kanban wakes: durable ownership and receipt-bound admission.

Legacy marker/cron dispatch remains in compat_shim. Native wakes never enter
an in-memory busy queue. Ambiguous post-admission outcomes stay pending;
they cannot be automatically called delivered or re-injected.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import time
import uuid
import textwrap


# Hermes dcbf5b71bc65fc6f7168c601bc04e84b22f912cd. These are method
# sources, not a version-string guess. A changed admission path stays pending
# until independently re-audited; no host file is ever patched.
HOST_METHODS = {
    "adapter": {
        "_start_session_processing": "acaab7630943ce50f78aac020c00e427d7e61c114279d7efc7ae9e0845af9ebb",
        "_process_message_background": "1c51144b41fd7e4579a719d6cae7206d1d8d9829bad643dd9c78b1dddacc08ac",
        "_session_key_profile": "846d00dd329de132a0fb1a88a818e182350494c17b7c6ab52d1f7a4e87c816aa",
    },
    "runner": {
        "_session_key_for_source": "173cc97f3a763a09cdc72a352a749b89d1ef5a494353ac38c54c93d00b6cca07",
        "_handle_message": "29ae9cb4b894cf04c342f948d4786be3809c0d1e03d29d51ae2ed8e7157b085a",
        "_handle_message_with_agent": "c2bc54c38caa66775c3df268c5fdc24a3d9a7afa715d71807d62df56768396a8",
    },
}


def check_host(runner, adapter):
    for label, obj in (("runner", runner), ("adapter", adapter)):
        for name, expected in HOST_METHODS[label].items():
            try:
                source = textwrap.dedent(inspect.getsource(getattr(obj, name)))
            except (AttributeError, TypeError, OSError) as exc:
                raise RuntimeError(f"native admission host drift: {label}.{name} unavailable") from exc
            if hashlib.sha256(source.encode()).hexdigest() != expected:
                raise RuntimeError(f"native admission host drift: {label}.{name}")


async def call(value):
    return await value if inspect.isawaitable(value) else value


async def claim(db, *, entry, payload, source_kind, dedupe_key):
    """Create/reclaim in one write transaction; the token fences stale owners."""
    from .compat_shim import _WAKE_RECEIPT_DDL, _INFLIGHT_RETRY_SECONDS
    now = time.time()
    token = uuid.uuid4().hex
    snapshot = json.dumps(entry.origin.to_dict(), sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()

    def write(conn):
        for ddl in _WAKE_RECEIPT_DDL:
            conn.execute(ddl)
        conn.execute("CREATE TABLE IF NOT EXISTS self_wake_claims (receipt_id INTEGER PRIMARY KEY, token TEXT NOT NULL)")
        cursor = conn.execute(
            "INSERT OR IGNORE INTO session_wake_receipts "
            "(created_at,updated_at,requested_at,source_kind,target_session_key,target_session_id,"
            "origin_snapshot,payload_hash,payload_preview,payload_bytes,status,dedupe_key) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'requested',?)",
            (now, now, now, source_kind, entry.session_key, entry.session_id, snapshot,
             digest, payload[:500], len(payload.encode()), dedupe_key))
        created = cursor.rowcount > 0
        if created:
            row = conn.execute("SELECT * FROM session_wake_receipts WHERE id=?", (cursor.lastrowid,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM session_wake_receipts WHERE target_session_key=? AND dedupe_key=?",
                               (entry.session_key, dedupe_key)).fetchone()
        row = dict(row)
        if row["payload_hash"] != digest or row["source_kind"] != source_kind:
            return row, None
        retryable = row["status"] == "failure" or (
            row["status"] == "requested" and now - row["updated_at"] > _INFLIGHT_RETRY_SECONDS)
        if not created and not retryable:
            return row, None
        conn.execute("INSERT OR REPLACE INTO self_wake_claims VALUES (?,?)", (row["id"], token))
        conn.execute("UPDATE session_wake_receipts SET status='requested',updated_at=?,requested_at=?,"
                     "target_session_id=?,origin_snapshot=?,error=NULL WHERE id=?",
                     (now, now, entry.session_id, snapshot, row["id"]))
        row.update(status="requested", updated_at=now, target_session_id=entry.session_id)
        return row, token
    return await call(db._execute_write(write))


async def transition(db, receipt_id, token, status, *, error=None, injected_id=None):
    """Only the owning attempt may change a receipt. dispatching is not ack."""
    def write(conn):
        cursor = conn.execute(
            "UPDATE session_wake_receipts SET status=?,updated_at=?,error=?,injected_message_id=? "
            "WHERE id=? AND EXISTS (SELECT 1 FROM self_wake_claims WHERE receipt_id=? AND token=?)",
            (status, time.time(), error, injected_id, receipt_id, receipt_id, token))
        return cursor.rowcount == 1
    return await call(db._execute_write(write))


async def exact_injection(db, session_id, receipt_id):
    messages = await call(db.get_messages(session_id))
    marker = f"internal-wake:{receipt_id}"
    matches = [m for m in messages if m.get("role") == "user" and m.get("platform_message_id") == marker]
    return int(matches[0]["id"]) if len(matches) == 1 else None


class AdmissionUncertain(RuntimeError):
    """The host may have created work; never retry from this exception alone."""


def admit(runner, adapter, entry, source, event):
    """Validate and acquire the actual adapter guard without yielding.

    _start_session_processing invokes the ordinary runner message handler,
    including authorization; only busy queueing/command coercion are skipped.
    """
    current = runner.session_store.lookup_by_session_key(entry.session_key)
    if (current is None or current.session_id != entry.session_id
            or current.origin.to_dict() != source.to_dict()):
        raise RuntimeError("target session or origin changed before admission")
    if runner._session_key_for_source(source) != entry.session_key:
        raise RuntimeError("stored origin does not derive the requested session")
    profile = getattr(source, "profile", None)
    if runner._authorization_adapter(source.platform, profile) is not adapter:
        raise RuntimeError("target adapter changed before admission")
    if getattr(runner, "_draining", False):
        raise RuntimeError("receiver draining; retry pending")
    task = getattr(adapter, "_session_tasks", {}).get(entry.session_key)
    if (entry.session_key in getattr(adapter, "_active_sessions", {})
            or (task is not None and not task.done())):
        raise RuntimeError("receiver busy; retry pending")
    if not getattr(adapter, "_message_handler", None):
        raise RuntimeError("receiver handler unavailable")
    try:
        started = adapter._start_session_processing(event, entry.session_key)
    except Exception as exc:
        raise AdmissionUncertain("host raised during final admission") from exc
    if not started:
        raise AdmissionUncertain("host did not confirm final admission")
    task = adapter._session_tasks.get(entry.session_key)
    if not isinstance(task, asyncio.Task):
        raise AdmissionUncertain("adapter did not return an owned task")
    return task


async def wake(runner, *, payload, source_kind, session_key=None, session_id=None, dedupe_key=None, expected_source=None):
    from gateway.platforms.base import MessageEvent, MessageType
    db = getattr(runner, "_session_db", None)
    if db is None:
        return {"status": "failure", "error": "session database unavailable"}
    original = runner._lookup_session_entry_for_wake(session_key=session_key, session_id=session_id)
    if original is None or original.origin is None:
        return {"status": "failure", "error": "target session or origin missing"}
    # Copy immutable identity values before the first async DB operation:
    # SessionEntry objects in the store can be mutated in place by a reset.
    from types import SimpleNamespace
    source = dataclasses.replace(original.origin)
    if expected_source is not None:
        for name in ("platform", "chat_id", "chat_type", "thread_id", "user_id", "user_id_alt", "scope_id", "profile"):
            actual = getattr(source, name, None) or None
            expected = getattr(expected_source, name, None) or None
            if name == "profile":
                actual, expected = actual or "default", expected or "default"
            if actual != expected:
                return {"status": "failure", "error": f"subscription origin mismatch: {name}"}
    entry = SimpleNamespace(session_key=original.session_key, session_id=original.session_id, origin=source)
    resolve_db = getattr(runner.session_store, "_db_for_key", None)
    if callable(resolve_db):
        target_db = await asyncio.to_thread(resolve_db, entry.session_key)
    else:
        target_db = db
    if target_db is None:
        return {"status": "failure", "error": "target profile message store unavailable"}
    row, token = await claim(db, entry=entry, payload=payload, source_kind=source_kind, dedupe_key=dedupe_key)
    rid = int(row["id"])
    result = {"receipt_id": rid, "target_session_key": entry.session_key,
              "target_session_id": row["target_session_id"]}
    if row["payload_hash"] != hashlib.sha256(payload.encode()).hexdigest() or row["source_kind"] != source_kind:
        return dict(result, status="failure", error="dedupe key conflicts with existing payload/source")
    if token is None:
        if row["status"] == "dispatched" and row.get("injected_message_id"):
            return dict(result, status="deduped")
        # Recovery is read-only: an ambiguous attempt may finish after caller
        # cancellation or process interruption. Never infer success from age.
        if row["status"] in {"dispatching", "pending"}:
            injected = await exact_injection(target_db, row["target_session_id"], rid)
            if injected is not None:
                return dict(result, status="deduped", injected_message_id=injected)
        return dict(result, status="pending", error="receipt not yet confirmed")
    adapter = runner._authorization_adapter(source.platform, getattr(source, "profile", None))
    event = MessageEvent(text=payload, message_type=MessageType.TEXT, source=source,
                         internal=True, message_id=f"internal-wake:{rid}")
    event.allow_gateway_control = False
    event.metadata = {"gateway_session_key": entry.session_key,
                      "gateway_session_id": entry.session_id, "gateway_session_strict": True}
    if not await transition(db, rid, token, "dispatching"):
        return dict(result, status="pending", error="attempt ownership changed")
    try:
        if adapter is None:
            raise RuntimeError("target adapter unavailable")
        check_host(runner, adapter)
        task = admit(runner, adapter, entry, source, event)
    except Exception as exc:
        status = "pending" if isinstance(exc, AdmissionUncertain) else "failure"
        await transition(db, rid, token, status, error=str(exc))
        return dict(result, status=status, error=str(exc))

    async def settle():
        try:
            await asyncio.shield(task)
            injected = await exact_injection(target_db, entry.session_id, rid)
            if injected is not None:
                await transition(db, rid, token, "dispatched", injected_id=injected)
                return dict(result, status="dispatched", injected_message_id=injected)
            error = "owned turn ended without an exact persisted wake; reconciliation pending"
        except BaseException as exc:
            error = f"owned turn outcome uncertain: {type(exc).__name__}"
        await transition(db, rid, token, "pending", error=error)
        return dict(result, status="pending", error=error)

    # The caller owns its cancellation, not the admitted turn. Keep settlement
    # alive and strongly referenced so cancellation cannot manufacture retry.
    pending = getattr(runner, "_self_wake_settlements", None)
    if pending is None:
        pending = runner._self_wake_settlements = set()
    settlement = asyncio.create_task(settle())
    pending.add(settlement)
    settlement.add_done_callback(pending.discard)
    return await asyncio.shield(settlement)

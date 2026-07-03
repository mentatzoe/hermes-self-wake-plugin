"""Compatibility shim: provides ``internal_session_wake_v1`` on vanilla Hermes.

WHY THIS EXISTS
---------------
The self-wake plugin requires the Hermes host capability
``internal_session_wake_v1``:

  - ``GatewayRunner.wake_session(*, payload, source_kind, session_key=None,
    session_id=None, dedupe_key=None) -> dict`` — the wake primitive.
  - ``SessionStore.lookup_by_session_key(session_key)`` — session resolution.
  - ``SessionDB.create_session_wake_receipt(...)`` /
    ``update_session_wake_receipt(...)`` — durable receipts.
  - The ``session_wake_receipts`` table in state.db.
  - Kanban notifier routing: a ``session:<key>`` / ``session_id:<id>`` marker in
    ``kanban_notify_subs.user_id`` must trigger ``wake_session`` instead of a
    visible ``adapter.send``.

Upstream Hermes does not yet expose this capability.  Rather than depend on
upstream accepting the core patch under ``docs/core-patch/``, this shim installs
the capability at runtime via class-level monkeypatching **when explicitly
enabled by the operator**.  This is the shareable, upgrade-portable path: the
plugin carries its own compatibility layer and does not require operators to
patch Hermes core.

WHAT IT TOUCHES (private Hermes internals)
------------------------------------------
All imports are lazy (inside functions) so this module loads on any host.

  - ``gateway.session.SessionStore`` — adds ``lookup_by_session_key``.
    Expects vanilla shape: ``self._entries: dict``, ``self._lock``,
    ``self._ensure_loaded_locked()``.  ``lookup_by_session_id`` already exists
    on vanilla Hermes.
  - ``hermes_state.SessionDB`` — adds ``create_session_wake_receipt`` /
    ``update_session_wake_receipt`` and creates the
    ``session_wake_receipts`` table (idempotent ``CREATE TABLE IF NOT EXISTS``).
    Expects vanilla shape: ``self._execute_write(fn)`` callable that runs ``fn``
    inside a write transaction with a live ``sqlite3.Connection``,
    ``self.get_messages(session_id)``.
  - ``gateway.run.GatewayRunner`` — adds ``wake_session``,
    ``_lookup_session_entry_for_wake``, ``_wake_message_ids_after``.  Expects
    vanilla shape: instances carry ``session_store``, ``_session_db``,
    ``adapters``.
  - ``gateway.kanban_watchers.GatewayKanbanWatchersMixin`` — adds
    ``_kanban_internal_wake_target`` and replaces ``_kanban_notifier_watcher``
    with the canonical patched version that routes ``session:`` /
    ``session_id:`` markers through ``wake_session``.  Expects vanilla shape:
    the original ``_kanban_notifier_watcher`` source contains
    ``await adapter.send(`` and ``sub["chat_id"], msg, metadata=metadata`` and
    does NOT already contain ``_kanban_internal_wake_target``.

The ``wake_session`` / receipt / lookup implementations are carried verbatim
(comments trimmed) from ``docs/core-patch/0001-internal-session-wake-v1.patch``
so, for the surfaces the shim provides (Kanban wake, receipts, session lookup, notifier routing), its behavior matches the core patch.  The notifier
replacement is the canonical patched ``_kanban_notifier_watcher``.

FAIL-CLOSED CONTRACT
--------------------
Before installing, the shim verifies each target's source shape matches what it
expects (:func:`_drift_check`).  If any private internal has drifted (renamed,
refactored, signature changed, send-site moved), the shim refuses to install
and returns a structured error naming the drifted target.  The plugin then
stays in ``inspect_only`` mode: ``self_wake_subscribe_kanban`` returns
``capability_missing`` rather than writing a ``session:`` marker that would
never fire.  There is no silent "looks subscribed but won't wake" state.

A second fail-closed guard lives in :func:`capabilities.probe_wake_capability`:
it only reports ``full`` mode when the notifier routing is actually present
(the ``notifier_routing`` probe), so a half-installed shim (capability surface
without notifier wiring) is reported as not-yet-usable.

NATIVE PREFERENCE
-----------------
If the host already has ``wake_session`` (upstream accepted the patch, or the
operator applied ``docs/core-patch/``), :func:`install_shim` detects the native
capability via :func:`capabilities.probe_wake_capability` and does NOT install.
The shim can also be disabled via config
(``self_wake.compat_shim_enabled: false``, the default).  When upstream later
exposes the capability natively, operators set ``compat_shim_enabled: false``
(or the shim auto-detects native and skips) and remove the shim — no plugin
behavior changes because the capability probes are identical.

WHAT THE SHIM DOES NOT PROVIDE
------------------------------
The core patch also refines active-session wake queueing in
``gateway/platforms/base.py`` (internal wakes queue without interrupting a
running agent).  The shim does NOT monkeypatch ``base.py``; on vanilla Hermes a
wake to an already-active session is handled by the host's default busy-session
policy (interrupt or queue per config).  The wake event is always delivered and
receipted — only the active-session queuing semantics differ.  This is
documented honestly in ``docs/compatibility.md``.  Cron-delivery wake
(``cron.wake_agent_on_delivery``) and send-message mirror wake are likewise not
wired by the shim; they require the core patch or a future upstream capability.
"""
from __future__ import annotations

import inspect
import logging
import time as _time
from typing import Any, Callable, Optional

# A "requested" receipt younger than this is treated as in-flight and not
# retried (two concurrent wakes with one dedupe key must not both dispatch);
# older than this it is treated as a crashed pre-dispatch attempt.
_INFLIGHT_RETRY_SECONDS = 120.0

logger = logging.getLogger(__name__)

# Module-level install state. ``_installed`` is True between a successful
# :func:`install_shim` and :func:`uninstall_shim`.  ``_originals`` holds
# ``(cls, name, original)`` tuples so :func:`uninstall_shim` can restore
# without re-importing the target modules (which may already be torn down in
# tests or after a Hermes reload).
_installed: bool = False
_originals: list[tuple[Any, str, Any]] = []
_install_report: Optional[dict] = None


# --------------------------------------------------------------------------- #
# Schema for the receipts table (mirrors the core-patch DDL exactly).
# --------------------------------------------------------------------------- #
_WAKE_RECEIPT_DDL = [
    """
    CREATE TABLE IF NOT EXISTS session_wake_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        requested_at REAL NOT NULL,
        dispatched_at REAL,
        responded_at REAL,
        source_kind TEXT NOT NULL,
        target_session_key TEXT NOT NULL,
        target_session_id TEXT NOT NULL,
        origin_snapshot TEXT,
        payload_hash TEXT NOT NULL,
        payload_preview TEXT,
        payload_bytes INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        injected_message_id INTEGER,
        assistant_message_id INTEGER,
        error TEXT,
        dedupe_key TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session_wake_receipts_target "
    "ON session_wake_receipts(target_session_key, created_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_session_wake_receipts_dedupe "
    "ON session_wake_receipts(target_session_key, dedupe_key) "
    "WHERE dedupe_key IS NOT NULL",
]


# --------------------------------------------------------------------------- #
# Shim implementations (carried from docs/core-patch/, comments trimmed).
# --------------------------------------------------------------------------- #
def _shim_lookup_by_session_key(self, session_key: str):
    """Return the active session entry for an exact session key, if any.

    Mirrors ``SessionStore.lookup_by_session_id`` (vanilla) and the
    ``lookup_by_session_key`` added by the core patch.
    """
    if not session_key:
        return None
    with self._lock:
        self._ensure_loaded_locked()
        return self._entries.get(session_key)


def _shim_ensure_wake_receipt_table(session_db) -> None:
    """Create the session_wake_receipts table+indexes if absent (idempotent).

    Uses ``_execute_write`` so the DDL runs inside the same WAL write
    transaction discipline as the rest of SessionDB.  Each statement is a
    single ``IF NOT EXISTS`` so this is safe to call on every receipt create.
    """
    def _do(conn):
        for stmt in _WAKE_RECEIPT_DDL:
            conn.execute(stmt)
    try:
        session_db._execute_write(_do)
    except Exception as exc:  # noqa: BLE001
        logger.debug("self-wake shim: ensure receipt table failed: %s", exc)


def _ensure_receipt_table_in_state_db(hermes_home) -> None:
    """Create the receipts table in state.db at install time (best-effort).

    Opens state.db directly (read-write) and runs the idempotent DDL so the
    capability probe sees the table immediately.  Skips silently when state.db
    does not exist yet — the lazy path in ``create_session_wake_receipt`` covers
    that case once Hermes creates state.db.
    """
    import sqlite3
    from . import capabilities as caps
    try:
        db = caps._state_db_path(hermes_home)
    except Exception:
        return
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(str(db))
        try:
            for stmt in _WAKE_RECEIPT_DDL:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("self-wake shim: install-time receipt table creation failed: %s", exc)


def _shim_create_session_wake_receipt(
    self,
    *,
    source_kind: str,
    target_session_key: str,
    target_session_id: str,
    origin_snapshot: Optional[dict],
    payload_hash: str,
    payload_preview: str,
    payload_bytes: int,
    dedupe_key: Optional[str] = None,
):
    """Create a durable wake receipt. Returns ``(row, created)``.

    When ``dedupe_key`` has already been used for the same target session, no
    new row is inserted and the existing row is returned with ``created=False``.
    """
    import json
    import time

    _shim_ensure_wake_receipt_table(self)
    now = time.time()
    origin_json = json.dumps(origin_snapshot, sort_keys=True) if origin_snapshot else None
    dedupe_value = str(dedupe_key) if dedupe_key else None

    def _do(conn):
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO session_wake_receipts (
                created_at, updated_at, requested_at, source_kind,
                target_session_key, target_session_id, origin_snapshot,
                payload_hash, payload_preview, payload_bytes, status,
                dedupe_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?)
            """,
            (
                now, now, now, source_kind,
                target_session_key, target_session_id, origin_json,
                payload_hash, payload_preview, payload_bytes, dedupe_value,
            ),
        )
        created = cursor.rowcount > 0
        if created:
            row_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM session_wake_receipts WHERE id = ?", (row_id,)
            ).fetchone()
        elif dedupe_value is not None:
            row = conn.execute(
                """
                SELECT * FROM session_wake_receipts
                WHERE target_session_key = ? AND dedupe_key = ?
                ORDER BY id LIMIT 1
                """,
                (target_session_key, dedupe_value),
            ).fetchone()
        else:
            row = None
        return (dict(row) if row else {}, created)

    return self._execute_write(_do)


def _shim_update_session_wake_receipt(
    self,
    receipt_id: int,
    *,
    status: Optional[str] = None,
    dispatched: bool = False,
    responded: bool = False,
    injected_message_id: Optional[int] = None,
    assistant_message_id: Optional[int] = None,
    error: Optional[str] = None,
):
    """Update a session wake receipt and return the updated row."""
    import time
    from typing import List

    now = time.time()

    def _do(conn):
        fields = ["updated_at = ?"]
        values: List[Any] = [now]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if dispatched:
            fields.append("dispatched_at = COALESCE(dispatched_at, ?)")
            values.append(now)
        if responded:
            fields.append("responded_at = COALESCE(responded_at, ?)")
            values.append(now)
        if injected_message_id is not None:
            fields.append("injected_message_id = ?")
            values.append(injected_message_id)
        if assistant_message_id is not None:
            fields.append("assistant_message_id = ?")
            values.append(assistant_message_id)
        if error is not None:
            fields.append("error = ?")
            values.append(error[:2000])
        values.append(receipt_id)
        conn.execute(
            f"UPDATE session_wake_receipts SET {', '.join(fields)} WHERE id = ?",
            tuple(values),
        )
        row = conn.execute(
            "SELECT * FROM session_wake_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        return dict(row) if row else None

    return self._execute_write(_do)


def _shim_lookup_session_entry_for_wake(self, *, session_key=None, session_id=None):
    """Resolve an existing SessionEntry for an internal wake request."""
    if bool(session_key) == bool(session_id):
        raise ValueError("provide exactly one of session_key or session_id")
    if session_id:
        return self.session_store.lookup_by_session_id(session_id)
    if session_key is None:
        raise ValueError("session_key is required when session_id is not provided")
    return self.session_store.lookup_by_session_key(session_key)


def _shim_wake_message_ids_after(self, session_id, *, previous_max_id, payload):
    """Best-effort lookup of the injected user row and assistant row."""
    session_db = getattr(self, "_session_db", None)
    if session_db is None:
        return None, None
    try:
        messages = session_db.get_messages(session_id)
    except Exception:
        logger.debug("failed to inspect wake messages for %s", session_id, exc_info=True)
        return None, None
    new_messages = [m for m in messages if int(m.get("id") or 0) > previous_max_id]
    injected_id = None
    assistant_id = None
    for msg in new_messages:
        if injected_id is None and msg.get("role") == "user" and msg.get("content") == payload:
            injected_id = int(msg["id"])
            continue
        if injected_id is not None and msg.get("role") == "assistant":
            assistant_id = int(msg["id"])
            break
    if injected_id is None:
        for msg in new_messages:
            if msg.get("role") == "user":
                injected_id = int(msg["id"])
                break
    if assistant_id is None:
        for msg in new_messages:
            if msg.get("role") == "assistant":
                assistant_id = int(msg["id"])
                break
    return injected_id, assistant_id


async def _shim_wake_session(
    self,
    *,
    payload: str,
    source_kind: str,
    session_key: Optional[str] = None,
    session_id: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> dict:
    """Inject a trusted internal event into an existing gateway session.

    Carried from the core patch. Resolves an existing SessionEntry, reuses its
    stored origin, dispatches ``MessageEvent(..., internal=True)`` through the
    live adapter pipeline, and records a bounded receipt.
    """
    import asyncio
    import dataclasses
    import hashlib

    session_db = getattr(self, "_session_db", None)
    if session_db is None:
        return {"status": "failure", "error": "session database unavailable for wake receipt"}

    try:
        entry = self._lookup_session_entry_for_wake(session_key=session_key, session_id=session_id)
    except Exception as exc:
        return {"status": "failure", "error": str(exc)}
    if entry is None:
        return {"status": "failure", "error": "target session not found"}
    if entry.origin is None:
        return {"status": "failure", "error": "target session has no stored origin"}

    payload = str(payload or "")
    payload_bytes = len(payload.encode("utf-8", errors="replace"))
    payload_hash = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    payload_preview = payload[:500]
    try:
        origin_snapshot = entry.origin.to_dict()
    except Exception:
        origin_snapshot = None

    receipt, created = session_db.create_session_wake_receipt(
        source_kind=source_kind,
        target_session_key=entry.session_key,
        target_session_id=entry.session_id,
        origin_snapshot=origin_snapshot,
        payload_hash=payload_hash,
        payload_preview=payload_preview,
        payload_bytes=payload_bytes,
        dedupe_key=dedupe_key,
    )
    if not created:
        existing_status = str(receipt.get("status") or "")
        retryable = existing_status == "failure"
        if existing_status == "requested":
            ts = receipt.get("updated_at") or receipt.get("requested_at") or 0
            try:
                retryable = (_time.time() - float(ts)) > _INFLIGHT_RETRY_SECONDS
            except (TypeError, ValueError):
                retryable = False
        if not retryable:
            return {
                "status": "deduped",
                "receipt_id": receipt.get("id"),
                "target_session_key": entry.session_key,
                "target_session_id": entry.session_id,
            }
        receipt = session_db.update_session_wake_receipt(
            int(receipt["id"]), status="requested", error=""
        ) or receipt

    receipt_id = int(receipt["id"])
    adapter = self.adapters.get(entry.origin.platform)
    if adapter is None:
        error = f"adapter unavailable for {entry.origin.platform.value}"
        row = session_db.update_session_wake_receipt(receipt_id, status="failure", error=error)
        return {"status": "failure", "error": error, "receipt_id": receipt_id, "receipt": row}

    before_max_id = 0
    try:
        existing_messages = session_db.get_messages(entry.session_id)
        before_max_id = max((int(m.get("id") or 0) for m in existing_messages), default=0)
    except Exception:
        logger.debug("failed to snapshot pre-wake message id", exc_info=True)

    # Resolve the adapter's canonical session key for the target origin so we
    # can detect an already-active task for the same lane.
    adapter_key = None
    try:
        from gateway.session import build_session_key
        adapter_config = getattr(adapter, "config", None)
        adapter_extra = getattr(adapter_config, "extra", {}) or {}
        adapter_key = build_session_key(
            entry.origin,
            group_sessions_per_user=adapter_extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=adapter_extra.get("thread_sessions_per_user", False),
        )
    except Exception:
        adapter_key = None
    target_keys = [entry.session_key]
    if adapter_key and adapter_key not in target_keys:
        target_keys.append(adapter_key)

    active_sessions = getattr(adapter, "_active_sessions", {})
    session_tasks = getattr(adapter, "_session_tasks", {})
    current_task = asyncio.current_task()
    target_already_active = False
    if isinstance(active_sessions, dict):
        target_already_active = any(key in active_sessions for key in target_keys)
    if not target_already_active and isinstance(session_tasks, dict):
        for key in target_keys:
            task = session_tasks.get(key)
            if task is None:
                continue
            done = getattr(task, "done", None)
            if task is current_task or not (done and done()):
                target_already_active = True
                break

    from gateway.platforms.base import MessageEvent, MessageType
    event = MessageEvent(
        text=payload,
        message_type=MessageType.TEXT,
        source=dataclasses.replace(entry.origin),
        internal=True,
        message_id=f"internal-wake:{receipt_id}",
    )
    try:
        setattr(event, "_hermes_internal_wake_receipt_id", receipt_id)
        if dedupe_key:
            setattr(event, "_hermes_internal_wake_dedupe_key", dedupe_key)
        setattr(event, "_hermes_internal_wake_source_kind", source_kind)
    except Exception:
        pass

    try:
        session_db.update_session_wake_receipt(receipt_id, status="dispatched", dispatched=True)
        await adapter.handle_message(event)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        row = session_db.update_session_wake_receipt(receipt_id, status="failure", error=error)
        logger.warning("internal wake failed for %s: %s", entry.session_key, error)
        return {"status": "failure", "error": error, "receipt_id": receipt_id, "receipt": row}

    # The payload IS injected past this point. Anything that fails below is
    # bookkeeping, not delivery: mark it dispatched_unconfirmed (non-retryable)
    # instead of failure, so the notifier does not rewind and re-inject a wake
    # that already happened.
    try:
        if target_already_active:
            row = session_db.update_session_wake_receipt(
                receipt_id, status="queued", dispatched=True
            )
            return {
                "status": "queued",
                "receipt_id": receipt_id,
                "target_session_key": entry.session_key,
                "target_session_id": entry.session_id,
                "receipt": row,
            }
        session_tasks = getattr(adapter, "_session_tasks", {})
        task = None
        if isinstance(session_tasks, dict):
            for key in target_keys:
                task = session_tasks.get(key)
                if task is not None:
                    break
        if task is not None and task is not asyncio.current_task():
            await asyncio.shield(task)
        injected_id, assistant_id = self._wake_message_ids_after(
            entry.session_id, previous_max_id=before_max_id, payload=payload
        )
        status = "agent_responded" if assistant_id is not None else "dispatched"
        row = session_db.update_session_wake_receipt(
            receipt_id,
            status=status,
            responded=assistant_id is not None,
            injected_message_id=injected_id,
            assistant_message_id=assistant_id,
        )
        return {
            "status": status,
            "receipt_id": receipt_id,
            "target_session_key": entry.session_key,
            "target_session_id": entry.session_id,
            "injected_message_id": injected_id,
            "assistant_message_id": assistant_id,
            "receipt": row,
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        row = None
        try:
            row = session_db.update_session_wake_receipt(
                receipt_id, status="dispatched_unconfirmed", error=error)
        except Exception:  # noqa: BLE001
            logger.warning("could not record dispatched_unconfirmed for receipt %s", receipt_id)
        logger.warning("internal wake dispatched but bookkeeping failed for %s: %s",
                       entry.session_key, error)
        return {"status": "dispatched_unconfirmed", "error": error,
                "receipt_id": receipt_id, "receipt": row}


def _shim_kanban_internal_wake_target(self, sub: dict):
    """Return (target_kind, target_value) for internal-wake subscriptions."""
    marker = str(sub.get("user_id") or "").strip()
    if marker.startswith("session_id:"):
        target = marker.split(":", 1)[1].strip()
        return ("session_id", target) if target else None
    if marker.startswith("session:"):
        target = marker.split(":", 1)[1].strip()
        return ("session_key", target) if target else None
    return None


async def _shim_kanban_notifier_watcher(self, interval: float = 5.0) -> None:
    """Canonical patched kanban notifier watcher (from docs/core-patch/).

    Identical to vanilla ``_kanban_notifier_watcher`` except that a
    ``session:<key>`` / ``session_id:<id>`` marker in ``sub["user_id"]`` routes
    the terminal event through ``self.wake_session`` instead of a visible
    ``adapter.send``.  Carried verbatim (comments trimmed) so behavior matches
    the core patch.
    """
    import asyncio
    import os
    from pathlib import Path

    logger = logging.getLogger("gateway.run")
    try:
        from hermes_cli.config import load_config as _load_config
    except Exception:
        logger.warning("kanban notifier: config loader unavailable; disabled")
        return
    env_override = os.environ.get("HERMES_KANBAN_DISPATCH_IN_GATEWAY", "").strip().lower()
    if env_override in {"0", "false", "no", "off"}:
        logger.info("kanban notifier: disabled via HERMES_KANBAN_DISPATCH_IN_GATEWAY env")
        return
    try:
        cfg = _load_config()
    except Exception as exc:
        logger.warning("kanban notifier: cannot load config (%s); disabled", exc)
        return
    kanban_cfg = cfg.get("kanban", {}) if isinstance(cfg, dict) else {}
    if not kanban_cfg.get("dispatch_in_gateway", True):
        logger.info("kanban notifier: disabled via config kanban.dispatch_in_gateway=false")
        return
    from gateway.config import Platform as _Platform
    try:
        from hermes_cli import kanban_db as _kb
    except Exception:
        logger.warning("kanban notifier: kanban_db not importable; notifier disabled")
        return

    TERMINAL_KINDS = ("completed", "blocked", "gave_up", "crashed", "timed_out")
    MAX_SEND_FAILURES = 3
    sub_fail_counts: dict = getattr(self, "_kanban_sub_fail_counts", {})
    self._kanban_sub_fail_counts = sub_fail_counts
    notifier_profile = getattr(self, "_kanban_notifier_profile", None)
    if not notifier_profile:
        notifier_profile = self._active_profile_name()
        self._kanban_notifier_profile = notifier_profile

    await asyncio.sleep(5)

    while self._running:
        try:
            def _collect():
                deliveries: list = []
                active_platforms = {
                    getattr(platform, "value", str(platform)).lower()
                    for platform in self.adapters.keys()
                }
                if not active_platforms:
                    logger.debug("kanban notifier: no connected adapters; skipping tick")
                    return deliveries
                try:
                    boards = _kb.list_boards(include_archived=False)
                except Exception:
                    boards = [_kb.read_board_metadata(_kb.DEFAULT_BOARD)]
                seen_db_paths: set = set()
                for board_meta in boards:
                    slug = board_meta.get("slug") or _kb.DEFAULT_BOARD
                    db_path = board_meta.get("db_path")
                    try:
                        resolved_db_path = str(Path(db_path).expanduser().resolve()) if db_path else str(_kb.kanban_db_path(slug).resolve())
                    except Exception:
                        resolved_db_path = f"slug:{slug}"
                    if resolved_db_path in seen_db_paths:
                        continue
                    seen_db_paths.add(resolved_db_path)
                    try:
                        conn = _kb.connect(board=slug)
                    except Exception as exc:
                        logger.debug("kanban notifier: cannot open board %s: %s", slug, exc)
                        continue
                    try:
                        subs = _kb.list_notify_subs(conn)
                        if not subs:
                            logger.debug("kanban notifier: board %s has no subscriptions", slug)
                        for sub in subs:
                            owner_profile = sub.get("notifier_profile") or None
                            if owner_profile and owner_profile != notifier_profile:
                                continue
                            platform = (sub.get("platform") or "").lower()
                            if platform not in active_platforms:
                                continue
                            old_cursor, cursor, events = _kb.claim_unseen_events_for_sub(
                                conn,
                                task_id=sub["task_id"],
                                platform=sub["platform"],
                                chat_id=sub["chat_id"],
                                thread_id=sub.get("thread_id") or "",
                                kinds=TERMINAL_KINDS,
                            )
                            if not events:
                                continue
                            task = _kb.get_task(conn, sub["task_id"])
                            deliveries.append({
                                "sub": sub, "old_cursor": old_cursor, "cursor": cursor,
                                "events": events, "task": task, "board": slug,
                            })
                    finally:
                        conn.close()
                return deliveries

            deliveries = await asyncio.to_thread(_collect)
            for d in deliveries:
                sub = d["sub"]
                task = d["task"]
                board_slug = d.get("board")
                platform_str = (sub["platform"] or "").lower()
                try:
                    plat = _Platform(platform_str)
                except ValueError:
                    await asyncio.to_thread(self._kanban_advance, sub, d["cursor"], board_slug)
                    continue
                adapter = self.adapters.get(plat)
                if adapter is None:
                    await asyncio.to_thread(
                        self._kanban_rewind, sub, d["cursor"], d.get("old_cursor", 0), board_slug)
                    continue
                title = (task.title if task else sub["task_id"])[:120]
                for ev in d["events"]:
                    kind = ev.kind
                    who = (task.assignee if task and task.assignee else None)
                    tag = f"@{who} " if who else ""
                    if kind == "completed":
                        handoff = ""
                        payload_summary = None
                        if ev.payload and ev.payload.get("summary"):
                            payload_summary = str(ev.payload["summary"])
                        if payload_summary:
                            lines = payload_summary.strip().splitlines()
                            h = lines[0][:200] if lines else payload_summary[:200]
                            handoff = f"\n{h}"
                        elif task and task.result:
                            lines = task.result.strip().splitlines()
                            r = lines[0][:160] if lines else task.result[:160]
                            handoff = f"\n{r}"
                        msg = f"✔ {tag}Kanban {sub['task_id']} done — {title}{handoff}"
                    elif kind == "blocked":
                        reason = ""
                        if ev.payload and ev.payload.get("reason"):
                            reason = f": {str(ev.payload['reason'])[:160]}"
                        msg = f"⏸ {tag}Kanban {sub['task_id']} blocked{reason}"
                    elif kind == "gave_up":
                        err = ""
                        if ev.payload and ev.payload.get("error"):
                            err = f"\n{str(ev.payload['error'])[:200]}"
                        msg = f"✖ {tag}Kanban {sub['task_id']} gave up after repeated spawn failures{err}"
                    elif kind == "crashed":
                        msg = f"✖ {tag}Kanban {sub['task_id']} worker crashed (pid gone); dispatcher will retry"
                    elif kind == "timed_out":
                        limit = 0
                        if ev.payload and ev.payload.get("limit_seconds"):
                            limit = int(ev.payload["limit_seconds"])
                        msg = f"⏱ {tag}Kanban {sub['task_id']} timed out (max_runtime={limit}s); will retry"
                    else:
                        continue
                    metadata: dict = {}
                    if sub.get("thread_id"):
                        metadata["thread_id"] = sub["thread_id"]
                    sub_key = (
                        sub["task_id"], sub["platform"],
                        sub["chat_id"], sub.get("thread_id") or "",
                    )
                    internal_wake_delivery = False
                    try:
                        wake_target = self._kanban_internal_wake_target(sub)
                        if wake_target is not None:
                            internal_wake_delivery = True
                            target_kind, target_value = wake_target
                            dedupe_key = f"kanban:{board_slug or ''}:{sub['task_id']}:{ev.id}"
                            wake_kwargs = {
                                "payload": msg,
                                "source_kind": "kanban",
                                "dedupe_key": dedupe_key,
                            }
                            if target_kind == "session_id":
                                wake_kwargs["session_id"] = target_value
                            else:
                                wake_kwargs["session_key"] = target_value
                            result = await self.wake_session(**wake_kwargs)
                            if result.get("status") == "failure":
                                raise RuntimeError(result.get("error") or "internal wake failed")
                            logger.debug(
                                "kanban notifier: internally woke %s for %s event on %s (receipt=%s)",
                                target_value, kind, sub["task_id"], result.get("receipt_id"),
                            )
                        else:
                            await adapter.send(sub["chat_id"], msg, metadata=metadata)
                            logger.debug(
                                "kanban notifier: delivered %s event for %s to %s/%s on board %s",
                                kind, sub["task_id"], platform_str, sub["chat_id"], board_slug,
                            )
                        if kind == "completed" and not internal_wake_delivery:
                            try:
                                await self._deliver_kanban_artifacts(
                                    adapter=adapter,
                                    chat_id=sub["chat_id"],
                                    metadata=metadata,
                                    event_payload=getattr(ev, "payload", None),
                                    task=task,
                                )
                            except Exception as art_exc:
                                logger.debug(
                                    "kanban notifier: artifact delivery for %s failed: %s",
                                    sub["task_id"], art_exc,
                                )
                        sub_fail_counts.pop(sub_key, None)
                    except Exception as exc:
                        fails = sub_fail_counts.get(sub_key, 0) + 1
                        sub_fail_counts[sub_key] = fails
                        logger.warning(
                            "kanban notifier: send failed for %s on %s (attempt %d/%d): %s",
                            sub["task_id"], platform_str, fails, MAX_SEND_FAILURES, exc,
                        )
                        if fails >= MAX_SEND_FAILURES:
                            logger.warning(
                                "kanban notifier: dropping subscription %s on %s after %d consecutive send failures",
                                sub["task_id"], platform_str, fails,
                            )
                            await asyncio.to_thread(self._kanban_unsub, sub, board_slug)
                            sub_fail_counts.pop(sub_key, None)
                        else:
                            await asyncio.to_thread(
                                self._kanban_rewind, sub, d["cursor"], d.get("old_cursor", 0), board_slug)
                        break
                else:
                    await asyncio.to_thread(self._kanban_advance, sub, d["cursor"], board_slug)
                    task_terminal = task and task.status in {"done", "archived"}
                    if task_terminal:
                        await asyncio.to_thread(self._kanban_unsub, sub, board_slug)
        except Exception as exc:
            logger.warning("kanban notifier tick failed: %s", exc)
        for _ in range(int(max(1, interval))):
            if not self._running:
                return
            await asyncio.sleep(1)


# --------------------------------------------------------------------------- #
# Drift detection — each target's vanilla source shape must match expectations.
# --------------------------------------------------------------------------- #
def _source_of(fn: Callable) -> str:
    """Best-effort source retrieval; empty string when unavailable."""
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return ""


def _drift_check() -> dict:
    """Verify every private internal the shim touches still has the expected shape.

    Returns ``{"ok": bool, "reason": str, "targets": {...}}``.  When ``ok`` is
    False, ``reason`` names the first drifted target and ``targets`` carries
    per-target pass/fail for diagnostics.  This is the fail-closed gate: a
    False result means :func:`install_shim` refuses and the plugin stays in
    ``inspect_only`` mode.
    """
    targets: dict[str, dict] = {}

    # --- SessionStore ---
    try:
        from gateway.session import SessionStore  # type: ignore
    except Exception as exc:
        targets["session_store"] = {"ok": False, "reason": f"gateway.session.SessionStore not importable: {exc}"}
    else:
        init_src = _source_of(SessionStore.__init__)
        has_entries = "_entries" in init_src
        has_lock = hasattr(SessionStore, "_ensure_loaded_locked") and callable(
            getattr(SessionStore, "_ensure_loaded_locked"))
        already = hasattr(SessionStore, "lookup_by_session_key")
        ok = has_entries and has_lock
        reason = ""
        if not has_entries:
            reason = "SessionStore.__init__ no longer references self._entries"
        elif not has_lock:
            reason = "SessionStore._ensure_loaded_locked missing"
        targets["session_store"] = {"ok": ok, "reason": reason, "already_patched": already}

    # --- SessionDB ---
    try:
        from hermes_state import SessionDB  # type: ignore
    except Exception as exc:
        targets["session_db"] = {"ok": False, "reason": f"hermes_state.SessionDB not importable: {exc}"}
    else:
        has_write = hasattr(SessionDB, "_execute_write") and callable(getattr(SessionDB, "_execute_write"))
        has_msgs = hasattr(SessionDB, "get_messages") and callable(getattr(SessionDB, "get_messages"))
        already = hasattr(SessionDB, "create_session_wake_receipt")
        ok = has_write and has_msgs
        reason = ""
        if not has_write:
            reason = "SessionDB._execute_write missing"
        elif not has_msgs:
            reason = "SessionDB.get_messages missing"
        targets["session_db"] = {"ok": ok, "reason": reason, "already_patched": already}

    # --- GatewayRunner ---
    try:
        from gateway.run import GatewayRunner  # type: ignore
    except Exception as exc:
        targets["gateway_runner"] = {"ok": False, "reason": f"gateway.run.GatewayRunner not importable: {exc}"}
    else:
        init_src = _source_of(GatewayRunner.__init__)
        has_store = "session_store" in init_src or "self.session_store" in init_src
        has_sdb = "_session_db" in init_src
        has_adapters = "self.adapters" in init_src or "adapters" in init_src
        already = hasattr(GatewayRunner, "wake_session")
        ok = has_store and has_sdb and has_adapters
        reason = ""
        if not has_store:
            reason = "GatewayRunner.__init__ no longer references session_store"
        elif not has_sdb:
            reason = "GatewayRunner.__init__ no longer references _session_db"
        elif not has_adapters:
            reason = "GatewayRunner.__init__ no longer references adapters"
        targets["gateway_runner"] = {"ok": ok, "reason": reason, "already_patched": already}

    # --- GatewayKanbanWatchersMixin (notifier routing) ---
    try:
        from gateway.kanban_watchers import GatewayKanbanWatchersMixin  # type: ignore
    except Exception as exc:
        targets["kanban_notifier"] = {"ok": False, "reason": f"gateway.kanban_watchers not importable: {exc}"}
    else:
        fn = getattr(GatewayKanbanWatchersMixin, "_kanban_notifier_watcher", None)
        if not callable(fn):
            targets["kanban_notifier"] = {"ok": False, "reason": "_kanban_notifier_watcher missing"}
        else:
            src = _source_of(fn)
            has_send = "await adapter.send(" in src and 'sub["chat_id"], msg, metadata=metadata' in src
            already_routed = "_kanban_internal_wake_target" in src
            ok = has_send and not already_routed
            reason = ""
            if already_routed:
                reason = "notifier already routes wake markers (native/patched)"
            elif not has_send:
                reason = ("_kanban_notifier_watcher send-site shape changed "
                          "(expected 'await adapter.send(sub[\"chat_id\"], msg, metadata=metadata)')")
            targets["kanban_notifier"] = {"ok": ok, "reason": reason, "already_patched": already_routed}

    # --- Extended anchors: every other host internal the vendored code calls.
    # The four structural anchors above catch shape drift at the patch sites;
    # these catch renames/removals that would otherwise fail OPEN inside the
    # notifier tick's blanket exception handler (silent no-wake).
    ext_missing: list[str] = []
    try:
        from gateway.kanban_watchers import GatewayKanbanWatchersMixin as _M  # type: ignore
        for _name in ("_kanban_advance", "_kanban_rewind", "_kanban_unsub",
                      "_deliver_kanban_artifacts"):
            if not callable(getattr(_M, _name, None)):
                ext_missing.append(f"GatewayKanbanWatchersMixin.{_name}")
    except Exception:
        pass  # already reported by the kanban_notifier anchor
    try:
        import hermes_cli.kanban_db as _kdb  # type: ignore
        for _name in ("list_boards", "read_board_metadata", "connect",
                      "list_notify_subs", "claim_unseen_events_for_sub",
                      "get_task", "kanban_db_path", "DEFAULT_BOARD"):
            if not hasattr(_kdb, _name):
                ext_missing.append(f"hermes_cli.kanban_db.{_name}")
    except Exception as exc:
        ext_missing.append(f"hermes_cli.kanban_db not importable: {exc}")
    try:
        import dataclasses as _dc
        from gateway.platforms.base import MessageEvent as _ME, MessageType as _MT  # type: ignore # noqa: F401
        if "internal" not in {f.name for f in _dc.fields(_ME)}:
            ext_missing.append("MessageEvent has no 'internal' field")
    except Exception as exc:
        ext_missing.append(f"gateway.platforms.base MessageEvent/MessageType: {exc}")
    try:
        from gateway.session import build_session_key as _bsk  # type: ignore
        _params = set(inspect.signature(_bsk).parameters)
        for _kw in ("group_sessions_per_user", "thread_sessions_per_user"):
            if _kw not in _params:
                ext_missing.append(f"build_session_key missing kwarg {_kw}")
    except Exception as exc:
        ext_missing.append(f"gateway.session.build_session_key: {exc}")
    targets["extended_anchors"] = {
        "ok": not ext_missing,
        "reason": "; ".join(ext_missing[:4]),
    }

    ok = all(t.get("ok") for t in targets.values()) if targets else False
    reason = ""
    if not ok:
        for name, t in targets.items():
            if not t.get("ok"):
                reason = f"{name}: {t.get('reason') or 'drifted'}"
                break
    return {"ok": ok, "reason": reason, "targets": targets}


# --------------------------------------------------------------------------- #
# Install / uninstall / status
# --------------------------------------------------------------------------- #
def is_installed() -> bool:
    """True when the shim has been installed and not yet uninstalled."""
    return _installed


def shim_status() -> dict:
    """Return a structured status for diagnostics (doctor)."""
    return {
        "installed": _installed,
        "report": _install_report,
    }


def install_shim(
    hermes_home: Optional[str] = None,
    *,
    force: bool = False,
    force_targets: Optional[dict] = None,
) -> dict:
    """Install the compat shim onto the live Hermes classes.

    Returns a structured report:
      - ``installed``: bool
      - ``reason``: why it did or did not install
      - ``drift``: the drift-check result (when not installed due to drift)
      - ``targets``: per-class install confirmations (when installed)

    Installation is refused (``installed: False``) when:
      1. The native capability is already present (native preference) — unless
         ``force``.
      2. The shim is disabled by config (``self_wake.compat_shim_enabled``) —
         unless ``force``.
      3. A drift check fails (private internals changed).

    ``force`` skips the native-preference and config gates (used by tests and
    explicit operator override).  ``force_targets`` lets tests inject fake
    classes instead of importing the real Hermes modules.
    """
    global _installed, _install_report

    if _installed:
        report = {"installed": True, "reason": "already_installed"}
        _install_report = report
        return report

    # 1. Native preference: if the host already ships the wake methods, do not
    # install. Gate on method presence, NOT the full probe: the full probe also
    # requires the receipts table and notifier routing, so on a fresh native
    # host (no state.db yet) it reports unavailable and the shim would try to
    # clobber native methods with vendored copies.
    if not force:
        from . import capabilities as caps
        native_wake = bool(caps._probe_gateway_wake_session().get("available"))
        native_receipts = bool(caps._probe_session_db_receipt_methods().get("available"))
        if native_wake and native_receipts:
            report = {"installed": False, "reason": "native_capability_present"}
            _install_report = report
            logger.info("self-wake shim: native wake methods present; not installing")
            return report

    # 2. Config gate: opt-in only.
    if not force:
        if not _shim_config_enabled():
            report = {"installed": False, "reason": "shim_disabled_by_config"}
            _install_report = report
            return report

    # 3. Drift check (or use injected fake targets).
    if force_targets is not None:
        targets = force_targets
        drift = {"ok": True, "reason": "", "targets": {"forced": True}}
    else:
        drift = _drift_check()
        if not drift["ok"]:
            report = {"installed": False, "reason": "drift_detected",
                      "drift": drift}
            _install_report = report
            logger.warning("self-wake shim: drift detected, refusing to install: %s",
                           drift["reason"])
            return report
        targets = {
            "session_store": _resolve("gateway.session", "SessionStore"),
            "session_db": _resolve("hermes_state", "SessionDB"),
            "gateway_runner": _resolve("gateway.run", "GatewayRunner"),
            "kanban_mixin": _resolve("gateway.kanban_watchers", "GatewayKanbanWatchersMixin"),
        }

    # 4. Install: save originals, assign shim methods to classes. A method the
    # host already provides is NEVER overwritten (partial-native hosts keep
    # their native implementations, which may carry upstream fixes); only the
    # notifier pair is always replaced — routing wake markers is the point,
    # and the drift check has already refused if the native watcher routes.
    originals: list[tuple[Any, str, Any]] = []
    skipped_native: list[str] = []
    SessionStore = targets["session_store"]
    SessionDB = targets["session_db"]
    GatewayRunner = targets["gateway_runner"]
    Mixin = targets["kanban_mixin"]

    def _assign(cls: Any, name: str, fn: Any, always: bool = False) -> None:
        existing = getattr(cls, name, None)
        if existing is not None and not always:
            skipped_native.append(f"{cls.__name__}.{name}")
            return
        originals.append((cls, name, existing))
        setattr(cls, name, fn)

    _assign(SessionStore, "lookup_by_session_key", _shim_lookup_by_session_key)
    _assign(SessionDB, "create_session_wake_receipt", _shim_create_session_wake_receipt)
    _assign(SessionDB, "update_session_wake_receipt", _shim_update_session_wake_receipt)
    _assign(GatewayRunner, "wake_session", _shim_wake_session)
    _assign(GatewayRunner, "_lookup_session_entry_for_wake", _shim_lookup_session_entry_for_wake)
    _assign(GatewayRunner, "_wake_message_ids_after", _shim_wake_message_ids_after)
    _assign(Mixin, "_kanban_internal_wake_target", _shim_kanban_internal_wake_target, always=True)
    _assign(Mixin, "_kanban_notifier_watcher", _shim_kanban_notifier_watcher, always=True)

    global _originals
    _originals = originals
    _installed = True

    # Create the session_wake_receipts table in state.db now (if state.db
    # exists) so the capability probe's receipt_table check passes immediately
    # and operators can inspect receipts before the first wake fires.  Safe:
    # CREATE TABLE IF NOT EXISTS.  If state.db is absent (very fresh host) we
    # skip — Hermes will create state.db on first session, and the shim's
    # create_session_wake_receipt also calls _ensure_wake_receipt_table lazily.
    _ensure_receipt_table_in_state_db(hermes_home)

    report = {
        "installed": True,
        "skipped_native_methods": skipped_native,
        "reason": "shim_installed",
        "targets": {
            "session_store.lookup_by_session_key": True,
            "session_db.receipt_methods": True,
            "gateway_runner.wake_session": True,
            "kanban_notifier.routing": True,
        },
    }
    _install_report = report
    logger.info("self-wake compat shim installed (provides internal_session_wake_v1 on vanilla Hermes)")
    return report


def uninstall_shim() -> dict:
    """Restore the pre-shim attributes. Returns a status dict.

    Safe to call when not installed (no-op).  Used by tests and by operators
    who want to remove the shim after a native capability lands upstream.
    Restores directly from the saved ``(cls, name, original)`` tuples so it
    does not need to re-import the target modules.
    """
    global _installed, _originals, _install_report
    if not _installed:
        return {"uninstalled": False, "reason": "not_installed"}

    for cls, name, original in _originals:
        try:
            _restore(cls, name, original)
        except Exception as exc:  # noqa: BLE001
            logger.debug("self-wake shim: restore %s.%s failed: %s", cls, name, exc)

    _installed = False
    _originals = []
    _install_report = {"installed": False, "reason": "uninstalled"}
    logger.info("self-wake compat shim uninstalled")
    return {"uninstalled": True}


def _restore(cls, name: str, original: Any) -> None:
    """Restore a class attribute; delete it if the shim added it (original None)."""
    if original is None:
        try:
            delattr(cls, name)
        except AttributeError:
            pass
    else:
        setattr(cls, name, original)


def _resolve(module_name: str, attr: str):
    mod = __import__(module_name, fromlist=[attr])
    return getattr(mod, attr)


def _shim_config_enabled() -> bool:
    """Read ``self_wake.compat_shim_enabled`` (and the nested ``compat_shim.enabled``
    form) from Hermes config.  Default False — the shim is opt-in.
    """
    from . import config as cfg_mod
    try:
        flat = bool(cfg_mod.get_bool("compat_shim_enabled", False))
    except Exception:
        flat = False
    # Also accept the nested ``compat_shim.enabled`` form documented in older
    # READMEs for backward-compatibility.
    try:
        full = cfg_mod.get_config()
        nested = full.get("compat_shim") if isinstance(full, dict) else None
        if isinstance(nested, dict) and bool(nested.get("enabled")):
            return True
    except Exception:
        pass
    return flat

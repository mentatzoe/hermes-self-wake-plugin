"""Host-session resolver adapter for the self-wake plugin.

The public plugin contract is *not* ``$HERMES_HOME/sessions/sessions.json``.
The public contract is "resolve an existing Hermes session to a wake target".

Current Hermes does not yet expose a stable resolver API for plugins, so this
module ships a read-only adapter over the gateway current-session cache
(``$HERMES_HOME/sessions/sessions.json``, a dict keyed by gateway session key)
plus ``$HERMES_HOME/state.db`` for optional title/last-message metadata. That
cache is current Hermes implementation detail and active-routing state, not a
canonical historical ledger. If Hermes later exposes a native resolver or
``session_surfaces`` table, the adapter should be swapped behind these helpers
without changing the model-facing tools.

This is a *read-only* discovery surface. It works in both ``full`` and
``inspect_only`` capability modes; it never wakes anything. All Hermes
internals are imported lazily so the module degrades cleanly when running
outside a Hermes process (e.g. in the plugin repo's own test suite).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .capabilities import _hermes_home

logger = logging.getLogger(__name__)

# Maximum sessions returned by resolve/list helpers unless the caller passes a
# larger explicit limit (bounded by _safe_int to a hard cap).
DEFAULT_LIMIT = 10
HARD_LIMIT = 50
CURRENT_RESOLVER_SOURCE = "current_session_cache_adapter"


def _current_session_cache_file(hermes_home: str | Path | None = None) -> Path:
    """Current Hermes gateway current-session cache path.

    Compatibility note: this path is the current-Hermes adapter substrate, not
    the plugin's public resolver contract.
    """
    return _hermes_home(hermes_home) / "sessions" / "sessions.json"


def _state_db(hermes_home: str | Path | None = None) -> Path:
    return _hermes_home(hermes_home) / "state.db"


def resolver_source(hermes_home: str | Path | None = None) -> dict[str, Any]:
    """Describe the active session-resolver adapter for diagnostics."""
    return {
        "kind": CURRENT_RESOLVER_SOURCE,
        "current_session_cache": str(_current_session_cache_file(hermes_home)),
        "metadata_db": str(_state_db(hermes_home)),
        "contract": "host session resolver adapter; cache path is not public API",
    }


def _safe_int(value: Any, default: int, *, lo: int = 1, hi: int = HARD_LIMIT) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def read_current_session_cache(hermes_home: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Read the current-Hermes gateway current-session cache.

    Returns a dict keyed by session_key -> entry dict. Returns an empty dict
    when the cache is missing, unreadable, or not a dict. Never raises.
    """
    path = _current_session_cache_file(hermes_home)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("self-wake: failed to read current-session cache %s: %s", path, exc)
        return {}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


def _origin(entry: dict[str, Any]) -> dict[str, Any]:
    origin = entry.get("origin")
    return origin if isinstance(origin, dict) else {}


def _entry_summary(session_key: str, entry: dict[str, Any],
                   db_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a stable, operator-facing summary of a session entry."""
    origin = _origin(entry)
    db_meta = db_meta or {}
    return {
        "session_key": entry.get("session_key") or session_key,
        "session_id": entry.get("session_id") or "",
        "platform": entry.get("platform") or origin.get("platform") or "",
        "chat_type": entry.get("chat_type") or origin.get("chat_type") or "",
        "chat_id": origin.get("chat_id") or "",
        "thread_id": origin.get("thread_id") or "",
        "display_name": entry.get("display_name") or origin.get("chat_name") or "",
        "title": db_meta.get("title") or "",
        "updated_at": entry.get("updated_at") or "",
        "message_count": db_meta.get("message_count"),
        "last_message_at": db_meta.get("last_message_at") or "",
        "last_message_preview": db_meta.get("last_message") or "",
        "origin": {
            k: origin.get(k)
            for k in ("platform", "chat_id", "chat_name", "chat_type", "user_id",
                      "user_name", "thread_id", "parent_chat_id", "guild_id",
                      "message_id")
            if origin.get(k) not in (None, "")
        },
    }


def _load_session_titles(session_ids: Iterable[str],
                         hermes_home: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Look up title/message metadata from state.db for the given session ids.

    Returns {session_id: {title, message_count, started_at, last_message_at,
    last_message}}. Returns {} if state.db is missing or unreadable. The
    sessions/messages schema is Hermes transcript metadata; if it changes, this
    degrades to {} (caller still has current-session-cache origin data).
    """
    ids = [sid for sid in session_ids if sid]
    db = _state_db(hermes_home)
    if not ids or not db.exists():
        return {}
    placeholders = ",".join("?" for _ in ids)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT s.id,
                       s.title,
                       s.message_count,
                       datetime(s.started_at,'unixepoch') AS started_at,
                       datetime(MAX(m.timestamp),'unixepoch') AS last_message_at,
                       (SELECT substr(content,1,240)
                          FROM messages
                         WHERE session_id=s.id
                         ORDER BY timestamp DESC, id DESC
                         LIMIT 1) AS last_message
                  FROM sessions s
             LEFT JOIN messages m ON m.session_id=s.id
                 WHERE s.id IN ({placeholders})
              GROUP BY s.id
                """,
                ids,
            ).fetchall()
            return {str(r["id"]): dict(r) for r in rows}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("self-wake: title lookup failed: %s", exc)
        return {}


def query_host_sessions(hermes_home: str | Path | None = None,
                        session_id: str | None = None,
                        session_key: str | None = None,
                        platform: str | None = None,
                        chat_id: str | None = None,
                        thread_id: str | None = None,
                        query: str | None = None,
                        limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Resolve candidate sessions through the active host resolver adapter.

    Current adapter: gateway current-session cache + state.db title metadata.
    All filters are optional and AND-combined. ``query`` is a case-insensitive
    substring over the entry summary JSON. Returns at most ``limit`` summaries
    (bounded by HARD_LIMIT), most-recently-updated first.
    """
    sessions = read_current_session_cache(hermes_home)
    limit = _safe_int(limit, DEFAULT_LIMIT)
    ids = [str(e.get("session_id") or "") for e in sessions.values()]
    title_meta = _load_session_titles(ids, hermes_home)
    matches: list[dict[str, Any]] = []
    for key, entry in sessions.items():
        db_meta = title_meta.get(str(entry.get("session_id") or ""), {})
        if _matches(key, entry, db_meta, session_id=session_id, session_key=session_key,
                    platform=platform, chat_id=chat_id, thread_id=thread_id, query=query):
            matches.append(_entry_summary(key, entry, db_meta))
    matches.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return matches[:limit]


def _matches(entry_key: str, entry: dict[str, Any],
             db_meta: dict[str, Any] | None,
             *, session_id: str | None, session_key: str | None,
             platform: str | None, chat_id: str | None, thread_id: str | None,
             query: str | None) -> bool:
    origin = _origin(entry)
    filters = {
        "session_key": entry_key,
        "session_id": entry.get("session_id") or "",
        "platform": entry.get("platform") or origin.get("platform") or "",
        "chat_id": origin.get("chat_id") or "",
        "thread_id": origin.get("thread_id") or "",
    }
    for name, value in filters.items():
        wanted = str({"session_id": session_id, "session_key": session_key,
                      "platform": platform, "chat_id": chat_id,
                      "thread_id": thread_id}.get(name) or "").strip()
        if wanted and str(value) != wanted:
            return False
    q = str(query or "").strip().lower()
    if q:
        hay = json.dumps(_entry_summary(entry_key, entry, db_meta), ensure_ascii=False).lower()
        if q not in hay:
            return False
    return True


def resolve_target_session(
    session_key: str | None = None,
    session_id: str | None = None,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_LIMIT,
    hermes_home: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Resolve candidate target sessions through the host resolver adapter.

    Returns a list of entry summaries. If ``session_key`` resolves to exactly
    one entry, returns a single-element list. If filters match multiple
    entries, returns up to ``limit`` summaries so the caller can disambiguate.
    A bare ``session_key`` with no matching entry returns a single summary
    carrying just the key (the caller may intentionally target a key not yet
    seen in this profile).
    """
    sessions = read_current_session_cache(hermes_home)
    session_key = (session_key or "").strip()
    session_id = (session_id or "").strip()

    if session_key and session_key in sessions:
        entry = sessions[session_key]
        db_meta = _load_session_titles(
            [str(entry.get("session_id") or "")], hermes_home).get(
            str(entry.get("session_id") or ""), {})
        return [_entry_summary(session_key, entry, db_meta)]

    matches = query_host_sessions(hermes_home, session_id=session_id,
                                  session_key=session_key or None, platform=platform,
                                  chat_id=chat_id, thread_id=thread_id, query=query,
                                  limit=limit)
    if matches:
        return matches

    # Caller-targeted key not yet in the current cache: preserve it so subscribe
    # can still build a session: marker, but report no resolved origin.
    if session_key and not session_id:
        return [{
            "session_key": session_key,
            "session_id": "",
            "platform": platform or "",
            "chat_type": "",
            "chat_id": chat_id or "",
            "thread_id": thread_id or "",
            "display_name": "",
            "title": "",
            "updated_at": "",
            "message_count": None,
            "last_message_at": "",
            "last_message_preview": "",
            "origin": {},
            "resolved_from_cache": False,
        }]
    return []


def is_ambiguous(matches: list[dict[str, Any]]) -> bool:
    """True when ``matches`` represents an ambiguous resolution (>1 candidate).

    A single match, or a single caller-supplied key with no current-cache entry,
    is not ambiguous. Two or more cache matches is ambiguous.
    """
    if len(matches) <= 1:
        return False
    # If all matches are the same session_key (e.g. duplicate entries), not
    # ambiguous.
    keys = {m.get("session_key") for m in matches}
    return len(keys) > 1

"""Host capability probes for the self-wake plugin.

Determines whether the Hermes host supports ``internal_session_wake_v1``.

Hermes does not currently expose a public ``hermes_cli.capabilities``
module, so probes are *structural and conservative*: they import host
internals lazily inside try/except and only report ``full`` mode when every
required primitive is present with the expected shape. Any import error,
missing attribute, or unexpected signature causes that probe to fail
(``False``), which keeps the plugin fail-closed on unsupported or
partially-patched hosts.

Each ``_probe_*`` helper is a module-level function so tests can monkeypatch
them to simulate ``full`` / ``inspect_only`` / ``unsupported`` hosts without
importing the real Hermes stack.

See ``docs/architecture.md`` and ``docs/core-patch/`` for the host capability
contract this probes.
"""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

# Minimum host capability version the plugin requires for wake-mutating
# operations. The core patch exposes ``internal_session_wake_v1``; there is no
# runtime version probe today, so ``version`` is set to 1 when all structural
# probes pass and ``None`` otherwise.
REQUIRED_CAPABILITY = "internal_session_wake"
REQUIRED_VERSION = 1


def _hermes_home(hermes_home: str | Path | None = None) -> Path:
    """Resolve the active HERMES_HOME path.

    Preference: explicit arg > $HERMES_HOME env > hermes_constants.get_hermes_home()
    > ~/.hermes. Never raises.
    """
    if hermes_home is not None:
        return Path(hermes_home).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        import os

        env = os.getenv("HERMES_HOME")
        return Path(env).expanduser() if env else Path.home() / ".hermes"


def _state_db_path(hermes_home: str | Path | None = None) -> Path:
    return _hermes_home(hermes_home) / "state.db"


def _has_callable(cls: Any, name: str) -> bool:
    fn = getattr(cls, name, None)
    return callable(fn)


def _probe_gateway_wake_session() -> dict:
    """Probe for ``GatewayRunner.wake_session`` with the v1 keyword contract."""
    try:
        from gateway.run import GatewayRunner  # type: ignore
    except Exception:
        return {"probe": "gateway.wake_session", "available": False,
                "reason": "gateway.run.GatewayRunner not importable"}
    fn = getattr(GatewayRunner, "wake_session", None)
    if not callable(fn):
        return {"probe": "gateway.wake_session", "available": False,
                "reason": "wake_session not present on GatewayRunner"}
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        # Builtins/C-extensions can lack a signature; treat conservatively.
        return {"probe": "gateway.wake_session", "available": False,
                "reason": "wake_session signature not introspectable"}
    params = sig.parameters
    # The v1 contract requires keyword-only payload + source_kind and optional
    # session_key/session_id/dedupe_key. ``self`` is present for methods.
    required = {"payload", "source_kind"}
    if not required.issubset(params.keys()):
        return {"probe": "gateway.wake_session", "available": False,
                "reason": f"wake_session missing required params {sorted(required)}"}
    return {"probe": "gateway.wake_session", "available": True}


def _probe_session_db_receipt_methods() -> dict:
    """Probe for SessionDB.create/update_session_wake_receipt."""
    try:
        from hermes_state import SessionDB  # type: ignore
    except Exception:
        return {"probe": "session_db.receipt_methods", "available": False,
                "reason": "hermes_state.SessionDB not importable"}
    missing = [m for m in ("create_session_wake_receipt", "update_session_wake_receipt")
               if not _has_callable(SessionDB, m)]
    if missing:
        return {"probe": "session_db.receipt_methods", "available": False,
                "reason": f"missing methods: {missing}"}
    return {"probe": "session_db.receipt_methods", "available": True}


def _probe_session_store_lookup() -> dict:
    """Probe for SessionStore.lookup_by_session_key / lookup_by_session_id."""
    try:
        from gateway.session import SessionStore  # type: ignore
    except Exception:
        return {"probe": "session_store.lookup", "available": False,
                "reason": "gateway.session.SessionStore not importable"}
    missing = [m for m in ("lookup_by_session_key", "lookup_by_session_id")
               if not _has_callable(SessionStore, m)]
    if missing:
        return {"probe": "session_store.lookup", "available": False,
                "reason": f"missing methods: {missing}"}
    return {"probe": "session_store.lookup", "available": True}


def _probe_receipt_table(hermes_home: str | Path | None = None) -> dict:
    """Probe for the ``session_wake_receipts`` table in state.db."""
    db = _state_db_path(hermes_home)
    if not db.exists():
        return {"probe": "receipt_table", "available": False,
                "reason": f"state.db not found at {db}"}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='session_wake_receipts'"
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {"probe": "receipt_table", "available": False,
                "reason": f"state.db unreadable: {exc}"}
    if not row:
        return {"probe": "receipt_table", "available": False,
                "reason": "session_wake_receipts table absent"}
    return {"probe": "receipt_table", "available": True}


def _probe_session_index_readable(hermes_home: str | Path | None = None) -> dict:
    """Probe whether the sessions.json index is present and readable."""
    path = _hermes_home(hermes_home) / "sessions" / "sessions.json"
    if not path.exists():
        return {"probe": "session_index", "available": False,
                "reason": f"sessions.json not found at {path}"}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"probe": "session_index", "available": False,
                    "reason": "sessions.json is not a dict keyed by session_key"}
    except Exception as exc:
        return {"probe": "session_index", "available": False,
                "reason": f"sessions.json unreadable: {exc}"}
    return {"probe": "session_index", "available": True}


def probe_wake_capability(hermes_home: str | Path | None = None) -> dict:
    """Probe for the ``internal_session_wake_v1`` host capability.

    Returns a dict with:
        - available: bool — True only when the host can actually wake sessions.
        - version: int or None — 1 when fully available, else None.
        - mode: "full" | "inspect_only" | "unsupported"
        - details: list of per-probe result dicts.

    Modes:
        full          — wake primitive + receipt methods + session lookup +
                        receipt table all present. Wake-mutating tools allowed.
        inspect_only  — wake primitive absent but session index/state.db
                        readable. Read-only discovery + doctor work; subscribe
                        and receipts fail closed.
        unsupported   — no readable session surfaces at all.
    """
    details = [
        _probe_gateway_wake_session(),
        _probe_session_db_receipt_methods(),
        _probe_session_store_lookup(),
        _probe_receipt_table(hermes_home),
        _probe_session_index_readable(hermes_home),
    ]
    by_probe = {d["probe"]: bool(d.get("available")) for d in details}
    wake_present = (
        by_probe.get("gateway.wake_session")
        and by_probe.get("session_db.receipt_methods")
        and by_probe.get("session_store.lookup")
        and by_probe.get("receipt_table")
    )
    index_readable = by_probe.get("session_index", False)
    # state.db existence (even without the receipt table) still counts as a
    # readable surface for inspect-only mode.
    state_db_exists = _state_db_path(hermes_home).exists()

    if wake_present:
        mode = "full"
        available = True
        version = REQUIRED_VERSION
    elif index_readable or state_db_exists or by_probe.get("receipt_table"):
        mode = "inspect_only"
        available = False
        version = None
    else:
        mode = "unsupported"
        available = False
        version = None

    return {
        "available": available,
        "version": version,
        "mode": mode,
        "required_capability": REQUIRED_CAPABILITY,
        "required_version": REQUIRED_VERSION,
        "details": details,
    }


def get_mode(hermes_home: str | Path | None = None) -> str:
    """Return the current compatibility mode: full, inspect_only, or unsupported."""
    return probe_wake_capability(hermes_home)["mode"]


def require_wake_capability(hermes_home: str | Path | None = None) -> Optional[dict]:
    """Return None if the wake capability is fully present, else an error dict.

    Used by wake-mutating tools (subscribe, probe) to fail closed before
    touching wake-state. A non-None return is a structured error suitable for
    direct inclusion in a tool's JSON response.
    """
    cap = probe_wake_capability(hermes_home)
    if cap["available"] and cap["version"] and cap["version"] >= REQUIRED_VERSION:
        return None
    return {
        "success": False,
        "error": "capability_missing",
        "mode": cap["mode"],
        "required_capability": REQUIRED_CAPABILITY,
        "required_version": REQUIRED_VERSION,
        "remediation": (
            "This operation requires the Hermes host capability "
            f"{REQUIRED_CAPABILITY}_v{REQUIRED_VERSION}. Apply the core patch "
            "from docs/core-patch/ or upgrade to a Hermes version that includes "
            "it. On vanilla Hermes the plugin runs in inspect-only mode and "
            "must not create wake subscriptions that would never fire."
        ),
    }

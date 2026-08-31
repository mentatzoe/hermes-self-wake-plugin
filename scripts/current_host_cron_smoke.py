#!/usr/bin/env python3
"""Current-host cron wake smoke using real Hermes scheduler/session/SQLite paths.

The only test double is an in-memory local platform transport: no external
Discord/Telegram network call is made.  Delivery still traverses the real
``cron.scheduler._deliver_result`` and ``gateway.delivery.DeliveryRouter``;
wake traverses the plugin adapter, the real Hermes MessageEvent type, and a
real disposable SessionDB receipt/message store.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
import threading
import types
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host-checkout",
        default="/Users/zmll/.hermes/hermes-agent",
        help="Exact Hermes host checkout under test",
    )
    args = parser.parse_args()
    host = Path(args.host_checkout).resolve()
    plugin_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin_root))
    sys.path.insert(0, str(host))

    home = Path(tempfile.mkdtemp(prefix="self-wake-cron-smoke-", dir="/tmp"))
    import os

    os.environ["HERMES_HOME"] = str(home)
    (home / "config.yaml").write_text(
        "cron:\n"
        "  wake_agent_on_delivery: true\n"
        "  wrap_response: false\n"
        "  mirror_delivery: false\n",
        encoding="utf-8",
    )

    from cron import scheduler
    from gateway import config as gateway_config_mod
    from gateway import run as gateway_run
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.session import SessionEntry, SessionSource
    from hermes_state import SessionDB
    from self_wake import compat_shim, cron_adapter

    host_commit = __import__("subprocess").check_output(
        ["git", "-C", str(host), "rev-parse", "HEAD"], text=True
    ).strip()
    compatibility = cron_adapter.check_compatibility(module=scheduler)
    if not compatibility.get("compatible"):
        print(json.dumps({"ok": False, "stage": "compatibility", **compatibility}))
        return 2

    # Real plugin discovery occurs during GatewayRunner construction, before
    # the active runner weakref/session store exists. Install in that ordering:
    # wrappers must remain adopted while the operational probe is temporarily
    # unavailable, then become healthy after the runner finishes construction.
    gateway_run._gateway_runner_ref = lambda: None
    install = cron_adapter.install(module=scheduler)
    startup_adoption = cron_adapter.probe(module=scheduler)

    session_key = "agent:main:discord:thread:1496647833573392454:1496647833573392454"
    session_id = "smoke_existing_session"
    origin = SessionSource(
        platform=Platform.DISCORD,
        chat_id="1496647833573392454",
        thread_id="1496647833573392454",
        chat_type="thread",
        user_id="smoke-user",
    )
    entry = SessionEntry(
        session_key=session_key,
        session_id=session_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        origin=origin,
        platform=Platform.DISCORD,
        chat_type="thread",
    )

    db_path = home / "state.db"
    session_db = SessionDB(db_path)
    session_db.create_session(session_id, "gateway")
    session_db.create_session_wake_receipt = types.MethodType(
        compat_shim._shim_create_session_wake_receipt, session_db
    )
    session_db.update_session_wake_receipt = types.MethodType(
        compat_shim._shim_update_session_wake_receipt, session_db
    )

    class Store:
        def list_sessions(self, active_minutes=None):
            return [entry]

        def lookup_by_session_key(self, value):
            return entry if value == session_key else None

        def lookup_by_session_id(self, value):
            return entry if value == session_id else None

    class LocalAdapter:
        splits_long_messages = False
        supports_inchannel_continuable = False

        def __init__(self):
            self.sent = []
            self.received = []
            self._active_sessions = {}
            self._session_tasks = {}
            self.config = types.SimpleNamespace(extra={})

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.sent.append({"chat_id": chat_id, "content": content, "metadata": metadata})
            return {"success": True, "message_id": "local-delivery-1"}

        async def handle_message(self, event):
            self.received.append(event)
            session_db.append_message(session_id, "user", event.text)
            session_db.append_message(
                session_id,
                "assistant",
                "Smoke responder consumed the cron delivery and queued follow-through.",
            )

    adapter = LocalAdapter()
    runner = types.SimpleNamespace(
        session_store=Store(),
        _session_db=session_db,
        adapters={Platform.DISCORD: adapter},
    )
    runner._lookup_session_entry_for_wake = types.MethodType(
        compat_shim._shim_lookup_session_entry_for_wake, runner
    )
    runner._wake_message_ids_after = types.MethodType(
        compat_shim._shim_wake_message_ids_after, runner
    )
    runner.wake_session = types.MethodType(compat_shim._shim_wake_session, runner)
    gateway_run._gateway_runner_ref = lambda: runner

    config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="local-smoke")}
    )
    gateway_config_mod.load_gateway_config = lambda: config
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    ready.wait(timeout=5)

    adoption = cron_adapter.probe(module=scheduler)
    payload = "[AZAZIE RETURN — POSITIVE] prepare the attached PDF"
    job = {
        "id": "smoke-cron-delivery",
        "name": "Azazie return monitor",
        "deliver": "origin",
        "origin": {
            "platform": "discord",
            "chat_id": origin.chat_id,
            "thread_id": origin.thread_id,
            "user_id": origin.user_id,
            "session_key": session_key,
            "session_id": session_id,
        },
    }
    try:
        delivery_error = scheduler._deliver_result(
            job, payload, adapters={Platform.DISCORD: adapter}, loop=loop
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
        cron_adapter.uninstall()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    receipt = conn.execute(
        "SELECT * FROM session_wake_receipts WHERE source_kind='cron_delivery' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    messages = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (session_id,)
    ).fetchall()
    conn.close()
    session_db.close()

    result = {
        "ok": bool(
            delivery_error is None
            and install.get("installed") is True
            and startup_adoption.get("available") is False
            and "active GatewayRunner unavailable" in str(startup_adoption.get("reason"))
            and adoption.get("available") is True
            and all((adoption.get("wrappers") or {}).values())
            and len((adoption.get("wrappers") or {})) == 2
            and len(adapter.sent) == 1
            and len(adapter.received) == 1
            and receipt is not None
            and receipt["status"] == "agent_responded"
            and any(row["role"] == "assistant" for row in messages)
        ),
        "host_commit": host_commit,
        "compatibility": compatibility,
        "adapter_install": install,
        "startup_adoption": startup_adoption,
        "adapter_adoption": adoption,
        "delivery_error": delivery_error,
        "delivery_count": len(adapter.sent),
        "internal_event_count": len(adapter.received),
        "receipt": dict(receipt) if receipt is not None else None,
        "message_roles": [row["role"] for row in messages],
        "target_session_key": session_key,
        "scratch_home": str(home),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

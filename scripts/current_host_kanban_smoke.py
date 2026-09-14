#!/usr/bin/env python3
"""Isolated native Kanban integration probe, never a live gateway canary.

Uses real Hermes Kanban/SessionStore/AsyncSessionDB and BasePlatformAdapter
routing. Only platform I/O and inference are local deterministic substitutes.
No credentials, production boards, real messages, or gateway restarts are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-checkout", required=True)
    parser.add_argument("--case", choices=["completed", "blocked", "crashed", "busy", "legacy", "notify"], default="completed")
    parser.add_argument("--sync-db", action="store_true")
    args = parser.parse_args()
    host = Path(args.host_checkout).resolve()
    plugin = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="kanban-return-probe-") as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        os.environ["HERMES_KANBAN_DB"] = str(home / "kanban.db")
        os.environ["HERMES_KANBAN_BOARD"] = "default"
        os.environ["HERMES_PROFILE"] = "default"
        (home / "config.yaml").write_text("self_wake:\n  compat_shim_enabled: true\nkanban:\n  dispatch_in_gateway: true\n", encoding="utf-8")
        sys.path.insert(0, str(host))
        sys.path.insert(0, str(plugin))
        from gateway.config import GatewayConfig, Platform, PlatformConfig
        from gateway.platforms.base import BasePlatformAdapter, SendResult
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource, SessionStore
        from hermes_cli import kanban_db as kb
        from hermes_state import AsyncSessionDB
        from self_wake import compat_shim

        install = compat_shim.install_shim()
        if not install.get("installed"):
            print(json.dumps({"ok": False, "stage": "install", "report": install}))
            return 2

        async def run():
            config = GatewayConfig()
            store = SessionStore(home / "sessions", config)
            origin = SessionSource(platform=Platform.DISCORD, chat_id="probe-parent", chat_type="dm", user_id="probe-user")
            entry = store.get_or_create_session(origin)
            db = store._db
            handled = []
            sent = []

            class LocalAdapter(BasePlatformAdapter):
                def __init__(self):
                    super().__init__(PlatformConfig(enabled=True), Platform.DISCORD)

                async def connect(self, *, is_reconnect=False):
                    return True

                async def disconnect(self):
                    self._mark_disconnected()

                async def send(self, chat_id, content, reply_to=None, metadata=None):
                    sent.append({"chat_id": chat_id, "text": content})
                    return SendResult(success=True, message_id="local-only")

                async def get_chat_info(self, chat_id):
                    return {"id": chat_id, "type": "dm"}

            adapter = LocalAdapter()
            runner = GatewayRunner.__new__(GatewayRunner)
            runner.config = config
            runner.session_store = store
            runner._session_db = db if args.sync_db else AsyncSessionDB(db)
            runner.adapters = {Platform.DISCORD: adapter}
            runner._profile_adapters = {}
            runner._running = True
            runner._draining = False
            runner._queued_events = {}
            runner._kanban_notifier_profile = "default"
            runner._kanban_sub_fail_counts = {}
            runner._is_user_authorized = lambda source: source.user_id == "probe-user"
            runner._effective_busy_input_mode = lambda source: "queue"

            async def responder(event):
                actual = store.get_or_create_session(event.source)
                handled.append({"session_id": actual.session_id, "session_key": actual.session_key, "text": event.text, "internal": event.internal, "allow_gateway_control": event.allow_gateway_control})
                db.append_message(actual.session_id, "user", event.text, platform_message_id=event.message_id)
                db.append_message(actual.session_id, "assistant", "Local probe responder consumed this terminal event.")
                return None

            adapter.set_message_handler(responder)
            adapter.set_busy_session_handler(runner._handle_active_session_busy_message)
            if args.case == "busy":
                adapter._active_sessions[entry.session_key] = asyncio.Event()

            conn = kb.connect()
            try:
                tid = kb.create_task(conn, title="Explicit isolated native return integration probe", assignee=None, session_id="worker-provenance-not-parent")
                kb.add_notify_sub(conn, task_id=tid, platform="discord", chat_id=origin.chat_id, chat_type="dm", user_id=("session:" + entry.session_key) if args.case == "legacy" else origin.user_id, notifier_profile="default", delivery_mode="notify" if args.case in {"legacy", "notify"} else "wake")
                before_cursor = kb.list_notify_subs(conn, tid)[0]["last_event_id"]
                if args.case == "blocked":
                    kb.block_task(conn, tid, reason="Explicit isolated probe: needs input")
                elif args.case == "crashed":
                    with kb.write_txn(conn):
                        kb._append_event(conn, tid, "crashed", {"error": "Explicit isolated probe event, not a real worker crash"})
                else:
                    kb.complete_task(conn, tid, summary="Explicit isolated native return probe completed")
            finally:
                conn.close()

            real_sleep = asyncio.sleep

            async def tick_sleep(delay):
                if delay == 5:
                    return
                if delay == 1:
                    runner._running = False
                await real_sleep(0)

            async def tick():
                runner._running = True
                with patch("asyncio.sleep", tick_sleep):
                    await runner._kanban_notifier_watcher(interval=1)

            await tick()
            busy_checks = {}
            if args.case == "busy":
                conn = kb.connect()
                try:
                    busy_subs = kb.list_notify_subs(conn, tid)
                finally:
                    conn.close()
                busy_checks = {
                    "busy_cursor_retained": busy_subs[0]["last_event_id"] == before_cursor,
                    "busy_no_receive_or_send": not handled and not sent,
                    "busy_no_memory_queue": not runner._queued_events and not adapter._pending_messages,
                }
                adapter._active_sessions.clear()
                await tick()
            # An unchanged tick must not dispatch the terminal event again.
            await tick()
            tasks = list(adapter._session_tasks.values())
            if tasks:
                await asyncio.gather(*tasks)
            conn = kb.connect()
            try:
                subs = kb.list_notify_subs(conn, tid)
            finally:
                conn.close()
            conn = sqlite3.connect(home / "state.db")
            conn.row_factory = sqlite3.Row
            try:
                has_receipts = conn.execute("SELECT 1 FROM sqlite_master WHERE name='session_wake_receipts'").fetchone()
                receipts = [dict(r) for r in conn.execute("SELECT status,target_session_id,target_session_key,injected_message_id,assistant_message_id FROM session_wake_receipts")] if has_receipts else []
            finally:
                conn.close()
            queued = list(runner._queued_events.get(entry.session_key, []))
            expects_wake = args.case != "notify"
            checks = {
                "subscription_retained": len(subs) == 1,
                "no_passive_ping_in_wake_mode": not sent if expects_wake else len(sent) == 1,
                "exact_parent_receipt": len(receipts) == 1 and receipts[0]["target_session_id"] == entry.session_id if expects_wake else not receipts,
                "exact_parent_consumed_once": ((len(handled) == 1 and handled[0]["session_id"] == entry.session_id and receipts[0]["status"] in {"dispatched", "agent_responded"} and receipts[0]["injected_message_id"] is not None) if expects_wake and receipts else not expects_wake and not handled),
                "cursor_advanced_after_delivery": bool(subs and subs[0]["last_event_id"] > before_cursor),
                "payload_not_gateway_control": all(not event.allow_gateway_control for event in queued) and all(not row["allow_gateway_control"] for row in handled),
                **busy_checks,
            }
            return {"ok": all(checks.values()), "probe": "isolated-real-host/local-transport-and-responder", "live_gateway_test": False, "case": args.case, "async_db": not args.sync_db, "checks": checks, "before_cursor": before_cursor, "after_cursor": subs[0]["last_event_id"] if subs else None, "receipts": receipts, "received_count": len(handled), "passive_send_count": len(sent), "queued_count": len(queued)}

        try:
            result = asyncio.run(run())
            result["host_commit"] = subprocess.check_output(["git", "-C", str(host), "rev-parse", "HEAD"], text=True).strip()
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1
        finally:
            compat_shim.uninstall_shim()


if __name__ == "__main__":
    raise SystemExit(main())

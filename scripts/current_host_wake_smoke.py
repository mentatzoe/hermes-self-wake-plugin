#!/usr/bin/env python3
"""Real-host wake transport/receipt probe, excluding the Kanban board consumer.

Uses a temporary Hermes home, real host classes and real message/receipt rows.
Transport and inference are local deterministic substitutes; this is neither
live-gateway delivery proof nor an end-to-end worker-return claim.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-checkout", required=True)
    parser.add_argument("--busy", action="store_true")
    parser.add_argument("--active-profile", default="default", help="Simulated single-profile owner; never opens that profile home")
    parser.add_argument("--subscription-profile", help="Override notifier owner for foreign-profile rejection probes")
    args = parser.parse_args()
    plugin = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="wake-transport-probe-") as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        (home / "config.yaml").write_text("self_wake:\n  compat_shim_enabled: true\n")
        sys.path.insert(0, str(Path(args.host_checkout).resolve()))
        sys.path.insert(0, str(plugin))
        from gateway.config import GatewayConfig, Platform, PlatformConfig
        from gateway.platforms.base import BasePlatformAdapter, SendResult
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource, SessionStore
        from hermes_state import AsyncSessionDB
        from self_wake import compat_shim

        store = SessionStore(home / "sessions", GatewayConfig())
        install = compat_shim.install_shim(hermes_home=str(home))
        if not install.get("installed"):
            print(json.dumps({"ok": False, "stage": "install", "report": install}))
            return 2

        async def run():
            source = SessionSource(platform=Platform.DISCORD, chat_id="probe-parent", chat_type="dm", user_id="probe-user")
            entry = store.get_or_create_session(source)
            db = store._db
            observed = []

            class LocalAdapter(BasePlatformAdapter):
                def __init__(self):
                    super().__init__(PlatformConfig(enabled=True), Platform.DISCORD)

                async def connect(self, *, is_reconnect=False):
                    return True

                async def disconnect(self):
                    self._mark_disconnected()

                async def send(self, chat_id, content, reply_to=None, metadata=None):
                    return SendResult(success=True, message_id="local-only")

                async def get_chat_info(self, chat_id):
                    return {"id": chat_id, "type": "dm"}

            adapter = LocalAdapter()
            runner = GatewayRunner.__new__(GatewayRunner)
            runner.config = GatewayConfig()
            runner.session_store = store
            runner._session_db = AsyncSessionDB(db)
            runner.adapters = {Platform.DISCORD: adapter}
            runner._draining = False
            runner._active_profile_name = lambda: args.active_profile

            async def responder(event):
                actual = store.get_or_create_session(event.source)
                observed.append((actual.session_id, event.allow_gateway_control))
                db.append_message(actual.session_id, "user", event.text, platform_message_id=event.message_id)
                db.append_message(actual.session_id, "assistant", "Local deterministic probe response; no model called.")
                return None

            adapter.set_message_handler(responder)
            sub = {"delivery_mode": "wake", "platform": "discord", "chat_id": source.chat_id, "chat_type": "dm", "user_id": source.user_id,
                   "notifier_profile": args.subscription_profile or args.active_profile}
            expected_source = compat_shim._native_subscription_source(runner, sub)
            kind, key = runner._kanban_internal_wake_target(sub)
            assert kind == "session_key" and key == entry.session_key
            if args.busy:
                adapter._active_sessions[entry.session_key] = asyncio.Event()
            payload = "Explicit isolated wake transport probe: completed terminal event."
            async def wake():
                return await runner.wake_session(payload=payload, source_kind="kanban", session_key=key, dedupe_key="isolated-probe", defer_if_busy=True, expected_source=expected_source)
            first = await wake()
            busy_retained = None
            if args.busy:
                busy_retained = first["status"] == "failure" and not observed
                adapter._active_sessions.clear()
                first = await wake()
            second = await wake()
            messages = db.get_messages(entry.session_id)
            result = {
                "ok": first["status"] == "dispatched" and first.get("injected_message_id") is not None and second["status"] == "deduped" and observed == [(entry.session_id, False)] and [m["role"] for m in messages] == ["user", "assistant"] and (busy_retained is not False),
                "probe": "real-host-wake-transport-with-local-responder",
                "live_gateway_test": False, "kanban_board_consumer_test": False,
                "busy_then_idle": busy_retained,
                "simulated_active_profile": args.active_profile,
                "subscription_profile": sub["notifier_profile"],
                "stored_source_profile": source.profile,
                "target_session_id": entry.session_id,
                "target_session_key": entry.session_key,
                "receipt_status": first["status"],
                "repeat_status": second["status"],
                "injected_message_id": first.get("injected_message_id"),
                "assistant_message_id": first.get("assistant_message_id"),
                "message_roles": [m["role"] for m in messages],
                "received_count": len(observed),
            }
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1

        try:
            return asyncio.run(run())
        finally:
            compat_shim.uninstall_shim()


if __name__ == "__main__":
    raise SystemExit(main())

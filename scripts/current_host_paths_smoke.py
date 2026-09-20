#!/usr/bin/env python3
"""Real-host cron and cross-process session-message checks in a temporary home.

The scheduler, routing index, adapter admission and SQLite receipts are real.
The transport and model response are deterministic local substitutes. This
never contacts a live platform and is not a live-adoption receipt.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-checkout", required=True)
    parser.add_argument("--case", choices=["cron", "message", "all"], default="all")
    parser.add_argument("--telegram-topic", action="store_true", help="Exercise channel-DM topic routing against a flat sibling")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    host = Path(args.host_checkout).resolve()
    sys.path.insert(0, str(host))
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="self-wake-paths-") as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        os.environ["PYTHONPATH"] = str(host)
        plugin_home = home / "plugins" / "self-wake"
        shutil.copytree(root / "self_wake", plugin_home / "self_wake", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(root / "plugin.yaml", plugin_home / "plugin.yaml")
        shutil.copy2(root / "__init__.py", plugin_home / "__init__.py")
        (home / "config.yaml").write_text(
            "self_wake:\n  compat_shim_enabled: true\n"
            "cron:\n  wake_agent_on_delivery: true\n  mirror_delivery: false\n  wrap_response: false\n"
            "plugins:\n  enabled: [self-wake]\n  entries:\n    self-wake:\n      allow_gateway_injection: true\n")
        from cron import scheduler
        from gateway import config as config_mod, run as run_mod
        from gateway.config import GatewayConfig, Platform, PlatformConfig
        from gateway.platforms.base import BasePlatformAdapter, SendResult
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource, SessionStore
        from hermes_state import AsyncSessionDB
        from self_wake import compat_shim, cron_adapter, outbox

        install = compat_shim.install_shim(hermes_home=str(home))
        if not install.get("installed") or not install.get("cron_adapter", {}).get("installed"):
            print(json.dumps({"ok": False, "stage": "install", "report": install}))
            return 2
        store = SessionStore(home / "sessions", GatewayConfig())
        platform = Platform.TELEGRAM if args.telegram_topic else Platform.DISCORD
        source = SessionSource(platform=platform, chat_id="12345" if args.telegram_topic else "probe-parent",
                               chat_type="group" if args.telegram_topic else "dm", user_id="probe-user",
                               thread_id="42" if args.telegram_topic else None)
        entry = store.get_or_create_session(source)
        wrong = store.get_or_create_session(SessionSource(platform=platform,
            chat_id=source.chat_id if args.telegram_topic else "other", chat_type=source.chat_type,
            user_id=source.user_id if args.telegram_topic else "other-user"))
        db = store._db
        (home / "sessions" / "sessions.json").unlink(missing_ok=True)

        async def run():
            class Adapter(BasePlatformAdapter):
                def __init__(self):
                    super().__init__(PlatformConfig(enabled=True), platform)
                    self.sent = []
                async def connect(self, *, is_reconnect=False):
                    return True
                async def disconnect(self):
                    self._mark_disconnected()
                async def send(self, chat_id, content, reply_to=None, metadata=None):
                    self.sent.append((chat_id, content))
                    return SendResult(success=True, message_id="local-message")
                async def get_chat_info(self, chat_id):
                    return {"id": chat_id, "type": "channel" if args.telegram_topic else "dm"}

            adapter = Adapter()
            runner = GatewayRunner.__new__(GatewayRunner)
            runner.config = GatewayConfig()
            runner.session_store = store
            runner._session_db = AsyncSessionDB(db)
            runner.adapters = {platform: adapter}
            runner._draining = False
            runner._running = True
            runner._active_profile_name = lambda: "default"
            runner._is_user_authorized = lambda origin, **kw: origin.user_id == "probe-user"
            run_mod._gateway_runner_ref = lambda: runner
            config_mod.load_gateway_config = lambda: GatewayConfig(platforms={platform: PlatformConfig(enabled=True, token="local-only")})
            received = []
            async def responder(event):
                assert not event.allow_gateway_control
                assert event.metadata["gateway_session_id"] == entry.session_id
                received.append(event)
                db.append_message(entry.session_id, "user", event.text, platform_message_id=event.message_id)
                db.append_message(entry.session_id, "assistant", "Local responder consumed the message and recorded follow-through.")
                event._streamed_final_response = "Local responder consumed the message and recorded follow-through."
            adapter.set_message_handler(responder)
            cases = {}
            if args.case in {"cron", "all"}:
                loop = asyncio.get_running_loop()
                job = {"id": "probe-cron", "name": "Probe", "execution_id": "fire-one", "deliver": "origin", "origin": source.to_dict()}
                error = await asyncio.to_thread(scheduler._deliver_result, job, "Cron follow-through probe", adapters=runner.adapters, loop=loop)
                # The post-delivery adapter only persists; the receiver owns retry.
                await outbox.drain_once(runner)
                first_count = len(received)
                await outbox.drain_once(runner)
                cases["cron"] = {"ok": error is None and len(adapter.sent) == 1 and first_count == 1 and len(received) == 1,
                                 "delivery_count": len(adapter.sent), "received_count": first_count,
                                 "observation": cron_adapter.status()["last_observation"]}
            if args.case in {"message", "all"}:
                before = len(received)
                message_file = home / "message.txt"
                message_file.write_text("Message from another process: record follow-through.")
                command = [sys.executable, "-m", "hermes_cli.main", "self-wake", "send", "--session-id", entry.session_id,
                           "--dedupe-key", "message-one", "--file", str(message_file)]
                proc = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, timeout=30, cwd=home)
                assert proc.returncode == 0, proc.stdout + proc.stderr
                request = json.loads(proc.stdout.strip().splitlines()[-1])
                # A real task owns the busy lane in this disposable adapter.
                release = asyncio.Event()
                async def occupied():
                    await release.wait()
                busy = asyncio.create_task(occupied())
                adapter._session_tasks[entry.session_key] = busy
                await outbox.drain_once(runner)
                retained = outbox.get(request["request_id"])["status"] == "queued" and len(received) == before
                release.set()
                await busy
                await outbox.drain_once(runner)
                duplicate = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, timeout=30, cwd=home)
                assert duplicate.returncode == 0, duplicate.stderr
                await outbox.drain_once(runner)
                state = outbox.get(request["request_id"])
                cases["message"] = {"ok": retained and len(received) == before + 1 and state["status"] == "agent_responded",
                                    "busy_retained": retained, "request": state, "new_turns": len(received) - before}
            with sqlite3.connect(home / "state.db") as conn:
                conn.row_factory = sqlite3.Row
                receipts = [dict(r) for r in conn.execute("SELECT id,source_kind,status,injected_message_id,assistant_message_id,target_session_id FROM session_wake_receipts")]
            result = {"ok": all(c["ok"] for c in cases.values()) and not db.get_messages(wrong.session_id)
                      and all(r["status"] == "agent_responded" and r["assistant_message_id"] for r in receipts),
                      "host_commit": subprocess.check_output(["git", "-C", str(host), "rev-parse", "HEAD"], text=True).strip(),
                      "live_gateway_test": False, "cases": cases, "receipts": receipts,
                      "wrong_target_messages": len(db.get_messages(wrong.session_id))}
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1
        try:
            return asyncio.run(run())
        finally:
            compat_shim.uninstall_shim()


if __name__ == "__main__":
    raise SystemExit(main())

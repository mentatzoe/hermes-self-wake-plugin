#!/usr/bin/env python3
"""Run native completion lifecycle regressions without touching the host checkout.

Uses the host's actual tests and implementation with local adapter substitutes.
This is not a live terminal/model canary or a durable self-wake receipt claim.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-checkout", required=True, type=Path)
    args = parser.parse_args()
    host = args.host_checkout.resolve()
    cases = [
        "test_concurrent_process_watchers_coalesce_one_session_completion_turn",
        "test_completion_arriving_during_batch_delivery_schedules_next_flush",
        "test_completion_batches_do_not_cross_conversation_routes",
        "test_failed_coalesced_delivery_retries_all_entries",
        "test_coalesced_success_records_every_completion_identity",
        "test_shutdown_cancels_batch_during_window_and_settles_waiter_for_retry",
        "test_shutdown_cancels_blocked_batch_delivery_and_keeps_it_retryable",
        "test_completion_enqueue_stays_retryable_after_shutdown_starts",
    ]
    with tempfile.TemporaryDirectory(prefix="native-completion-") as tmp:
        home = Path(tmp)
        test = home / "test_completion_delivery.py"
        shutil.copy2(host / "tests/gateway/test_completion_delivery.py", test)
        env = dict(os.environ, HERMES_HOME=tmp, PYTHONPATH=str(host),
                   PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--confcutdir", tmp,
                   *[str(test) + "::" + case for case in cases]]
        result = subprocess.run(command, cwd=home, env=env, capture_output=True, text=True, timeout=90)
        print(json.dumps({"ok": result.returncode == 0, "source": "native_background_completion",
                          "live_gateway_test": False, "cases": cases,
                          "output": result.stdout, "error": result.stderr}, sort_keys=True))
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

"""Receipt-backed end-to-end smoke against the exact local host checkout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HOST = Path(os.environ.get("HERMES_SELF_WAKE_HOST_CHECKOUT", "/nonexistent"))
EXPECTED_HOST_COMMIT = "dcbf5b71bc65fc6f7168c601bc04e84b22f912cd"


@pytest.mark.skipif(not (HOST / "cron" / "scheduler.py").exists(), reason="current host checkout absent")
def test_current_host_cron_delivery_to_existing_session_receipt_smoke():
    script = Path(__file__).resolve().parents[1] / "scripts" / "current_host_paths_smoke.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--host-checkout", str(HOST)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["ok"] is True
    assert result["host_commit"] == EXPECTED_HOST_COMMIT
    assert result["cases"]["cron"]["delivery_count"] == 1
    assert result["cases"]["message"]["busy_retained"]
    assert result["cases"]["message"]["new_turns"] == 1
    assert {r["source_kind"] for r in result["receipts"]} == {"cron_delivery", "session_message"}
    assert all(r["status"] == "agent_responded" and r["assistant_message_id"] for r in result["receipts"])
    assert result["wrong_target_messages"] == 0

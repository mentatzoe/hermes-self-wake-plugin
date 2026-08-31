"""Receipt-backed end-to-end smoke against the exact local host checkout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HOST = Path(os.environ.get("HERMES_SELF_WAKE_HOST_CHECKOUT", "/Users/zmll/.hermes/hermes-agent"))
EXPECTED_HOST_COMMIT = "21895bd39d9bc8a1cda307c9b0a0eb6fc98a8844"


@pytest.mark.skipif(not (HOST / "cron" / "scheduler.py").exists(), reason="current host checkout absent")
def test_current_host_cron_delivery_to_existing_session_receipt_smoke():
    script = Path(__file__).resolve().parents[1] / "scripts" / "current_host_cron_smoke.py"
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
    assert result["delivery_count"] == 1
    assert result["internal_event_count"] == 1
    assert result["adapter_adoption"]["source"] == "shim"
    assert result["adapter_adoption"]["wrappers"] == {
        "cron.scheduler._deliver_result": True,
        "cron.scheduler._maybe_mirror_cron_delivery": True,
    }
    assert result["receipt"]["source_kind"] == "cron_delivery"
    assert result["receipt"]["status"] == "agent_responded"
    assert result["receipt"]["target_session_key"] == result["target_session_key"]
    assert result["message_roles"] == ["user", "assistant"]

"""Tests for the diagnostics (doctor) module."""
from __future__ import annotations

from self_wake import doctor


def test_doctor_returns_structured(hermes_home):
    result = doctor.run_diagnostics()
    for key in ("ok", "mode", "checks", "failures", "warnings", "remediation",
                "capability", "summary"):
        assert key in result
    assert result["mode"] in ("full", "inspect_only", "unsupported")
    assert isinstance(result["checks"], list) and len(result["checks"]) >= 4


def test_doctor_unsupported_mode(unsupported_capability, hermes_home):
    """Unsupported host: ok=False, mode=unsupported, has remediation."""
    result = doctor.run_diagnostics()
    assert result["ok"] is False
    assert result["mode"] == "unsupported"
    assert result["capability"]["available"] is False
    assert len(result["remediation"]) >= 1


def test_doctor_inspect_only(inspect_only_capability, hermes_home):
    """Inspect-only host: reports missing capability + receipt table."""
    result = doctor.run_diagnostics()
    assert result["mode"] == "inspect_only"
    assert result["ok"] is False
    assert any("capability" in f for f in result["failures"])


def test_doctor_full_mode(full_capability, state_db_with_receipts, kanban_backend,
                          hermes_home):
    """Full host: ok=True, capability available, receipt table present."""
    result = doctor.run_diagnostics(backend=kanban_backend)
    assert result["mode"] == "full"
    assert result["ok"] is True
    assert result["capability"]["available"] is True
    # The failure receipt should surface as a warning.
    assert any("failure" in w for w in result["warnings"])


def test_doctor_reports_visible_only_subs(full_capability, state_db_with_receipts,
                                          kanban_backend, hermes_home):
    """A visible-only sub on a capable host is flagged as a warning."""
    from self_wake import kanban as kanban_mod
    kanban_mod.create_wake_subscription(
        task_id="t_vis",
        target_session_key="agent:main:discord:thread:1511162896762142980:1511162896762142980",
        backend=kanban_backend, force_degraded_visible_only=True)
    result = doctor.run_diagnostics(backend=kanban_backend)
    assert any("visible-only" in w for w in result["warnings"])


def test_doctor_check_names_cover_required_surfaces(full_capability,
                                                    state_db_with_receipts,
                                                    kanban_backend, hermes_home):
    """Doctor checks cover the design's required diagnostic surfaces."""
    result = doctor.run_diagnostics(backend=kanban_backend)
    names = {c["name"] for c in result["checks"]}
    for required in ("core_capability", "session_index", "receipt_table",
                     "kanban_db", "cron_wake_config"):
        assert required in names

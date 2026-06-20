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


# --------------------------------------------------------------------------- #
# H1: kanban_db diagnostic must not be tautologically ok
# --------------------------------------------------------------------------- #
def test_doctor_kanban_db_fails_when_backend_unavailable(hermes_home):
    """When the Kanban backend is unavailable (hermes_cli not importable),
    the kanban_db check must report "fail" — not a false-positive "ok, 0 subs"
    (review H1: the old ``subs or True`` check could never report fail)."""
    result = doctor.run_diagnostics()
    kanban_db = next(c for c in result["checks"] if c["name"] == "kanban_db")
    assert kanban_db["status"] == "fail"
    assert "kanban backend unavailable" in kanban_db["detail"]


def test_doctor_kanban_db_ok_with_zero_subs_when_available(
        full_capability, state_db_with_receipts, kanban_backend, hermes_home):
    """A reachable backend with zero subscriptions is "ok", distinct from an
    unreachable backend's "fail"."""
    result = doctor.run_diagnostics(backend=kanban_backend)
    kanban_db = next(c for c in result["checks"] if c["name"] == "kanban_db")
    assert kanban_db["status"] == "ok"
    assert "0 notify sub" in kanban_db["detail"]


# --------------------------------------------------------------------------- #
# M1: session_index should be ok only when n_sessions > 0
# --------------------------------------------------------------------------- #
def test_doctor_session_index_warns_when_empty(full_capability,
                                                state_db_with_receipts,
                                                kanban_backend, hermes_home,
                                                monkeypatch):
    """An empty/missing session index is "warn", not a misleading "ok"
    (review M1: the old ``n_sessions >= 0`` check was always ok)."""
    from self_wake import sessions as sessions_mod
    monkeypatch.setattr(sessions_mod, "read_sessions_index",
                        lambda hh=None: {})
    result = doctor.run_diagnostics(backend=kanban_backend)
    si = next(c for c in result["checks"] if c["name"] == "session_index")
    assert si["status"] == "warn"
    assert any("session index empty" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# M2: capability diagnostics distinguish import_changed / attribute_missing /
# receipt_table_absent
# --------------------------------------------------------------------------- #
def test_doctor_capability_import_changed_guidance(unsupported_capability, hermes_home):
    """When probes fail on import (Hermes upgraded, module paths changed),
    doctor must say so distinctly — not 'core patch not applied'
    (review M2)."""
    result = doctor.run_diagnostics()
    cap_check = next(c for c in result["checks"] if c["name"] == "core_capability")
    assert cap_check["status"] == "fail"
    assert "import_changed" in cap_check["detail"]
    assert "not importable" in cap_check["detail"]
    # The remediation must point at a module-path / upgrade cause, and must
    # NOT tell the operator to re-apply the core patch as the fix.
    assert "upgraded" in cap_check["remediation"]
    assert "module paths" in cap_check["remediation"]
    assert "Re-applying the core patch will NOT fix" in cap_check["remediation"]


def test_doctor_capability_attribute_missing_guidance(inspect_only_capability, hermes_home):
    """When modules import but wake_session/receipt methods are absent (core
    patch not applied), doctor must say 'patch not applied', distinct from
    a module-path change (review M2)."""
    result = doctor.run_diagnostics()
    cap_check = next(c for c in result["checks"] if c["name"] == "core_capability")
    assert cap_check["status"] == "fail"
    assert "attribute_missing" in cap_check["detail"]
    assert "not present" in cap_check["detail"]
    assert "core-patch" in cap_check["remediation"] or "core patch" in cap_check["remediation"]
    # And it must NOT be the import_changed message.
    assert "module paths" not in cap_check["remediation"]

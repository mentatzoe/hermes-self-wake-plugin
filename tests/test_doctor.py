"""Tests for diagnostics module."""

from self_wake.doctor import run_diagnostics


def test_doctor_returns_structured():
    """run_diagnostics returns expected structure."""
    result = run_diagnostics()
    assert isinstance(result, dict)
    assert "ok" in result
    assert "mode" in result
    assert "checks" in result
    assert "failures" in result
    assert "warnings" in result
    assert "remediation" in result
    assert result["mode"] in ("full", "inspect_only", "unsupported")


def test_doctor_in_stub_mode():
    """With stub implementation, doctor reports unsupported."""
    result = run_diagnostics()
    assert result["ok"] is False
    assert result["mode"] in ("unsupported", "inspect_only")

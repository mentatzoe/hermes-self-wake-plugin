"""Tests for capability probe module."""

from self_wake.capabilities import probe_wake_capability, get_mode, require_wake_capability


def test_probe_returns_structured_result():
    """Capability probe returns expected structure."""
    result = probe_wake_capability()
    assert isinstance(result, dict)
    assert "available" in result
    assert "version" in result
    assert "mode" in result
    assert "details" in result
    assert result["mode"] in ("full", "inspect_only", "unsupported")


def test_get_mode_returns_valid_string():
    """get_mode returns one of the expected mode strings."""
    mode = get_mode()
    assert mode in ("full", "inspect_only", "unsupported")


def test_require_capability_returns_error_when_missing():
    """require_wake_capability returns error dict when capability missing."""
    # With stub implementation, capability should be missing
    result = require_wake_capability()
    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["error"] == "capability_missing"

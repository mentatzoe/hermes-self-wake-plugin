"""Tests for the host capability probe module."""
from __future__ import annotations

from self_wake import capabilities as caps


def test_probe_returns_structured_result(hermes_home):
    result = caps.probe_wake_capability()
    for key in ("available", "version", "mode", "details", "required_capability", "required_version"):
        assert key in result
    assert result["mode"] in ("full", "inspect_only", "unsupported")
    assert result["required_capability"] == "internal_session_wake"
    assert result["required_version"] == 1
    assert isinstance(result["details"], list) and len(result["details"]) == 5


def test_unsupported_mode_when_nothing_readable(monkeypatch, tmp_path):
    """Empty HERMES_HOME with no sessions.json and no state.db → unsupported."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty"))
    # Force all structural probes to absent.
    monkeypatch.setattr(caps, "_probe_gateway_wake_session",
                        lambda: {"probe": "g", "available": False})
    monkeypatch.setattr(caps, "_probe_session_db_receipt_methods",
                        lambda: {"probe": "s", "available": False})
    monkeypatch.setattr(caps, "_probe_session_store_lookup",
                        lambda: {"probe": "l", "available": False})
    cap = caps.probe_wake_capability()
    assert cap["mode"] == "unsupported"
    assert cap["available"] is False
    assert cap["version"] is None


def test_inspect_only_mode_when_index_readable_but_no_wake(
        inspect_only_capability, hermes_home):
    """Read-only host: session index present, wake primitive absent."""
    cap = caps.probe_wake_capability()
    assert cap["mode"] == "inspect_only"
    assert cap["available"] is False


def test_full_mode_when_all_probes_pass(full_capability, state_db_with_receipts):
    """All structural probes present → full mode, version 1."""
    cap = caps.probe_wake_capability()
    assert cap["mode"] == "full"
    assert cap["available"] is True
    assert cap["version"] == 1


def test_require_capability_returns_none_when_full(full_capability, state_db_with_receipts):
    assert caps.require_wake_capability() is None


def test_require_capability_returns_error_when_missing(inspect_only_capability, hermes_home):
    """Fail-closed: inspect-only host cannot satisfy wake requirement."""
    err = caps.require_wake_capability()
    assert err is not None
    assert err["success"] is False
    assert err["error"] == "capability_missing"
    assert err["mode"] == "inspect_only"
    assert "internal_session_wake" in err["remediation"]


def test_require_capability_returns_error_when_unsupported(unsupported_capability, hermes_home):
    err = caps.require_wake_capability()
    assert err is not None
    assert err["error"] == "capability_missing"
    assert err["mode"] == "unsupported"


def test_get_mode_matches_probe(hermes_home):
    assert caps.get_mode() == caps.probe_wake_capability()["mode"]


def test_receipt_table_probe_detects_table(state_db_with_receipts):
    """The real receipt-table probe sees the table created by the fixture."""
    from self_wake.capabilities import _probe_receipt_table
    result = _probe_receipt_table()
    assert result["available"] is True


def test_receipt_table_probe_absent(hermes_home):
    """No state.db → receipt table probe reports absent."""
    from self_wake.capabilities import _probe_receipt_table
    result = _probe_receipt_table()
    assert result["available"] is False


def test_session_index_probe_dict_format(hermes_home):
    from self_wake.capabilities import _probe_session_index_readable
    assert _probe_session_index_readable()["available"] is True

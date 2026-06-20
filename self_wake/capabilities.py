"""Host capability probes for self-wake plugin.

Determines whether the Hermes host supports internal_session_wake_v1.
Returns structured capability reports.
"""


def probe_wake_capability() -> dict:
    """Probe for the internal_session_wake_v1 host capability.

    Returns:
        dict with keys:
        - available: bool
        - version: int or None
        - mode: "full", "inspect_only", or "unsupported"
        - details: list of probe results
    """
    # TODO: implement capability probes
    # Preferred: hermes_cli.capabilities.get_capabilities()
    # Structural fallback:
    #   - Check GatewayRunner.wake_session signature (when importable)
    #   - Check SessionDB.create_session_wake_receipt
    #   - Check SessionDB.update_session_wake_receipt
    #   - Check SessionStore.lookup_by_session_key
    #   - Check state.db session_wake_receipts table existence
    return {
        "available": False,
        "version": None,
        "mode": "unsupported",
        "details": [{"probe": "stub", "result": "not_implemented"}],
    }


def get_mode() -> str:
    """Return current compatibility mode: full, inspect_only, or unsupported."""
    cap = probe_wake_capability()
    return cap["mode"]


def require_wake_capability() -> dict | None:
    """Return None if wake capability is present, or an error dict if missing."""
    cap = probe_wake_capability()
    if cap["available"] and cap["version"] and cap["version"] >= 1:
        return None
    return {
        "success": False,
        "error": "capability_missing",
        "mode": cap["mode"],
        "remediation": (
            "This operation requires Hermes host capability "
            "internal_session_wake_v1. Apply the core patch from "
            "docs/core-patch/ or upgrade to a Hermes version that includes it."
        ),
    }

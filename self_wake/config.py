"""Plugin configuration resolution for self-wake.

Resolves plugin config from Hermes config or plugin-local defaults.
"""

# Configuration keys under self_wake.* in config.yaml
DEFAULTS = {
    "enabled": True,
    "compat_shim_enabled": False,
    "max_session_results": 20,
    "receipt_preview_max_chars": 200,
}


def get_config(key: str | None = None) -> dict | str | bool | int:
    """Get plugin configuration.

    Args:
        key: Optional specific config key. If None, returns all config.

    Returns:
        Config dict or specific value with defaults applied.
    """
    # TODO: implement
    # - Read from Hermes config.yaml under self_wake.*
    # - Fall back to DEFAULTS
    if key:
        return DEFAULTS.get(key)
    return dict(DEFAULTS)

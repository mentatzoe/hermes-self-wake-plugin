"""Plugin configuration resolution for self-wake.

Resolves plugin config from Hermes ``config.yaml`` under ``self_wake.*`` with
safe defaults. Hermes config access is imported lazily so the module works in
isolation (e.g. the plugin repo's test suite, which has no Hermes installed).
"""
from __future__ import annotations

from typing import Any

# Configuration keys under self_wake.* in config.yaml
DEFAULTS: dict[str, Any] = {
    "compat_shim_enabled": False,  # opt-in; the shim never installs unless enabled
    "receipt_preview_max_chars": 200,
}


def _load_user_config() -> dict[str, Any]:
    """Read the ``self_wake:`` section from Hermes config.yaml, if available."""
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config() or {}
    except Exception:
        return {}
    section = cfg.get("self_wake") if isinstance(cfg, dict) else None
    return section if isinstance(section, dict) else {}


def get_config(key: str | None = None) -> Any:
    """Get plugin configuration.

    Args:
        key: Optional specific config key. If None, returns all config.

    Returns:
        Config value with defaults applied. Returns a dict when ``key`` is
        None; otherwise the value for ``key`` (or None if unknown).
    """
    merged = dict(DEFAULTS)
    merged.update(_load_user_config())
    if key is None:
        return merged
    return merged.get(key)


def get_int(key: str, default: int, minimum: int = 0) -> int:
    """Typed int getter with fallback; values below ``minimum`` fall back too
    (a negative preview length would slice from the wrong end)."""
    val = get_config(key)
    try:
        parsed = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def get_bool(key: str, default: bool) -> bool:
    """Typed bool getter with fallback."""
    val = get_config(key)
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)  # YAML `key: 1` means enabled, same as the nested form
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return default

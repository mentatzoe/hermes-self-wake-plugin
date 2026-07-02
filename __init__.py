"""Hermes self-wake plugin entry point.

Delegates register() to the self_wake subpackage where the actual
implementation lives. Hermes plugin discovery (directory loader) requires
__init__.py with register(ctx) at the plugin directory root; without this
file `hermes plugins install` clones a repo that registers but never loads.
"""
try:
    # Hermes directory loader: executed as a package with the plugin dir on
    # the submodule search path.
    from .self_wake import register, __version__
except ImportError:
    # Top-level import (e.g. pytest collection with the repo root on
    # sys.path): the subpackage resolves absolutely.
    from self_wake import register, __version__

__all__ = ["register", "__version__"]

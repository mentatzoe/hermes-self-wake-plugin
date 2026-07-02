"""Regression tests for the Hermes directory-loader contract.

The Hermes plugin loader (`hermes_cli/plugins.py::_load_directory_module`)
imports a directory plugin by executing ``<plugin_dir>/__init__.py`` as a
package whose submodule search path is the plugin directory itself. A repo
that only exposes ``register()`` from the ``self_wake`` subpackage installs
cleanly but fails to load with ``No __init__.py in <plugin_dir>``.

These tests replicate the loader mechanics so CI catches that gap.
"""
import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
_NS_PARENT = "hermes_plugins_test_ns"


def _load_like_hermes_directory_loader():
    """Mimic hermes_cli.plugins._load_directory_module for this repo."""
    init_file = PLUGIN_DIR / "__init__.py"
    assert init_file.exists(), (
        f"No __init__.py in {PLUGIN_DIR} — the Hermes directory loader "
        "refuses to load this plugin without a root __init__.py"
    )

    if _NS_PARENT not in sys.modules:
        ns_pkg = types.ModuleType(_NS_PARENT)
        ns_pkg.__path__ = []
        ns_pkg.__package__ = _NS_PARENT
        sys.modules[_NS_PARENT] = ns_pkg

    module_name = f"{_NS_PARENT}.self_wake_plugin"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(PLUGIN_DIR)]
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # Leave sys.modules clean for other tests regardless of outcome.
        for key in [k for k in sys.modules if k.startswith(_NS_PARENT)]:
            if key != module_name:
                sys.modules.pop(key, None)
    return module


def test_root_init_loads_via_directory_loader():
    module = _load_like_hermes_directory_loader()
    assert callable(getattr(module, "register", None)), (
        "root __init__.py must re-export register(ctx) for the Hermes loader"
    )
    assert isinstance(getattr(module, "__version__", None), str)


def test_root_version_matches_manifest_and_package():
    module = _load_like_hermes_directory_loader()

    manifest_version = None
    for line in (PLUGIN_DIR / "plugin.yaml").read_text().splitlines():
        if line.strip().startswith("version:"):
            manifest_version = line.split(":", 1)[1].strip().strip("\"'")
            break
    assert manifest_version, "plugin.yaml must declare a version"
    assert module.__version__ == manifest_version, (
        f"root __init__ __version__ {module.__version__!r} != "
        f"plugin.yaml version {manifest_version!r}"
    )

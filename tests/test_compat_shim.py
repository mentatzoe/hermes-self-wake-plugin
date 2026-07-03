"""Tests for the compat shim (self_wake.compat_shim).

The shim provides ``internal_session_wake_v1`` on vanilla Hermes via class-level
monkeypatching.  These tests verify:

  - config gate (disabled by default, installs when enabled)
  - native preference (does not install when the host already has the capability)
  - fail-closed drift detection (refuses when private internals have changed)
  - fail-closed against an already-patched host (notifier already routes wakes)
  - the shim installs wake_session / lookup / receipts / notifier routing
  - the capability probe reports ``full`` mode + ``source: "shim"`` after install
    (the critical no-silent-half-wake guarantee: a half-installed shim that
    provides the capability surface but NOT notifier routing does NOT report
    full — see test_notifier_routing_drift_downgrades_from_full in
    test_capabilities.py)
  - the notifier marker parser routes ``session:`` / ``session_id:`` markers
  - uninstall restores originals; install is idempotent

The fakes below are real ``class`` definitions (not ``type()`` dynamos) so
``inspect.getsource`` can read them — the drift checks rely on source
introspection.
"""
from __future__ import annotations

import inspect
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any, Optional

import pytest

from self_wake import compat_shim as shim
from self_wake import capabilities as caps


# --------------------------------------------------------------------------- #
# Fake vanilla Hermes classes (real source so inspect.getsource works).
# These mimic the UNPATCHED shapes the shim is designed to wrap.
# --------------------------------------------------------------------------- #
class _FakeSessionStore:
    """Vanilla SessionStore shape: has _entries/_lock/_ensure_loaded_locked and
    lookup_by_session_id, but NOT lookup_by_session_key."""

    def __init__(self):
        self._entries: dict = {}
        self._loaded = False
        import threading
        self._lock = threading.Lock()

    def _ensure_loaded_locked(self) -> None:
        self._loaded = True

    def lookup_by_session_id(self, session_id: str):
        if not session_id:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            for entry in self._entries.values():
                if getattr(entry, "session_id", None) == session_id:
                    return entry
        return None


class _FakeSessionDB:
    """Vanilla SessionDB shape: has _execute_write + get_messages, but NO
    receipt methods and NO session_wake_receipts table."""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)")
        self._conn.commit()
        self._lock = _DummyLock()

    def _execute_write(self, fn):
        with self._lock:
            result = fn(self._conn)
            self._conn.commit()
            return result

    def get_messages(self, session_id):
        return []


class _DummyLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeGatewayRunner:
    """Vanilla GatewayRunner shape: __init__ wires session_store/_session_db/
    adapters, but has NO wake_session."""

    def __init__(self):
        self.session_store = None
        self._session_db = None
        self.adapters = {}


class _FakeKanbanMixin:
    """Vanilla GatewayKanbanWatchersMixin shape: _kanban_notifier_watcher calls
    adapter.send directly with the vanilla send pattern, has the vanilla
    helper methods the vendored watcher delegates to, and does NOT have
    _kanban_internal_wake_target."""

    def _kanban_advance(self, *a, **k):
        return None

    def _kanban_rewind(self, *a, **k):
        return None

    def _kanban_unsub(self, *a, **k):
        return None

    async def _deliver_kanban_artifacts(self, *a, **k):
        return None

    async def _kanban_notifier_watcher(self, interval: float = 5.0) -> None:
        # Vanilla send site — the exact pattern the drift check anchors on.
        sub = {"chat_id": "x"}
        msg = "hi"
        metadata = {}
        adapter = None
        await adapter.send(
            sub["chat_id"], msg, metadata=metadata,
        )


# --------------------------------------------------------------------------- #
# Drifted variants for fail-closed tests.
# --------------------------------------------------------------------------- #
class _DriftedKanbanMixin:
    """Notifier whose send site was refactored — the vanilla send pattern is
    gone, so the shim must refuse to install (fail closed)."""

    async def _kanban_notifier_watcher(self, interval: float = 5.0) -> None:
        sub = {"chat_id": "x"}
        adapter = None
        # Drifted: send call shape changed (no metadata kwarg, different args).
        await adapter.deliver(sub["chat_id"], "hi")


class _DriftedSessionDB:
    """SessionDB whose _execute_write was removed/renamed — drift."""

    def get_messages(self, session_id):
        return []


class _AlreadyPatchedKanbanMixin:
    """Notifier that already routes wake markers — the shim must not
    double-install (treated as drift/already-patched)."""

    async def _kanban_notifier_watcher(self, interval: float = 5.0) -> None:
        sub = {}
        await adapter.send(sub["chat_id"], "x", metadata={})  # type: ignore[name-defined]
        wake_target = self._kanban_internal_wake_target(sub)  # noqa: F841

    def _kanban_internal_wake_target(self, sub):
        return None


# --------------------------------------------------------------------------- #
# Fixtures: inject fake vanilla modules into sys.modules.
# --------------------------------------------------------------------------- #
def _install_fake_modules(monkeypatch, *, session_store=_FakeSessionStore,
                          session_db=_FakeSessionDB,
                          gateway_runner=_FakeGatewayRunner,
                          kanban_mixin=_FakeKanbanMixin):
    """Inject fake Hermes modules into sys.modules so caps/compat_shim probes
    resolve to the fakes."""
    fake_session_mod = types.ModuleType("gateway.session")
    fake_session_mod.SessionStore = session_store  # type: ignore[attr-defined]

    def _fake_build_session_key(*, platform="", chat_id="", thread_id=None,
                                user_id=None, group_sessions_per_user=False,
                                thread_sessions_per_user=False, **kwargs):
        return f"agent:main:{platform}:{chat_id}:{thread_id or ''}"

    fake_session_mod.build_session_key = _fake_build_session_key  # type: ignore[attr-defined]

    fake_run_mod = types.ModuleType("gateway.run")
    fake_run_mod.GatewayRunner = gateway_runner  # type: ignore[attr-defined]

    fake_kw_mod = types.ModuleType("gateway.kanban_watchers")
    fake_kw_mod.GatewayKanbanWatchersMixin = kanban_mixin  # type: ignore[attr-defined]

    fake_state_mod = types.ModuleType("hermes_state")
    fake_state_mod.SessionDB = session_db  # type: ignore[attr-defined]

    # Platform event types the vendored watcher constructs.
    import dataclasses as _dc
    import enum as _enum

    fake_base_mod = types.ModuleType("gateway.platforms.base")

    @_dc.dataclass
    class _FakeMessageEvent:
        text: str = ""
        internal: bool = False
        metadata: dict = _dc.field(default_factory=dict)

    class _FakeMessageType(_enum.Enum):
        TEXT = "text"

    fake_base_mod.MessageEvent = _FakeMessageEvent  # type: ignore[attr-defined]
    fake_base_mod.MessageType = _FakeMessageType  # type: ignore[attr-defined]
    fake_platforms_pkg = types.ModuleType("gateway.platforms")
    fake_platforms_pkg.base = fake_base_mod  # type: ignore[attr-defined]

    # Kanban DB module surface the vendored watcher calls.
    fake_kdb_mod = types.ModuleType("hermes_cli.kanban_db")
    for _name in ("list_boards", "read_board_metadata", "connect",
                  "list_notify_subs", "claim_unseen_events_for_sub",
                  "get_task", "kanban_db_path"):
        setattr(fake_kdb_mod, _name, lambda *a, **k: None)
    fake_kdb_mod.DEFAULT_BOARD = "default"  # type: ignore[attr-defined]
    fake_hermes_cli_pkg = types.ModuleType("hermes_cli")
    fake_hermes_cli_pkg.kanban_db = fake_kdb_mod  # type: ignore[attr-defined]

    fake_gateway_pkg = types.ModuleType("gateway")
    fake_gateway_pkg.run = fake_run_mod  # type: ignore[attr-defined]
    fake_gateway_pkg.session = fake_session_mod  # type: ignore[attr-defined]
    fake_gateway_pkg.kanban_watchers = fake_kw_mod  # type: ignore[attr-defined]
    fake_gateway_pkg.platforms = fake_platforms_pkg  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "gateway", fake_gateway_pkg)
    monkeypatch.setitem(sys.modules, "gateway.session", fake_session_mod)
    monkeypatch.setitem(sys.modules, "gateway.run", fake_run_mod)
    monkeypatch.setitem(sys.modules, "gateway.kanban_watchers", fake_kw_mod)
    monkeypatch.setitem(sys.modules, "hermes_state", fake_state_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms", fake_platforms_pkg)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", fake_base_mod)
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_hermes_cli_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_db", fake_kdb_mod)
    return {
        "session_store": session_store,
        "session_db": session_db,
        "gateway_runner": gateway_runner,
        "kanban_mixin": kanban_mixin,
    }


@pytest.fixture(autouse=True)
def _reset_shim():
    """Ensure each test starts with the shim uninstalled."""
    if shim.is_installed():
        shim.uninstall_shim()
    yield
    if shim.is_installed():
        shim.uninstall_shim()


@pytest.fixture()
def vanilla_hermes(monkeypatch, hermes_home):
    """Inject fake vanilla Hermes modules + create an (empty) state.db so the
    shim's install-time receipt-table creation has a DB to write to."""
    db = hermes_home / "state.db"
    # Touch state.db so the shim creates the receipts table at install time.
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    return _install_fake_modules(monkeypatch)


# --------------------------------------------------------------------------- #
# Config gate
# --------------------------------------------------------------------------- #
def test_shim_disabled_by_default(hermes_home, monkeypatch):
    """Without enabling config, the shim does not install."""
    # _shim_config_enabled reads config; in the test env config returns {} so
    # compat_shim_enabled stays False (default).
    monkeypatch.setattr(shim, "_shim_config_enabled", lambda: False)
    report = shim.install_shim()
    assert report["installed"] is False
    assert report["reason"] == "shim_disabled_by_config"
    assert shim.is_installed() is False


def test_shim_installs_when_enabled_on_vanilla(vanilla_hermes, monkeypatch):
    """With config enabled and vanilla-shaped fakes, the shim installs."""
    monkeypatch.setattr(shim, "_shim_config_enabled", lambda: True)
    report = shim.install_shim()
    assert report["installed"] is True
    assert report["reason"] == "shim_installed"
    assert shim.is_installed() is True


# --------------------------------------------------------------------------- #
# Native preference
# --------------------------------------------------------------------------- #
def test_shim_prefers_native_capability(hermes_home, monkeypatch):
    """When the native capability is present, the shim does not install — even
    if config is enabled."""
    monkeypatch.setattr(shim, "_shim_config_enabled", lambda: True)
    # Simulate a native host: the wake methods exist. The gate checks method
    # presence, not the full probe, so a fresh native host without state.db
    # still refuses the shim instead of clobbering native methods.
    monkeypatch.setattr(caps, "_probe_gateway_wake_session",
                        lambda: {"available": True})
    monkeypatch.setattr(caps, "_probe_session_db_receipt_methods",
                        lambda: {"available": True})
    report = shim.install_shim()
    assert report["installed"] is False
    assert report["reason"] == "native_capability_present"
    assert shim.is_installed() is False


def test_shim_force_overrides_native_and_config(vanilla_hermes):
    """force=True skips the native-preference and config gates (for tests /
    explicit operator override)."""
    report = shim.install_shim(force=True)
    assert report["installed"] is True
    assert shim.is_installed() is True


# --------------------------------------------------------------------------- #
# Fail-closed drift detection
# --------------------------------------------------------------------------- #
def test_shim_fail_closed_on_notifier_send_site_drift(hermes_home, monkeypatch):
    """When the notifier's send-site shape changed, the shim refuses."""
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db)); conn.commit(); conn.close()
    _install_fake_modules(monkeypatch, kanban_mixin=_DriftedKanbanMixin)
    report = shim.install_shim(force=True)
    assert report["installed"] is False
    assert report["reason"] == "drift_detected"
    drift = report["drift"]
    assert drift["ok"] is False
    assert "kanban_notifier" in drift["reason"]


def test_shim_fail_closed_on_session_db_drift(hermes_home, monkeypatch):
    """When SessionDB lost _execute_write, the shim refuses."""
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db)); conn.commit(); conn.close()
    _install_fake_modules(monkeypatch, session_db=_DriftedSessionDB)
    report = shim.install_shim(force=True)
    assert report["installed"] is False
    assert report["reason"] == "drift_detected"
    assert "session_db" in report["drift"]["reason"]


def test_shim_fail_closed_on_already_patched_host(hermes_home, monkeypatch):
    """When the notifier already routes wake markers, the shim treats it as
    drift (already-patched) and refuses — no double-install."""
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db)); conn.commit(); conn.close()
    _install_fake_modules(monkeypatch, kanban_mixin=_AlreadyPatchedKanbanMixin)
    report = shim.install_shim(force=True)
    assert report["installed"] is False
    assert report["reason"] == "drift_detected"
    assert "kanban_notifier" in report["drift"]["reason"]
    assert "already" in report["drift"]["reason"].lower() or "rout" in report["drift"]["reason"].lower()


# --------------------------------------------------------------------------- #
# Install surface: methods land on the classes
# --------------------------------------------------------------------------- #
def test_shim_installs_wake_session_with_v1_signature(vanilla_hermes):
    shim.install_shim(force=True)
    from gateway.run import GatewayRunner  # type: ignore
    assert hasattr(GatewayRunner, "wake_session")
    sig = inspect.signature(GatewayRunner.wake_session)
    params = sig.parameters
    # v1 contract: keyword-only payload + source_kind.
    assert "payload" in params
    assert "source_kind" in params
    assert "session_key" in params
    assert "session_id" in params
    assert "dedupe_key" in params


def test_shim_installs_lookup_by_session_key(vanilla_hermes):
    shim.install_shim(force=True)
    from gateway.session import SessionStore  # type: ignore
    assert hasattr(SessionStore, "lookup_by_session_key")
    assert callable(SessionStore.lookup_by_session_key)


def test_shim_installs_receipt_methods(vanilla_hermes):
    shim.install_shim(force=True)
    from hermes_state import SessionDB  # type: ignore
    assert hasattr(SessionDB, "create_session_wake_receipt")
    assert hasattr(SessionDB, "update_session_wake_receipt")


def test_shim_installs_notifier_routing(vanilla_hermes):
    """After install, the mixin has _kanban_internal_wake_target and the
    notifier is the shim version that routes wake markers."""
    shim.install_shim(force=True)
    from gateway.kanban_watchers import GatewayKanbanWatchersMixin  # type: ignore
    assert hasattr(GatewayKanbanWatchersMixin, "_kanban_internal_wake_target")
    fn = GatewayKanbanWatchersMixin._kanban_notifier_watcher
    # The installed notifier is the shim's, whose source routes wake markers.
    src = inspect.getsource(fn)
    assert "_kanban_internal_wake_target" in src
    assert "wake_session" in src


# --------------------------------------------------------------------------- #
# Receipt table creation (install-time + lazy)
# --------------------------------------------------------------------------- #
def test_shim_creates_receipt_table_at_install(vanilla_hermes, hermes_home):
    """The shim creates session_wake_receipts in state.db at install time."""
    db = hermes_home / "state.db"
    # Before install: no receipts table.
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "session_wake_receipts" not in tables

    shim.install_shim(force=True)

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "session_wake_receipts" in tables


def test_shim_create_receipt_writes_row(vanilla_hermes, tmp_path):
    """The shim's create_session_wake_receipt creates a table and inserts a row."""
    shim.install_shim(force=True)
    from hermes_state import SessionDB  # type: ignore
    sdb = SessionDB(tmp_path / "receipts.db")
    row, created = sdb.create_session_wake_receipt(
        source_kind="kanban",
        target_session_key="agent:main:discord:thread:1:1",
        target_session_id="sid-1",
        origin_snapshot={"platform": "discord"},
        payload_hash="abc123",
        payload_preview="hello",
        payload_bytes=5,
        dedupe_key="kanban:default:t_1:1",
    )
    assert created is True
    assert row["status"] == "requested"
    assert row["source_kind"] == "kanban"
    assert row["target_session_key"] == "agent:main:discord:thread:1:1"
    # Dedupe: same dedupe_key returns existing row, created=False.
    row2, created2 = sdb.create_session_wake_receipt(
        source_kind="kanban",
        target_session_key="agent:main:discord:thread:1:1",
        target_session_id="sid-1",
        origin_snapshot=None,
        payload_hash="abc123",
        payload_preview="hello",
        payload_bytes=5,
        dedupe_key="kanban:default:t_1:1",
    )
    assert created2 is False
    assert row2["id"] == row["id"]


# --------------------------------------------------------------------------- #
# The critical no-silent-half-wake guarantee
# --------------------------------------------------------------------------- #
def test_probe_reports_full_and_shim_source_after_install(vanilla_hermes, hermes_home):
    """After shim install on vanilla fakes, the capability probe reports
    full mode with source='shim' AND notifier_routing available.

    This is the core portability + no-silent-half-wake guarantee: the shim
    provides BOTH the capability surface AND the notifier routing, so the probe
    reports full (subscribe is allowed) AND wakes actually fire.  A half-shim
    (surface without routing) would report inspect_only here — see
    test_notifier_routing_drift_downgrades_from_full in test_capabilities.py.
    """
    cap = caps.probe_wake_capability()
    assert cap["mode"] != "full"  # vanilla, before shim
    assert cap["available"] is False

    shim.install_shim(force=True)

    cap = caps.probe_wake_capability()
    assert cap["mode"] == "full", cap
    assert cap["available"] is True
    assert cap["version"] == 1
    assert cap["source"] == "shim"
    # notifier_routing probe specifically passed.
    routing = next(d for d in cap["details"] if d["probe"] == "notifier_routing")
    assert routing["available"] is True


def test_vanilla_without_shim_does_not_report_full(vanilla_hermes, hermes_home):
    """On vanilla Hermes WITHOUT the shim, the probe must NOT report full —
    so self_wake_subscribe_kanban fails closed rather than writing a marker
    that would never fire.  This is the inspect-only floor."""
    cap = caps.probe_wake_capability()
    assert cap["mode"] != "full"
    assert cap["available"] is False
    err = caps.require_wake_capability()
    assert err is not None
    assert err["error"] == "capability_missing"
    # Remediation must mention the shim as an option.
    assert "compat shim" in err["remediation"] or "compat_shim_enabled" in err["remediation"]


# --------------------------------------------------------------------------- #
# Notifier marker parsing (the routing decision)
# --------------------------------------------------------------------------- #
def test_kanban_internal_wake_target_parses_session_marker(vanilla_hermes):
    """The wake-target parser routes session:/session_id: markers and leaves
    visible-only rows alone — this is the decision the notifier uses."""
    shim.install_shim(force=True)
    from gateway.kanban_watchers import GatewayKanbanWatchersMixin  # type: ignore
    # Use an unbound call (self unused by the parser).
    parse = GatewayKanbanWatchersMixin._kanban_internal_wake_target
    assert parse(None, {"user_id": "session:agent:main:discord:thread:1:1"}) == \
        ("session_key", "agent:main:discord:thread:1:1")
    assert parse(None, {"user_id": "session_id:sid-42"}) == ("session_id", "sid-42")
    assert parse(None, {"user_id": "123456"}) is None  # visible-only
    assert parse(None, {"user_id": None}) is None
    assert parse(None, {"user_id": "session:"}) is None  # empty target


# --------------------------------------------------------------------------- #
# Uninstall + idempotency
# --------------------------------------------------------------------------- #
def test_uninstall_restores_originals(vanilla_hermes):
    """After uninstall, the fake classes lose the shim-added methods (or regain
    their originals)."""
    shim.install_shim(force=True)
    from gateway.run import GatewayRunner  # type: ignore
    from gateway.session import SessionStore  # type: ignore
    from gateway.kanban_watchers import GatewayKanbanWatchersMixin  # type: ignore
    assert hasattr(GatewayRunner, "wake_session")
    assert hasattr(SessionStore, "lookup_by_session_key")
    assert hasattr(GatewayKanbanWatchersMixin, "_kanban_internal_wake_target")
    # The vanilla notifier is replaced by the shim notifier.
    assert GatewayKanbanWatchersMixin._kanban_notifier_watcher is shim._shim_kanban_notifier_watcher

    result = shim.uninstall_shim()
    assert result["uninstalled"] is True
    assert shim.is_installed() is False
    # wake_session was shim-added (no original) → removed.
    assert not hasattr(GatewayRunner, "wake_session")
    assert not hasattr(SessionStore, "lookup_by_session_key")
    assert not hasattr(GatewayKanbanWatchersMixin, "_kanban_internal_wake_target")
    # The original notifier is restored.
    assert GatewayKanbanWatchersMixin._kanban_notifier_watcher is _FakeKanbanMixin._kanban_notifier_watcher


def test_install_is_idempotent(vanilla_hermes):
    """Installing twice is safe; the second call reports already_installed."""
    r1 = shim.install_shim(force=True)
    assert r1["installed"] is True
    r2 = shim.install_shim(force=True)
    assert r2["installed"] is True
    assert r2["reason"] == "already_installed"
    assert shim.is_installed() is True


def test_uninstall_when_not_installed_is_noop(hermes_home):
    result = shim.uninstall_shim()
    assert result["uninstalled"] is False
    assert result["reason"] == "not_installed"


# --------------------------------------------------------------------------- #
# Doctor reports shim status
# --------------------------------------------------------------------------- #
def test_doctor_reports_shim_check(vanilla_hermes, hermes_home):
    """The doctor includes a compat_shim check reflecting shim state."""
    from self_wake import doctor
    # Before install: shim not installed → info.
    result = doctor.run_diagnostics()
    names = {c["name"] for c in result["checks"]}
    assert "compat_shim" in names
    shim_check = next(c for c in result["checks"] if c["name"] == "compat_shim")
    assert shim_check["status"] in ("info", "ok", "fail")

    shim.install_shim(force=True)
    result = doctor.run_diagnostics()
    shim_check = next(c for c in result["checks"] if c["name"] == "compat_shim")
    assert shim_check["status"] == "ok"
    assert "shim installed" in shim_check["detail"]
    # And the capability check reports source=shim.
    cap_check = next(c for c in result["checks"] if c["name"] == "core_capability")
    assert "source=shim" in cap_check["detail"]

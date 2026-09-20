"""The Home installer captures committed bytes and rolls back failed installs."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("self_wake_installer", Path(__file__).resolve().parents[1] / "scripts/install_plugin.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def source(monkeypatch):
    files = {"__init__.py": b"", "plugin.yaml": b"name: self-wake\n", "self_wake/outbox.py": b"value = 1\n"}
    monkeypatch.setattr(installer, "captured_files", lambda source, commit: files)
    return files


def test_dry_run_does_not_create_home(source, tmp_path):
    home = tmp_path / "not-created"
    result = installer.apply(tmp_path, "reviewed", home)
    assert not result["applied"] and not home.exists()


def test_apply_checks_bytes_and_preserves_predecessor(source, tmp_path):
    target = tmp_path / "plugins/self-wake"
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous bytes")
    result = installer.apply(tmp_path, "reviewed", tmp_path, execute=True)
    assert result["applied"]
    assert (Path(result["backup"]) / "previous.txt").read_text() == "previous bytes"
    assert all((target / name).read_bytes() == data for name, data in source.items())


def test_failed_swap_restores_previous_tree(source, tmp_path, monkeypatch):
    target = tmp_path / "plugins/self-wake"
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous bytes")
    replace = installer.os.replace
    def fail_stage(src, dst):
        if Path(src).name.startswith(".self-wake-install-"):
            raise OSError("injected failure")
        replace(src, dst)
    monkeypatch.setattr(installer.os, "replace", fail_stage)
    with pytest.raises(OSError, match="injected"):
        installer.apply(tmp_path, "reviewed", tmp_path, execute=True)
    assert (target / "previous.txt").read_text() == "previous bytes"


def test_symlink_home_rejected(source, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        installer.apply(tmp_path, "reviewed", link)

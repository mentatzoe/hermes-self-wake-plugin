#!/usr/bin/env python3
"""Stage an exact plugin commit; apply only with --apply. Never edits config/host."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import uuid


def safe_path(path):
    path = path.absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink not allowed: {parent}")
    return path


def captured_files(source, commit):
    exact = subprocess.check_output(["git", "-C", str(source), "rev-parse", "--verify", commit + "^{commit}"], text=True).strip()
    if exact != commit:
        raise ValueError("--commit must be the full reviewed commit ID")
    data = subprocess.check_output(["git", "-C", str(source), "archive", "--format=tar", exact, "__init__.py", "plugin.yaml", "self_wake", "skills"])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("unsafe archive entry")
            if member.isfile():
                files[member.name] = archive.extractfile(member).read()
            elif not member.isdir():
                raise ValueError("unsupported archive entry")
    if "plugin.yaml" not in files or "self_wake/outbox.py" not in files:
        raise ValueError("commit does not contain the modernized plugin")
    return files


def apply(source, commit, home, *, execute=False):
    home = safe_path(home)
    plugins = safe_path(home / "plugins")
    target = safe_path(plugins / "self-wake")
    files = captured_files(source, commit)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    report = {"commit": commit, "target": str(target), "files": hashes, "applied": False,
              "restart_required": True, "config_changed": False}
    if not execute:
        return report
    if not home.is_dir():
        raise ValueError("selected Hermes home must already exist")
    plugins.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".self-wake-install-", dir=home))
    backup_root = safe_path(home / "self-wake-install-backups")
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = backup_root / uuid.uuid4().hex
    moved = False
    installed = False
    try:
        for name, data in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if path.suffix == ".py":
                compile(data, name, "exec")
        if target.exists():
            if not target.is_dir():
                raise ValueError("existing plugin is not a directory")
            os.replace(target, backup)
            moved = True
        os.replace(stage, target)
        installed = True
        for name, digest in hashes.items():
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("installed bytes differ from the captured commit")
        report.update(applied=True, backup=str(backup) if moved else None)
        return report
    except BaseException:
        if installed:
            shutil.rmtree(target)
        if moved:
            os.replace(backup, target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--commit", required=True)
    parser.add_argument("--home", required=True, type=Path, help="Exact active profile home; never inferred")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(args.source, args.commit, args.home, execute=args.apply), sort_keys=True))


if __name__ == "__main__":
    main()

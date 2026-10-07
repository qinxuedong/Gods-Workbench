from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

import run
from gw.core import session


def _make_writable(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    root.chmod(root.stat().st_mode | stat.S_IWUSR)


def test_cookie_names_isolate_dev_and_prod(monkeypatch):
    assert session.runtime_cookie_names("dev") == ("gw_session_dev", "gw_oidc_flow_dev")
    assert session.runtime_cookie_names("prod") == ("gw_session_prod", "gw_oidc_flow_prod")
    assert session.runtime_cookie_names("dev") != session.runtime_cookie_names("prod")
    assert session.runtime_cookie_names("test") == ("gw_session", "gw_oidc_flow")


def test_migration_copies_only_data_and_auth_and_writes_receipt(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / "GodsWorkbenchClear"
    destination = tmp_path / "GodsWorkbench"
    (source / "data" / "nested").mkdir(parents=True)
    (source / "data" / "nested" / "state.json").write_text("{\"ok\":true}", encoding="utf-8")
    (source / "auth.sqlite3").write_bytes(b"auth-db")
    (source / "video").mkdir()
    (source / "video" / "should-not-move.mp4").write_bytes(b"video")

    receipt = run.migrate_legacy_data(source, destination, repo_root=repo)

    assert receipt["status"] == "success"
    assert (destination / "data" / "nested" / "state.json").read_text(encoding="utf-8") == "{\"ok\":true}"
    assert (destination / "auth" / "auth.sqlite3").read_bytes() == b"auth-db"
    assert not (destination / "video").exists()
    assert (destination / run.MIGRATION_RECEIPT_NAME).is_file()
    assert (source / "data" / "nested" / "state.json").read_text(encoding="utf-8") == "{\"ok\":true}"
    assert (source / "auth.sqlite3").read_bytes() == b"auth-db"
    assert all((p.stat().st_mode & stat.S_IWUSR) == 0 for p in source.rglob("*"))
    _make_writable(source)


def test_migration_failure_leaves_no_success_receipt_or_destination(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / "GodsWorkbenchClear"
    destination = tmp_path / "GodsWorkbench"
    (source / "data").mkdir(parents=True)
    (source / "data" / "state.json").write_text("state", encoding="utf-8")
    (source / "auth.sqlite3").write_bytes(b"auth")

    original_copy2 = run.shutil.copy2

    def fail_copy2(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(run.shutil, "copy2", fail_copy2)
    with pytest.raises(run.MigrationError):
        run.migrate_legacy_data(source, destination, repo_root=repo)
    assert not destination.exists()
    assert not list(tmp_path.glob(".GodsWorkbench.migration-*.staging"))
    assert not (source / run.MIGRATION_RECEIPT_NAME).exists()
    assert (source / "auth.sqlite3").read_bytes() == b"auth"
    assert (source / "auth.sqlite3").stat().st_mode & stat.S_IWUSR
    monkeypatch.setattr(run.shutil, "copy2", original_copy2)


def test_migration_is_one_time_and_rejects_existing_target(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = tmp_path / "GodsWorkbenchClear"
    destination = tmp_path / "GodsWorkbench"
    (source / "data").mkdir(parents=True)
    (source / "data" / "state.json").write_text("state", encoding="utf-8")
    run.migrate_legacy_data(source, destination, repo_root=repo)
    with pytest.raises(run.MigrationError, match="已存在"):
        run.migrate_legacy_data(source, destination, repo_root=repo)
    _make_writable(source)

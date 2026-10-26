"""MVP1-T0 允许根持久化、权限边界与目录挂载错误契约。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings as routes
from gw.api.app import create_app
from gw.core import storage
from gw.settings.service import (
    AssetStructureService,
    ProviderService,
    StorageAllowedRootsService,
    StorageSettingsService,
)

ADMIN = {"X-User-Role": "admin", "Authorization": "Bearer cleanroom-test"}
EDITOR = {"X-User-Role": "editor", "Authorization": "Bearer cleanroom-test"}


@pytest.fixture()
def api_client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(routes, "default_storage_settings_service", StorageSettingsService())
    monkeypatch.setattr(routes, "default_storage_allowed_roots_service", StorageAllowedRootsService())
    monkeypatch.setattr(routes, "default_provider_service", ProviderService())
    monkeypatch.setattr(routes, "default_asset_structure_service", AssetStructureService())
    with TestClient(create_app()) as client:
        yield client


def test_admin_can_persist_and_remove_allowed_root_without_deleting_index_data(
    api_client: TestClient, monkeypatch, tmp_path: Path
):
    system_root = tmp_path / "system"
    persistent_root = tmp_path / "persistent"
    system_root.mkdir()
    persistent_root.mkdir()
    monkeypatch.setenv(storage.ALLOWED_ROOTS_ENV, str(system_root))

    forbidden = api_client.post(
        "/api/storage-settings/roots",
        headers=EDITOR,
        json={"path": str(persistent_root), "expected_version": 1},
    )
    assert forbidden.status_code == 403

    initial = api_client.get("/api/storage-settings/roots", headers=ADMIN)
    assert initial.status_code == 200, initial.text
    roots = initial.json()["roots"]
    system = next(item for item in roots if item["source"] == "system")
    assert system["read_only"] is True
    assert system["availability"] == "available"

    added = api_client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": str(persistent_root), "expected_version": 1},
    )
    assert added.status_code == 200, added.text
    persistent = next(item for item in added.json()["roots"] if item["source"] == "persistent")
    assert persistent["path"] == str(persistent_root)
    assert persistent["active"] is True
    assert persistent["read_only"] is False
    assert Path(tmp_path / "data" / "allowed_roots.json").is_file()
    assert any(storage.directory_path_key(str(root)) == storage.directory_path_key(str(persistent_root)) for root in storage.allowed_roots())

    monkeypatch.setattr(routes, "default_storage_allowed_roots_service", StorageAllowedRootsService())
    restored = api_client.get("/api/storage-settings/roots", headers=ADMIN)
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == 2
    root_id = persistent["id"]
    removed = api_client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}",
        headers=ADMIN,
        json={"expected_version": 2},
    )
    assert removed.status_code == 200, removed.text
    tombstone = next(item for item in removed.json()["roots"] if item["id"] == root_id)
    assert tombstone["availability"] == "removed"
    assert tombstone["availability_reason"] == "MOUNT_ROOT_REMOVED"
    assert not any(storage.directory_path_key(str(root)) == storage.directory_path_key(str(persistent_root)) for root in storage.allowed_roots())
    assert persistent_root.is_dir()


def test_allowed_root_cas_conflict_and_system_root_cannot_be_deleted(
    api_client: TestClient, monkeypatch, tmp_path: Path
):
    system_root = tmp_path / "system"
    persistent_root = tmp_path / "persistent"
    system_root.mkdir()
    persistent_root.mkdir()
    monkeypatch.setenv(storage.ALLOWED_ROOTS_ENV, str(system_root))
    system_id = storage.root_identity_id(system_root)

    stale = api_client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": str(persistent_root), "expected_version": 8},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"
    protected = api_client.request(
        "DELETE",
        f"/api/storage-settings/roots/{system_id}",
        headers=ADMIN,
        json={"expected_version": 1},
    )
    assert protected.status_code == 403
    assert protected.json()["detail"]["code"] == "SYSTEM_ROOT_READ_ONLY"
    assert not (tmp_path / "data" / "allowed_roots.json").exists()


def test_environment_root_stays_read_only_while_admin_can_confirm_its_alias(
    api_client: TestClient, monkeypatch, tmp_path: Path
):
    system_root = tmp_path / "system"
    alias = tmp_path / "system-alias"
    system_root.mkdir()
    alias.mkdir()
    monkeypatch.setenv(storage.ALLOWED_ROOTS_ENV, str(system_root))
    monkeypatch.setattr(storage, "mount_alias_matches", lambda *_args: True)
    root_id = storage.root_identity_id(system_root)

    confirmed = api_client.post(
        f"/api/storage-settings/roots/{root_id}/aliases/confirm",
        headers=ADMIN,
        json={"path": str(alias), "expected_version": 1},
    )
    assert confirmed.status_code == 200, confirmed.text
    system = next(item for item in confirmed.json()["roots"] if item["id"] == root_id)
    assert system["source"] == "system"
    assert system["read_only"] is True
    assert system["aliases"] == [str(alias)]
    assert storage.root_identity_id(alias) == root_id
    assert any(storage.directory_path_key(str(root)) == storage.directory_path_key(str(alias)) for root in storage.allowed_roots())

    protected = api_client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}",
        headers=ADMIN,
        json={"expected_version": 2},
    )
    assert protected.status_code == 403
    assert protected.json()["detail"]["code"] == "SYSTEM_ROOT_READ_ONLY"


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("MOUNT_ACCESS_DENIED", "当前服务账户无权读取该目录"),
        ("MOUNT_OFFLINE", "网络目录不可达，或服务账户看不到映射盘"),
    ],
)
def test_mount_validation_returns_distinct_reachability_errors(
    api_client: TestClient, monkeypatch, tmp_path: Path, reason: str, message: str
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    candidate = allowed / "library"
    candidate.mkdir()
    monkeypatch.setenv(storage.ALLOWED_ROOTS_ENV, str(allowed))
    monkeypatch.setattr(
        storage,
        "_probe_mount_sync",
        lambda _path: {"available": False, "reason": reason, "message": message},
    )
    response = api_client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={"dirs": {"local": str(candidate)}, "expected_version": 1},
    )
    assert response.status_code in {403, 503}
    assert response.json()["detail"]["code"] == reason
    assert response.json()["detail"]["message"] == message


def test_mount_validation_distinguishes_missing_outside_and_unconfirmed_alias(
    api_client: TestClient, monkeypatch, tmp_path: Path
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "library").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv(storage.ALLOWED_ROOTS_ENV, str(allowed))

    missing = api_client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={"dirs": {"local": str(allowed / "missing")}, "expected_version": 1},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "MOUNT_NOT_FOUND"

    rejected = api_client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={"dirs": {"local": str(outside)}, "expected_version": 1},
    )
    assert rejected.status_code == 403
    assert rejected.json()["detail"]["code"] == "MOUNT_OUTSIDE_ALLOWED_ROOTS"

    monkeypatch.setattr(storage, "mount_alias_candidates", lambda *_args, **_kwargs: [{"root_id": "root-test", "path": str(outside)}])
    alias = api_client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={"dirs": {"local": str(outside)}, "expected_version": 1},
    )
    assert alias.status_code == 409
    assert alias.json()["detail"]["code"] == "MOUNT_ALIAS_UNCONFIRMED"


def test_network_missing_child_is_distinct_from_an_unreachable_share(
    monkeypatch, tmp_path: Path
):
    missing = tmp_path / "missing-library"
    monkeypatch.setattr(storage, "_is_network_path", lambda _path: True)
    monkeypatch.setattr(storage, "_network_root_path", lambda _path: str(tmp_path))
    reachable = storage._probe_mount_sync(str(missing))
    assert reachable["reason"] == "MOUNT_NOT_FOUND"

    monkeypatch.setattr(
        storage, "_network_root_path", lambda _path: str(tmp_path / "offline-share")
    )
    unreachable = storage._probe_mount_sync(str(missing))
    assert unreachable["reason"] == "MOUNT_OFFLINE"


def test_registered_network_drive_marker_survives_for_disconnection(
    api_client: TestClient, monkeypatch
):
    path = r"Q:\测试素材库"
    monkeypatch.setattr(
        storage,
        "probe_mount",
        lambda _path: {"available": True, "reason": "", "message": ""},
    )
    original_is_network_path = storage._is_network_path
    monkeypatch.setattr(storage, "_is_network_path", lambda candidate: candidate.upper().startswith("Q:"))
    added = api_client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": path, "expected_version": 1},
    )
    assert added.status_code == 200, added.text
    root_id = next(item["id"] for item in added.json()["roots"] if item["source"] == "persistent")
    assert storage._allowed_roots_state()["roots"][0]["network_mount"] is True

    monkeypatch.setattr(storage, "_is_network_path", original_is_network_path)
    monkeypatch.setattr(storage, "_mapped_drive_unc", lambda _path: None)
    assert storage._persisted_network_drive("Q:") is True
    assert storage.root_identity_id(Path(path)) == root_id


def test_mapped_drive_spelling_inside_allowed_root_saves_without_alias_merge(
    monkeypatch,
):
    root = r"Q:\素材库"
    candidate = r"Q:\素材库\样片"
    monkeypatch.setattr(storage.os, "name", "nt")
    monkeypatch.setattr(storage, "_probe_mount_sync", lambda _path: {"available": True, "reason": "", "message": ""})
    monkeypatch.setattr(storage, "_is_network_path", lambda _path: True)
    monkeypatch.setattr(storage, "allowed_roots", lambda: [Path(root)])
    monkeypatch.setattr(storage, "_ensure_reparse_targets_within_roots", lambda *_args: None)

    assert storage._validate_mount_sync(candidate) == ("", "")


def test_mount_probe_timeout_returns_offline_without_waiting_for_worker(monkeypatch):
    release_probe = threading.Event()
    monkeypatch.setattr(
        storage,
        "_probe_mount_sync",
        lambda _path: release_probe.wait(0.5)
        and {"available": True, "reason": "", "message": ""},
    )
    started = time.monotonic()
    status = storage.probe_mount(r"Z:\slow-share", timeout=0.02)
    elapsed = time.monotonic() - started
    release_probe.set()

    assert elapsed < 0.2
    assert status["available"] is False
    assert status["reason"] == "MOUNT_OFFLINE"


def test_confirmed_alias_keeps_root_identity_and_survives_service_restart(
    api_client: TestClient, monkeypatch, tmp_path: Path
):
    root = tmp_path / "drive-root"
    alias = tmp_path / "unc-alias"
    root.mkdir()
    alias.mkdir()
    monkeypatch.setattr(storage, "mount_alias_matches", lambda *_args: True)
    added = api_client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": str(root), "expected_version": 1},
    )
    assert added.status_code == 200, added.text
    root_id = added.json()["roots"][0]["id"]

    unconfirmed = api_client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={"dirs": {"local": str(alias)}, "expected_version": 1},
    )
    assert unconfirmed.json()["detail"]["code"] == "MOUNT_OUTSIDE_ALLOWED_ROOTS"

    confirmed = api_client.post(
        f"/api/storage-settings/roots/{root_id}/aliases/confirm",
        headers=ADMIN,
        json={"path": str(alias), "expected_version": 2},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["revision"] == 3
    assert storage.root_identity_id(root) == storage.root_identity_id(alias) == root_id
    assert any(storage.directory_path_key(str(item)) == storage.directory_path_key(str(alias)) for item in storage.allowed_roots())

    monkeypatch.setattr(routes, "default_storage_allowed_roots_service", StorageAllowedRootsService())
    restored = api_client.get("/api/storage-settings/roots", headers=ADMIN)
    assert restored.status_code == 200, restored.text
    saved_root = next(item for item in restored.json()["roots"] if item["id"] == root_id)
    assert saved_root["aliases"] == [str(alias)]
    assert json.loads((tmp_path / "data" / "allowed_roots.json").read_text(encoding="utf-8"))["revision"] == 3

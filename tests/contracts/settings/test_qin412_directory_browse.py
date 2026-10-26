"""QIN-412 管理员服务器目录浏览 API 契约。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api import routes_settings as routes
from gw.api.app import create_app
from gw.settings import service as settings_service
from gw.settings.service import (
    AssetStructureService,
    ProviderService,
    StorageSettingsService,
)

ADMIN = {"Authorization": "Bearer qin412-admin", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer qin412-editor", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer qin412-readonly", "X-User-Role": "readonly"}


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(routes, "default_storage_settings_service", StorageSettingsService())
    monkeypatch.setattr(routes, "default_provider_service", ProviderService())
    monkeypatch.setattr(routes, "default_asset_structure_service", AssetStructureService())
    return TestClient(create_app())


def test_directory_browse_requires_admin_and_authentication(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/api/storage-settings/browse").status_code == 401
        for headers in (EDITOR, READONLY):
            response = client.get("/api/storage-settings/browse", headers=headers)
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "FORBIDDEN"
        assert client.get("/api/storage-settings/browse", headers=ADMIN).status_code == 200


def test_directory_browse_lists_only_directories_and_caps_results(
    monkeypatch, tmp_path: Path
):
    root = tmp_path / "root"
    root.mkdir()
    (root / "子目录").mkdir()
    (root / "文件.txt").write_text("not returned", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()

    with _client(monkeypatch) as client:
        response = client.get(
            "/api/storage-settings/browse", params={"path": str(root)}, headers=ADMIN
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["path"] == str(root.resolve())
        assert [item["name"] for item in body["directories"]] == ["子目录"]
        assert all(Path(item["path"]).is_dir() for item in body["directories"])
        assert "文件.txt" not in response.text

        traversal = client.get(
            "/api/storage-settings/browse",
            params={"path": str(root / ".." / "outside")},
            headers=ADMIN,
        )
        assert traversal.status_code == 400, traversal.text
        assert traversal.json()["detail"]["code"] == "INVALID_PATH"
        relative = client.get(
            "/api/storage-settings/browse", params={"path": "relative"}, headers=ADMIN
        )
        assert relative.status_code == 400, relative.text
        link = tmp_path / "root-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        else:
            linked = client.get(
                "/api/storage-settings/browse", params={"path": str(link)}, headers=ADMIN
            )
            assert linked.status_code == 400, linked.text

    crowded = tmp_path / "crowded"
    crowded.mkdir()
    for index in range(settings_service.DIRECTORY_BROWSE_LIMIT + 5):
        (crowded / f"dir-{index:03}").mkdir()
    with _client(monkeypatch) as client:
        capped = client.get(
            "/api/storage-settings/browse", params={"path": str(crowded)}, headers=ADMIN
        )
    assert capped.status_code == 200, capped.text
    assert len(capped.json()["directories"]) == settings_service.DIRECTORY_BROWSE_LIMIT
    assert capped.json()["truncated"] is True


def test_directory_browse_skips_unreadable_children(monkeypatch, tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "readable").mkdir()
    (root / "blocked").mkdir()
    real_scandir = os.scandir

    class EntryProxy:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name
            self.path = entry.path

        def is_dir(self, *, follow_symlinks=True):
            if self.name == "blocked":
                raise PermissionError("inaccessible entry")
            return self._entry.is_dir(follow_symlinks=follow_symlinks)

    class ScandirProxy:
        def __init__(self, path):
            self.path = path
            self.iterator = None

        def __enter__(self):
            self.iterator = real_scandir(self.path)
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.iterator.close()
            return False

        def __iter__(self):
            return self

        def __next__(self):
            return EntryProxy(next(self.iterator))

    def patched_scandir(path):
        if Path(path) == root:
            return ScandirProxy(path)
        return real_scandir(path)

    monkeypatch.setattr(settings_service.os, "scandir", patched_scandir)
    with _client(monkeypatch) as client:
        response = client.get(
            "/api/storage-settings/browse", params={"path": str(root)}, headers=ADMIN
        )
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["directories"]] == ["readable"]


def test_directory_browse_is_read_only():
    with TestClient(create_app()) as client:
        response = client.post("/api/storage-settings/browse", headers=ADMIN)
    assert response.status_code == 405

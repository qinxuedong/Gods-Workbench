"""DIR-T6 单文件操作、god-recycler、派生物恢复与操作日志契约。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core import storage

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOVERNOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "governor"}


@pytest.fixture()
def local_runtime(tmp_path, monkeypatch):
    root = tmp_path / "sources"
    root.mkdir()
    (root / "原文件.txt").write_text("single-file", encoding="utf-8")
    upload = root / "preview.png"
    upload.write_bytes(b"derived-preview")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    return root, upload


@pytest.fixture()
def operation_client(local_runtime, monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    with TestClient(create_app()) as client:
        yield client


def _register_local_asset(client: TestClient, root: Path) -> str:
    response = client.post(
        f"{BASE}/assets/import",
        headers=AUTH,
        json={"items": [{"name": "原文件.txt", "kind": "document", "display_path": "原文件.txt"}]},
    )
    assert response.status_code == 200, response.text
    asset_id = response.json()["asset_ids"][0]
    revision = client.get(f"{BASE}/status", headers=AUTH).json()["revision"]
    patched = client.patch(
        f"{BASE}/assets/{asset_id}",
        headers=AUTH,
        json={
            "display_path": "原文件.txt",
            "root_id": storage.root_identity_id(root.resolve()),
            "expected_version": revision,
        },
    )
    assert patched.status_code == 200, patched.text
    assert client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH).content == b"single-file"
    return asset_id


def _revision(client: TestClient) -> int:
    return client.get(f"{BASE}/status", headers=AUTH).json()["revision"]


def test_rename_moves_physical_file_and_keeps_asset_id(local_runtime, operation_client):
    root = local_runtime[0]
    client = operation_client
    asset_id = _register_local_asset(client, root)
    before = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]

    response = client.post(
        f"{BASE}/assets/{asset_id}/file-operations/rename",
        headers=AUTH,
        json={"new_name": "新文件名.txt", "expected_version": before["version"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset_id"] == asset_id
    assert body["operation"]["state"] == "done"
    assert root.joinpath("原文件.txt").exists() is False
    assert root.joinpath("新文件名.txt").read_text(encoding="utf-8") == "single-file"
    assert client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH).content == b"single-file"
    assert client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]["asset_id"] == asset_id


def test_rename_conflict_and_journal_failure_shape(local_runtime, operation_client):
    root = local_runtime[0]
    client = operation_client
    asset_id = _register_local_asset(client, root)
    (root / "新文件名.txt").write_text("target", encoding="utf-8")
    version = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]["version"]

    response = client.post(
        f"{BASE}/assets/{asset_id}/file-operations/rename",
        headers=AUTH,
        json={"new_name": "新文件名.txt", "expected_version": version},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FILE_TARGET_EXISTS"
    assert root.joinpath("原文件.txt").read_text(encoding="utf-8") == "single-file"


def test_delete_moves_file_manifest_and_derived_to_recycler(local_runtime, operation_client):
    client = operation_client
    asset_id = _register_local_asset(client, local_runtime[0])
    derived = client.post(
        f"{BASE}/assets/{asset_id}/derived-assets",
        headers=AUTH,
        json={"name": "preview.png", "path": str(local_runtime[1])},
    )
    assert derived.status_code == 200, derived.text
    print("DERIVED_RESPONSE", derived.text)
    revision = _revision(client)

    response = client.request(
        "DELETE",
        f"{BASE}/assets/{asset_id}",
        headers=AUTH,
        json={"expected_version": revision},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset_id"] == asset_id
    assert body["operation"]["type"] == "delete"
    recycler = body["operation"]["recycler"]
    assert "god-recycler/document/" in recycler["original_file"]
    assert "god-recycler/document/" in recycler["manifest"]
    assert all("god-recycler/document/" in item for item in recycler["derived"])
    assert client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).status_code == 404

    manifest = client.get(
        "/api/download-output", params={"path": recycler["manifest"]}, headers=AUTH
    )
    assert manifest.status_code == 200, manifest.text
    payload = json.loads(manifest.content)
    assert payload["asset_id"] == asset_id
    assert payload["original"]["root_id"]
    assert payload["original"]["canonical_relpath"] == "原文件.txt"
    assert payload["derived"][0]["path"] == "preview.png"


def test_restore_manifest_preserves_derived_and_conflicts_are_explicit(local_runtime, operation_client):
    client = operation_client
    asset_id = _register_local_asset(client, local_runtime[0])
    derived = client.post(
        f"{BASE}/assets/{asset_id}/derived-assets",
        headers=AUTH,
        json={"name": "preview.png", "path": str(local_runtime[1])},
    )
    assert derived.status_code == 200, derived.text
    deleted = client.request(
        "DELETE", f"{BASE}/assets/{asset_id}", headers=AUTH,
        json={"expected_version": _revision(client)},
    )
    assert deleted.status_code == 200, deleted.text
    entry_id = deleted.json()["recycle_entry_id"]

    restored = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"expected_version": _revision(client)},
    )
    assert restored.status_code == 200, restored.text
    restore = restored.json()
    assert restore["asset_id"] == asset_id
    assert restore["operation"]["type"] == "restore"
    root = local_runtime[0]
    assert root.joinpath("原文件.txt").read_text(encoding="utf-8") == "single-file"
    assert root.joinpath("preview.png").read_bytes() == b"derived-preview"
    assert client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH).content == b"single-file"

    second = client.request(
        "DELETE", f"{BASE}/assets/{asset_id}", headers=AUTH,
        json={"expected_version": _revision(client)},
    )
    assert second.status_code == 200, second.text
    entry_id = second.json()["recycle_entry_id"]
    root.joinpath("原文件.txt").write_text("replacement", encoding="utf-8")
    root.joinpath("preview.png").write_bytes(b"external-preview")
    conflict = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"expected_version": _revision(client), "resolution": "reject"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "RESTORE_PATH_CONFLICT"

    suffixed = client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"expected_version": _revision(client), "resolution": "suffix"},
    )
    assert suffixed.status_code == 200, suffixed.text
    assert root.joinpath("原文件 (1).txt").read_text(encoding="utf-8") == "single-file"
    assert root.joinpath("preview (1).png").read_bytes() == b"derived-preview"
    assert root.joinpath("preview.png").read_bytes() == b"external-preview"


def test_pending_journal_startup_is_reconciled_or_marked_manual(local_runtime, operation_client):
    client = operation_client
    asset_id = _register_local_asset(client, local_runtime[0])
    version = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]["version"]
    data_dir = Path(os.environ["GW_DATA_DIR"])
    manual = data_dir / "data" / "journal" / "asset-file-operations.jsonl"
    manual.parent.mkdir(parents=True, exist_ok=True)
    manual.write_text(
        json.dumps(
            {
                "op_id": "op_manual_0001",
                "type": "rename",
                "asset_id": asset_id,
                "state": "pending",
                "source": {"root_id": "unknown", "canonical_relpath": "missing.txt"},
                "target": {"root_id": "unknown", "canonical_relpath": "target.txt"},
            },
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    with TestClient(create_app()) as restarted:
        response = restarted.get(
            f"{BASE}/file-operations/op_manual_0001", headers=AUTH
        )
    assert response.status_code == 200, response.text
    assert response.json()["operation"]["state"] == "manual_review"
    assert version == 2

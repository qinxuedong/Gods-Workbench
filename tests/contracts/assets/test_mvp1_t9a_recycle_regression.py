"""MVP1-T9a 单文件操作与回收站回归契约。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core import storage

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOVERNOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "governor"}
ADMIN = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "admin"}
JOURNAL_RELATIVE = Path("data") / "journal" / "asset-file-operations.jsonl"


@pytest.fixture()
def t9a_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "sources"
    source = root / "素材" / "原文件.txt"
    source.parent.mkdir(parents=True)
    source.write_text("t9a-regression-content", encoding="utf-8")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    return root, source


@pytest.fixture()
def t9a_client(t9a_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    with TestClient(create_app()) as client:
        yield client


def _revision(client: TestClient) -> int:
    response = client.get(f"{BASE}/status", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def _register_asset(
    client: TestClient,
    root: Path,
    source: Path,
    *,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> str:
    relative = source.relative_to(root).as_posix()
    response = client.post(
        f"{BASE}/assets/import",
        headers=AUTH,
        json={
            "items": [
                {
                    "name": source.name,
                    "kind": "document",
                    "display_path": relative,
                    "metadata": metadata or {"qa_marker": "mvp1-t9a"},
                    "tags": tags or ["t9a"],
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    asset_id = response.json()["asset_ids"][0]
    patched = client.patch(
        f"{BASE}/assets/{asset_id}",
        headers=AUTH,
        json={
            "display_path": relative,
            "root_id": storage.root_identity_id(root.resolve()),
            "expected_version": _revision(client),
        },
    )
    assert patched.status_code == 200, patched.text
    assert client.get(f"{BASE}/assets/{asset_id}/media", headers=AUTH).content == (
        b"t9a-regression-content"
    )
    return asset_id


def _journal_records() -> list[dict]:
    path = Path(os.environ["GW_DATA_DIR"]) / JOURNAL_RELATIVE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _assert_complete_operation(
    client: TestClient, payload: dict, *, expected_type: str, asset_id: str
) -> dict:
    operation = payload["operation"]
    required = {
        "op_id",
        "type",
        "asset_id",
        "actor",
        "source",
        "target",
        "fingerprint",
        "state",
        "started_at",
        "finished_at",
        "error_code",
        "error_message",
    }
    assert required <= operation.keys()
    assert operation["op_id"].startswith("fop_")
    assert operation["type"] == expected_type
    assert operation["asset_id"] == asset_id
    assert operation["actor"]
    assert isinstance(operation["source"], dict) and operation["source"]
    assert isinstance(operation["target"], dict) and operation["target"]
    assert re.fullmatch(r"[0-9a-f]{64}", operation["fingerprint"])
    assert operation["state"] == "done"
    assert operation["error_code"] is None
    assert operation["error_message"] is None
    started = datetime.fromisoformat(operation["started_at"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat(operation["finished_at"].replace("Z", "+00:00"))
    assert finished >= started

    readback = client.get(
        f"{BASE}/file-operations/{operation['op_id']}", headers=AUTH
    )
    assert readback.status_code == 200, readback.text
    assert readback.json()["operation"] == operation
    assert operation in _journal_records()
    return operation


def _delete_asset(client: TestClient, asset_id: str):
    return client.request(
        "DELETE",
        f"{BASE}/assets/{asset_id}",
        headers=AUTH,
        json={"expected_version": _revision(client)},
    )


def _restore_asset(client: TestClient, entry_id: str, *, resolution: str = "reject"):
    return client.post(
        f"{BASE}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"expected_version": _revision(client), "resolution": resolution},
    )


def test_t9a_chinese_rename_preserves_asset_identity_and_logs_operation(
    t9a_runtime, t9a_client
):
    root, source = t9a_runtime
    client = t9a_client
    asset_id = _register_asset(client, root, source)

    response = client.post(
        f"{BASE}/assets/{asset_id}/file-operations/rename",
        headers=AUTH,
        json={
            "new_name": "新素材名.txt",
            "expected_version": client.get(
                f"{BASE}/assets/{asset_id}", headers=AUTH
            ).json()["asset"]["version"],
        },
    )

    assert response.status_code == 200, response.text
    operation = _assert_complete_operation(
        client, response.json(), expected_type="rename", asset_id=asset_id
    )
    renamed = root / "素材" / "新素材名.txt"
    assert not source.exists()
    assert renamed.read_text(encoding="utf-8") == "t9a-regression-content"
    asset = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]
    assert asset["asset_id"] == asset_id
    assert asset["display_path"] == "素材/新素材名.txt"
    assert operation["target"]["display_path"] == asset["display_path"]


def test_t9a_rename_conflict_keeps_both_files_and_does_not_log_a_started_operation(
    t9a_runtime, t9a_client
):
    root, source = t9a_runtime
    client = t9a_client
    asset_id = _register_asset(client, root, source)
    target = source.with_name("占用.txt")
    target.write_text("keep-existing", encoding="utf-8")
    records_before = _journal_records()

    response = client.post(
        f"{BASE}/assets/{asset_id}/file-operations/rename",
        headers=AUTH,
        json={
            "new_name": target.name,
            "expected_version": client.get(
                f"{BASE}/assets/{asset_id}", headers=AUTH
            ).json()["asset"]["version"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "FILE_TARGET_EXISTS"
    assert source.read_text(encoding="utf-8") == "t9a-regression-content"
    assert target.read_text(encoding="utf-8") == "keep-existing"
    assert _journal_records() == records_before


def test_t9a_recycle_moves_original_and_derived_files_and_logs_operation(
    t9a_runtime, t9a_client
):
    root, source = t9a_runtime
    client = t9a_client
    asset_id = _register_asset(client, root, source)
    derived = root / "素材" / "预览.png"
    derived.write_bytes(b"derived-preview")
    derived_response = client.post(
        f"{BASE}/assets/{asset_id}/derived-assets",
        headers=AUTH,
        json={"name": derived.name, "path": str(derived)},
    )
    assert derived_response.status_code == 200, derived_response.text

    response = _delete_asset(client, asset_id)

    assert response.status_code == 200, response.text
    body = response.json()
    operation = _assert_complete_operation(
        client, body, expected_type="delete", asset_id=asset_id
    )
    assert not source.exists()
    assert not derived.exists()
    assert client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).status_code == 404
    recycler = operation["recycler"]
    assert "god-recycler/document/" in recycler["original_file"]
    assert len(recycler["derived"]) == 1
    manifest_response = client.get(
        "/api/download-output",
        params={"path": recycler["manifest"]},
        headers=AUTH,
    )
    assert manifest_response.status_code == 200, manifest_response.text
    manifest = json.loads(manifest_response.content)
    assert manifest["asset_id"] == asset_id
    assert manifest["original"]["canonical_relpath"] == "素材/原文件.txt"
    assert manifest["derived"][0]["name"] == derived.name


def test_t9a_restore_to_free_path_preserves_metadata_and_logs_operation(
    t9a_runtime, t9a_client
):
    root, source = t9a_runtime
    client = t9a_client
    metadata = {"qa_marker": "mvp1-t9a", "nested": {"retain": True}}
    asset_id = _register_asset(
        client, root, source, metadata=metadata, tags=["t9a", "回归"]
    )
    before = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]
    deleted = _delete_asset(client, asset_id)
    assert deleted.status_code == 200, deleted.text
    _assert_complete_operation(
        client, deleted.json(), expected_type="delete", asset_id=asset_id
    )

    restored = _restore_asset(client, deleted.json()["recycle_entry_id"])

    assert restored.status_code == 200, restored.text
    _assert_complete_operation(
        client, restored.json(), expected_type="restore", asset_id=asset_id
    )
    asset = client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).json()["asset"]
    assert asset["asset_id"] == before["asset_id"]
    assert asset["kind"] == before["kind"]
    assert asset["display_path"] == before["display_path"] == "素材/原文件.txt"
    assert asset["root_id"] == before["root_id"]
    assert asset["canonical_relpath"] == before["canonical_relpath"]
    assert asset["metadata"] == metadata
    assert asset["tags"] == ["t9a", "回归"]
    assert source.read_text(encoding="utf-8") == "t9a-regression-content"


def test_t9a_restore_conflict_reject_keeps_entry_and_original_file(
    t9a_runtime, t9a_client
):
    root, source = t9a_runtime
    client = t9a_client
    asset_id = _register_asset(client, root, source)
    deleted = _delete_asset(client, asset_id)
    assert deleted.status_code == 200, deleted.text
    _assert_complete_operation(
        client, deleted.json(), expected_type="delete", asset_id=asset_id
    )
    entry_id = deleted.json()["recycle_entry_id"]
    source.write_text("replacement", encoding="utf-8")
    records_before = _journal_records()

    rejected = _restore_asset(client, entry_id, resolution="reject")

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "RESTORE_PATH_CONFLICT"
    assert source.read_text(encoding="utf-8") == "replacement"
    assert client.get(f"{BASE}/assets/{asset_id}", headers=AUTH).status_code == 404
    assert _journal_records() == records_before


def test_t9a_restore_suffix_preserves_original_and_logs_operation(
    t9a_runtime, t9a_client
):
    root, source = t9a_runtime
    client = t9a_client
    asset_id = _register_asset(client, root, source)
    deleted = _delete_asset(client, asset_id)
    assert deleted.status_code == 200, deleted.text
    _assert_complete_operation(
        client, deleted.json(), expected_type="delete", asset_id=asset_id
    )
    source.write_text("replacement", encoding="utf-8")

    restored = _restore_asset(
        client, deleted.json()["recycle_entry_id"], resolution="suffix"
    )

    assert restored.status_code == 200, restored.text
    operation = _assert_complete_operation(
        client, restored.json(), expected_type="restore", asset_id=asset_id
    )
    restored_file = source.with_name("原文件 (1).txt")
    assert source.read_text(encoding="utf-8") == "replacement"
    assert restored_file.read_text(encoding="utf-8") == "t9a-regression-content"
    assert operation["suffix_applied"] is True
    assert restored.json()["display_path"] == "素材/原文件 (1).txt"


def test_t9a_long_chinese_relative_path_rename_logs_complete_operation(
    t9a_runtime, t9a_client
):
    root, _ = t9a_runtime
    client = t9a_client
    nested = root / ("长路径目录" * 6) / ("深层中文目录" * 4)
    nested.mkdir(parents=True)
    source = nested / (("中文素材" * 10) + ".txt")
    source.write_text("t9a-regression-content", encoding="utf-8")
    assert len(str(source)) >= 150
    asset_id = _register_asset(client, root, source)

    response = client.post(
        f"{BASE}/assets/{asset_id}/file-operations/rename",
        headers=AUTH,
        json={
            "new_name": ("新素材名" * 8) + ".txt",
            "expected_version": client.get(
                f"{BASE}/assets/{asset_id}", headers=AUTH
            ).json()["asset"]["version"],
        },
    )

    assert response.status_code == 200, response.text
    _assert_complete_operation(
        client, response.json(), expected_type="rename", asset_id=asset_id
    )
    assert not source.exists()
    assert source.with_name(("新素材名" * 8) + ".txt").read_text(
        encoding="utf-8"
    ) == "t9a-regression-content"


@pytest.mark.xfail(strict=False, reason="待 #329：持久化 allowed roots API 合并")
def test_t9a_recycle_operations_work_on_persisted_allowed_root(
    t9a_runtime, t9a_client, tmp_path: Path
):
    client = t9a_client
    root = tmp_path / "persisted-root"
    source = root / "新根素材.txt"
    source.parent.mkdir()
    source.write_text("t9a-regression-content", encoding="utf-8")

    snapshot = client.get("/api/storage-settings/roots", headers=ADMIN)
    assert snapshot.status_code == 200, snapshot.text
    registered = client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": str(root), "expected_version": snapshot.json()["revision"]},
    )
    assert registered.status_code == 200, registered.text
    root_id = storage.root_identity_id(root.resolve())
    assert any(item["id"] == root_id for item in registered.json()["roots"])
    asset_id = _register_asset(client, root, source)

    deleted = _delete_asset(client, asset_id)
    assert deleted.status_code == 200, deleted.text
    _assert_complete_operation(
        client, deleted.json(), expected_type="delete", asset_id=asset_id
    )
    restored = _restore_asset(client, deleted.json()["recycle_entry_id"])
    assert restored.status_code == 200, restored.text
    _assert_complete_operation(
        client, restored.json(), expected_type="restore", asset_id=asset_id
    )

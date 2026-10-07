# -*- coding: utf-8 -*-
"""Phase 12 A1 素材库与本地素材真实接入契约回归。

覆盖 docs/contracts/ASSET-LIBRARY-B4-INTERFACE-CATALOG.yaml 与
LOCAL-ASSET-INTERFACE-CATALOG.yaml 声明的 40 个操作：

- 未认证一律 401（认证先于参数校验）；
- 只读角色写操作一律 403；
- 写→读回真实生效；删除后不再返回；
- 本机文件严格锚定 GW_ALLOWED_ROOTS，未配置 403，越界 403 且不回显原路径；
- 重启用例：同一 GW_DATA_DIR 下新实例可读回上一实例写入的数据。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app

ROOT = Path(__file__).resolve().parents[3]
CATALOGS = (
    ROOT / "docs/contracts/ASSET-LIBRARY-B4-INTERFACE-CATALOG.yaml",
    ROOT / "docs/contracts/LOCAL-ASSET-INTERFACE-CATALOG.yaml",
)
FIXTURES = ROOT / "docs/fixtures"
EDITOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}

#: 四条需要真实文件系统准入的操作（未配置 GW_ALLOWED_ROOTS 时必须 403）。
FILE_OPS = {
    ("GET", "/api/asset-file-info"),
    ("POST", "/api/asset-file-reveal"),
    ("POST", "/api/asset-library/workflows/upload"),
    ("POST", "/api/local-assets/upload"),
}


def _pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for catalog in CATALOGS:
        text = catalog.read_text(encoding="utf-8-sig")
        pairs.extend(re.findall(r"\s+method: (\w+)\n\s+path: (\S+)", text))
    return pairs


def _path(path: str) -> str:
    """把归一化占位符替换为可用样例值。"""
    replacements = {
        "{p}": "sample-id",
        "asset_id={asset_id}": "asset_id=asset_0001",
    }
    for old, new in replacements.items():
        path = path.replace(old, new)
    return path


def _client() -> TestClient:
    return TestClient(create_app())


def _payload(method: str):
    return {} if method in {"PATCH", "POST", "DELETE", "PUT"} else None


def test_b4_contract_and_fixtures_are_complete():
    """契约必须覆盖 40 个方法+路径，且夹具齐备。"""
    fixture = json.loads((FIXTURES / "phase11-b4-route-matrix.json").read_text(encoding="utf-8"))
    assert fixture["block"] == "B4"
    assert fixture["path_count"] == 30
    assert fixture["method_path_count"] == 40
    pairs = _pairs()
    assert len(pairs) == 40
    assert len({path for _, path in pairs}) == 30
    for name in ("phase11-b4-route-matrix.json", "phase11-b4-empty-state.json"):
        assert (FIXTURES / name).is_file()


def test_b4_all_operations_require_authentication_first():
    """认证必须先于参数校验：未认证一律 401，不泄漏参数细节。"""
    with _client() as client:
        for method, normalized in _pairs():
            path = _path(normalized)
            payload = _payload(method)
            response = (client.request(method, path, json=payload) if payload is not None
                        else client.request(method, path))
            assert response.status_code == 401, (method, path, response.text)


def test_b4_write_operations_reject_readonly_role():
    """只读角色写操作一律 403。"""
    with _client() as client:
        for method, normalized in _pairs():
            if method not in {"POST", "PATCH", "DELETE", "PUT"}:
                continue
            path = _path(normalized)
            response = client.request(method, path, headers=READONLY, json={})
            assert response.status_code == 403, (method, path, response.text)


def test_b4_file_operations_fail_closed_without_allowed_roots():
    """未配置 GW_ALLOWED_ROOTS 时本机文件操作必须 403，且不回显原始路径。"""
    payload = {"path": "C:/outside/secret.txt", "avatar_path": "C:/outside/secret.txt",
               "content_base64": base64.b64encode(b"x").decode()}
    with _client() as client:
        for method, path in FILE_OPS:
            if method == "GET":
                response = client.get(path, headers=EDITOR, params={"path": payload["path"]})
            else:
                response = client.request(method, path, headers=EDITOR, json=payload)
            assert response.status_code == 403, (method, path, response.text)
            assert "secret.txt" not in response.text
            assert response.json()["detail"]["code"] in {
                "LOCAL_FILE_ACCESS_NOT_ADMITTED", "INVALID_PATH",
            }


def test_b4_file_operations_reject_path_traversal(tmp_path, monkeypatch):
    """即使配置了允许根目录，越界路径仍必须 403 且不回显原始路径。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    with _client() as client:
        outside = tmp_path / "outside" / "secret.txt"
        response = client.get("/api/asset-file-info", headers=EDITOR, params={"path": str(outside)})
        assert response.status_code == 403, response.text
        assert "secret.txt" not in response.text
        traversal = client.post("/api/asset-file-reveal", headers=EDITOR,
                                json={"path": str(allowed / ".." / "outside" / "x.txt")})
        assert traversal.status_code == 403, traversal.text


def test_b4_file_info_returns_real_metadata(tmp_path, monkeypatch):
    """真实文件应返回真实体积与图片宽高。"""
    from PIL import Image

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    image_path = allowed / "shot.png"
    Image.new("RGB", (37, 19), (10, 10, 10)).save(image_path)
    with _client() as client:
        response = client.get("/api/asset-file-info", headers=EDITOR, params={"path": str(image_path)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["width"] == 37 and body["height"] == 19
    assert body["size_bytes"] == image_path.stat().st_size
    assert body["display_path"] == "shot.png"


def test_b4_local_asset_write_read_delete_roundtrip(tmp_path, monkeypatch):
    """移动/删除后磁盘、索引 GET 与新应用实例读回必须一致。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    target = allowed / "clip.bin"
    payload = {"path": str(target), "content_base64": base64.b64encode(b"hello-asset").decode()}
    with _client() as client:
        uploaded = client.post("/api/local-assets/upload", headers=EDITOR, json=payload)
        assert uploaded.status_code == 201, uploaded.text
        assert target.read_bytes() == b"hello-asset"
        listed = client.get("/api/local-assets", headers=EDITOR).json()
        assert listed["count"] == 1
        asset_id = listed["items"][0]["asset_id"]

        target2 = allowed / "clip2.bin"
        moved = client.post("/api/local-assets/move", headers=EDITOR,
                            json={"source": str(target), "target": str(target2)})
        assert moved.status_code == 200, moved.text
        assert target2.read_bytes() == b"hello-asset" and not target.exists()
        assert moved.json()["asset_id"] == asset_id
        after_move = client.get("/api/local-assets", headers=EDITOR).json()
        assert after_move["count"] == 1
        assert after_move["items"][0]["asset_id"] == asset_id
        assert after_move["items"][0]["display_path"] == "clip2.bin"
        assert all(item["display_path"] != "clip.bin" for item in after_move["items"])

    # 新建应用实例读回证明不是仅依赖当前路由对象内存。
    with _client() as restarted:
        after_restart = restarted.get("/api/local-assets", headers=EDITOR).json()
        assert after_restart["count"] == 1
        assert after_restart["items"][0]["asset_id"] == asset_id
        assert after_restart["items"][0]["display_path"] == "clip2.bin"
        deleted = restarted.post("/api/local-assets/delete", headers=EDITOR,
                                 json={"paths": [str(allowed / "clip2.bin")]})
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] == ["clip2.bin"]
        assert deleted.json()["partial_failures"] == []
        assert not target2.exists()
        assert restarted.get("/api/local-assets", headers=EDITOR).json()["count"] == 0

    with _client() as after_delete_restart:
        final_state = after_delete_restart.get("/api/local-assets", headers=EDITOR).json()
        assert final_state["count"] == 0
        assert final_state["items"] == []


def test_b4_local_asset_ids_are_unique_stable_and_update_targets_second(tmp_path, monkeypatch):
    """多文件索引、重复索引与按第二个 ID 更新均不得串项。"""
    from gw.asset_library import repository as repo

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    alpha = allowed / "alpha.png"
    beta = allowed / "beta.png"
    alpha.write_bytes(b"alpha")
    beta.write_bytes(b"beta")

    indexed = repo.index_local_files([str(alpha), str(beta)])
    assert indexed["count"] == 2
    alpha_id, beta_id = [item["asset_id"] for item in indexed["items"]]
    assert alpha_id != beta_id
    assert alpha_id == "local_0001" and beta_id == "local_0002"

    again = repo.index_local_files([str(alpha)])
    assert again["items"][0]["asset_id"] == alpha_id
    assert again["id_repairs"] == []

    updated = repo.update_local_entry(beta_id, {"name": "beta-renamed.png"})
    assert updated["asset_id"] == beta_id
    with _client() as client:
        listed = client.get("/api/local-assets", headers=EDITOR).json()
        by_id = {item["asset_id"]: item for item in listed["items"]}
        assert by_id[alpha_id]["name"] == "alpha.png"
        assert by_id[beta_id]["name"] == "beta-renamed.png"
        assert listed["data_gaps"] == []

    with _client() as restarted:
        persisted = restarted.get("/api/local-assets", headers=EDITOR).json()
        by_id = {item["asset_id"]: item for item in persisted["items"]}
        assert by_id[alpha_id]["name"] == "alpha.png"
        assert by_id[beta_id]["name"] == "beta-renamed.png"


def test_b4_legacy_duplicate_asset_ids_fail_closed_then_repair_deterministically(tmp_path, monkeypatch):
    """历史重复 ID 不默认选中对象；重索引显式返回稳定、唯一的新映射。"""
    from gw.asset_library import repository as repo
    from gw.core.errors import CleanroomException

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    alpha = allowed / "alpha.png"
    beta = allowed / "beta.png"
    alpha.write_bytes(b"alpha")
    beta.write_bytes(b"beta")
    repo._local_state().write({
        "sequence": 1,
        "entries": {
            "alpha.png": {"asset_id": "local_0001", "display_path": "alpha.png", "name": "alpha.png"},
            "beta.png": {"asset_id": "local_0001", "display_path": "beta.png", "name": "beta.png"},
        },
    })

    with pytest.raises(CleanroomException) as ambiguous:
        repo.update_local_entry("local_0001", {"name": "不可静默选择"})
    assert ambiguous.value.status_code == 409
    assert ambiguous.value.code == "LOCAL_ASSET_ID_AMBIGUOUS"

    repaired = repo.index_local_files([str(alpha), str(beta)])
    assert len(repaired["id_repairs"]) == 2
    assert {item["previous_asset_id"] for item in repaired["id_repairs"]} == {"local_0001"}
    ids = [item["asset_id"] for item in repaired["items"]]
    assert len(ids) == len(set(ids)) == 2
    stable = {item["display_path"]: item["asset_id"] for item in repaired["items"]}
    again = repo.index_local_files([str(alpha), str(beta)])
    assert {item["display_path"]: item["asset_id"] for item in again["items"]} == stable
    assert again["id_repairs"] == []


def test_b4_moving_a_legacy_duplicate_id_repairs_before_removing_source(tmp_path, monkeypatch):
    """移动旧重复 ID 记录时，移动项也参与确定性修复，最终无歧义。"""
    from gw.asset_library import repository as repo

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    alpha = allowed / "alpha.png"
    beta = allowed / "beta.png"
    moved = allowed / "moved.png"
    alpha.write_bytes(b"alpha")
    beta.write_bytes(b"beta")
    repo._local_state().write({
        "sequence": 1,
        "entries": {
            "alpha.png": {"asset_id": "local_0001", "display_path": "alpha.png", "name": "alpha.png"},
            "beta.png": {"asset_id": "local_0001", "display_path": "beta.png", "name": "beta.png"},
        },
    })

    result = repo.move_local_file(str(alpha), str(moved))
    listing = repo.list_local_assets()
    by_path = {item["display_path"]: item for item in listing["items"]}

    assert not alpha.exists() and moved.read_bytes() == b"alpha"
    assert result["asset_id"] == by_path["moved.png"]["asset_id"]
    assert len(result["id_repairs"]) == 2
    assert {repair["display_path"] for repair in result["id_repairs"]} == {"alpha.png", "beta.png"}
    assert len({item["asset_id"] for item in listing["items"]}) == 2
    assert listing["data_gaps"] == []


def test_b4_local_asset_disk_operations_compensate_index_failures(tmp_path, monkeypatch):
    """索引存储故障时，移动和删除均恢复原文件及原索引，不留悬空路径。"""
    from gw.asset_library import repository as repo
    from gw.core.errors import CleanroomException

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    source = allowed / "source.bin"
    source.write_bytes(b"payload")
    repo.index_local_files([str(source)])
    real_state = repo._local_state()

    class FailingState:
        def read(self):
            return real_state.read()

        def mutate(self, _mutator):
            raise OSError("测试索引写入失败")

        def write(self, value):
            return real_state.write(value)

    monkeypatch.setattr(repo, "_local_state", lambda: FailingState())
    target = allowed / "target.bin"
    with pytest.raises(CleanroomException) as move_error:
        repo.move_local_file(str(source), str(target))
    assert move_error.value.code == "LOCAL_ASSET_INDEX_UPDATE_FAILED"
    assert source.read_bytes() == b"payload" and not target.exists()
    before_delete = repo.list_local_assets()
    assert before_delete["count"] == 1 and before_delete["items"][0]["display_path"] == "source.bin"

    deleted = repo.delete_local_files([str(source)])
    assert deleted["deleted"] == [] and deleted["count"] == 0
    assert deleted["partial_failures"][0]["filesystem_state"] == "restored"
    assert source.read_bytes() == b"payload"
    after_delete = repo.list_local_assets()
    assert after_delete["count"] == 1 and after_delete["items"][0]["display_path"] == "source.bin"


def test_b4_library_item_lifecycle_and_cas():
    """创建库 → 分类 → 条目 → 重命名 → 删除，并校验稳定 ID 与 CAS 409。"""
    with _client() as client:
        created = client.post("/api/asset-library/libraries", headers=EDITOR,
                              json={"name": "角色参考库", "expected_version": 1})
        assert created.status_code == 201, created.text
        library_id = created.json()["library"]["library_id"]
        assert library_id == "library_0001"

        category = client.post("/api/asset-library/categories", headers=EDITOR,
                               json={"library_id": library_id, "name": "定妆", "type": "image",
                                     "expected_version": 1})
        assert category.status_code == 201, category.text
        category_id = category.json()["category"]["category_id"]

        batch = client.post("/api/asset-library/items/batch", headers=EDITOR,
                            json={"library_id": library_id, "category_id": category_id,
                                  "names": ["hero.png", "scene.mp4"], "expected_version": 2})
        assert batch.status_code == 201, batch.text
        assert batch.json()["asset_ids"] == ["asset_0001", "asset_0002"]

        classified = client.post("/api/asset-library/items/classify", headers=EDITOR,
                                 json={"asset_ids": ["asset_0001"]})
        assert classified.status_code == 200
        assert classified.json()["results"][0]["classification"] == "image"

        renamed = client.patch("/api/asset-library/items/asset_0001", headers=EDITOR,
                               json={"name": "hero-final.png"})
        assert renamed.status_code == 200 and renamed.json()["name"] == "hero-final.png"

        snapshot = client.get("/api/asset-library", headers=EDITOR).json()["library"]
        names = [item["name"] for lib in snapshot["libraries"]
                 for cat in lib["categories"] for item in cat["items"]]
        assert "hero-final.png" in names

        conflict = client.post("/api/asset-library/libraries", headers=EDITOR,
                               json={"name": "冲突库", "expected_version": 999})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "VERSION_CONFLICT"

        removed = client.delete("/api/asset-library/items/asset_0002", headers=EDITOR)
        assert removed.status_code == 200
        snapshot_after = client.get("/api/asset-library", headers=EDITOR).json()["library"]
        remaining = [item["asset_id"] for lib in snapshot_after["libraries"]
                     for cat in lib["categories"] for item in cat["items"]]
        assert "asset_0002" not in remaining


def test_b4_content_versions_and_real_pdf():
    """内容写→读→版本→恢复→删除 必须真实生效，PDF 必须是合法字节流。"""
    with _client() as client:
        first = client.patch("/api/asset-content", headers=EDITOR,
                             params={"asset_id": "asset_0001"},
                             json={"content": "第一版"})
        assert first.status_code == 200, first.text
        second = client.patch("/api/asset-content", headers=EDITOR,
                              params={"asset_id": "asset_0001"},
                              json={"content": "第二版"})
        assert second.status_code == 200
        current = client.get("/api/asset-content", headers=EDITOR,
                             params={"asset_id": "asset_0001"}).json()
        assert current["content"] == "第二版"

        versions = client.get("/api/asset-content/versions", headers=EDITOR,
                              params={"asset_id": "asset_0001"}).json()
        assert len(versions["versions"]) == 2
        history_id = [v["version_id"] for v in versions["versions"] if not v["current"]][0]

        restored = client.post("/api/asset-content/versions/%s/restore" % history_id,
                               headers=EDITOR, params={"asset_id": "asset_0001"})
        assert restored.status_code == 200, restored.text
        assert restored.json()["content"] == "第一版"

        pdf = client.get("/api/asset-content/pdf", headers=EDITOR,
                         params={"asset_id": "asset_0001"})
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF-")
        assert pdf.content.rstrip().endswith(b"%%EOF")


def test_b4_classification_job_is_real_and_queryable():
    """后台分类返回真实 job_id，并可通过 jobs 端点查回。"""
    with _client() as client:
        started = client.post("/api/asset-classification/background", headers=EDITOR,
                              json={"asset_ids": []})
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]
        assert started.json()["poll_hint"] == "/api/asset-classification/jobs/%s" % job_id
        queried = client.get("/api/asset-classification/jobs/%s" % job_id, headers=EDITOR)
        assert queried.status_code == 200
        assert queried.json()["state"] == "completed"


def test_b4_state_survives_restart_within_same_data_dir():
    """同一 GW_DATA_DIR 下新建实例必须能读回上一实例写入的数据。"""
    with _client() as client:
        assert client.patch("/api/asset-classification-prompt", headers=EDITOR,
                            json={"prompt": "落盘的提示词"}).status_code == 200
    with _client() as client:
        prompt = client.get("/api/asset-classification-prompt", headers=EDITOR).json()
        assert prompt["prompt"] == "落盘的提示词"


def test_b4_local_assets_with_same_relative_path_fail_closed_across_roots(tmp_path, monkeypatch):
    """多允许根出现同名相对路径时，索引/移动/删除不得跨根覆盖。"""
    from gw.asset_library import repository as repo
    from gw.core.errors import CleanroomException

    root_a = tmp_path / "allowed-a"
    root_b = tmp_path / "allowed-b"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", os.pathsep.join((str(root_a), str(root_b))))
    file_a = root_a / "same.png"
    file_b = root_b / "same.png"
    file_a.write_bytes(b"A" * 10)
    file_b.write_bytes(b"B" * 22)

    first = repo.index_local_files([str(file_a)])
    asset_a_id = first["items"][0]["asset_id"]
    assert first["items"][0]["size_bytes"] == 10
    updated_a = repo.update_local_entry(asset_a_id, {"name": "root-a-only.png"})
    assert updated_a["name"] == "root-a-only.png"
    assert repo.list_local_assets()["items"][0]["asset_id"] == asset_a_id

    with pytest.raises(CleanroomException) as index_error:
        repo.index_local_files([str(file_b)])
    assert index_error.value.status_code == 409
    assert index_error.value.code == "LOCAL_ASSET_PATH_AMBIGUOUS"

    with pytest.raises(CleanroomException) as move_error:
        repo.move_local_file(str(file_b), str(root_b / "moved.png"))
    assert move_error.value.status_code == 409
    assert move_error.value.code == "LOCAL_ASSET_PATH_AMBIGUOUS"
    with pytest.raises(CleanroomException) as delete_error:
        repo.delete_local_files([str(file_b)])
    assert delete_error.value.status_code == 409
    assert delete_error.value.code == "LOCAL_ASSET_PATH_AMBIGUOUS"
    assert file_a.read_bytes() == b"A" * 10
    assert file_b.read_bytes() == b"B" * 22
    assert not (root_b / "moved.png").exists()

    moved = root_a / "moved.png"
    move_a = repo.move_local_file(str(file_a), str(moved))
    assert move_a["asset_id"] == asset_a_id
    assert moved.read_bytes() == b"A" * 10
    assert file_b.read_bytes() == b"B" * 22

    second = repo.index_local_files([str(file_b)])
    asset_b_id = second["items"][0]["asset_id"]
    assert asset_b_id != asset_a_id
    assert second["items"][0]["size_bytes"] == 22

    # API/新状态读取仅暴露稳定展示信息，不暴露内部允许根摘要或原始绝对根路径。
    with _client() as client:
        response = client.get("/api/local-assets", headers=EDITOR)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert {item["asset_id"] for item in body["items"]} == {asset_a_id, asset_b_id}
    assert {item["display_path"] for item in body["items"]} == {"same.png", "moved.png"}
    serialized = json.dumps(body, ensure_ascii=False)
    assert "_root_identity" not in serialized
    assert str(root_a) not in serialized and str(root_b) not in serialized
    assert body["data_gaps"] == []

    removed_b = repo.delete_local_files([str(file_b)])
    assert removed_b["deleted"] == ["same.png"]
    assert not file_b.exists()
    assert moved.read_bytes() == b"A" * 10
    after_delete = repo.list_local_assets()
    assert after_delete["count"] == 1
    assert after_delete["items"][0]["asset_id"] == asset_a_id
    assert after_delete["items"][0]["display_path"] == "moved.png"
    assert after_delete["data_gaps"] == []


def test_b4_local_asset_upload_cross_root_conflict_preserves_existing_target(tmp_path, monkeypatch):
    """上传命中另一允许根的同名索引时，HTTP 409 不得覆盖目标原内容。"""
    root_a = tmp_path / "allowed-a"
    root_b = tmp_path / "allowed-b"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", os.pathsep.join((str(root_a), str(root_b))))
    file_a = root_a / "same.png"
    file_b = root_b / "same.png"
    file_a.write_bytes(b"root-A-indexed")
    file_b.write_bytes(b"root-B-original-payload")

    from gw.asset_library import repository as repo
    indexed = repo.index_local_files([str(file_a)])
    before_hash = hashlib.sha256(file_b.read_bytes()).hexdigest()

    with _client() as client:
        response = client.post("/api/local-assets/upload", headers=EDITOR, json={
            "path": str(file_b),
            "content_base64": base64.b64encode(b"overwritten-despite-409").decode("ascii"),
        })
        listing = client.get("/api/local-assets", headers=EDITOR).json()

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "LOCAL_ASSET_PATH_AMBIGUOUS"
    assert hashlib.sha256(file_b.read_bytes()).hexdigest() == before_hash
    assert file_b.read_bytes() == b"root-B-original-payload"
    assert listing["count"] == 1
    assert listing["items"][0]["asset_id"] == indexed["items"][0]["asset_id"]
    assert listing["items"][0]["display_path"] == "same.png"
    assert listing["data_gaps"] == []


def test_b4_local_asset_upload_index_failure_restores_existing_and_removes_new_target(
    tmp_path, monkeypatch,
):
    """真实 HTTP 上传在索引提交故障后恢复旧字节，并移除仅新建的未索引文件。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    existing = allowed / "existing.png"
    existing.write_bytes(b"original-indexed-content")

    from gw.asset_library import repository as repo
    repo.index_local_files([str(existing)])
    real_state = repo._local_state()

    class FailingState:
        def read(self):
            return real_state.read()

        def mutate(self, _mutator):
            raise OSError("注入索引提交失败")

    monkeypatch.setattr(repo, "_local_state", lambda: FailingState())
    original_hash = hashlib.sha256(existing.read_bytes()).hexdigest()
    new_target = allowed / "new.png"

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        existing_response = client.post("/api/local-assets/upload", headers=EDITOR, json={
            "path": str(existing),
            "content_base64": base64.b64encode(b"new-content-not-committed").decode("ascii"),
        })
        new_response = client.post("/api/local-assets/upload", headers=EDITOR, json={
            "path": str(new_target),
            "content_base64": base64.b64encode(b"new-unindexed-content").decode("ascii"),
        })
        listing = client.get("/api/local-assets", headers=EDITOR).json()

    assert existing_response.status_code == 500, existing_response.text
    assert existing_response.json()["detail"]["code"] == "LOCAL_ASSET_INDEX_UPDATE_FAILED"
    assert hashlib.sha256(existing.read_bytes()).hexdigest() == original_hash
    assert existing.read_bytes() == b"original-indexed-content"

    assert new_response.status_code == 500, new_response.text
    assert new_response.json()["detail"]["code"] == "LOCAL_ASSET_INDEX_UPDATE_FAILED"
    assert not new_target.exists()
    assert sorted(path.name for path in allowed.iterdir()) == ["existing.png"]
    assert listing["count"] == 1
    assert listing["items"][0]["display_path"] == "existing.png"
    assert listing["items"][0]["size_bytes"] == len(b"original-indexed-content")
    assert listing["data_gaps"] == []


def test_b4_local_asset_upload_serializes_conflicting_index_registration(tmp_path, monkeypatch):
    """上传预检到索引提交期间，同相对路径索引必须等待同一域锁。"""
    allowed_a = tmp_path / "allowed-a"
    allowed_b = tmp_path / "allowed-b"
    allowed_a.mkdir()
    allowed_b.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", os.pathsep.join((str(allowed_a), str(allowed_b))))
    source = allowed_a / "same.png"
    target = allowed_b / "same.png"
    source.write_bytes(b"root-A-source")

    from gw.asset_library import repository as repo
    from gw.core.errors import CleanroomException

    stage_ready = threading.Event()
    release_upload = threading.Event()
    index_attempted = threading.Event()

    class ObservedRLock:
        def __init__(self):
            self._lock = threading.RLock()
            self._metadata_lock = threading.Lock()
            self._owner_ident = None
            self._owner_name = None
            self._depth = 0

        @property
        def owner_name(self):
            with self._metadata_lock:
                return self._owner_name

        def __enter__(self):
            name = threading.current_thread().name
            if name == "same-path-indexer":
                index_attempted.set()
            self._lock.acquire()
            ident = threading.get_ident()
            with self._metadata_lock:
                if self._owner_ident == ident:
                    self._depth += 1
                else:
                    self._owner_ident = ident
                    self._owner_name = name
                    self._depth = 1
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            with self._metadata_lock:
                self._depth -= 1
                if self._depth == 0:
                    self._owner_ident = None
                    self._owner_name = None
            self._lock.release()

    monkeypatch.setattr(repo, "_LOCAL_ASSET_DOMAIN_LOCK", ObservedRLock())
    original_stage = repo._write_upload_stage

    def pause_after_stage(path, payload=None, source=None):
        stage = original_stage(path, payload, source)
        if threading.current_thread().name == "asset-uploader":
            stage_ready.set()
            if not release_upload.wait(5):
                stage.unlink(missing_ok=True)
                raise TimeoutError("等待索引竞争测试释放上传超时")
        return stage

    monkeypatch.setattr(repo, "_write_upload_stage", pause_after_stage)
    results = {}

    def capture(key, operation):
        try:
            results[key] = operation()
        except BaseException as exc:
            results[key] = exc

    payload = base64.b64encode(b"root-B-uploaded").decode("ascii")
    uploader = threading.Thread(
        target=capture,
        args=("upload", lambda: repo.upload_and_index_local_asset(str(target), payload)),
        name="asset-uploader",
    )
    indexer = threading.Thread(
        target=capture,
        args=("index", lambda: repo.index_local_files([str(source)])),
        name="same-path-indexer",
    )
    uploader.start()
    try:
        assert stage_ready.wait(3), "上传未到达文件提交前的屏障"
        assert repo._LOCAL_ASSET_DOMAIN_LOCK.owner_name == "asset-uploader"
        indexer.start()
        assert index_attempted.wait(3), "竞争索引未尝试进入本机素材域锁"
    finally:
        release_upload.set()
        uploader.join(5)
        if indexer.ident is not None:
            indexer.join(5)

    assert not uploader.is_alive(), "上传线程发生死锁"
    assert not indexer.is_alive(), "竞争索引线程发生死锁"
    assert isinstance(results.get("upload"), dict), repr(results.get("upload"))
    assert isinstance(results.get("index"), CleanroomException)
    assert results["index"].status_code == 409
    assert results["index"].code == "LOCAL_ASSET_PATH_AMBIGUOUS"
    assert source.read_bytes() == b"root-A-source"
    assert target.read_bytes() == b"root-B-uploaded"
    listing = repo.list_local_assets()
    assert listing["count"] == 1
    assert listing["data_gaps"] == []
    assert not list(allowed_b.glob(".gw-upload-pending-*"))

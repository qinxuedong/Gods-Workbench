"""C3 删除 / 恢复（AC4 + C4-AC6 同进同出）。

产物同时登记在资产注册表和资产库（同一 asset_id）。C4-AC6 裁定：注册表是存在性
唯一真源，注册表删除/恢复时素材库条目必须同进同出——删除把素材库条目一并移除并
暂存进回收站条目 payload["library_item"]，恢复时原样放回；素材库侧删除桥接条目
（带 workflow_owner 且注册表有同名资产）改走注册表删除，保证可恢复、不两处分叉。
"""
from __future__ import annotations

from media_doubles import GOVERNOR, HEADERS, TASKS, prepare_workflow
from test_c3_media_closed_loop import assert_traceable, registry_asset, run_to_completion

REGISTRY = "/api/asset-registry"


def _completed_output(client):
    _, workflow = prepare_workflow(client)
    started, done = run_to_completion(client, workflow, "rh_cloud")
    assert done["status"] == "completed", done
    return workflow, started, done["outputs"][0]["asset_id"]


def _recycle_ids(client):
    response = client.get(f"{REGISTRY}/recycle-bin", headers=HEADERS)
    assert response.status_code == 200, response.text
    return [item["entry_id"] for item in response.json()["items"]]


def _registry_revision(client, headers=HEADERS):
    response = client.get(f"{REGISTRY}/status", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def _delete(client, asset_id):
    response = client.request("DELETE", f"{REGISTRY}/assets/{asset_id}", headers=HEADERS,
                              json={"expected_version": _registry_revision(client)})
    assert response.status_code == 200, response.text
    return response.json()["recycle_entry_id"]


def _restore(client, entry_id):
    return client.post(f"{REGISTRY}/recycle-bin/{entry_id}/restore", headers=GOVERNOR,
                       json={"expected_version": _registry_revision(client, GOVERNOR)})


def _library_version(client, asset_id):
    response = client.get("/api/asset-library", headers=HEADERS)
    assert response.status_code == 200, response.text
    library = response.json()["library"]
    for item in library["libraries"]:
        if any(asset["asset_id"] == asset_id for category in item["categories"] for asset in category["items"]):
            return item["version"]
    raise AssertionError(f"asset {asset_id} is missing from the asset library")


def _library_item(client, asset_id):
    """从素材库 API 读回 asset_id 对应条目；不存在返回 None。"""
    response = client.get("/api/asset-library", headers=HEADERS)
    assert response.status_code == 200, response.text
    for library in response.json()["library"]["libraries"]:
        for category in library["categories"]:
            for item in category["items"]:
                if item["asset_id"] == asset_id:
                    return item
    return None


def test_collected_output_delete_then_restore_keeps_task_links(media_client, fake_media, asset_counts):
    """C3-MD-DELETE-RESTORE-01"""
    workflow, started, asset_id = _completed_output(media_client)
    original = registry_asset(media_client, asset_id)
    settled = asset_counts()
    entry_id = _delete(media_client, asset_id)
    deleted = asset_counts()
    assert deleted["registry"] == settled["registry"] - 1
    assert asset_id not in deleted["registry_ids"]
    assert entry_id in _recycle_ids(media_client)
    assert media_client.get(f"{REGISTRY}/assets/{asset_id}", headers=HEADERS).status_code == 404
    # C4-AC6 同进同出：注册表删除时素材库同 asset_id 条目一并移除。
    assert deleted["library_image"] == settled["library_image"] - 1
    assert asset_id not in deleted["library_image_ids"]
    restored = _restore(media_client, entry_id)
    assert restored.status_code == 200, restored.text
    assert asset_counts() == settled
    assert entry_id not in _recycle_ids(media_client)
    again = registry_asset(media_client, asset_id)
    for field in ("task_id", "kind", "url", "name"):
        assert again.get(field) == original.get(field), field
    for field in ("task_id", "job_id", "workflow_id", "workflow_version", "source_context"):
        assert again["metadata"].get(field) == original["metadata"].get(field), field
    assert_traceable(media_client, started, workflow, asset_id)


def test_collect_after_restore_does_not_duplicate(media_client, fake_media, asset_counts):
    """C3-MD-DELETE-RESTORE-02"""
    _, started, asset_id = _completed_output(media_client)
    settled = asset_counts()
    assert _restore(media_client, _delete(media_client, asset_id)).status_code == 200
    collected = media_client.post(f"{TASKS}/{started['job_id']}/collect", headers=HEADERS)
    assert collected.status_code == 200, collected.text
    assert [item["asset_id"] for item in collected.json()["outputs"]] == [asset_id]
    assert asset_counts() == settled


def test_collect_while_deleted_does_not_resurrect_or_duplicate(media_client, fake_media, asset_counts):
    """C3-MD-DELETE-RESTORE-03：已完成回收的任务再 collect 直接返回原任务，不复活、不重复（裁定第 6 条）。"""
    _, started, asset_id = _completed_output(media_client)
    _delete(media_client, asset_id)
    deleted = asset_counts()
    queries = fake_media.rh.count("query_rh_task_outputs")
    collected = media_client.post(f"{TASKS}/{started['job_id']}/collect", headers=HEADERS)
    assert collected.status_code == 200, collected.text
    assert collected.json()["collection_status"] == "completed"
    assert asset_counts() == deleted
    assert asset_id not in asset_counts()["registry_ids"]
    assert fake_media.rh.count("query_rh_task_outputs") == queries
    assert fake_media.rh.count("create_rh_cloud_task") == 1


def test_restore_twice_returns_404_or_409(media_client, fake_media, asset_counts):
    """C3-MD-DELETE-RESTORE-04：同一回收条目恢复两次 → 404 或 409，资产不重复。"""
    _, _, asset_id = _completed_output(media_client)
    entry_id = _delete(media_client, asset_id)
    assert _restore(media_client, entry_id).status_code == 200
    settled = asset_counts()
    second = _restore(media_client, entry_id)
    assert second.status_code in (404, 409), second.text
    assert asset_counts() == settled
    unknown = _restore(media_client, "rcy_does_not_exist")
    assert unknown.status_code == 404, unknown.text


def test_restore_requires_governance_role(media_client, fake_media):
    """裁定第 7 条：恢复需要 governance 权限，编辑者不能恢复。"""
    _, _, asset_id = _completed_output(media_client)
    entry_id = _delete(media_client, asset_id)
    denied = media_client.post(f"{REGISTRY}/recycle-bin/{entry_id}/restore", headers=HEADERS,
                               json={"expected_version": _registry_revision(media_client)})
    assert denied.status_code in (401, 403), denied.text
    assert entry_id in _recycle_ids(media_client)


def test_restore_reinserts_library_item_intact(media_client, fake_media, asset_counts):
    """C4-AC6-RESTORE-01：恢复后素材库条目与删除前逐字段一致（同 id/同库/同分类/同字段）。"""
    _, _, asset_id = _completed_output(media_client)
    before = _library_item(media_client, asset_id)
    assert before is not None
    assert _restore(media_client, _delete(media_client, asset_id)).status_code == 200
    after = _library_item(media_client, asset_id)
    assert after == before
    for field in ("asset_id", "library_id", "category_id", "name", "url", "created_at"):
        assert after[field] == before[field], field


def test_library_delete_goes_through_registry(media_client, fake_media, asset_counts):
    """C4-AC6-LIB-DELETE-01：素材库侧删除桥接条目改走注册表删除（同进同出、可恢复）。"""
    _, _, asset_id = _completed_output(media_client)
    settled = asset_counts()

    # 批量端点：删除后两份存储同时 -1，注册表出现可恢复回收条目。
    library = media_client.get("/api/asset-library", headers=HEADERS).json()["library"]
    library_item = next(item for item in library["libraries"]
                        if any(asset["asset_id"] == asset_id for category in item["categories"] for asset in category["items"]))
    batch = media_client.post("/api/asset-library/items/delete", headers=HEADERS,
                              json={"asset_ids": [asset_id], "expected_version": library_item["version"]})
    assert batch.status_code == 200, batch.text
    assert batch.json()["deleted"] == [asset_id]
    deleted = asset_counts()
    assert deleted["registry"] == settled["registry"] - 1
    assert deleted["library_image"] == settled["library_image"] - 1
    assert asset_id not in deleted["registry_ids"]
    assert asset_id not in deleted["library_image_ids"]
    entry_id = _recycle_ids(media_client)[-1]
    assert _restore(media_client, entry_id).status_code == 200
    assert asset_counts() == settled

    # 单条端点：同样经注册表删除并留下回收条目。
    single = media_client.delete(f"/api/asset-library/items/{asset_id}", headers=HEADERS,
                                 params={"expected_version": _library_version(media_client, asset_id)})
    assert single.status_code == 200, single.text
    assert single.json()["deleted"] is True
    assert asset_counts()["registry"] == settled["registry"] - 1
    assert asset_counts()["library_image"] == settled["library_image"] - 1
    entry_id = _recycle_ids(media_client)[-1]
    assert _restore(media_client, entry_id).status_code == 200
    assert asset_counts() == settled


def test_restore_twice_does_not_duplicate_library_items(media_client, fake_media, asset_counts):
    """C4-AC6-RESTORE-02：同一回收条目恢复两次不重复素材库条目；第二次恢复 404/409。"""
    _, _, asset_id = _completed_output(media_client)
    settled = asset_counts()
    entry_id = _delete(media_client, asset_id)
    assert _restore(media_client, entry_id).status_code == 200
    second = _restore(media_client, entry_id)
    assert second.status_code in (404, 409), second.text
    counts = asset_counts()
    assert counts == settled
    assert counts["library_image_ids"].count(asset_id) == 1
    assert counts["registry_ids"].count(asset_id) == 1


def test_failed_registry_delete_rolls_back_library_item(media_client, fake_media, asset_counts):
    """C4-AC6-ROLLBACK-01：注册表删除失败（CAS 冲突）时素材库条目原样回插，两处计数不变。"""
    import pytest
    from gw.asset_registry import repository as repo
    from gw.core.errors import CleanroomException
    _, _, asset_id = _completed_output(media_client)
    settled = asset_counts()
    before = _library_item(media_client, asset_id)
    with pytest.raises(CleanroomException):
        repo.delete_asset(asset_id, 10**9, None)
    assert asset_counts() == settled
    assert _library_item(media_client, asset_id) == before

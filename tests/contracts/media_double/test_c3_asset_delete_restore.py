"""C3 删除 / 恢复（AC4，方案 A：只验资产注册表，不改生产代码）。

已知且不在 C3 范围：产物同时登记在资产注册表和资产库，但注册表删除 / 恢复不会同步资产库那份
（08:37 修订：另开 issue 跟踪）。本文件如实断言资产库那份在删除期间保持不变。
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


def _delete(client, asset_id):
    response = client.delete(f"{REGISTRY}/assets/{asset_id}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["recycle_entry_id"]


def _restore(client, entry_id):
    return client.post(f"{REGISTRY}/recycle-bin/{entry_id}/restore", headers=GOVERNOR)


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
    # 方案 A 已知不一致：资产库那份不跟着删。
    assert deleted["library_image"] == settled["library_image"]
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
    denied = media_client.post(f"{REGISTRY}/recycle-bin/{entry_id}/restore", headers=HEADERS)
    assert denied.status_code in (401, 403), denied.text
    assert entry_id in _recycle_ids(media_client)

"""C6-AC2：资产注册表与素材库删除、回收、恢复回归锁定。

只新增 C6 测试，真实走工作流、资产注册表、素材库和回收站端点；不修改业务实现。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from media_doubles import GOVERNOR, HEADERS, TASKS, count_assets, prepare_workflow

REGISTRY = "/api/asset-registry"


def _run_to_completion(client: TestClient, workflow: dict) -> dict:
    started = client.post(
        f"{TASKS}/execute",
        json={
            "workflow_id": workflow["workflow_id"],
            "target": "rh_cloud",
            "expected_version": workflow["version"],
        },
        headers={**HEADERS, "Idempotency-Key": "c6-ac2-delete-restore"},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    running = client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert running.status_code == 200, running.text
    assert running.json()["status"] == "running"
    completed = client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "completed", body
    assert body["collection_status"] == "completed", body
    return body


def _library_asset_ids(client: TestClient) -> list[str]:
    response = client.get("/api/asset-library", headers=HEADERS)
    assert response.status_code == 200, response.text
    return [
        item["asset_id"]
        for library in response.json()["library"]["libraries"]
        for category in library["categories"]
        if category["type"] == "image"
        for item in category["items"]
    ]


def test_c6_ac2_delete_recycle_restore_keeps_counts_and_asset_id(
    media_client: TestClient,
    fake_media,
):
    """删除→回收→恢复后，两份存储恢复原计数且只保留同一 asset_id。"""
    _, workflow = prepare_workflow(media_client)
    completed = _run_to_completion(media_client, workflow)
    asset_id = completed["outputs"][0]["asset_id"]

    before = count_assets(media_client)
    assert asset_id in before["registry_ids"]
    assert asset_id in before["library_image_ids"]
    assert before["registry_ids"].count(asset_id) == 1
    assert before["library_image_ids"].count(asset_id) == 1

    deleted = media_client.delete(f"{REGISTRY}/assets/{asset_id}", headers=HEADERS)
    assert deleted.status_code == 200, deleted.text
    entry_id = deleted.json()["recycle_entry_id"]
    after_delete = count_assets(media_client)
    assert after_delete["registry"] == before["registry"] - 1
    assert after_delete["library_image"] == before["library_image"] - 1
    assert asset_id not in after_delete["registry_ids"]
    assert asset_id not in after_delete["library_image_ids"]

    restored = media_client.post(
        f"{REGISTRY}/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
    )
    assert restored.status_code == 200, restored.text
    after_restore = count_assets(media_client)
    assert after_restore == before
    assert _library_asset_ids(media_client).count(asset_id) == 1

    registry = media_client.get(f"{REGISTRY}/assets/{asset_id}", headers=HEADERS)
    assert registry.status_code == 200, registry.text
    assert registry.json()["asset"]["asset_id"] == asset_id
    assert fake_media.rh.count("create_rh_cloud_task") == 1

# -*- coding: utf-8 -*-
"""C9-T4：R2 全域回收恢复的稳定引用、CAS、幂等与部分失败契约。"""

from __future__ import annotations

import pytest

from gw.asset_registry.service import AssetRegistryService
from gw.canvas_closure.service import CanvasClosureService
from gw.core.errors import CleanroomException
from gw.god_canvas.service import GodCanvasService


AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOVERNOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "governor"}


def _fixture_services() -> tuple[CanvasClosureService, AssetRegistryService, str, str, dict]:
    """建立带项目→镜头→任务→资产→审阅→交付引用的最小 R2 夹具。"""
    canvas_service = GodCanvasService(seed_golden_fixture=False)
    registry = AssetRegistryService()
    closure = CanvasClosureService(
        canvas_service=canvas_service,
        asset_registry_service=registry,
    )
    asset_id = "ast-c9-0001"
    created = closure.save_reference_canvas(
        title="C9 R2 单镜头",
        items=[{"asset_id": asset_id, "source_ref": {"job_id": "job-c9-0001"}}],
        annotations=[],
        drawings=[],
        groups=[],
        viewport={"x": 0, "y": 0, "zoom": 1},
        owner_key="c9-owner",
    )
    canvas_id = created["canvas"]["canvas_id"]
    references = {
        "project_id": "prj-c9-0001",
        "episode_id": "ep-c9-0001",
        "scene_id": "sc-c9-0001",
        "shot_id": "shot-c9-0001",
        "canvas_id": canvas_id,
        "job_id": "job-c9-0001",
        "asset_id": asset_id,
        "version_id": "asset-c9-0001-v001",
        "review_id": "review-c9-0001",
        "decision_id": "decision-c9-0001",
        "delivery_id": "delivery-c9-0001",
    }
    registry.register_asset(
        asset_id,
        source_ref={"job_id": "job-c9-0001", "canvas_id": canvas_id},
        references=references,
        payload={"version_id": "asset-c9-0001-v001"},
    )
    return closure, registry, canvas_id, asset_id, references


def test_c9_t4_recycle_preserves_r2_reference_graph_and_recovery_projection():
    closure, registry, canvas_id, asset_id, references = _fixture_services()

    recycled = closure.recycle_with_references(
        canvas_id,
        asset_ids=[asset_id],
        expected_version=2,
        references=references,
        idempotency_key="recycle:c9:0001",
    )

    assert recycled["closure_status"] == "recovery_required"
    assert recycled["next_action"] == "restore_reference"
    assert recycled["recovery"]["operation"] == "recycle"
    assert recycled["recovery"]["state"] == "recovery_required"
    assert recycled["recovery"]["idempotency_key"] == "recycle:c9:0001"
    assert recycled["recovery"]["failed_scopes"] == []
    assert recycled["references"]["shot_id"] == "shot-c9-0001"
    assert recycled["references"]["delivery_id"] == "delivery-c9-0001"
    assert {subject["asset_id"] for subject in recycled["recovery"]["preserved_subjects"]
            if "asset_id" in subject} == {asset_id}
    with pytest.raises(CleanroomException) as missing:
        registry.get_asset(asset_id)
    assert missing.value.code == "ASSET_NOT_FOUND"


def test_c9_t4_restore_is_idempotent_and_reads_back_same_stable_references():
    closure, registry, canvas_id, asset_id, references = _fixture_services()
    recycled = closure.recycle_with_references(
        canvas_id,
        [asset_id],
        expected_version=2,
        references=references,
        idempotency_key="recycle:c9:0002",
    )
    restore_key = f"restore:{recycled['recycle_entry_id']}"

    restored = closure.restore_recycled(
        recycled["recycle_entry_id"],
        expected_version=recycled["canvas"]["version"],
        idempotency_key=restore_key,
    )
    replay = closure.restore_recycled(
        recycled["recycle_entry_id"],
        expected_version=999,
        idempotency_key=restore_key,
    )

    assert restored == replay
    assert restored["closure_status"] == "closed"
    assert restored["recovery"]["operation"] == "restore"
    assert restored["recovery"]["state"] == "completed"
    assert restored["recovery"]["idempotency_key"] == restore_key
    assert restored["next_action"] is None
    assert restored["asset_ids"] == [asset_id]
    assert registry.get_asset(asset_id)["version_id"] == "asset-c9-0001-v001"
    assert restored["references"]["delivery_id"] == "delivery-c9-0001"


def test_c9_t4_partial_restore_keeps_entry_and_exposes_retry_action():
    closure, registry, canvas_id, asset_id, references = _fixture_services()
    recycled = closure.recycle_with_references(
        canvas_id,
        [asset_id],
        expected_version=2,
        references=references,
        idempotency_key="recycle:c9:0003",
    )
    registry.register_asset(asset_id, source_ref={"job_id": "conflicting-source"})

    with pytest.raises(CleanroomException) as failure:
        closure.restore_recycled(
            recycled["recycle_entry_id"],
            expected_version=recycled["canvas"]["version"],
            idempotency_key="restore:c9:0003",
        )

    assert failure.value.status_code == 409
    assert failure.value.code == "UNIFIED_RECYCLE_PARTIAL_FAILURE"
    assert failure.value.extra["closure_status"] == "partially_recovered"
    assert failure.value.extra["recovery"]["failed_scopes"] == ["asset"]
    assert failure.value.extra["next_action"] == "retry_recovery"
    assert closure.recycle_entry(recycled["recycle_entry_id"])["entry_id"] == recycled["recycle_entry_id"]
    assert registry.get_asset(asset_id)["source_ref"] == {"job_id": "conflicting-source"}


def test_c9_t4_asset_restore_replay_keeps_version_and_references():
    registry = AssetRegistryService()
    registry.register_asset(
        "ast-c9-cas",
        source_ref={"job_id": "job-c9-cas"},
        references={"shot_id": "shot-c9-cas", "delivery_id": "delivery-c9-cas"},
        payload={"version_id": "asset-c9-cas-v001"},
    )
    current = registry.root_snapshot()["revision"]
    deleted = registry.delete_asset(
        "ast-c9-cas",
        expected_version=current,
        idempotency_key="delete:c9:cas",
    )
    restore_key = f"restore:{deleted['recycle_entry_id']}"

    restored = registry.restore_from_recycle(
        deleted["recycle_entry_id"],
        expected_version=deleted["revision"],
        idempotency_key=restore_key,
    )
    replay = registry.restore_from_recycle(
        deleted["recycle_entry_id"],
        expected_version=999,
        idempotency_key=restore_key,
    )

    assert restored == replay
    assert restored["closure_status"] == "closed"
    assert restored["recovery"]["preserved_subjects"] == [
        {"asset_id": "ast-c9-cas", "version_id": "asset-c9-cas-v001"}
    ]
    assert registry.get_asset("ast-c9-cas")["references"]["delivery_id"] == "delivery-c9-cas"


def test_c9_t4_asset_registry_routes_expose_recovery_projection(client):
    created = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={"items": [{"name": "C9-R2.png", "kind": "image"}]},
    )
    assert created.status_code == 200, created.text
    asset_id = created.json()["asset_ids"][0]

    deleted = client.request(
        "DELETE",
        f"/api/asset-registry/assets/{asset_id}",
        headers=AUTH,
        json={"idempotency_key": "delete:c9:route"},
    )
    assert deleted.status_code == 200, deleted.text
    entry_id = deleted.json()["recycle_entry_id"]
    assert deleted.json()["closure_status"] == "recovery_required"
    assert deleted.json()["next_action"] == "restore_reference"
    assert deleted.json()["recovery"]["preserved_subjects"] == [{"asset_id": asset_id}]

    restored = client.post(
        f"/api/asset-registry/recycle-bin/{entry_id}/restore",
        headers=GOVERNOR,
        json={"idempotency_key": f"restore:{entry_id}"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["closure_status"] == "closed"
    assert restored.json()["next_action"] is None

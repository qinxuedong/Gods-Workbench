"""C8-T5：画布与统一资产的回收、恢复和引用保留契约。"""

from __future__ import annotations

import pytest

from gw.asset_registry.service import AssetRegistryService
from gw.canvas_closure.service import CanvasClosureService
from gw.core.errors import CleanroomException
from gw.god_canvas.service import GodCanvasService


def _fixture_services() -> tuple[CanvasClosureService, AssetRegistryService, str, str]:
    """建立一个带来源引用的单镜头画布夹具。"""
    canvas_service = GodCanvasService(seed_golden_fixture=False)
    registry = AssetRegistryService()
    closure = CanvasClosureService(
        canvas_service=canvas_service,
        asset_registry_service=registry,
    )
    asset_id = "ast-c8-0001"
    created = closure.save_reference_canvas(
        title="C8 单镜头参考",
        items=[{"asset_id": asset_id, "source_ref": {"job_id": "job-c8-0001"}}],
        annotations=[],
        drawings=[],
        groups=[],
        viewport={"x": 0, "y": 0, "zoom": 1},
        owner_key="c8-owner",
    )
    canvas_id = created["canvas"]["canvas_id"]
    registry.register_asset(
        asset_id,
        source_ref={"job_id": "job-c8-0001", "canvas_id": canvas_id},
        references={"canvas_id": canvas_id, "canvas_version": 2},
    )
    return closure, registry, canvas_id, asset_id


def test_c8_t5_recycle_restore_keeps_ids_sources_and_references():
    closure, registry, canvas_id, asset_id = _fixture_services()

    recycled = closure.recycle_with_references(
        canvas_id,
        asset_ids=[asset_id],
        expected_version=2,
    )

    assert recycled["status"] == "completed"
    assert recycled["canvas"]["canvas_id"] == canvas_id
    assert recycled["canvas"]["deleted_at"] is not None
    assert recycled["assets"] == [{"asset_id": asset_id, "entry_id": "rcy_0001"}]
    assert recycled["references"] == {
        "canvas_id": canvas_id,
        "canvas_version": 2,
        "source_ref": {"job_id": "job-c8-0001", "canvas_id": canvas_id},
    }
    with pytest.raises(CleanroomException) as deleted:
        registry.get_asset(asset_id)
    assert deleted.value.code == "ASSET_NOT_FOUND"

    restored = closure.restore_recycled(
        recycled["recycle_entry_id"],
        expected_version=recycled["canvas"]["version"],
    )

    assert restored["status"] == "completed"
    assert restored["canvas"]["canvas_id"] == canvas_id
    assert restored["canvas"]["deleted_at"] is None
    assert restored["asset_ids"] == [asset_id]
    assert registry.get_asset(asset_id)["asset_id"] == asset_id
    assert registry.get_asset(asset_id)["source_ref"] == {
        "job_id": "job-c8-0001",
        "canvas_id": canvas_id,
    }
    assert registry.get_asset(asset_id)["references"] == {
        "canvas_id": canvas_id,
        "canvas_version": 2,
    }


def test_c8_t5_repeated_restore_is_explicit_and_does_not_duplicate():
    closure, registry, canvas_id, asset_id = _fixture_services()
    recycled = closure.recycle_with_references(
        canvas_id, [asset_id], expected_version=2
    )
    closure.restore_recycled(recycled["recycle_entry_id"], expected_version=3)

    with pytest.raises(CleanroomException) as failure:
        closure.restore_recycled(recycled["recycle_entry_id"], expected_version=4)

    assert failure.value.status_code == 404
    assert failure.value.code == "UNIFIED_RECYCLE_ENTRY_NOT_FOUND"
    assert registry.get_asset(asset_id)["asset_id"] == asset_id
    assert registry.recycle_bin()["items"] == []


def test_c8_t5_partial_failure_is_visible_and_leaves_canvas_active():
    closure, _registry, canvas_id, _asset_id = _fixture_services()

    with pytest.raises(CleanroomException) as failure:
        closure.recycle_with_references(
            canvas_id,
            asset_ids=["ast-c8-missing"],
            expected_version=2,
        )

    assert failure.value.status_code == 409
    assert failure.value.code == "UNIFIED_RECYCLE_PARTIAL_FAILURE"
    assert failure.value.extra["partial_failures"] == [
        {
            "asset_id": "ast-c8-missing",
            "code": "ASSET_NOT_FOUND",
            "next_step": "刷新资产来源后重试",
        }
    ]
    assert closure._canvas_service.get_canvas_meta(canvas_id)["deleted_at"] is None


def test_c8_t5_asset_recycle_uses_cas_and_unknown_entry_is_not_success():
    registry = AssetRegistryService()
    registry.register_asset("ast-c8-cas", source_ref={"job_id": "job-c8-cas"})
    current = registry.root_snapshot()["revision"]

    with pytest.raises(CleanroomException) as conflict:
        registry.delete_asset("ast-c8-cas", expected_version=current - 1)
    assert conflict.value.status_code == 409
    assert conflict.value.code == "VERSION_CONFLICT"
    assert registry.get_asset("ast-c8-cas")["asset_id"] == "ast-c8-cas"

    deleted = registry.delete_asset("ast-c8-cas", expected_version=current)
    assert deleted["recycle_entry_id"] == "rcy_0001"
    with pytest.raises(CleanroomException) as unknown:
        registry.restore_from_recycle("rcy-unknown")
    assert unknown.value.status_code == 404
    assert unknown.value.code == "RECYCLE_ENTRY_NOT_FOUND"


def test_c8_t5_restore_conflict_keeps_canvas_in_trash():
    closure, registry, canvas_id, asset_id = _fixture_services()
    recycled = closure.recycle_with_references(
        canvas_id, [asset_id], expected_version=2
    )
    registry.register_asset(asset_id, source_ref={"job_id": "conflicting-source"})

    with pytest.raises(CleanroomException) as failure:
        closure.restore_recycled(recycled["recycle_entry_id"], expected_version=3)

    assert failure.value.status_code == 409
    assert failure.value.code == "UNIFIED_RECYCLE_PARTIAL_FAILURE"
    assert closure._canvas_service.get_canvas_meta(canvas_id)["deleted_at"] is not None
    assert registry.get_asset(asset_id)["source_ref"] == {
        "job_id": "conflicting-source"
    }

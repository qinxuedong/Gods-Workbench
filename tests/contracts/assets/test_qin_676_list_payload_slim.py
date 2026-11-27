"""QIN-676：卡片列表 metadata 投影与完整详情契约。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
ASSET_COUNT = 200


def _large_metadata() -> dict:
    workflow = "workflow-node-" * 350
    prompt = "prompt-token-" * 160
    raw_json = "raw-metadata-" * 160
    return {
        "source_id": "comfyui",
        "classification": {"category": "generated", "label": "ComfyUI"},
        "collect_sources": ["comfyui", "manual"],
        "preview_url": "/api/asset-preview/sample",
        "width": 2048,
        "height": 1152,
        "frame_rate": 24,
        "duration": 12.5,
        "original_filename": "generated.png",
        "extension": "png",
        "workflow_id": "workflow-001",
        "job_id": "job-001",
        "source_context": {
            "project_id": "project_001",
            "canvas_id": "canvas_001",
            "raw_context": raw_json,
        },
        "lut": {"preview_url": "/api/lut-preview/sample", "raw": raw_json},
        "generation": {
            "comfyui": {
                "source": "comfyui",
                "has_workflow": True,
                "has_prompt_graph": True,
                "workflow": workflow,
                "prompt": prompt,
            }
        },
        "workflow": workflow,
        "prompt": {"text": prompt},
        "raw_json": raw_json,
    }


@pytest.fixture()
def large_asset_page(client: TestClient) -> tuple[dict, list[str], int]:
    metadata = _large_metadata()
    response = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={
            "items": [
                {
                    "name": f"qin-676-generated-{index:03d}.png",
                    "kind": "image",
                    "metadata": metadata,
                }
                for index in range(ASSET_COUNT)
            ]
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    return metadata, result["asset_ids"], result["revision"]


def test_qin676_card_views_reduce_large_metadata_payload_by_70_percent(
    client: TestClient, large_asset_page: tuple[dict, list[str], int]
):
    """AC1：卡片/网格/列表 200 条大元数据页面至少缩减 70%。"""
    _metadata, _asset_ids, _revision = large_asset_page
    full = client.get(
        "/api/asset-registry/assets",
        headers=AUTH,
        params={"kind": "image", "limit": ASSET_COUNT},
    )
    assert full.status_code == 200, full.text
    full_data = full.json()
    assert len(full_data["items"]) == ASSET_COUNT

    for view in ("card", "grid", "list"):
        response = client.get(
            "/api/asset-registry/assets",
            headers=AUTH,
            params={"kind": "image", "limit": ASSET_COUNT, "view": view},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["items"] == data["assets"]
        assert len(data["items"]) == ASSET_COUNT
        assert len(response.content) <= len(full.content) * 0.30

        metadata = data["items"][0]["metadata"]
        assert metadata["source_id"] == "comfyui"
        assert metadata["classification"]["label"] == "ComfyUI"
        assert metadata["collect_sources"] == ["comfyui", "manual"]
        assert metadata["width"] == 2048
        assert metadata["duration"] == 12.5
        assert metadata["source_context"] == {
            "project_id": "project_001",
            "canvas_id": "canvas_001",
        }
        assert metadata["lut"] == {"preview_url": "/api/lut-preview/sample"}
        assert metadata["generation"]["comfyui"] == {
            "source": "comfyui",
            "has_workflow": True,
            "has_prompt_graph": True,
        }
        assert "workflow" not in metadata
        assert "prompt" not in metadata
        assert "raw_json" not in metadata


def test_qin676_detail_and_edit_keep_complete_metadata(
    client: TestClient, large_asset_page: tuple[dict, list[str], int]
):
    """AC2：列表投影不修改存储，详情和编辑仍看到完整 metadata。"""
    original_metadata, asset_ids, revision = large_asset_page
    asset_id = asset_ids[0]
    detail = client.get(f"/api/asset-registry/assets/{asset_id}", headers=AUTH)
    assert detail.status_code == 200, detail.text
    stored_asset = detail.json()["asset"]
    assert stored_asset["metadata"] == original_metadata

    edited = client.patch(
        f"/api/asset-registry/assets/{asset_id}",
        headers=AUTH,
        json={
                "expected_version": revision,
            "metadata": {"qin_676_edit_marker": "preserved"},
        },
    )
    assert edited.status_code == 200, edited.text
    edited_metadata = edited.json()["asset"]["metadata"]
    assert edited_metadata["workflow"] == original_metadata["workflow"]
    assert edited_metadata["prompt"] == original_metadata["prompt"]
    assert edited_metadata["raw_json"] == original_metadata["raw_json"]
    assert edited_metadata["qin_676_edit_marker"] == "preserved"

    after_edit = client.get(f"/api/asset-registry/assets/{asset_id}", headers=AUTH)
    assert after_edit.json()["asset"]["metadata"] == edited_metadata


def test_qin676_projected_pages_keep_cursor_and_total(
    client: TestClient, large_asset_page: tuple[dict, list[str], int]
):
    """AC5：启用列表投影不改变分页游标、顺序与总数。"""
    _metadata, _asset_ids, _revision = large_asset_page
    first = client.get(
        "/api/asset-registry/assets",
        headers=AUTH,
        params={"kind": "image", "limit": 100, "view": "card"},
    )
    assert first.status_code == 200, first.text
    first_data = first.json()
    assert first_data["total"] == ASSET_COUNT
    assert first_data["has_more"] is True
    assert first_data["next_cursor"]

    second = client.get(
        "/api/asset-registry/assets",
        headers=AUTH,
        params={
            "kind": "image",
            "limit": 100,
            "view": "card",
            "cursor": first_data["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_data = second.json()
    assert second_data["total"] == ASSET_COUNT
    assert second_data["has_more"] is False
    first_ids = [item["asset_id"] for item in first_data["items"]]
    second_ids = [item["asset_id"] for item in second_data["items"]]
    assert len(first_ids) == len(second_ids) == 100
    assert not set(first_ids).intersection(second_ids)


def test_qin676_video_card_keeps_only_search_badge_segment_fields(client: TestClient):
    """AC5：视频卡片保留搜索命中徽标字段，不回传切片分析载荷。"""
    imported = client.post(
        "/api/asset-registry/assets/import",
        headers=AUTH,
        json={
            "items": [
                {
                    "name": "qin-676-video.mp4",
                    "kind": "video",
                    "metadata": {
                        "video_segments": [
                            {
                                "start_sec": 2.5,
                                "end_sec": 5.5,
                                "tags": ["forest", "smoke"],
                                "summary": "wildfire response",
                                "raw_analysis": "large-analysis-" * 300,
                            }
                        ]
                    },
                }
            ]
        },
    )
    assert imported.status_code == 200, imported.text
    response = client.get(
        "/api/asset-registry/assets",
        headers=AUTH,
        params={"kind": "video", "view": "card"},
    )
    assert response.status_code == 200, response.text
    segments = response.json()["items"][0]["metadata"]["video_segments"]
    assert segments == [
        {
            "start_sec": 2.5,
            "end_sec": 5.5,
            "tags": ["forest", "smoke"],
            "summary": "wildfire response",
        }
    ]

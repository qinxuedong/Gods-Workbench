"""C18-T3 资产注册表摄制及实体母库复合过滤契约。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gw.asset_registry import repository as asset_repository
from gw.entities import repository as entity_repository

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PATH = "/api/asset-registry/assets"


def _create_entity(entity_type: str, name: str) -> str:
    result = entity_repository.create_entity(
        {"project_id": "c18-filter-fixture", "entity_type": entity_type, "name": name}
    )
    return result["entity"]["entity_id"]


def _register_assets(
    client: TestClient, records: list[tuple[str, dict[str, object]]]
) -> list[str]:
    response = client.post(
        PATH + "/import",
        headers=AUTH,
        json={"items": [{"name": name, "kind": "image"} for name, _ in records]},
    )
    assert response.status_code == 200, response.text
    asset_ids = response.json()["asset_ids"]
    for (name, metadata), asset_id in zip(records, asset_ids, strict=True):
        asset_repository.register_asset_record(
            asset_id=asset_id,
            name=name,
            kind="image",
            metadata=metadata,
        )
    return asset_ids


def _query(
    client: TestClient, filters: dict[str, object], **extra: object
) -> tuple[dict[str, object], object]:
    params: dict[str, object] = {
        "scope": json.dumps(["asset"], separators=(",", ":")),
        "filters": json.dumps(filters, separators=(",", ":")),
        "sort": "name",
        "limit": 50,
    }
    params.update(extra)
    response = client.get(PATH, headers=AUTH, params=params)
    return response.json(), response


def test_c18_filters_compose_across_cinematography_and_master_entities(
    client: TestClient,
) -> None:
    character_id = _create_entity("character", "Mira")
    prop_id = _create_entity("prop", "Key")
    location_id = _create_entity("location", "Archive")
    asset_ids = _register_assets(
        client,
        [
            (
                "hero-close-up.png",
                {
                    "cinematography": {
                        "shot_size": "CU",
                        "camera_movement": "DOLLY_IN",
                        "focal_length": "WIDE",
                        "lighting_phase": "NIGHT",
                        "aspect_ratio": "2.39:1",
                    },
                    "entity_ids": [character_id, prop_id],
                },
            ),
            (
                "establishing-scene.png",
                {
                    "cinematography": {
                        "shot_size": "EWS",
                        "camera_movement": "STATIC",
                        "focal_length": "ULTRA_WIDE",
                        "lighting_phase": "DAWN",
                        "aspect_ratio": "16:9",
                    },
                    "entity_ids": [location_id],
                },
            ),
            (
                "vertical-portrait.png",
                {
                    "shot_size": "MCU",
                    "camera_movement": "TRUCK_RIGHT",
                    "focal_length": "PORTRAIT",
                    "lighting_phase": "GOLDEN_HOUR",
                    "aspect_ratio": "9:16",
                    "entity_ids": [character_id],
                },
            ),
        ],
    )

    filters = {
        "shot_size": ["CU"],
        "camera_movement": ["DOLLY_IN"],
        "focal_length": ["WIDE"],
        "lighting_phase": ["NIGHT"],
        "aspect_ratio": ["2.39:1"],
        "entity_type": ["character"],
        "entity_id": [character_id],
    }
    body, response = _query(client, filters)
    assert response.status_code == 200, response.text
    assert body["total"] == 1
    assert [item["asset_id"] for item in body["items"]] == [asset_ids[0]]
    assert body["applied_filters"]["shot_size"] == ["CU"]
    assert body["applied_filters"]["camera_movement"] == ["DOLLY_IN"]
    assert body["applied_filters"]["focal_length"] == ["WIDE"]
    assert body["applied_filters"]["lighting_phase"] == ["NIGHT"]
    assert body["applied_filters"]["aspect_ratio"] == ["2.39:1"]
    assert body["applied_filters"]["entity_type"] == ["character"]
    assert body["applied_filters"]["entity_id"] == [character_id]

    expected_by_filter = [
        ({"shot_size": ["EWS"]}, {asset_ids[1]}),
        ({"camera_movement": ["TRUCK_RIGHT"]}, {asset_ids[2]}),
        ({"focal_length": ["ULTRA_WIDE"]}, {asset_ids[1]}),
        ({"lighting_phase": ["GOLDEN_HOUR"]}, {asset_ids[2]}),
        ({"aspect_ratio": ["9:16"]}, {asset_ids[2]}),
        ({"entity_type": ["location"]}, {asset_ids[1]}),
        ({"entity_type": ["prop"]}, {asset_ids[0]}),
        ({"entity_id": [prop_id]}, {asset_ids[0]}),
    ]
    for single_filter, expected in expected_by_filter:
        body, response = _query(client, single_filter)
        assert response.status_code == 200, response.text
        assert {item["asset_id"] for item in body["items"]} == expected


def test_c18_new_filters_are_bound_into_cursor_query_fingerprint(
    client: TestClient,
) -> None:
    asset_ids = _register_assets(
        client,
        [
            ("a-close.png", {"cinematography": {"shot_size": "CU"}}),
            ("b-close.png", {"cinematography": {"shot_size": "CU"}}),
            ("c-wide.png", {"cinematography": {"shot_size": "EWS"}}),
        ],
    )
    first, response = _query(client, {"shot_size": ["CU"]}, limit=1)
    assert response.status_code == 200, response.text
    assert first["items"][0]["asset_id"] == asset_ids[0]
    cursor = first["page"]["next_cursor"]
    assert cursor

    _, mismatch = _query(client, {"shot_size": ["EWS"]}, cursor=cursor, limit=1)
    assert mismatch.status_code == 400, mismatch.text
    assert mismatch.json()["detail"]["code"] == "INVALID_CURSOR"


def test_c18_structured_queries_without_new_fields_remain_compatible(
    client: TestClient,
) -> None:
    created = client.post(
        PATH + "/import",
        headers=AUTH,
        json={
            "items": [
                {"name": "a.png", "kind": "image"},
                {"name": "b.png", "kind": "image"},
                {"name": "c.mp4", "kind": "video"},
            ]
        },
    )
    assert created.status_code == 200, created.text

    legacy = client.get(
        PATH, headers=AUTH, params={"kind": "image", "sort": "asset_id"}
    )
    empty_structured, empty_response = _query(client, {}, sort="asset_id")
    old_structured, old_response = _query(
        client, {"kind": ["image"]}, sort="asset_id"
    )
    assert legacy.status_code == empty_response.status_code == old_response.status_code == 200
    legacy_ids = {item["asset_id"] for item in legacy.json()["items"]}
    empty_ids = {item["asset_id"] for item in empty_structured["items"]}
    structured_ids = {item["asset_id"] for item in old_structured["items"]}
    all_ids = set(created.json()["asset_ids"])
    assert len(legacy_ids) == 2
    assert empty_ids == all_ids
    assert structured_ids == legacy_ids


@pytest.mark.parametrize(
    "filters",
    [
        {"shot_size": ["CLOSE"]},
        {"camera_movement": ["ROLL"]},
        {"focal_length": ["50mm"]},
        {"lighting_phase": ["SUNSET"]},
        {"aspect_ratio": ["1:1"]},
        {"entity_type": ["set"]},
        {"entity_id": ["contains space"]},
    ],
)
def test_c18_invalid_cinematography_filters_fail_closed(
    client: TestClient, filters: dict[str, object]
) -> None:
    _, response = _query(client, filters)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "UNSUPPORTED_FILTER_VALUE"

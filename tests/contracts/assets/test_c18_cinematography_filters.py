"""C18-T3 资产注册表摄制及实体母库复合过滤契约。"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.asset_registry import repository as asset_repository
from gw.entities import repository as entity_repository
from gw.core import storage

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PATH = "/api/asset-registry/assets"


def _create_entity(
    entity_type: str, name: str, *, project_id: str = "c18-filter-fixture"
) -> str:
    result = entity_repository.create_entity(
        {"project_id": project_id, "entity_type": entity_type, "name": name}
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


def _create_project(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/asset-registry/projects",
        headers=AUTH,
        json={"name": name, "project_type": "film"},
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _assign_asset_project(asset_id: str, project_id: str) -> None:
    def mutate(raw: dict[str, object]) -> None:
        asset = raw["assets"][asset_id]
        asset["project_id"] = project_id
        asset["project_ids"] = [project_id]
        raw["revision"] += 1

    asset_repository.state().mutate(mutate)


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


def test_c18_dirty_entity_record_is_skipped_without_http_500(
    client: TestClient,
) -> None:
    character_id = _create_entity("character", "Mira")
    asset_ids = _register_assets(
        client,
        [("mira.png", {"entity_ids": [character_id, "ent_dirty_1"]})],
    )
    raw = entity_repository.state().read()
    raw["entities"]["ent_dirty_1"] = {
        "entity_id": "ent_dirty_1",
        "project_id": "c18-filter-fixture",
        "name": "broken row",
        "created_at": "not-a-date",
    }
    raw["revision"] += 1
    entity_repository.state().write(raw)

    body, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 200, response.text
    assert body["total"] == 1
    assert [item["asset_id"] for item in body["items"]] == asset_ids


def test_c18_entity_filter_obeys_explicit_project_scope(
    client: TestClient,
) -> None:
    project_a = _create_project(client, "C18 filter project A")
    project_b = _create_project(client, "C18 filter project B")
    entity_a = _create_entity("character", "Mira", project_id=project_a)
    entity_b = _create_entity("character", "Noah", project_id=project_b)
    asset_ids = _register_assets(
        client,
        [
            ("linked-a.png", {"entity_ids": [entity_a]}),
            # 故意构造跨项目陈旧引用，验证显式项目范围约束。
            ("cross-project.png", {"entity_ids": [entity_b]}),
        ],
    )
    for asset_id in asset_ids:
        _assign_asset_project(asset_id, project_a)

    unscoped, unscoped_response = _query(client, {"entity_id": [entity_b]})
    scoped, scoped_response = _query(
        client, {"project_id": [project_a], "entity_id": [entity_b]}
    )
    scoped_local, scoped_local_response = _query(
        client, {"project_id": [project_a], "entity_id": [entity_a]}
    )

    assert unscoped_response.status_code == 200, unscoped_response.text
    assert {item["asset_id"] for item in unscoped["items"]} == {asset_ids[1]}
    assert scoped_response.status_code == 200, scoped_response.text
    assert scoped["items"] == []
    assert scoped_local_response.status_code == 200, scoped_local_response.text
    assert {item["asset_id"] for item in scoped_local["items"]} == {asset_ids[0]}


def test_c18_filter_strings_support_csv_or_legacy_mix_and_top_level_fallback(
    client: TestClient,
) -> None:
    first_entity = _create_entity("character", "Mira")
    second_entity = _create_entity("character", "Noah")
    asset_ids = _register_assets(
        client,
        [
            ("close.png", {"cinematography": {"shot_size": "CU"}, "entity_ids": [first_entity]}),
            ("wide.png", {}),
        ],
    )
    raw = asset_repository.state().read()
    raw["assets"][asset_ids[1]]["shot_size"] = "EWS"
    raw["assets"][asset_ids[1]]["entity_ids"] = [second_entity]
    asset_repository.state().write(raw)

    body, response = _query(
        client,
        {
            "kind": "image",
            "shot_size": "CU, EWS",
            "entity_type": "character",
            "entity_id": f"{first_entity}, {second_entity}",
        },
    )

    assert response.status_code == 200, response.text
    assert {item["asset_id"] for item in body["items"]} == set(asset_ids)


def test_c18_null_and_empty_new_filters_are_noops(client: TestClient) -> None:
    asset_ids = _register_assets(
        client,
        [("close.png", {"cinematography": {"shot_size": "CU"}}), ("wide.png", {})],
    )

    body, response = _query(
        client, {"shot_size": [], "entity_type": None, "entity_id": []}
    )

    assert response.status_code == 200, response.text
    assert {item["asset_id"] for item in body["items"]} == set(asset_ids)


def test_c18_archived_entities_do_not_match_filters(client: TestClient) -> None:
    entity_id = _create_entity("character", "Mira")
    _register_assets(
        client, [("archived-character.png", {"entity_ids": [entity_id]})]
    )
    revision = entity_repository.state().read()["revision"]
    entity_repository.archive_entity(entity_id, expected_version=revision)

    body, response = _query(client, {"entity_id": [entity_id]})

    assert response.status_code == 200, response.text
    assert body["total"] == 0
    assert body["items"] == []


def test_c18_entity_filter_cache_invalidates_when_content_changes_without_revision(
    client: TestClient,
) -> None:
    entity_id = _create_entity("character", "Mira")
    _register_assets(client, [("mira.png", {"entity_ids": [entity_id]})])

    first, first_response = _query(client, {"entity_type": ["character"]})
    assert first_response.status_code == 200, first_response.text
    assert first["total"] == 1

    raw = entity_repository.state().read()
    raw["entities"][entity_id]["entity_type"] = "location"
    entity_repository.state().write(raw)

    updated, updated_response = _query(client, {"entity_type": ["character"]})

    assert updated_response.status_code == 200, updated_response.text
    assert updated["total"] == 0
    assert updated["items"] == []


def test_c18_entity_filter_cache_uses_read_snapshot_during_external_write_race(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_a = _create_project(client, "C18 race project A")
    project_b = _create_project(client, "C18 race project B")
    entity_id = _create_entity("character", "Mira", project_id=project_a)
    asset_ids = _register_assets(
        client, [("race-character.png", {"entity_ids": [entity_id]})]
    )
    _assign_asset_project(asset_ids[0], project_a)

    first, first_response = _query(
        client, {"project_id": [project_a], "entity_type": ["character"]}
    )
    assert first_response.status_code == 200, first_response.text
    assert first["total"] == 1

    entity_state = entity_repository.state()
    entity_file = storage.data_root() / f"{entity_state.namespace}.json"
    original_load = storage.load
    original_read_bytes = Path.read_bytes
    rewrite_pending = True

    def rewrite_after_snapshot() -> None:
        nonlocal rewrite_pending
        if not rewrite_pending:
            return
        rewrite_pending = False
        raw = json.loads(original_read_bytes(entity_file))
        raw["entities"][entity_id]["project_id"] = project_b
        entity_file.write_bytes(
            json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
        )

    def load_with_external_write(namespace: str, default: object = None) -> object:
        result = original_load(namespace, default)
        if namespace == entity_state.namespace:
            rewrite_after_snapshot()
        return result

    def read_bytes_with_external_write(path: Path) -> bytes:
        content = original_read_bytes(path)
        if path == entity_file:
            rewrite_after_snapshot()
        return content

    monkeypatch.setattr(storage, "load", load_with_external_write)
    monkeypatch.setattr(Path, "read_bytes", read_bytes_with_external_write)

    raced, raced_response = _query(
        client, {"project_id": [project_a], "entity_type": ["character"]}
    )
    assert raced_response.status_code == 200, raced_response.text
    assert raced["total"] == 1

    updated, updated_response = _query(
        client, {"project_id": [project_a], "entity_type": ["character"]}
    )
    assert updated_response.status_code == 200, updated_response.text
    assert updated["total"] == 0
    assert updated["items"] == []
    assert rewrite_pending is False


def test_c18_entity_filter_cache_detects_same_size_same_mtime_rewrite(
    client: TestClient,
) -> None:
    project_a = _create_project(client, "C18 metadata project A")
    project_b = _create_project(client, "C18 metadata project B")
    entity_id = _create_entity("character", "Mira", project_id=project_a)
    asset_ids = _register_assets(
        client, [("same-stat-character.png", {"entity_ids": [entity_id]})]
    )
    _assign_asset_project(asset_ids[0], project_a)

    first, first_response = _query(
        client, {"project_id": [project_a], "entity_type": ["character"]}
    )
    assert first_response.status_code == 200, first_response.text
    assert first["total"] == 1

    entity_state = entity_repository.state()
    entity_file = storage.data_root() / f"{entity_state.namespace}.json"
    original_stat = entity_file.stat()
    raw = entity_state.read()
    raw["entities"][entity_id]["project_id"] = project_b
    entity_state.write(raw)
    os.utime(entity_file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    rewritten_stat = entity_file.stat()
    assert rewritten_stat.st_size == original_stat.st_size
    assert rewritten_stat.st_mtime_ns == original_stat.st_mtime_ns

    updated, updated_response = _query(
        client, {"project_id": [project_a], "entity_type": ["character"]}
    )
    assert updated_response.status_code == 200, updated_response.text
    assert updated["total"] == 0
    assert updated["items"] == []


def test_c18_entity_filter_fails_closed_when_stored_revision_is_missing(
    client: TestClient,
) -> None:
    _create_entity("character", "Mira")
    raw = entity_repository.state().read()
    raw.pop("revision")
    entity_repository.state().write(raw)

    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"


def test_c18_entity_filter_fails_closed_when_entity_data_is_not_a_dict(
    client: TestClient,
) -> None:
    entity_repository.state().write(["invalid", "entity data"])

    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"


def test_c18_entity_filter_defaults_revision_only_when_file_is_missing(
    client: TestClient,
) -> None:
    entity_state = entity_repository.state()
    entity_file = storage.data_root() / f"{entity_state.namespace}.json"
    entity_file.unlink(missing_ok=True)

    body, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 200, response.text
    assert body["total"] == 0
    assert body["items"] == []


def test_c18_entity_filter_logs_and_fails_closed_on_read_oserror(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _create_entity("character", "Mira")
    entity_state = entity_repository.state()
    entity_file = storage.data_root() / f"{entity_state.namespace}.json"
    original_read_bytes = Path.read_bytes

    def fail_entity_read(path: Path) -> bytes:
        if path == entity_file:
            raise PermissionError("fixture read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_entity_read)

    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
    assert "读取实体母库文件失败" in caplog.text


@pytest.mark.parametrize(
    "entity_revision", [True, 0, -1, "oops", 1.5, None, "5", 1.0]
)
def test_c18_entity_filter_fails_closed_for_invalid_stored_revision(
    client: TestClient, entity_revision: object, caplog: pytest.LogCaptureFixture
) -> None:
    _, warm_response = _query(client, {"entity_type": ["character"]})
    assert warm_response.status_code == 200, warm_response.text

    entity_id = _create_entity("character", "Mira")
    _register_assets(client, [("mira.png", {"entity_ids": [entity_id]})])
    raw = entity_repository.state().read()
    raw["revision"] = entity_revision
    entity_repository.state().write(raw)

    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
    assert "revision 非法" in caplog.text


def test_c18_entity_filter_fails_closed_when_file_is_invalid_json(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _create_entity("character", "Mira")
    entity_file = storage.file_for(entity_repository.NS_ENTITIES)
    entity_file.write_bytes(b"{not a valid json")

    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
    assert "文件损坏" in caplog.text or "数据无效" in caplog.text


def test_c18_entity_filter_fails_closed_when_file_is_invalid_utf8(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _create_entity("character", "Mira")
    entity_file = storage.file_for(entity_repository.NS_ENTITIES)
    entity_file.write_bytes(b"\xff\xfe\x00\x00\x12\x34")

    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
    assert "文件损坏" in caplog.text or "数据无效" in caplog.text


def test_c18_entity_filter_fails_closed_when_file_is_empty_or_whitespace_or_bom(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _create_entity("character", "Mira")
    entity_file = storage.file_for(entity_repository.NS_ENTITIES)

    # 1. 零字节
    entity_file.write_bytes(b"")
    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"

    # 2. 纯空白
    entity_file.write_bytes(b"   \r\n\t  ")
    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"

    # 3. 带 BOM
    entity_file.write_bytes(b'\xef\xbb\xbf{"revision": 1, "entities": {}}')
    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
    assert "BOM" in caplog.text


@pytest.mark.parametrize("invalid_entities", [None, [], "not_a_dict", 123])
def test_c18_entity_filter_fails_closed_when_entities_field_is_missing_or_not_dict(
    client: TestClient, invalid_entities: object, caplog: pytest.LogCaptureFixture
) -> None:
    _create_entity("character", "Mira")
    entity_file = storage.file_for(entity_repository.NS_ENTITIES)
    payload: dict[str, object] = {"revision": 2}
    if invalid_entities is not None:
        payload["entities"] = invalid_entities
    entity_file.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    caplog.clear()
    _, response = _query(client, {"entity_type": ["character"]})

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "ENTITY_INDEX_UNAVAILABLE"
    assert "entities" in caplog.text


def test_c18_entity_filter_cursor_paginates_and_detects_entity_changes(
    client: TestClient,
) -> None:
    entity_id = _create_entity("character", "Mira")
    asset_ids = _register_assets(
        client,
        [
            ("a-first.png", {"entity_ids": [entity_id]}),
            ("b-second.png", {"entity_ids": [entity_id]}),
        ],
    )
    filters = {"entity_id": [entity_id]}
    first, first_response = _query(client, filters, limit=1)
    cursor = first["page"]["next_cursor"]
    assert first_response.status_code == 200, first_response.text
    assert first["items"][0]["asset_id"] == asset_ids[0]
    assert cursor

    second, second_response = _query(client, filters, cursor=cursor, limit=1)
    assert second_response.status_code == 200, second_response.text
    assert second["items"][0]["asset_id"] == asset_ids[1]

    revision = entity_repository.state().read()["revision"]
    entity_repository.update_entity(
        entity_id,
        {"expected_version": revision, "name": "Mira revised"},
    )
    _, stale = _query(client, filters, cursor=cursor, limit=1)
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"

    matching_type, matching_type_response = _query(
        client, {"entity_type": ["character"]}
    )
    assert matching_type_response.status_code == 200, matching_type_response.text
    assert matching_type["total"] == 2


def test_c18_invalid_filter_error_names_the_field_path(client: TestClient) -> None:
    _, response = _query(client, {"shot_size": ["CLOSE"]})

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "UNSUPPORTED_FILTER_VALUE"
    assert "filters.shot_size.0" in response.json()["detail"]["message"]


@pytest.mark.parametrize("entity_revision", [True, 0])
def test_c18_entity_cursor_rejects_invalid_entity_revision(
    client: TestClient, entity_revision: object
) -> None:
    entity_id = _create_entity("character", "Mira")
    _register_assets(
        client,
        [
            ("a-first.png", {"entity_ids": [entity_id]}),
            ("b-second.png", {"entity_ids": [entity_id]}),
        ],
    )
    first, response = _query(client, {"entity_id": [entity_id]}, limit=1)
    assert response.status_code == 200, response.text
    cursor = first["page"]["next_cursor"]
    decoded = json.loads(
        base64.b64decode(
            cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
        )
    )
    decoded["entity_revision"] = entity_revision
    malformed_cursor = base64.urlsafe_b64encode(
        json.dumps(decoded, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")

    _, malformed = _query(
        client, {"entity_id": [entity_id]}, cursor=malformed_cursor, limit=1
    )

    assert malformed.status_code == 400, malformed.text
    assert malformed.json()["detail"]["code"] == "INVALID_CURSOR"

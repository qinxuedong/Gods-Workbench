# -*- coding: utf-8 -*-
"""C12-T1 ASC 元数据契约与实体母库 API 测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gw.api.routes_entities import router
from gw.cinematography.schema import (
    BreakdownCategory,
    CameraAngle,
    CameraMovement,
    CinematographyMetadata,
    CompositionRule,
    FocalLengthTier,
    ShotSize,
)
from gw.core.errors import CleanroomException


AUTH = {"Authorization": "Bearer c12-t1-test", "X-User-Role": "editor"}
READONLY = {"Authorization": "Bearer c12-t1-test", "X-User-Role": "readonly"}


def _app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(CleanroomException)
    async def cleanroom_error(_request, exc: CleanroomException):
        return __import__("fastapi").responses.JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope().model_dump(exclude_none=True),
        )

    app.include_router(router)
    return app


def test_asc_dictionaries_are_closed_and_strongly_typed():
    assert len(ShotSize) == 6
    assert len(CameraAngle) == 6
    assert len(CameraMovement) == 11
    assert len(FocalLengthTier) == 5
    assert len(CompositionRule) == 6
    assert len(BreakdownCategory) == 8

    metadata = CinematographyMetadata(
        shot_size="CU",
        camera_angle="DUTCH_ANGLE",
        camera_movement="DOLLY_IN",
        focal_length="PORTRAIT",
        composition="LOOK_ROOM",
    )
    assert metadata.shot_size is ShotSize.CU
    assert metadata.camera_movement is CameraMovement.DOLLY_IN
    assert metadata.model_dump(mode="json")["focal_length"] == "PORTRAIT"

    with pytest.raises(ValidationError):
        CinematographyMetadata(camera_movement="ZOOM")
    with pytest.raises(ValidationError):
        CinematographyMetadata(unknown_field="value")


def test_entity_catalog_rest_crud_persists_and_enforces_cas(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        missing_auth = client.get("/api/entities?project_id=proj_c12")
        assert missing_auth.status_code == 401

        created = client.post(
            "/api/entities",
            headers=AUTH,
            json={
                "project_id": "proj_c12",
                "entity_type": "character",
                "name": "Mira",
                "feature_base_prompt": "silver-haired navigator",
                "lora_trigger_words": ["mira_v001", "navigator_style"],
                "cinematography": {
                    "shot_size": "MCU",
                    "camera_angle": "EYE_LEVEL",
                    "camera_movement": "STATIC",
                    "focal_length": "STANDARD",
                    "composition": "RULE_OF_THIRDS",
                },
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["fixture_id"] == "c12-t1-master-catalog-001"
        assert body["data_status"] == "ok"
        assert body["entity"]["entity_id"] == "ent_0001"
        assert body["entity"]["project_id"] == "proj_c12"
        assert body["entity"]["entity_type"] == "character"
        assert body["entity"]["feature_base_prompt"] == "silver-haired navigator"
        assert body["entity"]["lora_trigger_words"] == ["mira_v001", "navigator_style"]
        assert body["revision"] == 2

        listed = client.get("/api/entities?project_id=proj_c12", headers=AUTH)
        assert listed.status_code == 200
        assert [item["entity_id"] for item in listed.json()["entities"]] == ["ent_0001"]

        updated = client.patch(
            "/api/entities/ent_0001",
            headers=AUTH,
            json={"expected_version": body["revision"], "description": "lead character"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["entity"]["description"] == "lead character"
        assert updated.json()["revision"] == 3

        stale = client.patch(
            "/api/entities/ent_0001",
            headers=AUTH,
            json={"expected_version": body["revision"], "name": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"
        assert stale.json()["detail"]["expected_version"] == body["revision"]
        assert stale.json()["detail"]["current_version"] == 3

        readonly = client.post(
            "/api/entities",
            headers=READONLY,
            json={"project_id": "proj_c12", "entity_type": "prop", "name": "key"},
        )
        assert readonly.status_code == 403

    # 新的仓储读取实例仍从相同测试数据根恢复，不依赖进程内缓存。
    from gw.entities import repository

    restored = repository.list_entities("proj_c12")
    assert restored["entities"][0]["entity_id"] == "ent_0001"
    assert restored["entities"][0]["description"] == "lead character"


def test_entity_catalog_rejects_invalid_scope_and_entity_type(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))

    with TestClient(_app()) as client:
        invalid = client.post(
            "/api/entities",
            headers=AUTH,
            json={"project_id": "项目", "entity_type": "unknown", "name": "x"},
        )
        assert invalid.status_code == 422

        empty_name = client.post(
            "/api/entities",
            headers=AUTH,
            json={"project_id": "proj_c12", "entity_type": "prop", "name": "   "},
        )
        assert empty_name.status_code == 422
